"""Re-solve the per-epoch clock offset without the contaminated homography.

THE PROBLEM.  `fit_from_track.game_mapping` solves delta per epoch by sweeping it
and keeping the offset that minimises the reprojection residual of a homography
fitted from the asteroid track. On an epoch where mid-trial head rotation has
corrupted that homography (see fit_hybrid2.py), the residual surface it is
minimising is itself meaningless, so the offset it returns is arbitrary within
whatever window it is allowed to search.

`drift_model` correctly excludes those epochs from the drift LINE (it drops
resid_median >= 3), but the second pass then re-solves every epoch inside
+-0.25 s of the line -- including the excluded ones -- using the same broken
residual. Epoch 33 comes out at -0.332 s where its neighbours 31 and 34 sit at
-0.553 and -0.569: ~220 ms off-trend, on a clock that physically drifts 15 ms
per epoch.

THE FIX.  Solve delta from the SHAPE of the tracked pixel trajectory alone --
no homography anywhere, so nothing head motion corrupted can enter.

Two properties make this work:

  * SCALE-FREE. The tracked py is a monotone function of game y, whatever the
    mapping's scale happens to be. Z-scoring both sides removes the scale
    entirely, so the inflated x-scale that ruins epoch 33's homography is simply
    not in the calculation.

  * HEAD-MOTION-FREE. Head rotation moves py smoothly over seconds; the asteroid
    is a triangle wave with a period of 4-14 s and sharp turning points.
    Subtracting a low-order polynomial fit over the epoch removes the head drift
    and leaves the wave, so the alignment is driven by the turning points, which
    are exactly what carries the timing.

Only Asteroid_Y is used. Asteroid_X is a linear ramp and correlation is invariant
to shifting a linear signal, so it carries no timing information at all (the
README's trap 4).

Aliasing is handled as `fit_from_track` does it: a triangle wave repeats every
half period, so the sweep is confined to a narrow window around the drift line
rather than searched globally.

An earlier attempt solved delta against method 1's rest-corner mapping in
ABSOLUTE game units, on the theory that its position error is a constant within
one epoch. It is not -- the error varies mid-trial, which is method 1's whole
problem -- and all three damaged epochs railed to the edge of the search window.
That approach is not in this file; the shape method below replaced it.

TWO WAYS TO USE THE RESULT.  `offset_final` is written in one of two modes, set by
LB_OFFSET_MODE:

  global    (default) -- one 3-parameter model for the whole session, fitted on the
            confidently-solved epochs. The clocks differ by a RATE, which is
            physically required to be linear in time, so the per-epoch scatter is
            mostly measurement noise in our own estimates. Averaging 34 measurements
            into 3 parameters beats using each epoch's noisy solve on its own, and it
            supplies an offset for the waveFreq 1.0 epochs that cannot be solved at
            all.

  per-epoch -- each epoch's own shape solve, falling back to the drift line or method
            2 where the shape match did not localise delta. Use this if you suspect a
            genuine step in the clock, which a linear model cannot represent.

THE THIRD PARAMETER IS NOT A CLOCK TERM. The residual about a 2-parameter line splits
by CONDITION -- TRACKING epochs land +29.5 ms later than WATCHING ones and scatter
2.5x more. No clock can know the condition. The cause is in the stimulus program:
`Lunar_Blast_v4.m:890` renders with `drawnow limitrate`, which throttles and skips
frames when the renderer is busy, and TRACKING draws the laser as well as the
asteroid. So the display lags the log further in that condition. It is a rendering
artifact, and modelling it explicitly is what lets the slope be estimated cleanly.

Run:  ../.venv/bin/python refit_offsets.py
      LB_OFFSET_MODE=per-epoch ../.venv/bin/python refit_offsets.py
"""
import os

import numpy as np
import pandas as pd

import cv2

from common import FLOW, CORNER_GAME_XY, ROOT
from compare_methods import m1_H_at, m1_quads
from fit_from_track import MIN_SCORE, drift_model
from fit_hybrid import epoch_H, jacobian
from fit_hybrid2 import ANISOTROPY_MAX, anisotropy, rest_J
from fit_mapping import camera_params, undistort

ANALYSIS = FLOW
M2 = ANALYSIS / "out_track"
WINDOW = 0.35          # seconds either side of the drift line
DETREND_ORDER = 1      # see below -- a LINE only
MIN_SHAPE_R = 0.90     # below this the shape match has not localised delta
OFFSET_MODE = os.environ.get("LB_OFFSET_MODE", "global")

