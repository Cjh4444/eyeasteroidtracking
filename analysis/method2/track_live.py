"""METHOD 2, stage T1 (interactive) -- supervised asteroid tracking.

Plays each epoch forward, drawing the tracked box as it goes. When the track
drifts you pause, rewind to the frame where it went wrong, redraw the box there,
and continue -- the tracker re-seeds from your correction and everything after
that frame is retracked.

This beats fire-and-forget tracking because the failure modes are silent: a
tracker stuck on static game art template-matches itself perfectly and reports
score 1.00 every frame. Watching it is the only honest check, so the tool is
built around watching it.

It auto-pauses whenever confidence drops or the motion gate starts coasting, so
you are only asked to look at the frames that actually need you.

Run:  LB_OUT=out_track ../.venv/bin/python track_live.py

Three tracking algorithms, switchable mid-epoch with 'm' (retracks from the
current frame, so you can flip to a slower one only where it is needed):

  fast      CSRT + fixed template. Quick, fine on slow epochs.
  accurate  Adds background subtraction. The static game art cancels against a
            per-epoch median, so what is left is whatever moved -- which stays
            true under the motion blur that defeats template matching on the
            high-waveFreq epochs. Slower to start (one background pass).
  blob      Background subtraction alone, no template. Most robust to heavy blur
            and appearance change, least constrained -- lean on the motion gate.

Controls
  space          play / pause
  left click     put the box here, re-seed, and retrack from this frame on
  left / right   step one frame back / forward   (also , and .)
  < / >          jump 10 frames back / forward
  m              cycle algorithm and retrack from here
  + / -          grow / shrink the box
  [ / ]          slower / faster playback
  r              restart this epoch from its first frame
  a / enter      accept this epoch and move to the next
  s              skip this epoch
  j / k          next / previous epoch
  u              undo back to the last correction
  q / esc        save and quit

Skipped one by accident? Press 'k' to step back an epoch, or quit and re-run --
a skip saves nothing, so it resumes at the first unfinished epoch. To go straight
to specific ones: --epochs 10,13

Each accepted epoch is written to <LB_OUT>/asteroid_track.csv immediately, so
quitting never loses work and re-running resumes at the first unfinished epoch.
"""
import argparse
import sys

import cv2
import numpy as np
import pandas as pd

from common import OUT, SCENE_VIDEO, WORLD_TS_CSV
from track_asteroid import (DEFAULT_BOX, MAX_STEP_PX, MIN_SCORE, SEARCH_R,
                            VEL_DAMP, make_tracker, propose_seeds, refine)

TRACK_CSV = OUT / "asteroid_track.csv"
SEEDS_CSV = OUT / "asteroid_seeds.csv"
WIN = "supervised asteroid tracking"

ALGOS = ("fast", "accurate", "blob")
BG_SAMPLES = 48           # frames sampled to build the static-art background
BLOB_SMOOTH = 5           # px blur on the difference image before peak finding

# Arrow keys come back as full key codes from waitKeyEx, and the codes differ per
# backend (macOS/Cocoa vs Qt vs Windows), so accept all of them.
KEY_LEFT = {63234, 65361, 2424832, 81}
KEY_RIGHT = {63235, 65363, 2555904, 83}

DISPLAY_SCALE = 0.75
TRAIL = 60                # frames of path history drawn behind the box
STUCK_WIN = 45            # frames over which the asteroid must visibly move
STUCK_PX = 8.0            # ...by at least this much, or the track is stuck
JPEG_Q = 90               # frame cache quality; ~70 KB/frame at 1600x1200 gray


# --- per-epoch state ----------------------------------------------------------

