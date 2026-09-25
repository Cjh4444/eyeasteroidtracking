"""METHOD 2, stage T2 -- compare gaze to the tracked asteroid.

Both signals live in scene-camera pixels on the SAME Neon UTC clock:
  * asteroid  -- tracked per video frame, timestamped by world_timestamps.csv
  * gaze      -- gaze.csv, timestamped identically

So the comparison needs no homography AND no cross-clock alignment. If the head
turns, the asteroid and the gaze point move together in the image and their
difference is unchanged. This is the property method 1 could not offer: there,
mid-trial head motion had to be interpolated between rest anchors that are up to
100 px apart.

Pixel error is converted to degrees of visual angle via the scene-camera
intrinsics, which is the interpretable unit for eye-tracking accuracy.

Emits <LB_OUT>/gaze_vs_asteroid.csv and <LB_OUT>/gaze_mapped.csv (the latter for
plot.py, in game units, via a per-epoch homography fitted from the track itself).
"""
import json

import cv2
import numpy as np
import pandas as pd

from common import GAME_XLIM, GAME_YLIM, OUT
from fit_mapping import camera_params, undistort
from sync import load_gaze

MIN_SCORE = 0.35
MAX_JOIN_MS = 25.0     # gaze sample must be within this of a tracked frame


def px_to_deg(dx, dy, K):
    """Small-angle-exact conversion of a pixel offset to degrees of visual angle."""
    return (np.degrees(np.arctan2(dx, K[0, 0])), np.degrees(np.arctan2(dy, K[1, 1])))


def join_gaze(track, gaze, K, D):
    """Attach each valid gaze sample to the asteroid position at that instant."""
    track = track.sort_values("ts_ns").reset_index(drop=True)
    g = gaze[gaze["valid"]].sort_values("ts_ns").reset_index(drop=True)

    good = track[track["score"] >= MIN_SCORE]
    if len(good) < 10:
        raise SystemExit("almost nothing tracked confidently")

    # Undistort both sides before differencing -- lens distortion is ~50 px at
    # the frame edges and would otherwise leak straight into the error.
    tu = undistort(good[["ast_px", "ast_py"]].to_numpy(), K, D)
    gu = undistort(g[["px", "py"]].to_numpy(), K, D)

    tt = good["ts_ns"].to_numpy().astype(np.float64)
    gt = g["ts_ns"].to_numpy().astype(np.float64)
    ax = np.interp(gt, tt, tu[:, 0], left=np.nan, right=np.nan)
    ay = np.interp(gt, tt, tu[:, 1], left=np.nan, right=np.nan)

    # Reject gaze samples that fall in a tracking gap rather than between two
    # neighbouring frames.
    idx = np.clip(np.searchsorted(tt, gt), 1, len(tt) - 1)
    near = np.minimum(np.abs(tt[idx] - gt), np.abs(tt[idx - 1] - gt)) <= MAX_JOIN_MS * 1e6

    out = pd.DataFrame(dict(
        ts_ns=g["ts_ns"], gaze_x=gu[:, 0], gaze_y=gu[:, 1], ast_x=ax, ast_y=ay))
    out["dx_px"] = out["gaze_x"] - out["ast_x"]
    out["dy_px"] = out["gaze_y"] - out["ast_y"]
    out.loc[~near, ["ast_x", "ast_y", "dx_px", "dy_px"]] = np.nan
    out["dx_deg"], out["dy_deg"] = px_to_deg(out["dx_px"], out["dy_px"], K)
    out["err_deg"] = np.hypot(out["dx_deg"], out["dy_deg"])
    out["epoch"] = np.interp(gt, tt, good["epoch"].to_numpy()).round().astype(int)
    out.loc[~near, "epoch"] = -1
    return out


