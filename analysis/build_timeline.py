"""Stage 1 — put the MATLAB stimulus log on the Neon UTC-nanosecond clock.

Emits:
  out/timeline.csv      every stimulus sample with an absolute t_utc_ns
  out/rest_windows.csv  one row per rest period, with the video frame to annotate

The rest windows are the whole point: during each one the game draws a gray fill
spanning exactly game [0,100]x[0,65] (Lunar_Blast_v4.m:439) with the fixation
cross at (50, 32.5) on top of it (:441). Those are our calibration targets.
"""
import re
import sys

import numpy as np
import pandas as pd

from common import (ALIGN_COUNTDOWN_DUR, CONFIG_CSV, EPOCH_GLOB, MATLAB_DIR,
                    MATLAB_TZ, OUT, REST_MARGIN, WORLD_TS_CSV)

EPOCH_RE = re.compile(r"_epoch_(\d{3})_waveFreq_")


def load_epochs() -> pd.DataFrame:
    paths = sorted(MATLAB_DIR.glob(EPOCH_GLOB))
    if not paths:
        sys.exit(f"no epoch CSVs matched {EPOCH_GLOB} in {MATLAB_DIR}")
    frames = []
    for p in paths:
        df = pd.read_csv(p)
        df["source_file"] = p.name
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    # EpochStart is MATLAB `now` stamped the instant asteroid motion begins
    # (Lunar_Blast_v4.m:901); Time_s is relative to it.
    start_local = pd.to_datetime(df["EpochStart"], format="%Y-%m-%d %H:%M:%S.%f")
    start_utc = start_local.dt.tz_localize(MATLAB_TZ).dt.tz_convert("UTC")
    # pandas 2 infers datetime64[ms] from millisecond-precision input, so pin the
    # unit to ns before going to int64 or we silently land 1000x off.
    df["epoch_start_ns"] = start_utc.dt.as_unit("ns").astype("int64")
    df["t_utc_ns"] = df["epoch_start_ns"] + (df["Time_s"] * 1e9).round().astype("int64")
    return df.sort_values("t_utc_ns").reset_index(drop=True)


def derive_rest_windows(epochs: pd.DataFrame, cfg: pd.DataFrame) -> pd.DataFrame:
    """One rest window per gap between consecutive epochs.

    Structure of a gap is: feedback animation -> rest screen (REST_DURATION, from
    the config) -> 3 s countdown -> next epoch. Anchoring on the *trailing* edge
    (next epoch start) is what makes this robust: the countdown is a fixed 3.0 s
    and REST_DURATION is known exactly, whereas the feedback animation varies in
    length depending on whether the asteroid was deflected.
    """
    per_epoch = (epochs.groupby("EpochIndex")
                       .agg(epoch_start_ns=("epoch_start_ns", "first"),
                            end_ns=("t_utc_ns", "max"),
                            condition=("Condition", "first"),
                            wave_freq=("waveFreq", "first"))
                       .reset_index()
                       .sort_values("EpochIndex"))
    rest_dur = dict(zip(cfg["epoch"], cfg["REST_DURATION"]))

    rows = []
    for i in range(len(per_epoch) - 1):
        cur, nxt = per_epoch.iloc[i], per_epoch.iloc[i + 1]
        dur_s = float(rest_dur[cur["EpochIndex"]])
        rest_end = nxt["epoch_start_ns"] - int(ALIGN_COUNTDOWN_DUR * 1e9)
        rest_start = rest_end - int(dur_s * 1e9)

        # Sanity: the derived window must sit inside the observed gap.
        ok = rest_start > cur["end_ns"] and rest_end < nxt["epoch_start_ns"]
        m = int(REST_MARGIN * 1e9)
        rows.append(dict(
            rest_id=i + 1,
            after_epoch=int(cur["EpochIndex"]),
            before_epoch=int(nxt["EpochIndex"]),
            rest_dur_s=dur_s,
            start_ns=rest_start + m,
            end_ns=rest_end - m,
            mid_ns=(rest_start + rest_end) // 2,
            gap_s=(nxt["epoch_start_ns"] - cur["end_ns"]) / 1e9,
            inside_gap=bool(ok),
        ))
    return pd.DataFrame(rows)


def attach_frame_index(rests: pd.DataFrame) -> pd.DataFrame:
    """world_timestamps.csv row i is scene-video frame i (Pupil docs)."""
    wts = pd.read_csv(WORLD_TS_CSV)["timestamp [ns]"].to_numpy()
    idx = np.searchsorted(wts, rests["mid_ns"].to_numpy())
    idx = np.clip(idx, 0, len(wts) - 1)
    # searchsorted gives the insertion point; step back if the previous frame is closer.
    prev = np.clip(idx - 1, 0, len(wts) - 1)
    take_prev = np.abs(wts[prev] - rests["mid_ns"]) < np.abs(wts[idx] - rests["mid_ns"])
    idx = np.where(take_prev, prev, idx)

    rests = rests.copy()
    rests["frame_idx"] = idx
    rests["frame_ts_ns"] = wts[idx]
    rests["video_t_s"] = (wts[idx] - wts[0]) / 1e9
    # How far the chosen frame sits from the intended midpoint.
    rests["frame_err_ms"] = (wts[idx] - rests["mid_ns"]) / 1e6
    return rests


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = pd.read_csv(CONFIG_CSV)
    epochs = load_epochs()
    rests = attach_frame_index(derive_rest_windows(epochs, cfg))

    epochs.to_csv(OUT / "timeline.csv", index=False)
    rests.to_csv(OUT / "rest_windows.csv", index=False)

    n_epochs = epochs["EpochIndex"].nunique()
    print(f"epochs        : {n_epochs} ({len(epochs)} stimulus samples)")
    print(f"session span  : {epochs['t_utc_ns'].min()} .. {epochs['t_utc_ns'].max()}")
    print(f"rest windows  : {len(rests)}  all inside gap: {rests['inside_gap'].all()}")
    if not rests["inside_gap"].all():
        print(rests.loc[~rests["inside_gap"]].to_string(index=False))
    print(f"frame pick err: max {rests['frame_err_ms'].abs().max():.1f} ms")
    print(f"rest duration : {rests['rest_dur_s'].min():.0f}-{rests['rest_dur_s'].max():.0f} s")
    print(f"\nwrote {OUT/'timeline.csv'}\nwrote {OUT/'rest_windows.csv'}")


if __name__ == "__main__":
    main()
