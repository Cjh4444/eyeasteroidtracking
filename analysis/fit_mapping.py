"""Stage 4 -- map gaze from scene-camera pixels into game coordinates.

Each annotated rest gives four clicked corners of the gray rest fill, which spans
exactly game [0,100]x[0,65] (Lunar_Blast_v4.m:439). That is a 4-point homography
from undistorted scene pixels to game units, and it needs none of the unknowns we
lack (screen size, resolution, viewing distance, MATLAB window geometry).

For times between rests we interpolate the four CORNER POSITIONS and rebuild the
homography, rather than interpolating matrix entries -- corners interpolate
sensibly under head motion, matrix entries do not.

Validation: the fixation cross sits at game (50, 32.5) on that same rectangle, so
applying each homography to her median gaze during that rest should land there.
The residual measures undistortion + homography + gaze calibration in one number.

Emits out/gaze_mapped.csv and out/mapping_report.csv.
"""
import json
import sys

import cv2
import numpy as np
import pandas as pd

from common import (CORNER_GAME_XY, CROSS_XY, GAME_XLIM, GAME_YLIM, OUT,
                    SCENE_CAM_JSON)
from sync import load_gaze

CORNERS_CSV = OUT / "corners.csv"


def camera_params():
    cam = json.load(open(SCENE_CAM_JSON))
    K = np.array(cam["camera_matrix"], dtype=np.float64)
    # The Neon export carries 8 coefficients (OpenCV rational model). Pass all 8;
    # truncating to 5 leaves visible residual distortion at the frame edges.
    D = np.array(cam["distortion_coefficients"], dtype=np.float64).reshape(1, -1)
    return K, D


def undistort(pts, K, D):
    """Distorted pixel coords -> undistorted pixel coords (same K)."""
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.undistortPoints(p, K, D, P=K).reshape(-1, 2)


def load_corners(K, D) -> pd.DataFrame:
    if not CORNERS_CSV.exists():
        sys.exit(f"no annotations yet: run annotate_corners.py to create {CORNERS_CSV}")
    c = pd.read_csv(CORNERS_CSV).sort_values("ts_ns").reset_index(drop=True)
    quads, crosses = [], []
    has_cross = {"cross_x", "cross_y"}.issubset(c.columns)
    for _, r in c.iterrows():
        quads.append(undistort([(r[f"x{n}"], r[f"y{n}"]) for n in range(4)], K, D))
        if has_cross and pd.notna(r["cross_x"]) and pd.notna(r["cross_y"]):
            crosses.append(undistort([(r["cross_x"], r["cross_y"])], K, D)[0])
        else:
            crosses.append(np.array([np.nan, np.nan]))
    c["quad_undist"] = quads
    c["cross_undist"] = crosses
    return c


def homography(quad, cross=None):
    """Scene pixels -> game units.

    With only the four corners this is the exact 4-point transform. When the
    operator also clicked the observed fixation cross we have a fifth
    correspondence -- (50, 32.5) -- so we least-squares fit all five instead,
    which is better conditioned and averages down the per-click error.
    """
    src = list(np.asarray(quad, dtype=np.float64))
    dst = list(np.asarray(CORNER_GAME_XY, dtype=np.float64))
    if cross is not None and np.all(np.isfinite(cross)):
        src.append(np.asarray(cross, dtype=np.float64))
        dst.append(np.asarray(CROSS_XY, dtype=np.float64))
        H, _ = cv2.findHomography(np.array(src), np.array(dst), method=0)
        return H
    return cv2.getPerspectiveTransform(
        np.array(src, dtype=np.float32), np.array(dst, dtype=np.float32))


def apply_H(H, pts):
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(p, H).reshape(-1, 2)


def interp_quads(t_ns, corners):
    """Corner positions at arbitrary times, linearly between annotated rests.

    Returns (N,4,2). Constant extrapolation outside the annotated span.
    """
    anchor_t = corners["ts_ns"].to_numpy().astype(np.float64)
    anchor_q = np.stack(corners["quad_undist"].to_numpy())  # (A,4,2)
    t = np.asarray(t_ns, dtype=np.float64)

    anchor_c = np.stack(corners["cross_undist"].to_numpy())  # (A,2)

    if len(anchor_t) == 1:
        return (np.repeat(anchor_q, len(t), axis=0),
                np.repeat(anchor_c[None], len(t), axis=0))

    out = np.empty((len(t), 4, 2))
    for c in range(4):
        for d in range(2):
            out[:, c, d] = np.interp(t, anchor_t, anchor_q[:, c, d])
    # Interpolate the cross too, but only where every contributing anchor has
    # one -- interpolating across a missing click would invent a correspondence.
    outc = np.empty((len(t), 2))
    for d in range(2):
        outc[:, d] = np.interp(t, anchor_t, anchor_c[:, d])
    if not np.all(np.isfinite(anchor_c)):
        outc[:] = np.nan
    return out, outc


