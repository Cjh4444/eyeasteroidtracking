"""Measure the MATLAB-to-Neon clock offset with no eyes involved.

sync.py aligns gaze against the stimulus, but that estimate is confounded: it
came out frequency- and condition-dependent (+160 ms at waveFreq 1.0 rising to
+603 ms at 3.5; WATCHING +430 vs TRACKING +700), which a real clock offset cannot
be. Most of it is smooth-pursuit lag -- a result we want to keep, not correct away.

So measure the clock offset from something with no reaction time: the asteroid is
visible on the screen in the scene video. Warp each frame into game coordinates
using the annotated homography, subtract a per-epoch median background (the game
art -- Earth, Moon, launcher, stars -- is static; only the asteroid moves), and
take the brightest moving blob. Correlating THAT against the MATLAB log compares
two clocks directly.

Then: pursuit_lag = sync.py offset - clock offset, as a real per-condition result.

Emits out/asteroid_detected.csv and out/clock_offset.json.
"""
import argparse
import json

import cv2
import numpy as np
import pandas as pd

from common import GAME_XLIM, GAME_YLIM, OUT, SCENE_VIDEO, WORLD_TS_CSV
from fit_mapping import camera_params, homography, interp_quads, load_corners

# Game space rendered at 4 px per game unit -- enough to localise a 2.5-unit
# radius asteroid to a fraction of a unit, cheap enough to warp thousands of frames.
PPU = 4
W, H = int(GAME_XLIM[1] * PPU), int(GAME_YLIM[1] * PPU)


def undistort_maps(K, D, size):
    """Build the remap tables once; cv2.undistort would rebuild them per frame."""
    return cv2.initUndistortRectifyMap(K, D, None, K, size, cv2.CV_16SC2)


def warp_epoch(cap, frames, quads, crosses, maps):
    """Decode the given frames and warp each into game space."""
    out = np.zeros((len(frames), H, W), dtype=np.uint8)
    # Game units -> the warped raster (note the y flip: game y is up, image y down).
    S = np.array([[PPU, 0, 0], [0, -PPU, H], [0, 0, 1]], dtype=np.float64)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frames[0]))
    want = set(int(f) for f in frames)
    pos, got = int(frames[0]), {}
    while len(got) < len(want) and pos <= max(want):
        ok, frame = cap.read()
        if not ok:
            break
        if pos in want:
            got[pos] = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        pos += 1
    for i, f in enumerate(frames):
        g = got.get(int(f))
        if g is None:
            continue
        g = cv2.remap(g, maps[0], maps[1], cv2.INTER_LINEAR)
        M = S @ homography(quads[i], crosses[i])
        out[i] = cv2.warpPerspective(g, M, (W, H))
    return out


def detect(stack):
    """Brightest moving blob per frame, in game units.

    The static game art cancels against the per-pixel median, so what survives is
    the asteroid (and its glow/tail, which share its centroid closely enough).
    """
    bg = np.median(stack, axis=0)
    xs, ys, conf = [], [], []
    gx = (np.arange(W) + 0.5) / PPU
    gy = GAME_YLIM[1] - (np.arange(H) + 0.5) / PPU
    for fr in stack:
        d = np.clip(fr.astype(np.float32) - bg, 0, None)
        if d.max() < 12:                      # nothing moved -- asteroid hidden
            xs.append(np.nan); ys.append(np.nan); conf.append(0.0)
            continue
        m = d >= 0.55 * d.max()               # keep the brightest blob only
        w = d * m
        s = w.sum()
        xs.append(float((w.sum(0) * gx).sum() / s))
        ys.append(float((w.sum(1) * gy).sum() / s))
        conf.append(float(d.max()))
    return np.array(xs), np.array(ys), np.array(conf)


def epoch_offset(det_ep, d):
    """Time offset for one epoch, by RMS distance rather than correlation.

    The detections are already in game units, so we can compare them to the log
    directly instead of correlating. That matters: correlation is invariant to
    sign and scale, so a half-period shift -- which inverts a triangle wave --
    scores just as well as the truth. RMS with no free scale has no such
    degeneracy, and the monotonic x ramp pins it down further.
    """
    good = det_ep[det_ep["conf"] > 12]
    if len(good) < 60:
        return np.nan, np.nan
    ts = good["ts_ns"].to_numpy().astype(np.float64)
    best = (np.inf, np.nan)
    for off in np.arange(-2.5, 2.501, 1 / 30):
        ry = np.interp(ts + off * 1e9, d["t_utc_ns"], d["Asteroid_Y"],
                       left=np.nan, right=np.nan)
        rx = np.interp(ts + off * 1e9, d["t_utc_ns"], d["Asteroid_X"],
                       left=np.nan, right=np.nan)
        k = np.isfinite(ry) & np.isfinite(rx)
        if k.sum() < 60:
            continue
        err = float(np.sqrt(np.mean((good["ast_y"].to_numpy()[k] - ry[k]) ** 2
                                    + (good["ast_x"].to_numpy()[k] - rx[k]) ** 2)))
        if err < best[0]:
            best = (err, off)
    return best[1], best[0]