class Epoch:
    """One epoch's frames, cached in memory, plus the track built over them.

    Frames are cached JPEG-encoded rather than raw: an epoch is ~420 frames of
    1600x1200 grayscale, which is 800 MB raw but ~30 MB as JPEG, and decoding one
    costs ~2 ms. That buys instant random access, which is what makes rewinding
    feel immediate.
    """

    def __init__(self, ep, frames, ts, cap, algo="fast"):
        self.ep, self.frame_ids, self.ts = ep, frames, ts
        self.algo = algo
        self._bg = None
        self.jpg = []
        for i, f in enumerate(frames):
            if i == 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(f))
            ok, fr = cap.read()
            if not ok:
                break
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            self.jpg.append(cv2.imencode(".jpg", g, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])[1])
        self.n = len(self.jpg)
        self.xy = np.full((self.n, 2), np.nan)
        self.score = np.zeros(self.n)
        self.box = DEFAULT_BOX
        self.done_to = -1          # last frame with a track value
        self.seed_frames = []      # indices the operator corrected, for undo
        self.trk = None
        self.tmpl = None
        self.vel = np.zeros(2)

    def gray(self, i):
        return cv2.imdecode(self.jpg[i], cv2.IMREAD_GRAYSCALE)

    def background(self):
        """Median of frames spread across the epoch = the static game art.

        The asteroid is somewhere different in each sampled frame, so it averages
        out; Earth, Moon, launcher and stars survive. Subtracting this leaves the
        moving object, blurred or not.
        """
        if self._bg is None:
            idx = np.linspace(0, self.n - 1, min(BG_SAMPLES, self.n)).astype(int)
            self._bg = np.median(np.stack([self.gray(k) for k in idx]),
                                 axis=0).astype(np.float32)
        return self._bg

    def blob_at(self, g, cx, cy, radius=SEARCH_R):
        """Brightest moving thing near (cx, cy). Returns (x, y, score)."""
        d = cv2.GaussianBlur(np.clip(g.astype(np.float32) - self.background(), 0, None),
                             (0, 0), BLOB_SMOOTH)
        H_, W_ = d.shape
        x0, x1 = int(max(0, cx - radius)), int(min(W_, cx + radius))
        y0, y1 = int(max(0, cy - radius)), int(min(H_, cy + radius))
        win = d[y0:y1, x0:x1]
        if win.size == 0 or win.max() < 6:
            return cx, cy, 0.0
        m = win >= 0.6 * win.max()
        w = win * m
        tot = w.sum()
        if tot <= 0:
            return cx, cy, 0.0
        yy, xx = np.mgrid[y0:y1, x0:x1]
        # Normalise against the frame-wide spread so the score is comparable to
        # the template's correlation score.
        conf = float(np.clip(win.max() / max(1.0, d.max()), 0, 1))
        return float((w * xx).sum() / tot), float((w * yy).sum() / tot), conf

    def seed(self, i, x, y):
        """Anchor the track at frame i and discard everything after it."""
        g = self.gray(i)
        h = self.box // 2
        yi, xi = int(round(y)), int(round(x))
        t = g[yi - h:yi + h, xi - h:xi + h]
        if t.size == 0:
            return False
        self.tmpl = t.copy()
        self.trk = make_tracker("csrt")
        self.trk.init(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR),
                      (int(x - h), int(y - h), self.box, self.box))
        self.xy[i] = (x, y)
        self.score[i] = 1.0
        self.xy[i + 1:] = np.nan
        self.score[i + 1:] = 0.0
        self.done_to = i
        self.vel[:] = 0
        if i not in self.seed_frames:
            self.seed_frames.append(i)
        return True

    def advance(self):
        """Track one more frame. Returns the new frame index, or None if done."""
        i = self.done_to + 1
        if self.tmpl is None or i >= self.n:
            return None
        g = self.gray(i)
        prev = self.xy[i - 1] if i > 0 else self.xy[i]
        pred = prev + self.vel * VEL_DAMP

        cand = []
        if self.algo in ("fast", "accurate"):
            rx, ry, s = refine(g, self.tmpl, pred[0], pred[1])
            cand.append(((rx, ry), s))
            tok, bb = self.trk.update(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR))
            if tok:
                cand.append(((bb[0] + bb[2] / 2.0, bb[1] + bb[3] / 2.0), s * 0.99))
        if self.algo in ("accurate", "blob"):
            bx, by, bs = self.blob_at(g, pred[0], pred[1])
            # Preferred under blur, where the template score collapses but the
            # moving-object signal does not.
            cand.append(((bx, by), bs * (1.05 if self.algo == "blob" else 0.95)))

        # Gate against the PREVIOUS position, not the prediction: at a direction
        # reversal the prediction points the wrong way, and gating on it throws
        # away the correct match exactly when the asteroid turns around.
        best, best_s = None, -1.0
        for (cx, cy), cs in cand:
            if np.hypot(cx - prev[0], cy - prev[1]) <= MAX_STEP_PX and cs > best_s:
                best, best_s = (cx, cy), cs

        if best is not None and best_s >= MIN_SCORE:
            new = np.array(best)
        else:
            new = pred                      # coast, and flag it
            best_s = min(best_s, MIN_SCORE - 0.01)

        self.vel = new - prev
        self.xy[i] = new
        self.score[i] = max(best_s, 0.0)
        self.done_to = i
        return i

    def undo(self):
        """Roll back to the previous operator correction."""
        if len(self.seed_frames) < 2:
            return
        self.seed_frames.pop()
        i = self.seed_frames[-1]
        x, y = self.xy[i]
        self.seed_frames.pop()
        self.seed(i, x, y)

    def stuck_at(self, i):
        """Is the box parked? The silent failure mode worth shouting about.

        A tracker locked onto static game art template-matches itself perfectly
        and reports score 1.00 forever, so confidence cannot detect this. Actual
        displacement can: the asteroid is never stationary for a second and a half
        mid-trial.
        """
        lo = i - STUCK_WIN
        if lo < 0 or not np.isfinite(self.xy[lo:i + 1, 0]).all():
            return False
        seg = self.xy[lo:i + 1]
        return float(np.abs(seg - seg[0]).max()) < STUCK_PX

    def rows(self):
        ok = np.isfinite(self.xy[:, 0])
        return pd.DataFrame(dict(epoch=self.ep, frame=self.frame_ids[:self.n][ok],
                                 ts_ns=self.ts[:self.n][ok],
                                 ast_px=self.xy[ok, 0], ast_py=self.xy[ok, 1],
                                 score=self.score[ok]))


