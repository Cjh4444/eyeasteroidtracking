"""METHOD 3 -- differential: put the measured gaze-asteroid offset onto the log path.

    gaze_game(tau) = asteroid_matlab(tau + delta)  +  J . (gaze_px - ast_px)

where J is the local Jacobian of the screen->game mapping at the asteroid.

Why this beats method 2's absolute mapping, on both counts that have hurt us:

  * CLOCK SENSITIVITY. Method 2 maps gaze absolutely, so a timing error mispairs
    the correspondences and corrupts the whole homography. Here the timing error
    only attaches the wrong offset vector -- and the offset is the tracking error,
    which is small and slowly varying, where asteroid position moves at up to
    60 game units/s. The induced error scales with d(offset)/dt, not with
    d(position)/dt.

  * MAPPING SENSITIVITY. Only the LOCAL DERIVATIVE of the mapping is used, never
    its absolute value. Any translation error in the homography cancels exactly,
    and perspective variation across the screen stops mattering because gaze sits
    within a few degrees of the asteroid.

The cost: this reconstructs gaze RELATIVE to the target, so it inherits the
asteroid's own trajectory. It answers "how far off target was she, in game units,
at each moment" -- not "where on the screen was she looking" independent of the
stimulus. For this experiment that is the question anyway.

Run:  LB_OUT=out_hybrid ../.venv/bin/python fit_hybrid.py
"""
import numpy as np
import pandas as pd

import cv2

from common import FLOW, GAME_XLIM, GAME_YLIM, OUT, ROOT
from fit_mapping import camera_params, undistort
from fit_from_track import MIN_SCORE, y_flipped
from sync import load_gaze

M2 = FLOW / "out_track"


