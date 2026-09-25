"""Stage 2 -- solve the MATLAB-to-Neon clock offset.

Deliberately independent of the screen mapping: raw gaze `elevation [deg]` already
correlates with Asteroid_Y up to sign and scale, so this needs no homography and
cannot be contaminated by (or contaminate) the corner annotations.

The asteroid's vertical motion is a triangle wave at 1.0-3.5 cycles per 14 s trial,
repeated over 42 epochs -- a strong, high-SNR alignment signal.

Emits out/sync.json.
"""
import json

import os

import numpy as np
import pandas as pd

from common import GAZE_CSV, OUT

GRID_HZ = 100.0
SWEEP_S = 2.0
STEP_S = 0.005


def load_gaze() -> pd.DataFrame:
    g = pd.read_csv(GAZE_CSV, usecols=[
        "timestamp [ns]", "gaze x [px]", "gaze y [px]", "worn", "blink id",
        "azimuth [deg]", "elevation [deg]"])
    g = g.rename(columns={"timestamp [ns]": "ts_ns", "gaze x [px]": "px",
                          "gaze y [px]": "py", "azimuth [deg]": "az",
                          "elevation [deg]": "el"})
    # `worn` is NOT required by default, and that is deliberate.
    #
    # Neon's worn detector goes false for large stretches of epochs 38-42 (43 %
    # of epoch 41), which is what puts the visible gaps in those epochs' plots.
    # It is a FALSE negative. Measured against the tracked asteroid -- a check
    # that needs no mapping and no clock -- the worn==0 samples track just as
    # well as the worn==1 ones, and in four of the five epochs slightly better:
    #
    #     epoch   worn=1    worn=0
    #        38    3.27      2.96 deg
    #        39    3.83      3.31
    #        40    2.90      2.61
    #        41    3.97      3.49
    #        42    3.78      4.37
    #
    # Glasses genuinely off a face do not produce gaze that lands 3 deg from a
    # moving target. Filtering on `worn` throws away ~1100 good samples in epoch
    # 41 alone. Blinks are a real occlusion and are still excluded.
    #
    # Set LB_REQUIRE_WORN=1 to restore the old, stricter behaviour.
    valid = g["blink id"].isna()
    if os.environ.get("LB_REQUIRE_WORN") == "1":
        valid &= g["worn"] == 1
    g["valid"] = valid
    return g.sort_values("ts_ns").reset_index(drop=True)


def epoch_grids(timeline: pd.DataFrame, gaze: pd.DataFrame):
    """Per epoch: a common time grid, the asteroid Y, and an interpolator input."""
    out = []
    gts = gaze["ts_ns"].to_numpy()
    gel = gaze["el"].to_numpy()
    gaz = gaze["az"].to_numpy()
    gvalid = gaze["valid"].to_numpy()

    for ep, d in timeline.groupby("EpochIndex"):
        d = d.sort_values("t_utc_ns")
        t0, t1 = d["t_utc_ns"].iloc[0], d["t_utc_ns"].iloc[-1]
        n = int((t1 - t0) / 1e9 * GRID_HZ)
        grid = t0 + (np.arange(n) / GRID_HZ * 1e9).astype("int64")
        ast_y = np.interp(grid, d["t_utc_ns"], d["Asteroid_Y"])
        ast_x = np.interp(grid, d["t_utc_ns"], d["Asteroid_X"])
        out.append(dict(epoch=int(ep), grid=grid, ast_y=ast_y, ast_x=ast_x,
                        cond=d["Condition"].iloc[0], freq=float(d["waveFreq"].iloc[0])))
    return out, (gts, gel, gaz, gvalid)


def corr_at(offset_s, eps, gz, key="ast_y", chan="el"):
    """Mean per-epoch |correlation| between shifted gaze and the stimulus."""
    gts, gel, gaz, gvalid = gz
    sig = gel if chan == "el" else gaz
    shift = int(offset_s * 1e9)
    vals = []
    for e in eps:
        t = e["grid"] + shift
        idx = np.searchsorted(gts, t)
        idx = np.clip(idx, 0, len(gts) - 1)
        ok = gvalid[idx]
        # Only use grid points whose nearest gaze sample is within 10 ms.
        ok &= np.abs(gts[idx] - t) < 10_000_000
        if ok.sum() < 100:
            continue
        a, b = sig[idx][ok], e[key][ok]
        if a.std() < 1e-6 or b.std() < 1e-6:
            continue
        vals.append(abs(np.corrcoef(a, b)[0, 1]))
    return float(np.mean(vals)) if vals else 0.0


def best_offset(eps, gz, key="ast_y", chan="el", lo=-SWEEP_S, hi=SWEEP_S, step=STEP_S):
    offs = np.arange(lo, hi + step / 2, step)
    curve = np.array([corr_at(o, eps, gz, key, chan) for o in offs])
    return float(offs[curve.argmax()]), float(curve.max()), offs, curve


def main() -> None:
    timeline = pd.read_csv(OUT / "timeline.csv")
    gaze = load_gaze()
    print(f"gaze samples: {len(gaze)}  valid: {gaze['valid'].mean()*100:.1f}%")

    eps, gz = epoch_grids(timeline, gaze)
    print(f"epochs: {len(eps)}")

    # Coarse then fine, so the 5 ms sweep stays cheap.
    c_off, c_r, _, _ = best_offset(eps, gz, step=0.05)
    off, r, offs, curve = best_offset(eps, gz, lo=c_off - 0.2, hi=c_off + 0.2)
    print(f"coarse {c_off*1000:+.0f} ms (r={c_r:.3f}) -> fine {off*1000:+.1f} ms (r={r:.3f})")
    print(f"r at zero offset: {corr_at(0.0, eps, gz):.3f}")

    # Per-epoch offsets, to test for drift over the 18.6 min session.
    per = []
    for e in eps:
        o, rr, _, _ = best_offset([e], gz, lo=off - 0.5, hi=off + 0.5, step=0.005)
        per.append(dict(epoch=e["epoch"], offset_s=o, r=rr,
                        cond=e["cond"], freq=e["freq"]))
    per_df = pd.DataFrame(per)
    good = per_df[per_df["r"] > 0.5]
    slope, intercept = np.polyfit(good["epoch"], good["offset_s"], 1) if len(good) > 2 else (np.nan, np.nan)
    print(f"per-epoch: {len(good)}/{len(per_df)} with r>0.5, "
          f"median {good['offset_s'].median()*1000:+.0f} ms, "
          f"IQR {(good['offset_s'].quantile(.75)-good['offset_s'].quantile(.25))*1000:.0f} ms")
    print(f"drift slope: {slope*1000:+.2f} ms/epoch  (~{slope*1000*42:+.0f} ms over session)")

    per_df.to_csv(OUT / "sync_per_epoch.csv", index=False)
    json.dump(dict(offset_s=off, corr=r, corr_at_zero=corr_at(0.0, eps, gz),
                   drift_ms_per_epoch=float(slope * 1000) if slope == slope else None,
                   n_epochs_good=int(len(good)),
                   sweep_offsets_s=offs.tolist(), sweep_corr=curve.tolist()),
              open(OUT / "sync.json", "w"), indent=2)
    print(f"\nwrote {OUT/'sync.json'}\nwrote {OUT/'sync_per_epoch.csv'}")


if __name__ == "__main__":
    main()
