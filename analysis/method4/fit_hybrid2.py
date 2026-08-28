"""METHOD 4 -- differential, with the Jacobian taken from an INDEPENDENT source.

Method 3 reconstructs gaze as

    gaze_game(tau) = asteroid_matlab(tau + delta)  +  J . (gaze_px - ast_px)

and that formulation is right: the offset (gaze_px - ast_px) is head-motion
invariant by construction, because gaze and the tracked asteroid sit in the same
scene-camera image on the same Neon clock.

But the offset is in PIXELS. Turning it into game units needs J, and method 3
takes J from `fit_hybrid.epoch_H` -- a single homography fitted across the WHOLE
epoch's asteroid track. That is exactly the quantity mid-trial head motion
destroys, so the contamination method 3 was designed to avoid comes straight back
in through the scale factor.

WHY THE TRACK-FITTED J FAILS.  The track accumulates over 14 s, so any head
rotation during the trial adds to the asteroid's apparent pixel path. On epoch 33
Sarah yaws -19.7 deg (the largest head motion in the session) while the asteroid
travels +40 game units to the right; the rotation cancels most of the asteroid's
apparent horizontal motion, so the track sweeps only ~85 px of x where a still
head would give ~205 px. The homography absorbs that by inflating its x scale,
and every horizontal tracking error in that epoch comes out ~3x too big.

THE FIX.  Take J from method 1's rest-period corner homographies instead. Two
reasons this is not just moving the problem around:

  * Each rest homography is built from FOUR CORNERS CLICKED IN A SINGLE VIDEO
    FRAME. Head motion is frozen within one frame, so it cannot accumulate the
    way it does across a 14 s track. There is no time extent to contaminate.

  * Method 1's known fatal flaw does not apply here. What killed method 1 was
    interpolating ABSOLUTE POSITION between anchors up to 100 px apart. J
    discards translation entirely (see `jacobian`), so that error mode is
    structurally irrelevant. Measured across the 41 rests, the scale at screen
    centre changes a median of 1.2 % between consecutive rests -- interpolating
    J is safe where interpolating position was not.

The two methods' failure modes are complementary: method 1 is bad at translation
and good at scale, the track-fitted homography is the reverse. Method 3 takes the
wrong half from each; method 4 takes the right half from each.

THE FREE FALSIFICATION TEST.  The game sets DataAspectRatio [1 1 1]
(Lunar_Blast_v4.m:52-53), so the screen->game mapping must be ISOTROPIC: one game
unit is the same number of pixels horizontally and vertically. The anisotropy of
J (ratio of its singular values) must therefore be ~1.0. It is a physical
constraint, free to check, and it flags every epoch head motion has corrupted --
in the same family as `y_flipped()`. `anisotropy()` below exposes it and
`ANISOTROPY_MAX` rejects fits that violate it.

Run:  LB_OUT=out_hybrid2 ../.venv/bin/python fit_hybrid2.py
"""
import os

import numpy as np
import pandas as pd

import cv2

from common import FLOW, CORNER_GAME_XY, GAME_XLIM, GAME_YLIM, OUT, ROOT
from compare_methods import m1_H_at, m1_quads
from fit_from_track import MIN_SCORE
from fit_hybrid import jacobian
from fit_mapping import camera_params, undistort
from sync import load_gaze

M1 = FLOW / "out_frame_annotate_method"
M2 = FLOW / "out_track"

# DataAspectRatio [1 1 1] makes the true mapping isotropic, so J's singular
# values must be equal. Allow a little slack for corner-click noise and genuine
# perspective; anything beyond this is head-motion contamination, not geometry.
ANISOTROPY_MAX = 1.15


def anisotropy(J):
    """Ratio of J's singular values. Physically must be ~1.0 -- see module docstring."""
    sv = np.linalg.svd(J, compute_uv=False)
    return float(sv[0] / sv[1])


def rest_J(ts, ast_px, anchor_t, anchor_q):
    """J at each of `ts`, from the rest-corner quads interpolated to that time.

    One homography per tracked frame (a few hundred per epoch), evaluated at the
    asteroid's own pixel position so the local perspective is the right one.
    """
    quads = m1_H_at(ts, anchor_t, anchor_q)
    dst = np.array(CORNER_GAME_XY, dtype=np.float32)
    J = np.empty((len(ts), 2, 2))
    for i in range(len(ts)):
        H = cv2.getPerspectiveTransform(quads[i].astype(np.float32), dst)
        J[i] = jacobian(H, ast_px[i][None, :])[0]
    return J