def y_flipped(H):
    """Does H map image-y-down to game-y-UP, as it physically must?

    Shifting a triangle wave by half a period inverts it, and a homography can
    absorb that inversion by flipping its y-scale -- producing a fit that is
    numerically excellent and physically impossible. The screen cannot invert, so
    rejecting the flip removes the false solution.
    """
    p = cv2.perspectiveTransform(
        np.array([[[800.0, 400.0]], [[800.0, 500.0]]], dtype=np.float64), H).reshape(-1, 2)
    return (p[1, 1] - p[0, 1]) >= 0        # game y should DECREASE as image y grows


def game_mapping(track, timeline, gaze, K, D, prior=None):
    """Per-epoch homography fitted from the asteroid track itself.

    The asteroid sweeps a large part of the screen during a trial, and its game
    coordinates are known from the MATLAB log, so the track supplies hundreds of
    correspondences per epoch -- far more than four clicked corners, and gathered
    DURING the trial rather than between trials.

    This is the one place the MATLAB clock matters, so it is kept off the primary
    result path: the gaze-vs-asteroid error above never uses it.

    The clock offset is SOLVED per epoch rather than assumed. Pairing a tracked
    pixel with the logged game position at the same nominal timestamp assumes the
    two clocks agree; they do not, and at 30 game units/s a half-second error
    mispairs by ~15 units, which no homography can absorb. That is exactly what
    makes it identifiable: sweep the offset, refit, and keep the one that
    minimises reprojection residual.
    """
    K_, D_ = K, D
    rows, report = [], []
    gv = gaze[gaze["valid"]]
    gu_all = undistort(gv[["px", "py"]].to_numpy(), K_, D_)
    gts = gv["ts_ns"].to_numpy().astype(np.float64)

    for ep, t in track.groupby("epoch"):
        t = t[t["score"] >= MIN_SCORE].sort_values("ts_ns")
        d = timeline[timeline["EpochIndex"] == ep].sort_values("t_utc_ns")
        if len(t) < 30 or len(d) < 30:
            continue
        src = undistort(t[["ast_px", "ast_py"]].to_numpy(), K_, D_)
        ts = t["ts_ns"].to_numpy().astype(np.float64)

        def fit(off):
            dst = np.c_[np.interp(ts + off * 1e9, d["t_utc_ns"], d["Asteroid_X"],
                                  left=np.nan, right=np.nan),
                        np.interp(ts + off * 1e9, d["t_utc_ns"], d["Asteroid_Y"],
                                  left=np.nan, right=np.nan)]
            k = np.isfinite(dst).all(1)
            if k.sum() < 50:
                return None
            H_, inl_ = cv2.findHomography(src[k], dst[k], cv2.RANSAC, 2.0)
            if H_ is None or y_flipped(H_):
                return None
            proj = cv2.perspectiveTransform(src[k].reshape(-1, 1, 2), H_).reshape(-1, 2)
            return H_, inl_, float(np.median(np.hypot(*(proj - dst[k]).T))), dst, k

        # Second pass searches a narrow window around the drift model. The two
        # clocks drift apart smoothly (~15 ms per epoch), so an epoch that lands
        # far from the line is a bad fit, not a real jump.
        if prior is not None:
            lo_, hi_ = prior(ep) - 0.25, prior(ep) + 0.25
        else:
            lo_, hi_ = -2.0, 2.0

        best, best_off = None, 0.0
        for off in np.arange(lo_, hi_ + 1e-9, 1 / 30):       # coarse: one frame
            r = fit(off)
            if r and (best is None or r[2] < best[2]):
                best, best_off = r, off
        for off in np.arange(best_off - 1 / 30, best_off + 1 / 30, 0.005):
            r = fit(off)
            if r and r[2] < best[2]:
                best, best_off = r, off
        if best is None:
            continue
        H, inl, resid_med, dst, k = best
        proj = cv2.perspectiveTransform(src[k].reshape(-1, 1, 2), H).reshape(-1, 2)
        resid = np.hypot(*(proj - dst[k]).T)
        report.append(dict(epoch=int(ep), n=int(k.sum()), inliers=int(inl.sum()),
                           offset_s=float(best_off),
                           resid_median=float(resid_med),
                           resid_p90=float(np.quantile(resid, .9))))

        sel = (gts >= ts[0]) & (gts <= ts[-1])
        if not sel.any():
            continue
        gg = cv2.perspectiveTransform(gu_all[sel].reshape(-1, 1, 2), H).reshape(-1, 2)
        rows.append(pd.DataFrame(dict(ts_ns=gv["ts_ns"].to_numpy()[sel],
                                      px=gv["px"].to_numpy()[sel],
                                      py=gv["py"].to_numpy()[sel],
                                      az=gv["az"].to_numpy()[sel],
                                      el=gv["el"].to_numpy()[sel],
                                      valid=True,
                                      game_x=gg[:, 0], game_y=gg[:, 1])))
    return (pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()), pd.DataFrame(report)


