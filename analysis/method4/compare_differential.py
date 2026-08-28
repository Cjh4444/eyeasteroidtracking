"""Score the differential methods (3 and 4) against method 2 -- and honestly.

WHY NOT compare_methods.py.  That harness scores "given the asteroid's measured
pixel position, predict its game coordinates". The task is ABSOLUTE, and it is
degenerate for the differential methods: both place the asteroid on the MATLAB
log path by construction, so they would score a meaningless zero. They need a
different yardstick.

THE YARDSTICK.  `out_track/gaze_vs_asteroid.csv` holds the gaze-to-asteroid
separation in DEGREES, computed from raw scene-camera pixels and the camera
intrinsics alone -- no homography, no clock, no MATLAB log. It is the most
trustworthy quantity in the project. Every method's reconstructed separation is
just that measurement pushed through a scale factor, so the scale is the only
thing left to score.

TEST A -- ANISOTROPY (physical).  DataAspectRatio [1 1 1] forces the mapping to
be isotropic, so J's singular-value ratio must be ~1.0. Non-circular for method
3. NOT independent evidence for method 4, whose J is built from four corners of
a rectangle of known aspect and is therefore near-isotropic by construction --
reported for completeness, not as a win.

TEST B -- CALIBRATION-BIAS STABILITY (the real test, non-circular for both).
Sarah's gaze calibration offset is a property of her eyes and how the glasses sat
on her face. It is constant, or near enough, across an 18-minute session -- the
README measures it globally at (-2.00, -1.81) deg. Reconstructed into game units
it must therefore also be near-constant per epoch. A method whose per-epoch scale
is corrupted will show that corruption as a per-epoch bias outlier. This test
uses no corner data and no aspect-ratio assumption; it only requires that a
physical property of the subject does not jump around between trials.

TEST C -- BEHAVIOURAL FALSIFICATION (fully independent).  `epoch_summary.csv`
reports ContactPct from the game itself, which never touched any mapping. If
reconstructed tracking error is real, it must rise as ContactPct falls. Scale
corruption is uncorrelated with waveFreq, so removing it should TIGHTEN this
relationship. Reported as Spearman correlation.

Run:  ../.venv/bin/python compare_differential.py
"""
import glob

import numpy as np
import pandas as pd

import cv2

from common import FLOW, ROOT
from compare_methods import m1_quads
from fit_from_track import MIN_SCORE
from fit_hybrid import epoch_H, jacobian
from fit_hybrid2 import anisotropy, rest_J
from fit_mapping import camera_params, undistort
from sync import load_gaze

ANALYSIS = FLOW
M2 = ANALYSIS / "out_track"


def summary_table():
    f = glob.glob(str(ROOT / "test_sarah_LB" / "*_epoch_summary.csv"))
    s = pd.read_csv(f[0])
    return s.set_index("EpochIndex")[["ContactPct", "waveFreq", "Condition"]]