def build(track, timeline, gaze, offsets, K, D, anchor_t, anchor_q, perturb=0.0):
    """Reconstruct gaze in game units with the rest-corner Jacobian."""
    g = gaze[gaze["valid"]]
    gt = g["ts_ns"].to_numpy().astype(np.float64)
    gu = undistort(g[["px", "py"]].to_numpy(), K, D)

    rows, report = [], []
    for ep, t in track.groupby("epoch"):
        t = t[t["score"] >= MIN_SCORE].sort_values("ts_ns")
        d = timeline[timeline["EpochIndex"] == ep].sort_values("t_utc_ns")
        if len(t) < 60 or len(d) < 30 or ep not in offsets:
            continue
        off = offsets[ep] + perturb

        tu = undistort(t[["ast_px", "ast_py"]].to_numpy(), K, D)
        tt = t["ts_ns"].to_numpy().astype(np.float64)

        Jt = rest_J(tt, tu, anchor_t, anchor_q)
        aniso = anisotropy(Jt.mean(0))
        if aniso > ANISOTROPY_MAX:
            report.append(dict(epoch=int(ep), anisotropy=aniso, ok=False, n=0))
            continue

        sel = (gt >= tt[0]) & (gt <= tt[-1])
        if sel.sum() < 20:
            continue
        gts, gpts = gt[sel], gu[sel]

        ax = np.interp(gts, tt, tu[:, 0])
        ay = np.interp(gts, tt, tu[:, 1])
        base_x = np.interp(gts + off * 1e9, d["t_utc_ns"], d["Asteroid_X"],
                           left=np.nan, right=np.nan)
        base_y = np.interp(gts + off * 1e9, d["t_utc_ns"], d["Asteroid_Y"],
                           left=np.nan, right=np.nan)

        # J varies slowly, so interpolate its four entries onto the gaze clock
        # rather than rebuilding a homography per gaze sample.
        Jg = np.empty((len(gts), 2, 2))
        for a in range(2):
            for b in range(2):
                Jg[:, a, b] = np.interp(gts, tt, Jt[:, a, b])

        delta = np.c_[gpts[:, 0] - ax, gpts[:, 1] - ay]
        dg = np.einsum("nij,nj->ni", Jg, delta)

        rows.append(pd.DataFrame(dict(
            ts_ns=g["ts_ns"].to_numpy()[sel], px=g["px"].to_numpy()[sel],
            py=g["py"].to_numpy()[sel], az=g["az"].to_numpy()[sel],
            el=g["el"].to_numpy()[sel], valid=True,
            game_x=base_x + dg[:, 0], game_y=base_y + dg[:, 1],
            epoch=int(ep))))
        report.append(dict(epoch=int(ep), anisotropy=aniso, ok=True, n=int(sel.sum())))

    mapped = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return mapped, pd.DataFrame(report)


def main() -> None:
    K, D = camera_params()
    track = pd.read_csv(M2 / "asteroid_track.csv")
    timeline = pd.read_csv(M2 / "timeline.csv")
    # Per-epoch clock offset. By default these come from method 2, which solves
    # them from the same track-fitted homography head motion corrupts -- so on a
    # damaged epoch the offset is as untrustworthy as the mapping was. Point
    # LB_OFFSETS at refit_offsets.py's output to use the re-solved ones.
    src = os.environ.get("LB_OFFSETS")
    if src:
        o = pd.read_csv(FLOW / src)
        offsets = dict(zip(o["epoch"], o["offset_final"]))
        print(f"clock offsets from {src}: " +
              ", ".join(f"{k}={v}" for k, v in o["source"].value_counts().items()))
    else:
        rep = pd.read_csv(M2 / "track_mapping_report.csv")
        offsets = dict(zip(rep["epoch"], rep["offset_s"]))
    gaze = load_gaze()
    anchor_t, anchor_q = m1_quads(K, D)

    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("timeline.csv", "rest_windows.csv", "sync_per_epoch.csv",
                 "track_mapping_report.csv", "asteroid_track.csv"):
        src = M2 / name
        if src.exists() and not (OUT / name).exists():
            (OUT / name).write_bytes(src.read_bytes())

    mapped, report = build(track, timeline, gaze, offsets, K, D, anchor_t, anchor_q)
    mapped.drop(columns=["epoch"]).to_csv(OUT / "gaze_mapped.csv", index=False)
    report.to_csv(OUT / "jacobian_report.csv", index=False)

    on = mapped["game_x"].between(*GAME_XLIM) & mapped["game_y"].between(*GAME_YLIM)
    print(f"reconstructed {len(mapped)} gaze samples over {mapped['epoch'].nunique()} epochs")
    print(f"  inside the game area: {on.mean()*100:.1f}%")
    print(f"  anisotropy: median {report['anisotropy'].median():.3f}  "
          f"max {report['anisotropy'].max():.3f}  "
          f"rejected {int((~report['ok']).sum())}/{len(report)}")
    print(f"\nwrote {OUT/'gaze_mapped.csv'}")


if __name__ == "__main__":
    main()