def drift_model(report):
    """Robust line through the per-epoch offsets: offset(epoch) in seconds.

    Fitted on the epochs that agree with each other, so a single bad epoch cannot
    drag the model. Returns None if there is no usable trend.
    """
    r = report[report["resid_median"] < 3].copy()
    if len(r) < 8:
        return None, None
    for _ in range(3):                     # trim outliers, refit
        sl, ic = np.polyfit(r["epoch"], r["offset_s"], 1)
        resid = r["offset_s"] - (sl * r["epoch"] + ic)
        keep = np.abs(resid) <= max(0.15, 2.5 * resid.std())
        if keep.all():
            break
        r = r[keep]
    return (lambda ep: sl * ep + ic), (sl, ic, len(r))


def pursuit_lag(track, gaze, timeline):
    """Per-epoch lead/lag of gaze against the asteroid. No clock model involved.

    Gaze and the tracked asteroid are both timestamped by the Neon clock, so
    shifting one against the other measures eye behaviour directly -- nothing here
    touches the MATLAB log except to label conditions. Composing offsets from two
    different clocks (what method 1 attempted) cannot measure this: it lands a
    +2 s "lag" that no eye can produce.

    Sign: positive = gaze trails the asteroid; negative = gaze anticipates it.
    The sweep is capped at +/-0.6 s, inside physiological range and well clear of
    the triangle wave's half period (2.0 s at the fastest waveFreq), so there is
    no alias to fall into.
    """
    g = gaze[gaze["valid"]]
    gt = g["ts_ns"].to_numpy().astype(np.float64)
    gy = g["py"].to_numpy()
    cond = timeline.groupby("EpochIndex")[["Condition", "waveFreq"]].first()

    rows = []
    for ep, t in track.groupby("epoch"):
        t = t[t["score"] >= MIN_SCORE].sort_values("ts_ns")
        tt = t["ts_ns"].to_numpy().astype(np.float64)
        ay = t["ast_py"].to_numpy()
        best = (-9.0, np.nan)
        for o in np.arange(-0.6, 0.601, 0.005):
            ref = np.interp(tt + o * 1e9, gt, gy, left=np.nan, right=np.nan)
            k = np.isfinite(ref)
            if k.sum() < 100 or np.std(ref[k]) < 1e-6:
                continue
            r = np.corrcoef(ay[k], ref[k])[0, 1]
            if r > best[0]:
                best = (float(r), float(o))
        rows.append(dict(epoch=int(ep), r=best[0], lag_s=best[1]))
    return pd.DataFrame(rows).join(cond, on="epoch")


