"""Head-to-head accuracy check: corner annotation vs asteroid tracking.

Comparing each method against its own fit objective would be meaningless, so both
are scored on the SAME held-out task:

    given the asteroid's measured pixel position in a video frame, predict its
    game coordinates, and compare against the MATLAB log.

That task is:
  * fully out-of-sample for METHOD 1 -- its homography comes from the rest
    periods between trials and never saw a single mid-trial frame.
  * scored with 2-fold cross-validation for METHOD 2 -- fitting and testing on
    the same frames would flatter it, so the homography is fit on even frames and
    tested on odd, and vice versa.

Each method is also given its own best time alignment (the offset is solved per
epoch, per method), so neither is penalised for the clock drift.

Run:  ../.venv/bin/python compare_methods.py
"""
import numpy as np
import pandas as pd

import cv2

from common import FLOW, CORNER_GAME_XY, CROSS_XY, GAME_XLIM, GAME_YLIM, ROOT
from fit_mapping import camera_params, undistort

ANALYSIS = FLOW
M1 = ANALYSIS / "out_frame_annotate_method"
M2 = ANALYSIS / "out_track"
OFFSETS = np.arange(-2.0, 2.001, 1 / 30)


def y_flipped(H):
    p = cv2.perspectiveTransform(
        np.array([[[800.0, 400.0]], [[800.0, 500.0]]], dtype=np.float64), H).reshape(-1, 2)
    return (p[1, 1] - p[0, 1]) >= 0


def m1_quads(K, D):
    """Method 1's annotated corners, undistorted, with their timestamps."""
    c = pd.read_csv(M1 / "corners.csv").sort_values("ts_ns").reset_index(drop=True)
    q = np.stack([undistort([(r[f"x{n}"], r[f"y{n}"]) for n in range(4)], K, D)
                  for _, r in c.iterrows()])
    return c["ts_ns"].to_numpy().astype(np.float64), q


def m1_H_at(ts, anchor_t, anchor_q):
    """Method 1's mapping at arbitrary times: interpolate corners, rebuild H."""
    out = np.empty((len(ts), 4, 2))
    for ci in range(4):
        for d in range(2):
            out[:, ci, d] = np.interp(ts, anchor_t, anchor_q[:, ci, d])
    return out


def solve(src, dst_fn, offsets=OFFSETS, ransac=2.0):
    """Best (offset, H, residual) over the offset sweep. dst_fn(off) -> (dst, keep)."""
    best = None
    for off in offsets:
        dst, keep = dst_fn(off)
        if keep.sum() < 50:
            continue
        H, _ = cv2.findHomography(src[keep], dst[keep], cv2.RANSAC, ransac)
        if H is None or y_flipped(H):
            continue
        proj = cv2.perspectiveTransform(src[keep].reshape(-1, 1, 2), H).reshape(-1, 2)
        r = float(np.median(np.hypot(*(proj - dst[keep]).T)))
        if best is None or r < best[2]:
            best = (off, H, r)
    return best


def main() -> None:
    K, D = camera_params()
    track = pd.read_csv(M2 / "asteroid_track.csv")
    tl = pd.read_csv(M2 / "timeline.csv")
    anchor_t, anchor_q = m1_quads(K, D)

    rows = []
    for ep, t in track.groupby("epoch"):
        t = t[t["score"] >= 0.35].sort_values("ts_ns")
        d = tl[tl["EpochIndex"] == ep].sort_values("t_utc_ns")
        if len(t) < 60 or len(d) < 30:
            continue
        src = undistort(t[["ast_px", "ast_py"]].to_numpy(), K, D)
        ts = t["ts_ns"].to_numpy().astype(np.float64)

        def dst_fn(off):
            dst = np.c_[np.interp(ts + off * 1e9, d["t_utc_ns"], d["Asteroid_X"],
                                  left=np.nan, right=np.nan),
                        np.interp(ts + off * 1e9, d["t_utc_ns"], d["Asteroid_Y"],
                                  left=np.nan, right=np.nan)]
            return dst, np.isfinite(dst).all(1)

        # --- METHOD 1: its own homography, never fitted to any of this ---------
        quads = m1_H_at(ts, anchor_t, anchor_q)
        m1_err = np.nan
        best_off = None
        for off in OFFSETS:                    # give method 1 its best alignment
            dst, keep = dst_fn(off)
            if keep.sum() < 50:
                continue
            errs = []
            for i in np.linspace(0, len(ts) - 1, 60).astype(int):
                if not keep[i]:
                    continue
                H = cv2.getPerspectiveTransform(quads[i].astype(np.float32),
                                                np.array(CORNER_GAME_XY, dtype=np.float32))
                p = cv2.perspectiveTransform(src[i].reshape(1, 1, 2), H).reshape(2)
                errs.append(np.hypot(*(p - dst[i])))
            if errs:
                e = float(np.median(errs))
                if np.isnan(m1_err) or e < m1_err:
                    m1_err, best_off = e, off

        # --- METHOD 2: 2-fold cross-validated ---------------------------------
        m2_errs = []
        for fold in (0, 1):
            fit_i = np.arange(len(ts)) % 2 == fold
            test_i = ~fit_i
            b = solve(src[fit_i], lambda o: (lambda dk: (dk[0][fit_i], dk[1][fit_i]))(dst_fn(o)))
            if b is None:
                continue
            off, H, _ = b
            dst, keep = dst_fn(off)
            sel = test_i & keep
            if sel.sum() < 20:
                continue
            proj = cv2.perspectiveTransform(src[sel].reshape(-1, 1, 2), H).reshape(-1, 2)
            m2_errs.append(float(np.median(np.hypot(*(proj - dst[sel]).T))))

        rows.append(dict(epoch=int(ep), waveFreq=float(d["waveFreq"].iloc[0]),
                         cond=d["Condition"].iloc[0],
                         m1_err=m1_err, m1_off_ms=best_off * 1000 if best_off is not None else np.nan,
                         m2_err=float(np.mean(m2_errs)) if m2_errs else np.nan))
        print(f"  epoch {int(ep):2d}: method1 {m1_err:6.2f}   method2 {rows[-1]['m2_err']:5.2f}",
              flush=True)

    r = pd.DataFrame(rows)
    r.to_csv(ANALYSIS / "method_comparison.csv", index=False)

    print("\n" + "=" * 66)
    print("ASTEROID LOCALISATION ERROR (game units; 1 unit = 1% of screen width)")
    print("=" * 66)
    for name, col in (("method 1  (rest corners, out-of-sample)", "m1_err"),
                      ("method 2  (asteroid track, 2-fold CV)  ", "m2_err")):
        s = r[col].dropna()
        print(f"{name}  n={len(s):2d}  median {s.median():6.2f}  "
              f"p90 {s.quantile(.9):6.2f}  max {s.max():6.2f}")
    both = r.dropna(subset=["m1_err", "m2_err"])
    print(f"\nmethod 2 better on {int((both['m2_err'] < both['m1_err']).sum())}/{len(both)} epochs")
    print(f"ratio of medians: {both['m1_err'].median() / both['m2_err'].median():.1f}x")
    print("\nby waveFreq:")
    print(both.groupby("waveFreq")[["m1_err", "m2_err"]].median().round(2).to_string())


if __name__ == "__main__":
    main()