# --- drawing ------------------------------------------------------------------

def render(e, i, playing, speed, msg):
    disp = cv2.cvtColor(cv2.resize(e.gray(i), None, fx=DISPLAY_SCALE, fy=DISPLAY_SCALE,
                                   interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR)
    S = DISPLAY_SCALE

    # Path history, fading backwards, so drift is obvious as a kink.
    lo = max(0, i - TRAIL)
    pts = e.xy[lo:i + 1]
    for k in range(1, len(pts)):
        if not (np.isfinite(pts[k]).all() and np.isfinite(pts[k - 1]).all()):
            continue
        a = int(60 + 195 * k / max(1, len(pts)))
        cv2.line(disp, (int(pts[k - 1][0] * S), int(pts[k - 1][1] * S)),
                 (int(pts[k][0] * S), int(pts[k][1] * S)), (0, a, a), 1, cv2.LINE_AA)

    if np.isfinite(e.xy[i]).all():
        x, y = e.xy[i] * S
        h = int(e.box * S / 2)
        weak = e.score[i] < MIN_SCORE
        col = (60, 90, 255) if weak else (0, 230, 0)
        cv2.rectangle(disp, (int(x - h), int(y - h)), (int(x + h), int(y + h)), col, 2)
        cv2.drawMarker(disp, (int(x), int(y)), col, cv2.MARKER_CROSS, 14, 1)

    # Frames the operator corrected, marked on the scrub bar.
    W, H = disp.shape[1], disp.shape[0]
    y0 = H - 34
    cv2.rectangle(disp, (10, y0), (W - 10, y0 + 8), (55, 55, 55), -1)
    if e.done_to >= 0:
        cv2.rectangle(disp, (10, y0), (10 + int((W - 20) * e.done_to / max(1, e.n - 1)), y0 + 8),
                      (0, 140, 140), -1)
    for sf in e.seed_frames:
        sx = 10 + int((W - 20) * sf / max(1, e.n - 1))
        cv2.line(disp, (sx, y0 - 4), (sx, y0 + 12), (0, 220, 255), 2)
    cx = 10 + int((W - 20) * i / max(1, e.n - 1))
    cv2.line(disp, (cx, y0 - 6), (cx, y0 + 14), (255, 255, 255), 2)

    if e.stuck_at(i):
        cv2.putText(disp, "BOX PARKED - likely tracking static art", (12, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (60, 90, 255), 2, cv2.LINE_AA)
    weak_n = int((e.score[:e.done_to + 1] < MIN_SCORE).sum()) if e.done_to >= 0 else 0
    cv2.putText(disp, f"epoch {e.ep}   frame {i+1}/{e.n}   "
                f"{'PLAYING' if playing else 'PAUSED'} x{speed}   "
                f"algo {e.algo.upper()}   "
                f"score {e.score[i]:.2f}   low-conf {weak_n}   box {e.box}px",
                (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (240, 240, 240), 1, cv2.LINE_AA)
    if msg:
        cv2.putText(disp, msg, (12, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.52,
                    (120, 220, 255), 1, cv2.LINE_AA)
    cv2.putText(disp, "space play | click=re-seed | <-/-> step | < > 10 | m algo | +- box | "
                "[ ] speed | u undo | r restart | a accept | s skip | q quit",
                (12, H - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (170, 170, 170), 1, cv2.LINE_AA)
    return disp


# --- main ---------------------------------------------------------------------

def save(done):
    if done:
        pd.concat(done.values(), ignore_index=True).sort_values(
            ["epoch", "frame"]).to_csv(TRACK_CSV, index=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--algo", default="fast", choices=ALGOS,
                    help="starting algorithm; 'm' cycles it mid-epoch")
    ap.add_argument("--epochs", default="",
                    help="work on only these, e.g. 10 or 10,13,27. Already-saved "
                         "epochs are kept; these are redone.")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    tl = pd.read_csv(OUT / "timeline.csv")
    wts = pd.read_csv(WORLD_TS_CSV)["timestamp [ns]"].to_numpy()

    frames_by_ep, t0_by_ep = {}, {}
    for ep, d in tl.groupby("EpochIndex"):
        lo, hi = np.searchsorted(wts, [int(d["t_utc_ns"].min()), int(d["t_utc_ns"].max())])
        if hi - lo >= 30:
            frames_by_ep[int(ep)] = np.arange(lo, hi)
            t0_by_ep[int(ep)] = lo

    done = {}
    if TRACK_CSV.exists():
        old = pd.read_csv(TRACK_CSV)
        for ep, g in old.groupby("epoch"):
            done[int(ep)] = g
        print(f"resuming: {len(done)} epochs already tracked")

    cap = cv2.VideoCapture(str(SCENE_VIDEO))
    if not cap.isOpened():
        sys.exit("could not open scene video")
    from pathlib import Path
    prop = propose_seeds(Path("out_frame_annotate_method/corners.csv"),
                         {e: 0 for e in frames_by_ep}, t0_by_ep)

    order = sorted(frames_by_ep)
    if args.epochs:
        only = {int(x) for x in args.epochs.split(",")}
        order = [e for e in order if e in only]
        if not order:
            sys.exit(f"none of {sorted(only)} are trackable epochs")
        pos = 0
        print(f"working on epochs {order} (others already saved are untouched)")
    else:
        pos = next((k for k, e in enumerate(order) if e not in done), 0)

    state = {"click": None}

    def on_mouse(ev, x, y, flags, _):
        if ev == cv2.EVENT_LBUTTONDOWN:
            state["click"] = (x / DISPLAY_SCALE, y / DISPLAY_SCALE)

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)

    quit_all = False
    while not quit_all and 0 <= pos < len(order):
        ep = order[pos]
        print(f"loading epoch {ep} ...", flush=True)
        e = Epoch(ep, frames_by_ep[ep], wts[frames_by_ep[ep]], cap, algo=args.algo)
        if ep in prop:
            e.box = prop[ep][2]
            e.seed(0, prop[ep][0], prop[ep][1])
        i, playing, speed, msg = 0, False, 1, "click the asteroid to seed, then press space"

        while True:
            if state["click"] is not None:
                cx, cy = state["click"]; state["click"] = None
                if e.seed(i, cx, cy):
                    msg = f"re-seeded at frame {i+1}; retracking from here"
            disp = render(e, i, playing, speed, msg)
            cv2.imshow(WIN, disp)
            kx = cv2.waitKeyEx(max(1, int(33 / speed)) if playing else 20)
            k = kx & 0xFF if 0 <= kx < 0x110000 else -1

            if playing:
                for _ in range(speed):
                    if i < e.done_to:
                        i += 1
                    else:
                        j = e.advance()
                        if j is None:
                            playing = False; msg = "end of epoch -- 'a' to accept"; break
                        i = j
                        if e.score[i] < MIN_SCORE:
                            playing = False
                            msg = "LOW CONFIDENCE -- rewind and re-seed if the box drifted"
                            break
                        if e.stuck_at(i):
                            playing = False
                            msg = ("BOX IS PARKED -- it is probably locked onto static "
                                   "art, not the asteroid. Re-seed on the asteroid.")
                            break

            if k in (ord("q"), 27):
                quit_all = True; break
            elif k == ord(" "):
                playing = not playing; msg = ""
            elif k == ord(".") or kx in KEY_RIGHT:
                playing = False
                j = i + 1 if i < e.done_to else e.advance()
                i = i if j is None else j
            elif k == ord(",") or kx in KEY_LEFT:
                playing = False; i = max(0, i - 1)
            elif k == ord(">"):
                playing = False; i = min(e.done_to, i + 10)
            elif k == ord("<"):
                playing = False; i = max(0, i - 10)
            elif k in (ord("+"), ord("=")):
                e.box = min(200, e.box + 4)
            elif k == ord("-"):
                e.box = max(12, e.box - 4)
            elif k == ord("]"):
                speed = min(8, speed * 2)
            elif k == ord("["):
                speed = max(1, speed // 2)
            elif k == ord("m"):
                e.algo = ALGOS[(ALGOS.index(e.algo) + 1) % len(ALGOS)]
                playing = False
                if np.isfinite(e.xy[i]).all():
                    e.seed(i, *e.xy[i])       # retrack from here with the new algo
                msg = f"algorithm -> {e.algo}" + (
                    " (building background...)" if e.algo != "fast" and e._bg is None else "")
            elif k == ord("u"):
                e.undo(); i = min(i, e.done_to); msg = "undone to previous correction"
            elif k == ord("r"):
                if ep in prop:
                    e.seed(0, prop[ep][0], prop[ep][1])
                i = 0; playing = False; msg = "restarted"
            elif k == ord("s"):
                pos += 1; break
            elif k in (ord("a"), 13, 10):
                while e.advance() is not None:      # finish the tail if any
                    pass
                done[ep] = e.rows()
                save(done)
                cov = np.isfinite(e.xy[:, 0]).mean() * 100
                print(f"  epoch {ep:2d} accepted: {cov:.0f}% covered, "
                      f"{int((e.score < MIN_SCORE).sum())} low-confidence frames, "
                      f"{len(e.seed_frames)} correction(s)")
                pos += 1; break
            elif k == ord("j"):
                pos += 1; break
            elif k == ord("k"):
                pos = max(0, pos - 1); break

    cap.release()
    cv2.destroyAllWindows()
    save(done)
    print(f"\n{len(done)}/{len(order)} epochs tracked -> {TRACK_CSV}")


if __name__ == "__main__":
    main()