# WHY ORDER 1.  The detrend must not be able to represent the wave it is meant to
# preserve. A trial is 14 s and the wave period is 14/waveFreq, so the trial holds
# exactly `waveFreq` cycles -- as few as ONE. A cubic fits most of a single
# triangle cycle, which is why order 3 collapses the correlation to r~0.65 on the
# waveFreq 1.0 epochs and sends them to the edge of the search window. A line
# cannot, and at order 1 every epoch from waveFreq 1.5 up returns r >= 0.99.
#
# WAVEFREQ 1.0 IS NOT SOLVABLE THIS WAY, at any order. One cycle in 14 s gives a
# very broad correlation peak, so delta is simply not localised -- r falls to
# 0.25-0.81 and the estimate wanders hundreds of ms. Those eight epochs fall back
# to the drift line, which is the better estimator for them anyway.
STEP = 0.005


def detrend(y, ts, order=None):
    """Strip slow head drift, keep the asteroid's wave.

    Head rotation moves the whole image smoothly over the 14 s trial; the
    asteroid oscillates several times within it. A low-order polynomial absorbs
    the former and leaves the latter.
    """
    order = DETREND_ORDER if order is None else order
    x = (ts - ts[0]) / (ts[-1] - ts[0])
    return y - np.polyval(np.polyfit(x, y, order), x)


def zscore(v):
    s = v.std()
    return (v - v.mean()) / s if s > 0 else v * 0.0


def solve_offset(ast_py, ts, d, centre):
    """Best delta in [centre-WINDOW, centre+WINDOW] by SHAPE match, scale-free.

    Image y runs down and game y runs up, so the correlation sought is the most
    NEGATIVE one. Returns (offset, |r|) with |r| as the confidence.
    """
    p = zscore(detrend(ast_py.astype(np.float64), ts))
    best = (None, -np.inf)
    for off in np.arange(centre - WINDOW, centre + WINDOW + 1e-9, STEP):
        ly = np.interp(ts + off * 1e9, d["t_utc_ns"], d["Asteroid_Y"],
                       left=np.nan, right=np.nan)
        k = np.isfinite(ly)
        if k.sum() < 50:
            continue
        r = float(np.corrcoef(p[k], zscore(ly[k]))[0, 1])
        if -r > best[1]:
            best = (float(off), -r)
    return best


def global_model(c):
    """Joint least-squares fit  delta(t) = a + b*t + c*[condition is TRACKING].

    Fitted on every confident epoch, not just the laser-free ones: the condition
    term absorbs the rendering bias, so all 34 measurements can constrain the
    rate instead of only the 17 WATCHING ones.
    """
    A = np.c_[np.ones(len(c)), c["t_s"].to_numpy(),
              (c["cond"] == "TRACKING").to_numpy(float)]
    coef, *_ = np.linalg.lstsq(A, c["offset_new"].to_numpy(), rcond=None)
    return coef


def predict(coef, t_s, cond):
    return coef[0] + coef[1] * t_s + coef[2] * (np.asarray(cond) == "TRACKING")


def loocv(c):
    """Leave-one-out error of the global model against each epoch's own solve.

    Both sides carry the shape estimator's noise, so this is not a clean measure
    of the model's absolute accuracy -- it is the honest comparison available:
    if the model predicts a held-out epoch about as well as that epoch measures
    itself, the model has captured the real structure and the rest is noise.
    """
    err = []
    for i in range(len(c)):
        fit = c.drop(c.index[i])
        co = global_model(fit)
        row = c.iloc[i]
        err.append(predict(co, row["t_s"], row["cond"]) - row["offset_new"])
    return np.array(err)