def fit_drift(per):
    """Robust line through the per-epoch offsets: offset(epoch), in seconds."""
    r = per.dropna(subset=["offset_s"]).copy()
    r = r[r["rms"] < r["rms"].quantile(0.75) * 2]
    if len(r) < 8:
        return None
    for _ in range(3):
        sl, ic = np.polyfit(r["epoch"], r["offset_s"], 1)
        resid = r["offset_s"] - (sl * r["epoch"] + ic)
        keep = np.abs(resid) <= max(0.15, 2.5 * resid.std())
        if keep.all():
            break
        r = r[keep]
    return float(sl), float(ic), int(len(r)), float(resid.std())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--redetect", action="store_true",
                    help="re-run the video detection instead of reusing "
                         "asteroid_detected.csv")
    args = ap.parse_args()

    cached = OUT / "asteroid_detected.csv"
    if cached.exists() and not args.redetect:
        print(f"reusing {cached} (pass --redetect to rebuild)")
        finish(pd.read_csv(cached), pd.read_csv(OUT / "timeline.csv"))
        return

    K, D = camera_params()
    corners = load_corners(K, D)
    timeline = pd.read_csv(OUT / "timeline.csv")
    wts = pd.read_csv(WORLD_TS_CSV)["timestamp [ns]"].to_numpy()
    cap = cv2.VideoCapture(str(SCENE_VIDEO))
    if not cap.isOpened():
        raise SystemExit("could not open scene video")
    maps = undistort_maps(K, D, (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                 int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))))

    rows = []
    for ep, d in timeline.groupby("EpochIndex"):
        d = d.sort_values("t_utc_ns")
        t0, t1 = int(d["t_utc_ns"].iloc[0]), int(d["t_utc_ns"].iloc[-1])
        lo, hi = np.searchsorted(wts, [t0, t1])
        frames = np.arange(lo, hi)
        if len(frames) < 30:
            continue
        ts = wts[frames]
        quads, crosses = interp_quads(ts, corners)
        stack = warp_epoch(cap, frames, quads, crosses, maps)
        x, y, c = detect(stack)
        rows.append(pd.DataFrame(dict(epoch=int(ep), frame=frames, ts_ns=ts,
                                      ast_x=x, ast_y=y, conf=c)))
        print(f"  epoch {int(ep):2d}: {len(frames)} frames, "
              f"{np.isfinite(y).sum()} detections, median conf {np.median(c):.0f}")
    cap.release()

    det = pd.concat(rows, ignore_index=True)
    det.to_csv(OUT / "asteroid_detected.csv", index=False)
    finish(det, timeline)


def finish(det, timeline):
    """Solve the offset per epoch, fit the drift, and recompute the pursuit lag."""
    per = []
    for ep, d_ep in det.groupby("epoch"):
        d = timeline[timeline["EpochIndex"] == ep].sort_values("t_utc_ns")
        if len(d) < 30:
            continue
        off, rms = epoch_offset(d_ep, d)
        per.append(dict(epoch=int(ep), offset_s=off, rms=rms))
    per = pd.DataFrame(per)

    fit = fit_drift(per)
    if fit is None:
        raise SystemExit("could not fit a drift model -- too few usable epochs")
    sl, ic, nfit, scatter = fit
    per["offset_model_s"] = sl * per["epoch"] + ic
    per.to_csv(OUT / "clock_offset_per_epoch.csv", index=False)

    print(f"\nclock drift (method 1's own detections):")
    print(f"  {sl*1000:+.1f} ms per epoch, fitted on {nfit} consistent epochs")
    print(f"  {ic*1000:+.0f} ms at epoch 0  ->  {(sl*42+ic)*1000:+.0f} ms at epoch 42"
          f"   ({sl*41*1000:+.0f} ms across the session)")
    print(f"  scatter about the line: {scatter*1000:.0f} ms")

    # No pursuit lag is reported here. Deriving it as (gaze-vs-stimulus offset)
    # minus (clock offset) requires a clock model this method cannot supply: the
    # scatter about its own drift line is ~675 ms, against ~91 ms for the same
    # quantity measured from the asteroid track. Composing two offsets of that
    # quality produced a +2 s "pursuit lag", which is physiologically impossible.
    #
    # Measure it instead from gaze against the TRACKED asteroid -- both are on the
    # Neon clock, so no model is involved at all. That gives a ~195 ms anticipatory
    # LEAD (see method 2 / pursuit_lag.csv).

    json.dump(dict(
        model="offset_s = slope * epoch + intercept  (MATLAB -> Neon, seconds)",
        slope_s_per_epoch=sl, intercept_s=ic,
        n_epochs_fitted=nfit, scatter_s=scatter,
        drift_across_session_s=sl * 41,
        note=("Supersedes the earlier single global offset. The two clocks drift "
              "apart, so no constant offset exists; the previous -0.775 s figure "
              "and the 1.33 s pursuit lag it implied were artefacts of forcing one."),
        reliable=False,
        warning=("Scatter about this drift line is ~675 ms, vs ~91 ms for the "
                 "same quantity from the asteroid track. Method 1's detections "
                 "warp through its own mid-trial homography, which is the thing "
                 "that is unreliable. Prefer method 2's model. No pursuit lag is "
                 "derived from this."),
    ), open(OUT / "clock_offset.json", "w"), indent=2)
    print(f"\nwrote {OUT/'clock_offset.json'}")
    print(f"wrote {OUT/'clock_offset_per_epoch.csv'}")


if __name__ == "__main__":
    main()