def main() -> None:
    K, D = camera_params()
    track = pd.read_csv(OUT / "asteroid_track.csv")
    timeline = pd.read_csv(OUT / "timeline.csv")
    gaze = load_gaze()

    conf = (track["score"] >= MIN_SCORE)
    print(f"tracked frames: {len(track)}  confident: {conf.mean()*100:.1f}%  "
          f"epochs: {track['epoch'].nunique()}")

    gv = join_gaze(track, gaze, K, D)
    gv.to_csv(OUT / "gaze_vs_asteroid.csv", index=False)
    ok = gv.dropna(subset=["err_deg"])
    print(f"\ngaze-to-asteroid error (head-motion invariant, no homography):")
    print(f"  n = {len(ok)} samples over {ok['epoch'].nunique()} epochs")
    print(f"  median {ok['err_deg'].median():.2f} deg   "
          f"p25 {ok['err_deg'].quantile(.25):.2f}   p75 {ok['err_deg'].quantile(.75):.2f}")
    print(f"  systematic bias: dx {ok['dx_deg'].median():+.2f} deg  "
          f"dy {ok['dy_deg'].median():+.2f} deg")

    by = ok.groupby("epoch")["err_deg"].median()
    cond = timeline.groupby("EpochIndex")[["Condition", "waveFreq"]].first()
    j = cond.join(by.rename("median_err_deg")).dropna()
    print("\n  by condition:")
    print(j.groupby("Condition")["median_err_deg"].agg(["size", "median"]).round(2).to_string())
    print("\n  by waveFreq:")
    print(j.groupby("waveFreq")["median_err_deg"].agg(["size", "median"]).round(2).to_string())

    lag = pursuit_lag(track, gaze, timeline)
    lag.to_csv(OUT / "pursuit_lag.csv", index=False)
    gl = lag[lag["r"] > 0.8]
    print(f"\npursuit lag, gaze vs tracked asteroid (both on the Neon clock, "
          f"no clock model):")
    print(f"  {len(gl)}/{len(lag)} epochs with r>0.8   "
          f"median {gl['lag_s'].median()*1000:+.0f} ms "
          f"({'anticipates' if gl['lag_s'].median() < 0 else 'trails'})")
    for by in ("Condition", "waveFreq"):
        agg = gl.groupby(by)["lag_s"].agg(n="size", ms=lambda s: round(s.median() * 1000))
        print(f"  by {by}: " + "  ".join(f"{i}={r.ms:+.0f}ms" for i, r in agg.iterrows()))

    mapped, rep = game_mapping(track, timeline, gaze, K, D)
    model, params = drift_model(rep)
    if model is not None:
        sl, ic, nfit = params
        print(f"\nMATLAB/Neon clock drift: {sl*1000:+.1f} ms per epoch "
              f"(fitted on {nfit} consistent epochs)")
        print(f"  offset {ic*1000:+.0f} ms at epoch 0 -> {(sl*42+ic)*1000:+.0f} ms at epoch 42"
              f"  =  {(sl*41)*1000:+.0f} ms of drift across the session")
        # Refit every epoch inside a narrow window around the model, which pulls
        # the stragglers onto the trend instead of letting them sit at an alias.
        mapped, rep = game_mapping(track, timeline, gaze, K, D, prior=model)
    if len(rep):
        print(f"\nper-epoch homography fitted from the track "
              f"({rep['n'].sum()} correspondences, {rep['inliers'].sum()} inliers):")
        print(f"  reprojection residual: median {rep['resid_median'].median():.2f} game units, "
              f"p90 {rep['resid_p90'].median():.2f}")
        o = rep["offset_s"] * 1000
        print(f"  solved MATLAB->Neon offset per epoch: median {o.median():+.0f} ms, "
              f"IQR {o.quantile(.25):+.0f}..{o.quantile(.75):+.0f}, "
              f"range {o.min():+.0f}..{o.max():+.0f} ms")
        rep.to_csv(OUT / "track_mapping_report.csv", index=False)
    if len(mapped):
        mapped.to_csv(OUT / "gaze_mapped.csv", index=False)
        on = (mapped["game_x"].between(*GAME_XLIM) & mapped["game_y"].between(*GAME_YLIM))
        print(f"  gaze inside the game area: {on.mean()*100:.1f}%")
        print(f"\nwrote {OUT/'gaze_mapped.csv'}")
    print(f"wrote {OUT/'gaze_vs_asteroid.csv'}")


if __name__ == "__main__":
    main()
