"""METHOD 2, stage T1 -- track the asteroid in the scene video.

Method 1 maps gaze into game coordinates via a homography anchored at the rest
periods. Its own diagnostics showed the weakness: the screen centre moves up to
100 px BETWEEN consecutive rests (median 25 px, ~4.4 game units), so whatever the
head does mid-trial is invisible to an interpolation between anchors.

This method sidesteps that. The asteroid is located in raw scene-camera pixels
every frame, and gaze is already in raw scene-camera pixels. Their difference is
therefore head-motion invariant by construction -- if the head turns, the asteroid
and the gaze point move together in the image and the difference is unchanged.
No homography sits in the critical path.

Two phases:
  A. seeding   -- confirm one box on the asteroid per epoch (auto-proposed)
  B. tracking  -- OpenCV tracker + template refinement, fully automatic

Run:  LB_OUT=out_track ../.venv/bin/python track_asteroid.py

Seeding keys
  left click    place the box centre on the asteroid
  + / -         grow / shrink the box
  . / ,         step one video frame forward / back
  a / enter     accept this seed
  s             skip this epoch
  b             go back to the previous epoch
  q / esc       save seeds and quit

Emits <LB_OUT>/asteroid_seeds.csv and <LB_OUT>/asteroid_track.csv.
"""
import argparse
import sys

import cv2
import numpy as np
import pandas as pd

from common import (AST_SPAWN_X, AST_SPAWN_Y, CORNER_GAME_XY, OUT, SCENE_VIDEO,
                    WORLD_TS_CSV)

SEEDS_CSV = OUT / "asteroid_seeds.csv"
TRACK_CSV = OUT / "asteroid_track.csv"
WIN = "seed the asteroid  |  click its centre, 'a' to accept"

DISPLAY_SCALE = 0.75
SEARCH_R = 40          # template search radius in px around the prediction
MIN_SCORE = 0.35       # below this the frame is marked unreliable
DEFAULT_BOX = 34       # px, ~2.5 game units diameter at the observed 5.7 px/unit

# Physical limit on how far the asteroid can move between two 30 Hz frames.
# Measured steps run to ~12 px on the waveFreq 3.5 epochs, so 30 px leaves room
# for head motion on top while still stopping a lost tracker from running away
# across the frame -- the dominant failure mode with a poor seed.
#
# NOTE this is measured from the PREVIOUS POSITION, never from the velocity
# prediction. Gating on the prediction rejects the true match at every direction
# reversal of the triangle wave (prediction points the wrong way, so the real
# asteroid lands ~2x the step size away), which is what broke the fast epochs.
MAX_STEP_PX = 30.0
VEL_DAMP = 0.6         # velocity used to CENTRE THE SEARCH only, not to gate


# --- seeding ------------------------------------------------------------------

def propose_seeds(corners_csv, epochs, wts):
    """Guess each epoch's start box from the method-1 corner annotations.

    Only a starting hint -- the tracker refines from here, and the operator can
    correct it. Falls back to the frame centre when no annotations exist.
    """
    prop = {}
    if corners_csv and corners_csv.exists():
        c = pd.read_csv(corners_csv).sort_values("ts_ns").reset_index(drop=True)
        quads = c[[f"{a}{n}" for n in range(4) for a in ("x", "y")]].to_numpy().reshape(-1, 4, 2)
        dst = np.array(CORNER_GAME_XY, dtype=np.float32)
        for ep, t0 in epochs.items():
            i = int(np.argmin(np.abs(c["ts_ns"].to_numpy() - t0)))
            H = cv2.getPerspectiveTransform(quads[i].astype(np.float32), dst)
            pt = cv2.perspectiveTransform(
                np.array([[[AST_SPAWN_X, AST_SPAWN_Y]]], dtype=np.float64),
                np.linalg.inv(H)).reshape(2)
            scale = np.linalg.norm(quads[i][1] - quads[i][0]) / 100.0
            prop[ep] = (float(pt[0]), float(pt[1]), max(16, int(6.0 * scale)))
    return prop