def map_points(t_ns, pts_undist, corners):
    """Map undistorted gaze points to game coords using the time-local homography.

    Homographies are rebuilt per distinct quad; gaze is 200 Hz so we bucket by
    ~1/30 s to keep this from being 220k getPerspectiveTransform calls.
    """
    quads, crosses = interp_quads(t_ns, corners)
    out = np.full((len(t_ns), 2), np.nan)
    bucket = (np.asarray(t_ns, dtype=np.float64) // 33_000_000).astype("int64")
    _, first_idx, inverse = np.unique(bucket, return_index=True, return_inverse=True)
    for b, fi in enumerate(first_idx):
        sel = inverse == b
        H = homography(quads[fi], crosses[fi])
        out[sel] = apply_H(H, pts_undist[sel])
    return out


def validate(corners, gaze, rests, K, D):
    """Three independent checks per annotated rest.

    corner_err  -- corner-only homography's prediction of the cross vs the
                   operator's clicked cross. Pure measure of corner-click
                   accuracy; involves no gaze at all.
    px_off      -- median gaze during the rest vs the clicked cross, in raw
                   scene pixels. A homography-free read on gaze calibration.
    cross_err   -- median gaze pushed through the mapping we actually use,
                   compared to game (50, 32.5). The end-to-end number.
    """
    rows = []
    for _, c in corners.iterrows():
        r = dict(rest_id=int(c["rest_id"]))
        quad, cross = c["quad_undist"], c["cross_undist"]
        has_cross = np.all(np.isfinite(cross))

        if has_cross:
            pred = apply_H(np.linalg.inv(homography(quad)), [CROSS_XY])[0]
            r["corner_err_px"] = float(np.hypot(*(pred - cross)))
            g4 = apply_H(homography(quad), [cross])[0]
            r["corner_err_game"] = float(np.hypot(g4[0] - CROSS_XY[0], g4[1] - CROSS_XY[1]))

        w = rests[rests["rest_id"] == c["rest_id"]]
        m = pd.DataFrame()
        if not w.empty:
            w = w.iloc[0]
            m = gaze[(gaze["ts_ns"] >= w["start_ns"]) & (gaze["ts_ns"] <= w["end_ns"])
                     & gaze["valid"]]
        r["n"] = len(m)
        if len(m) >= 50:
            med = np.median(undistort(m[["px", "py"]].to_numpy(), K, D), axis=0)
            if has_cross:
                r["px_off_x"] = float(med[0] - cross[0])
                r["px_off_y"] = float(med[1] - cross[1])
                r["px_off"] = float(np.hypot(*(med - cross)))
            gx, gy = apply_H(homography(quad, cross if has_cross else None), [med])[0]
            r["cross_x"], r["cross_y"] = float(gx), float(gy)
            r["cross_dx"] = float(gx - CROSS_XY[0])
            r["cross_dy"] = float(gy - CROSS_XY[1])
            r["cross_err"] = float(np.hypot(gx - CROSS_XY[0], gy - CROSS_XY[1]))
        rows.append(r)
    return pd.DataFrame(rows)


def main() -> None:
    K, D = camera_params()
    corners = load_corners(K, D)
    rests = pd.read_csv(OUT / "rest_windows.csv")
    gaze = load_gaze()
    print(f"annotated rests: {len(corners)} / {len(rests)}")

    rep = validate(corners, gaze, rests, K, D)

    if "corner_err_px" in rep and rep["corner_err_px"].notna().any():
        ce = rep["corner_err_px"].dropna()
        cg = rep["corner_err_game"].dropna()
        print(f"\ncorner-click accuracy (corner homography vs clicked cross):")
        print(f"  median {ce.median():.1f} px  ({cg.median():.2f} game units)   "
              f"p90 {ce.quantile(.9):.1f} px   max {ce.max():.1f} px")

    if "px_off" in rep and rep["px_off"].notna().any():
        po = rep.dropna(subset=["px_off"])
        print(f"\ngaze vs clicked cross, in raw scene pixels (no homography):")
        print(f"  median offset dx {po['px_off_x'].median():+.1f}  dy {po['px_off_y'].median():+.1f}  "
              f"|d| {po['px_off'].median():.1f} px")

    ok = rep.dropna(subset=["cross_err"]) if "cross_err" in rep else rep.iloc[:0]
    if len(ok):
        print(f"\nend-to-end cross residual (game units; 1 unit = 1% of screen width):")
        print(f"  median {ok['cross_err'].median():.2f}   "
              f"p90 {ok['cross_err'].quantile(.9):.2f}   max {ok['cross_err'].max():.2f}")
        print(f"  systematic offset: dx {ok['cross_dx'].median():+.2f}  dy {ok['cross_dy'].median():+.2f}")
        bad = ok[ok["cross_err"] > 5]
        if len(bad):
            print(f"  {len(bad)} rest(s) over 5 units -- check these annotations:")
            print(bad[["rest_id", "cross_x", "cross_y", "cross_err"]].round(2).to_string(index=False))

    # Map the whole session.
    pts = undistort(gaze[["px", "py"]].to_numpy(), K, D)
    xy = map_points(gaze["ts_ns"].to_numpy(), pts, corners)
    gaze["game_x"], gaze["game_y"] = xy[:, 0], xy[:, 1]

    on = ((gaze["game_x"].between(*GAME_XLIM)) & (gaze["game_y"].between(*GAME_YLIM))
          & gaze["valid"])
    print(f"\ngaze inside the game area: {on.mean()*100:.1f}% of all samples")

    gaze[["ts_ns", "px", "py", "az", "el", "valid", "game_x", "game_y"]].to_csv(
        OUT / "gaze_mapped.csv", index=False)
    rep.to_csv(OUT / "mapping_report.csv", index=False)
    print(f"\nwrote {OUT/'gaze_mapped.csv'}\nwrote {OUT/'mapping_report.csv'}")


if __name__ == "__main__":
    main()