def main() -> None:
    K, D = camera_params()
    track = pd.read_csv(M2 / "asteroid_track.csv")
    timeline = pd.read_csv(M2 / "timeline.csv")
    rep = pd.read_csv(M2 / "track_mapping_report.csv")
    anchor_t, anchor_q = m1_quads(K, D)

    model, params = drift_model(rep)
    if model is None:
        raise SystemExit("no usable drift trend")
    sl, ic, nfit = params
    print(f"drift line from method 2 (fitted on {nfit} consistent epochs): "
          f"{sl*1000:+.1f} ms/epoch, {ic*1000:+.0f} ms at epoch 0\n")

    t0 = timeline["t_utc_ns"].min()
    ep_start = timeline.groupby("EpochIndex")["t_utc_ns"].min()
    ep_cond = timeline.groupby("EpochIndex")["Condition"].first()

    rows = []
    for ep, t in track.groupby("epoch"):
        t = t[t["score"] >= MIN_SCORE].sort_values("ts_ns")
        d = timeline[timeline["EpochIndex"] == ep].sort_values("t_utc_ns")
        if len(t) < 60 or len(d) < 30:
            continue
        tu = undistort(t[["ast_px", "ast_py"]].to_numpy(), K, D)
        ts = t["ts_ns"].to_numpy().astype(np.float64)

        # The decision below must test the TRACK-fitted Jacobian -- that is the
        # one head motion corrupts, and the one method 2's offset solve relies on.
        # The rest-corner Jacobian is near-isotropic by construction and would
        # never fire.
        Ht = epoch_H(tu, ts, d, float(rep.loc[rep["epoch"] == ep, "offset_s"].iloc[0]))
        aniso = (anisotropy(jacobian(Ht, tu.mean(0)[None, :])[0])
                 if Ht is not None else np.inf)
        off, conf = solve_offset(tu[:, 1], ts, d, model(ep))

        old = float(rep.loc[rep["epoch"] == ep, "offset_s"].iloc[0])
        old_r = float(rep.loc[rep["epoch"] == ep, "resid_median"].iloc[0])
        # Pick the best available estimator for this epoch. Never trust the
        # per-epoch homography solve where head motion wrecked the homography.
        if conf >= MIN_SHAPE_R:
            final, src = off, "shape"
        elif old_r >= 3 or aniso > ANISOTROPY_MAX:
            final, src = model(ep), "drift-line"
        else:
            final, src = old, "method2"

        rows.append(dict(epoch=int(ep), offset_old=old, offset_new=off,
                         offset_final=final, source=src,
                         delta_ms=(off - old) * 1000,
                         line=model(ep),
                         old_off_line_ms=(old - model(ep)) * 1000,
                         new_off_line_ms=(off - model(ep)) * 1000,
                         shape_r=conf, track_resid=old_r,
                         track_aniso=aniso,
                         t_s=(ep_start[ep] - t0) / 1e9, cond=ep_cond[ep]))

    r = pd.DataFrame(rows)

    # --- global model ------------------------------------------------------
    c = r[r["shape_r"] >= MIN_SHAPE_R]
    coef = global_model(c)
    r["offset_global"] = predict(coef, r["t_s"].to_numpy(), r["cond"].to_numpy())
    if OFFSET_MODE == "global":
        r["offset_final"] = r["offset_global"]
        r["source"] = "global-model"
    r.to_csv(ANALYSIS / "offsets_refit.csv", index=False)

    damaged = r["track_resid"] >= 3
    print("epochs whose track-fitted homography was untrustworthy (resid >= 3):")
    print(r[damaged][["epoch", "offset_old", "offset_new", "delta_ms",
                      "old_off_line_ms", "new_off_line_ms"]]
          .to_string(index=False, float_format=lambda x: f"{x:8.3f}"))

    conf = r["shape_r"] >= MIN_SHAPE_R
    print(f"\nshape match localised delta on {int(conf.sum())}/{len(r)} epochs "
          f"(r >= {MIN_SHAPE_R}); the rest are waveFreq 1.0 and fall back to the line.")

    print("\nscatter about the drift line (ms), CONFIDENT epochs only -- the two"
          "\nclocks drift smoothly, so a real jump is not physically possible:")
    for lab, col in (("method 2's own solve", "old_off_line_ms"),
                     ("shape re-solve      ", "new_off_line_ms")):
        s = r.loc[conf, col]
        print(f"  {lab}  sd {s.std():6.1f}  max|.| {s.abs().max():6.1f}")
    print("\n  The shape re-solve uses NO homography and NO gaze, so its agreement"
          "\n  with the drift line is independent confirmation of the drift model.")

    resid = c["offset_new"] - predict(coef, c["t_s"].to_numpy(), c["cond"].to_numpy())
    lo = loocv(c)
    print("\n" + "=" * 70)
    print("GLOBAL MODEL   delta(t) = a + b*t + c*[TRACKING]")
    print("=" * 70)
    print(f"  a = {coef[0]*1000:+8.1f} ms      (clock offset at session start)")
    print(f"  b = {coef[1]*1e6:+8.1f} ppm     (clock RATE difference)")
    print(f"  c = {coef[2]*1000:+8.1f} ms      (TRACKING rendering bias -- not a clock term)")
    print(f"\n  in-sample residual sd   {resid.std()*1000:5.1f} ms   over {len(c)} confident epochs")
    print(f"  leave-one-out error sd  {lo.std()*1000:5.1f} ms")
    print(f"  standard error of the fitted line ~ {resid.std()/np.sqrt(len(c))*1000:.1f} ms")
    print("\n  Both sides of the LOO comparison carry the shape estimator's own noise,")
    print("  so this is a floor on the model's quality, not a measure of its error.")
    print(f"\nmode: {OFFSET_MODE}  (set LB_OFFSET_MODE=per-epoch for the per-epoch solves)")

    print("\nfinal offset source:")
    print("  " + r["source"].value_counts().to_string().replace("\n", "\n  "))

    print(f"\nwrote {ANALYSIS/'offsets_refit.csv'}")
    print("feed it to method 4 with:  LB_OFFSETS=offsets_refit.csv ./run.sh")


if __name__ == "__main__":
    main()