def main() -> None:
    K, D = camera_params()
    track = pd.read_csv(M2 / "asteroid_track.csv")
    timeline = pd.read_csv(M2 / "timeline.csv")
    rep = pd.read_csv(M2 / "track_mapping_report.csv")
    offsets = dict(zip(rep["epoch"], rep["offset_s"]))
    gaze = load_gaze()
    anchor_t, anchor_q = m1_quads(K, D)
    summ = summary_table()

    gv = gaze[gaze["valid"]]
    gt_all = gv["ts_ns"].to_numpy().astype(np.float64)
    gu_all = undistort(gv[["px", "py"]].to_numpy(), K, D)

    rows = []
    for ep, t in track.groupby("epoch"):
        t = t[t["score"] >= MIN_SCORE].sort_values("ts_ns")
        d = timeline[timeline["EpochIndex"] == ep].sort_values("t_utc_ns")
        if len(t) < 60 or ep not in offsets:
            continue
        off = offsets[ep]
        tu = undistort(t[["ast_px", "ast_py"]].to_numpy(), K, D)
        tt = t["ts_ns"].to_numpy().astype(np.float64)

        H3 = epoch_H(tu, tt, d, off)
        if H3 is None:
            continue
        sel = (gt_all >= tt[0]) & (gt_all <= tt[-1])
        if sel.sum() < 20:
            continue
        gts, gp = gt_all[sel], gu_all[sel]
        ax = np.interp(gts, tt, tu[:, 0])
        ay = np.interp(gts, tt, tu[:, 1])
        delta = np.c_[gp[:, 0] - ax, gp[:, 1] - ay]

        # the reference: separation in degrees, raw pixels + intrinsics only
        dx_deg = np.degrees(np.arctan2(delta[:, 0], K[0, 0]))
        dy_deg = np.degrees(np.arctan2(delta[:, 1], K[1, 1]))

        # method 3: J from the epoch's own asteroid-track homography
        J3 = jacobian(H3, np.c_[ax, ay])
        d3 = np.einsum("nij,nj->ni", J3, delta)

        # method 4: J from the rest-period corners
        Jt = rest_J(tt, tu, anchor_t, anchor_q)
        J4 = np.empty((len(gts), 2, 2))
        for a in range(2):
            for b in range(2):
                J4[:, a, b] = np.interp(gts, tt, Jt[:, a, b])
        d4 = np.einsum("nij,nj->ni", J4, delta)

        # method 2: gaze mapped absolutely through the same track homography,
        # minus the log path -- the separation its plots actually depict
        gm = cv2.perspectiveTransform(gp.reshape(-1, 1, 2), H3).reshape(-1, 2)
        base = np.c_[np.interp(gts + off * 1e9, d["t_utc_ns"], d["Asteroid_X"]),
                     np.interp(gts + off * 1e9, d["t_utc_ns"], d["Asteroid_Y"])]
        d2 = gm - base

        r = dict(epoch=int(ep),
                 aniso3=anisotropy(J3.mean(0)), aniso4=anisotropy(Jt.mean(0)),
                 deg=float(np.nanmedian(np.hypot(dx_deg, dy_deg))),
                 bias_x_deg=float(np.nanmedian(dx_deg)),
                 bias_y_deg=float(np.nanmedian(dy_deg)))
        for lab, dd in (("m2", d2), ("m3", d3), ("m4", d4)):
            r[f"{lab}_sep"] = float(np.nanmedian(np.hypot(*dd.T)))
            r[f"{lab}_bx"] = float(np.nanmedian(dd[:, 0]))
            r[f"{lab}_by"] = float(np.nanmedian(dd[:, 1]))
        rows.append(r)

    r = pd.DataFrame(rows).join(summ, on="epoch")
    r.to_csv(ANALYSIS / "differential_comparison.csv", index=False)

    print("=" * 74)
    print("TEST A -- ANISOTROPY OF J   (must be ~1.0; >1.15 is head-motion damage)")
    print("=" * 74)
    for lab, col in (("method 3 (track-fitted J)", "aniso3"),
                     ("method 4 (rest-corner J) ", "aniso4")):
        s = r[col]
        print(f"{lab}  median {s.median():5.3f}  max {s.max():5.3f}  "
              f"over 1.15: {int((s > 1.15).sum())}/{len(s)}")
    bad = r.nlargest(4, "aniso3")[["epoch", "aniso3", "aniso4"]]
    print("\nworst offenders:")
    print(bad.to_string(index=False, float_format=lambda x: f"{x:6.2f}"))

    print("\n" + "=" * 74)
    print("TEST B -- CALIBRATION-BIAS STABILITY  (a physical constant; lower = better)")
    print("=" * 74)
    print("per-epoch reconstructed bias, in game units; spread across the session:")
    print(f"{'':>10} {'x: sd':>8} {'x: range':>10} {'y: sd':>8} {'y: range':>10}")
    for lab in ("m2", "m3", "m4"):
        bx, by = r[f"{lab}_bx"], r[f"{lab}_by"]
        print(f"{'method '+lab[1]:>10} {bx.std():8.2f} {bx.max()-bx.min():10.2f} "
              f"{by.std():8.2f} {by.max()-by.min():10.2f}")

    print("\nscale consistency -- reconstructed units per measured degree")
    print("(the same physical separation must convert the same way every epoch):")
    print(f"{'':>10} {'median':>8} {'CV %':>8}   worst epochs")
    for lab in ("m2", "m3", "m4"):
        ratio = r[f"{lab}_sep"] / r["deg"]
        cv = 100 * ratio.std() / ratio.mean()
        w = r.loc[(ratio / ratio.median() - 1).abs().nlargest(3).index, "epoch"].tolist()
        print(f"{'method '+lab[1]:>10} {ratio.median():8.2f} {cv:8.1f}   {w}")

    print("\n" + "=" * 74)
    print("TEST C -- BEHAVIOURAL FALSIFICATION  (vs ContactPct, fully independent)")
    print("=" * 74)
    print("reconstructed tracking error must rise as the game's own contact rate falls:")
    for lab in ("m2", "m3", "m4"):
        rho = r[f"{lab}_sep"].corr(r["ContactPct"], method="spearman")
        print(f"{'method '+lab[1]:>10}  spearman(sep, ContactPct) = {rho:+.3f}")
    print(f"{'reference':>10}  spearman(deg, ContactPct) = "
          f"{r['deg'].corr(r['ContactPct'], method='spearman'):+.3f}   <- the yardstick")

    print(f"\nwrote {ANALYSIS/'differential_comparison.csv'}")


if __name__ == "__main__":
    main()