def seed_ui(cap, epochs, frames0, proposals):
    """Confirm one box per epoch. Mostly just pressing enter."""
    order = sorted(epochs)
    seeds = {}
    if SEEDS_CSV.exists():
        for _, r in pd.read_csv(SEEDS_CSV).iterrows():
            seeds[int(r["epoch"])] = (float(r["x"]), float(r["y"]), int(r["box"]))
        print(f"resuming: {len(seeds)} seeds already saved")

    state = {"pt": None, "box": DEFAULT_BOX, "cursor": None}

    def on_mouse(ev, x, y, flags, _):
        state["cursor"] = (x, y)
        if ev == cv2.EVENT_LBUTTONDOWN:
            state["pt"] = (x / DISPLAY_SCALE, y / DISPLAY_SCALE)

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)

    i, off, frame, need = 0, 0, None, True
    while 0 <= i < len(order):
        ep = order[i]
        if need:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frames0[ep] + off))
            ok, frame = cap.read()
            if not ok:
                off = 0; need = False; continue
            p = seeds.get(ep) or proposals.get(ep)
            state["pt"] = (p[0], p[1]) if p else (frame.shape[1] / 2, frame.shape[0] / 2)
            state["box"] = p[2] if p else DEFAULT_BOX
            need = False

        disp = cv2.resize(frame, None, fx=DISPLAY_SCALE, fy=DISPLAY_SCALE,
                          interpolation=cv2.INTER_AREA)
        px, py = (int(v * DISPLAY_SCALE) for v in state["pt"])
        h = int(state["box"] * DISPLAY_SCALE / 2)
        cv2.rectangle(disp, (px - h, py - h), (px + h, py + h), (0, 230, 0), 2)
        cv2.drawMarker(disp, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 18, 1)
        cv2.putText(disp, f"epoch {ep}  ({i+1}/{len(order)})  box {state['box']}px"
                    f"  frame {int(frames0[ep]+off)}  [{off:+d}]",
                    (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (240, 240, 240), 1, cv2.LINE_AA)
        cv2.putText(disp, "click the asteroid centre | +/- box | . , frame | a accept | s skip | b back | q quit",
                    (12, disp.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (170, 170, 170), 1, cv2.LINE_AA)
        cv2.imshow(WIN, disp)

        k = cv2.waitKey(20) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k in (ord("a"), 13, 10):
            seeds[ep] = (state["pt"][0], state["pt"][1], state["box"])
            pd.DataFrame([dict(epoch=e, x=v[0], y=v[1], box=v[2])
                          for e, v in sorted(seeds.items())]).to_csv(SEEDS_CSV, index=False)
            print(f"  epoch {ep:2d} seeded at ({state['pt'][0]:.0f}, {state['pt'][1]:.0f})")
            i += 1; off = 0; need = True
        elif k == ord("s"):
            i += 1; off = 0; need = True
        elif k == ord("b"):
            i = max(0, i - 1); off = 0; need = True
        elif k in (ord("+"), ord("=")):
            state["box"] = min(200, state["box"] + 4)
        elif k == ord("-"):
            state["box"] = max(12, state["box"] - 4)
        elif k == ord("."):
            off += 1; need = True
        elif k == ord(","):
            off -= 1; need = True

    cv2.destroyAllWindows()
    return seeds


# --- tracking -----------------------------------------------------------------

def subpixel(res, x, y):
    """Parabolic peak interpolation -- the asteroid centre between pixels."""
    dx = dy = 0.0
    if 0 < x < res.shape[1] - 1:
        a, b, c = res[y, x - 1], res[y, x], res[y, x + 1]
        d = a - 2 * b + c
        if abs(d) > 1e-9:
            dx = 0.5 * (a - c) / d
    if 0 < y < res.shape[0] - 1:
        a, b, c = res[y - 1, x], res[y, x], res[y + 1, x]
        d = a - 2 * b + c
        if abs(d) > 1e-9:
            dy = 0.5 * (a - c) / d
    return float(np.clip(dx, -1, 1)), float(np.clip(dy, -1, 1))


def refine(gray, tmpl, cx, cy):
    """Template match in a window around (cx, cy). Returns (x, y, score)."""
    th, tw = tmpl.shape
    H, W = gray.shape
    x0 = int(np.clip(cx - SEARCH_R - tw // 2, 0, W - tw - 1))
    y0 = int(np.clip(cy - SEARCH_R - th // 2, 0, H - th - 1))
    x1 = int(np.clip(cx + SEARCH_R + tw // 2, tw + 1, W - 1))
    y1 = int(np.clip(cy + SEARCH_R + th // 2, th + 1, H - 1))
    win = gray[y0:y1, x0:x1]
    if win.shape[0] <= th or win.shape[1] <= tw:
        return cx, cy, 0.0
    res = cv2.matchTemplate(win, tmpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    dx, dy = subpixel(res, loc[0], loc[1])
    return (x0 + loc[0] + dx + tw / 2.0, y0 + loc[1] + dy + th / 2.0, float(score))


def make_tracker(name):
    if name == "csrt":
        return cv2.TrackerCSRT_create()
    if name == "kcf":
        return cv2.TrackerKCF_create()
    return cv2.TrackerMIL_create()


def track_epoch(cap, frames, seed, tracker_name):
    """Tracker proposes, template refines, a motion gate vetoes the impossible.

    The gate is what makes this reliable: a template match that implies a jump
    larger than the asteroid can physically make is rejected outright, and the
    track coasts on its last velocity instead. Without it a single bad match
    drags the tracker off across the frame and it never recovers.
    """
    x, y, box = seed
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frames[0]))
    ok, frame = cap.read()
    if not ok:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    h = box // 2
    tmpl = gray[int(y) - h:int(y) + h, int(x) - h:int(x) + h].copy()
    if tmpl.size == 0:
        return None

    trk = make_tracker(tracker_name)
    trk.init(frame, (int(x - h), int(y - h), box, box))

    vx = vy = 0.0
    xs, ys, sc = [], [], []
    for i, f in enumerate(frames):
        if i > 0:
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        px_, py_ = x + vx * VEL_DAMP, y + vy * VEL_DAMP     # where we expect it
        cand, score = [], []

        rx, ry, s = refine(gray, tmpl, px_, py_)
        cand.append((rx, ry)); score.append(s)

        if i > 0:
            tok, bb = trk.update(frame)
            if tok:
                cand.append((bb[0] + bb[2] / 2.0, bb[1] + bb[3] / 2.0))
                score.append(s * 0.99)          # tie-break toward the template

        # Keep the best candidate that the asteroid could actually have reached.
        best, best_s = None, -1.0
        for (cx, cy), cs in zip(cand, score):
            if np.hypot(cx - x, cy - y) <= MAX_STEP_PX and cs > best_s:
                best, best_s = (cx, cy), cs

        if best is not None and best_s >= MIN_SCORE:
            nx, ny = best
        else:
            nx, ny = px_, py_                   # coast; flagged by a low score
            best_s = min(best_s, MIN_SCORE - 0.01)

        vx, vy = nx - x, ny - y
        x, y = nx, ny
        xs.append(x); ys.append(y); sc.append(max(best_s, 0.0))
    n = len(xs)
    return np.array(xs), np.array(ys), np.array(sc), frames[:n]


def quality_check(df, timeline):
    """Honest per-epoch validity, because the match score alone is not.

    A tracker stuck on static art template-matches itself perfectly and scores
    1.00, so score says nothing about whether we followed the asteroid. These two
    do: the track must sweep a plausible vertical distance, and it must correlate
    with the logged trajectory at SOME lag (the lag itself is not used here --
    only whether the shape matches).
    """
    offs = np.arange(-1.5, 1.501, 0.02)
    rows = []
    for ep, t in df.groupby("epoch"):
        t = t.sort_values("ts_ns")
        d = timeline[timeline["EpochIndex"] == ep].sort_values("t_utc_ns")
        best = 0.0
        if len(d) > 30 and t["ast_py"].std() > 1e-6:
            for o in offs:
                ref = np.interp(t["ts_ns"] + o * 1e9, d["t_utc_ns"], d["Asteroid_Y"],
                                left=np.nan, right=np.nan)
                k = np.isfinite(ref)
                if k.sum() < 100 or np.std(ref[k]) < 1e-6:
                    continue
                best = max(best, abs(np.corrcoef(t["ast_py"][k], ref[k])[0, 1]))
        y_travel = float(t["ast_py"].max() - t["ast_py"].min())
        rows.append(dict(epoch=int(ep), cond=d["Condition"].iloc[0] if len(d) else "?",
                         freq=float(d["waveFreq"].iloc[0]) if len(d) else np.nan,
                         confident=float((t["score"] >= MIN_SCORE).mean()),
                         y_travel=y_travel,
                         x_travel=float(t["ast_px"].max() - t["ast_px"].min()),
                         best_r=float(best)))
    qc = pd.DataFrame(rows)
    qc["pass"] = (qc["best_r"] > 0.9) & (qc["y_travel"] > 250)
    return qc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracker", default="csrt", choices=["csrt", "kcf", "mil"])
    ap.add_argument("--seed-corners", default="out_frame_annotate_method/corners.csv",
                    help="method-1 annotations, used only to propose seed boxes")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--track-only", action="store_true")
    ap.add_argument("--epochs", default="", help="only these, e.g. 27,31,35")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    tl = pd.read_csv(OUT / "timeline.csv")
    wts = pd.read_csv(WORLD_TS_CSV)["timestamp [ns]"].to_numpy()

    epochs, frames_by_ep = {}, {}
    for ep, d in tl.groupby("EpochIndex"):
        t0, t1 = int(d["t_utc_ns"].min()), int(d["t_utc_ns"].max())
        lo, hi = np.searchsorted(wts, [t0, t1])
        if hi - lo < 30:
            continue
        epochs[int(ep)] = t0
        frames_by_ep[int(ep)] = np.arange(lo, hi)

    only = {int(x) for x in args.epochs.split(",")} if args.epochs else None
    if only:
        epochs = {e: v for e, v in epochs.items() if e in only}
        frames_by_ep = {e: v for e, v in frames_by_ep.items() if e in only}
        print(f"restricted to epochs {sorted(epochs)}")

    cap = cv2.VideoCapture(str(SCENE_VIDEO))
    if not cap.isOpened():
        sys.exit("could not open scene video")

    if not args.track_only:
        from pathlib import Path
        prop = propose_seeds(Path(args.seed_corners), epochs,
                             {e: f[0] for e, f in frames_by_ep.items()})
        seeds = seed_ui(cap, epochs, {e: f[0] for e, f in frames_by_ep.items()}, prop)
    else:
        seeds = {int(r["epoch"]): (r["x"], r["y"], int(r["box"]))
                 for _, r in pd.read_csv(SEEDS_CSV).iterrows()}
    if only:
        seeds = {e: v for e, v in seeds.items() if e in only}

    if args.seed_only:
        cap.release(); print(f"\n{len(seeds)} seeds -> {SEEDS_CSV}"); return

    print(f"\ntracking {len(seeds)} epochs with {args.tracker}...")
    rows = []
    for ep in sorted(seeds):
        frames = frames_by_ep[ep]
        out = track_epoch(cap, frames, seeds[ep], args.tracker)
        if out is None:
            print(f"  epoch {ep:2d}: FAILED to seed"); continue
        xs, ys, sc, fr = out
        rows.append(pd.DataFrame(dict(epoch=ep, frame=fr, ts_ns=wts[fr],
                                      ast_px=xs, ast_py=ys, score=sc)))
        print(f"  epoch {ep:2d}: {len(fr)} frames, "
              f"{(sc >= MIN_SCORE).mean()*100:5.1f}% confident, "
              f"median score {np.median(sc):.2f}, "
              f"px travel {np.hypot(*(np.array([xs.max()-xs.min(), ys.max()-ys.min()]))):.0f}")
    cap.release()

    if not rows:
        sys.exit("no epochs tracked")
    df = pd.concat(rows, ignore_index=True)
    if only and TRACK_CSV.exists():          # re-running a subset: merge, don't clobber
        old = pd.read_csv(TRACK_CSV)
        df = pd.concat([old[~old["epoch"].isin(df["epoch"])], df], ignore_index=True)
    df = df.sort_values(["epoch", "frame"]).reset_index(drop=True)
    df.to_csv(TRACK_CSV, index=False)
    print(f"\n{len(df)} tracked frames -> {TRACK_CSV}")

    qc = quality_check(df, tl)
    qc.to_csv(OUT / "track_qc.csv", index=False)
    bad = qc[~qc["pass"]]
    print(f"\nQC: {int(qc['pass'].sum())}/{len(qc)} epochs pass "
          f"(|r| vs the MATLAB log > 0.9 and y-travel > 250 px)")
    if len(bad):
        print(bad[["epoch", "cond", "freq", "y_travel", "best_r"]].round(2).to_string(index=False))
        print(f"\nre-seed just those:  --epochs {','.join(map(str, bad['epoch']))}")


if __name__ == "__main__":
    main()