def jacobian(H, pts):
    """d(game)/d(pixel) at each point -- the 2x2 local linear part of H.

    Using only this is the whole point: H's translation never enters, so an
    absolute error in the mapping cannot bias the reconstructed offset.
    """
    p = np.asarray(pts, dtype=np.float64)
    x, y = p[:, 0], p[:, 1]
    w = H[2, 0] * x + H[2, 1] * y + H[2, 2]
    gx = (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / w
    gy = (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / w
    J = np.empty((len(p), 2, 2))
    J[:, 0, 0] = (H[0, 0] - gx * H[2, 0]) / w
    J[:, 0, 1] = (H[0, 1] - gx * H[2, 1]) / w
    J[:, 1, 0] = (H[1, 0] - gy * H[2, 0]) / w
    J[:, 1, 1] = (H[1, 1] - gy * H[2, 1]) / w
    return J


def epoch_H(src, ts, d, off):
    """Homography for one epoch at a known time offset (no sweep -- it is given)."""
    dst = np.c_[np.interp(ts + off * 1e9, d["t_utc_ns"], d["Asteroid_X"],
                          left=np.nan, right=np.nan),
                np.interp(ts + off * 1e9, d["t_utc_ns"], d["Asteroid_Y"],
                          left=np.nan, right=np.nan)]
    k = np.isfinite(dst).all(1)
    if k.sum() < 50:
        return None
    H, _ = cv2.findHomography(src[k], dst[k], cv2.RANSAC, 2.0)
    if H is None or y_flipped(H):
        return None
    return H


def build(track, timeline, gaze, offsets, K, D, perturb=0.0):
    """Reconstruct gaze in game units. `perturb` shifts the clock, for the
    sensitivity test."""
    g = gaze[gaze["valid"]]
    gt = g["ts_ns"].to_numpy().astype(np.float64)
    gu = undistort(g[["px", "py"]].to_numpy(), K, D)

    rows = []
    for ep, t in track.groupby("epoch"):
        t = t[t["score"] >= MIN_SCORE].sort_values("ts_ns")
        d = timeline[timeline["EpochIndex"] == ep].sort_values("t_utc_ns")
        if len(t) < 60 or len(d) < 30 or ep not in offsets:
            continue
        off = offsets[ep] + perturb

        tu = undistort(t[["ast_px", "ast_py"]].to_numpy(), K, D)
        tt = t["ts_ns"].to_numpy().astype(np.float64)
        H = epoch_H(tu, tt, d, offsets[ep])      # H itself uses the unperturbed fit
        if H is None:
            continue

        sel = (gt >= tt[0]) & (gt <= tt[-1])
        if sel.sum() < 20:
            continue
        gts, gpts = gt[sel], gu[sel]

        # Asteroid position at each gaze instant: pixels (measured) and game
        # units (the MATLAB path, sampled through the clock offset).
        ax = np.interp(gts, tt, tu[:, 0])
        ay = np.interp(gts, tt, tu[:, 1])
        base_x = np.interp(gts + off * 1e9, d["t_utc_ns"], d["Asteroid_X"],
                           left=np.nan, right=np.nan)
        base_y = np.interp(gts + off * 1e9, d["t_utc_ns"], d["Asteroid_Y"],
                           left=np.nan, right=np.nan)

        # The measured offset, rotated/scaled into game units by the LOCAL
        # derivative at the asteroid -- H's translation never used.
        delta = np.c_[gpts[:, 0] - ax, gpts[:, 1] - ay]
        J = jacobian(H, np.c_[ax, ay])
        dg = np.einsum("nij,nj->ni", J, delta)

        rows.append(pd.DataFrame(dict(
            ts_ns=g["ts_ns"].to_numpy()[sel], px=g["px"].to_numpy()[sel],
            py=g["py"].to_numpy()[sel], az=g["az"].to_numpy()[sel],
            el=g["el"].to_numpy()[sel], valid=True,
            game_x=base_x + dg[:, 0], game_y=base_y + dg[:, 1],
            epoch=int(ep))))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main() -> None:
    K, D = camera_params()
    track = pd.read_csv(M2 / "asteroid_track.csv")
    timeline = pd.read_csv(M2 / "timeline.csv")
    rep = pd.read_csv(M2 / "track_mapping_report.csv")
    offsets = dict(zip(rep["epoch"], rep["offset_s"]))
    gaze = load_gaze()

    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("timeline.csv", "rest_windows.csv", "sync_per_epoch.csv",
                 "track_mapping_report.csv", "asteroid_track.csv"):
        src = M2 / name
        if src.exists() and not (OUT / name).exists():
            (OUT / name).write_bytes(src.read_bytes())

    mapped = build(track, timeline, gaze, offsets, K, D)
    mapped.drop(columns=["epoch"]).to_csv(OUT / "gaze_mapped.csv", index=False)
    on = mapped["game_x"].between(*GAME_XLIM) & mapped["game_y"].between(*GAME_YLIM)
    print(f"reconstructed {len(mapped)} gaze samples over {mapped['epoch'].nunique()} epochs")
    print(f"  inside the game area: {on.mean()*100:.1f}%")

    # --- clock sensitivity ---------------------------------------------------
    # The quantity that matters is the VERTICAL SEPARATION between the plotted
    # gaze and asteroid curves -- how far off target she was. A clock error slides
    # both methods' gaze curves along the time axis equally (unavoidable), but:
    #
    #   method 2 computes the two curves independently, so a clock error changes
    #           the gap between them -- it corrupts the measured tracking error.
    #   method 3 constructs gaze AS asteroid-plus-offset, so the gap is exactly
    #           the measured offset regardless of what the clock says.
    print("\nchange in the gaze-to-asteroid SEPARATION under a forced clock error")
    print("(game units; this is the tracking error the plots are meant to show):")
    print(f"{'error':>8} {'method 2':>10} {'method 3':>10}")
    gv = gaze[gaze["valid"]]
    gt_all = gv["ts_ns"].to_numpy().astype(np.float64)
    gu_all = undistort(gv[["px", "py"]].to_numpy(), K, D)
    for eps in (0.05, 0.10, 0.20):
        d2s, d3s = [], []
        for ep, t in track.groupby("epoch"):
            t = t[t["score"] >= MIN_SCORE].sort_values("ts_ns")
            d = timeline[timeline["EpochIndex"] == ep].sort_values("t_utc_ns")
            if len(t) < 60 or ep not in offsets:
                continue
            off = offsets[ep]
            tu = undistort(t[["ast_px", "ast_py"]].to_numpy(), K, D)
            tt = t["ts_ns"].to_numpy().astype(np.float64)
            H0, H1 = epoch_H(tu, tt, d, off), epoch_H(tu, tt, d, off + eps)
            if H0 is None or H1 is None:
                continue
            sel = (gt_all >= tt[0]) & (gt_all <= tt[-1])
            if sel.sum() < 20:
                continue
            gp, gts = gu_all[sel], gt_all[sel]
            ax = np.interp(gts, tt, tu[:, 0]); ay = np.interp(gts, tt, tu[:, 1])
            delta = np.c_[gp[:, 0] - ax, gp[:, 1] - ay]

            def sep2(o, H):
                """method 2: gaze mapped absolutely, minus the log at the abscissa."""
                g = cv2.perspectiveTransform(gp.reshape(-1, 1, 2), H).reshape(-1, 2)
                base = np.c_[np.interp(gts + o * 1e9, d["t_utc_ns"], d["Asteroid_X"]),
                             np.interp(gts + o * 1e9, d["t_utc_ns"], d["Asteroid_Y"])]
                return g - base

            def sep3(H):
                """method 3: separation IS the local-Jacobian offset, by construction."""
                return np.einsum("nij,nj->ni", jacobian(H, np.c_[ax, ay]), delta)

            d2s.append(np.hypot(*(sep2(off + eps, H1) - sep2(off, H0)).T))
            d3s.append(np.hypot(*(sep3(H1) - sep3(H0)).T))
        m2d = float(np.nanmedian(np.concatenate(d2s)))
        m3d = float(np.nanmedian(np.concatenate(d3s)))
        print(f"{eps*1000:6.0f}ms {m2d:10.2f} {m3d:10.3f}")

    print(f"\nwrote {OUT/'gaze_mapped.csv'}")


if __name__ == "__main__":
    main()
