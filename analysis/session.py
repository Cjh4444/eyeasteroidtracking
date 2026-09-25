"""Session discovery and validation -- what makes this pipeline run on ANY recording.

A *session* is one MATLAB Lunar Blast log folder paired with one Neon raw-data
export. Everything the pipeline needs is found inside those two folders by pattern,
recorded once in `sessions/<name>/session.json`, and every stage then reads it from
there. That file is the only place a session's paths live; nothing else in the
pipeline names a subject, a date or a file.

Each session's outputs live under `sessions/<name>/` in the same layout method 4
uses, so the stage scripts are unchanged apart from where they look:

    sessions/<name>/session.json
    sessions/<name>/out_frame_annotate_method/corners.csv   <- interactive, U1
    sessions/<name>/out_track/asteroid_track.csv            <- interactive, U2
    sessions/<name>/out_hybrid2/figures/                    <- the deliverable

The session is selected with LB_SESSION; `lb.py` sets it for every stage.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
ROOT = PIPELINE.parent
SESSIONS = PIPELINE / "sessions"

# Patterns the MATLAB side writes (Lunar_Blast_v4.m). Deliberately loose on the
# subject prefix and on the config's suffix (`_lunar_config_2.csv` exists).
EPOCH_RE = re.compile(r"_epoch_(\d{3})_waveFreq_[\d.]+_\w+\.csv$")
EPOCH_GLOB = "*_epoch_[0-9][0-9][0-9]_waveFreq_*.csv"
SUMMARY_GLOB = "*_epoch_summary.csv"
CONFIG_GLOB = "USED_*lunar_config*.csv"

EPOCH_COLS = {"EpochStart", "EpochIndex", "Condition", "waveFreq", "Time_s",
              "Asteroid_X", "Asteroid_Y"}
CONFIG_COLS = {"epoch", "REST_DURATION"}
SUMMARY_COLS = {"EpochIndex", "ContactPct"}
GAZE_COLS = {"timestamp [ns]", "gaze x [px]", "gaze y [px]", "worn", "blink id",
             "azimuth [deg]", "elevation [deg]"}

DEFAULT_TZ = "America/New_York"


class Session:
    """Resolved paths for one session. Built from session.json."""

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        self.dir = SESSIONS / name
        self.matlab_dir = ROOT / cfg["matlab_dir"]
        self.neon_dir = ROOT / cfg["neon_dir"]
        self.config_csv = self.matlab_dir / cfg["config_csv"]
        self.epoch_summary = self.matlab_dir / cfg["epoch_summary"]
        self.epoch_glob = cfg["epoch_glob"]
        self.scene_video = self.neon_dir / cfg["scene_video"] if cfg.get("scene_video") else None
        self.timezone = cfg.get("timezone", DEFAULT_TZ)

    def neon(self, name: str) -> Path:
        return self.neon_dir / name


def session_names() -> list[str]:
    return sorted(p.parent.name for p in SESSIONS.glob("*/session.json"))


def load(name: str) -> Session:
    p = SESSIONS / name / "session.json"
    if not p.exists():
        known = ", ".join(session_names()) or "none yet"
        sys.exit(f"no session '{name}' ({p} missing). Known sessions: {known}.\n"
                 f"Create one with:  python lb.py init {name} --matlab DIR --neon DIR")
    return Session(name, json.loads(p.read_text()))


def active() -> Session:
    """The session named by LB_SESSION -- how every stage script finds its data."""
    name = os.environ.get("LB_SESSION")
    if not name:
        sys.exit("LB_SESSION is not set. Run stages through lb.py, e.g.\n"
                 "  python lb.py run <session>\n"
                 f"Known sessions: {', '.join(session_names()) or 'none yet'}")
    return load(name)


# --- discovery -------------------------------------------------------------------

def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p.resolve())


def _one(paths: list[Path], what: str, where: Path) -> Path:
    if len(paths) == 1:
        return paths[0]
    if not paths:
        sys.exit(f"no {what} found in {where}")
    sys.exit(f"{len(paths)} candidates for {what} in {where}:\n  " +
             "\n  ".join(p.name for p in paths) + "\nremove the extras or edit session.json")


def discover(matlab_dir: Path, neon_dir: Path, timezone: str = DEFAULT_TZ) -> dict:
    """Find every input by pattern. The Neon folder may be the export itself or any
    parent of it -- Pupil Cloud downloads nest the export one level down."""
    matlab_dir, neon_dir = matlab_dir.resolve(), neon_dir.resolve()
    if not matlab_dir.is_dir():
        sys.exit(f"MATLAB folder not found: {matlab_dir}")
    if not neon_dir.is_dir():
        sys.exit(f"Neon folder not found: {neon_dir}")

    epochs = sorted(p for p in matlab_dir.glob(EPOCH_GLOB) if EPOCH_RE.search(p.name))
    if not epochs:
        sys.exit(f"no epoch CSVs matching {EPOCH_GLOB} in {matlab_dir}")
    summary = _one(sorted(matlab_dir.glob(SUMMARY_GLOB)), "epoch summary", matlab_dir)
    config = _one(sorted(matlab_dir.glob(CONFIG_GLOB)), "USED_*lunar_config*.csv", matlab_dir)

    # Narrow the glob to this session's own prefix so a stray epoch file from
    # another run in the same folder cannot be pulled in silently.
    prefixes = {EPOCH_RE.split(p.name)[0] for p in epochs}
    if len(prefixes) != 1:
        sys.exit(f"epoch files from {len(prefixes)} different runs in {matlab_dir}:\n  " +
                 "\n  ".join(sorted(prefixes)))
    prefix = prefixes.pop()

    gaze = _one(sorted(neon_dir.rglob("gaze.csv")), "gaze.csv", neon_dir)
    export = gaze.parent
    videos = sorted(export.glob("*.mp4"))
    video = videos[0] if len(videos) == 1 else None

    return dict(
        matlab_dir=_rel(matlab_dir),
        neon_dir=_rel(export),
        epoch_glob=f"{prefix}_epoch_[0-9][0-9][0-9]_waveFreq_*.csv",
        epoch_summary=summary.name,
        config_csv=config.name,
        scene_video=video.name if video else None,
        timezone=timezone,
    )


# --- validation ------------------------------------------------------------------

def validate(s: Session) -> list[tuple[str, str]]:
    """Checks that the data is shaped the way the pipeline assumes.

    Returns (level, message) with level in {"ok", "warn", "FAIL"}. Nothing here
    changes the analysis -- it only refuses to start on data that would produce a
    confident, wrong answer.
    """
    import cv2
    import numpy as np
    import pandas as pd

    out: list[tuple[str, str]] = []
    ok = lambda m: out.append(("ok", m))
    warn = lambda m: out.append(("warn", m))
    fail = lambda m: out.append(("FAIL", m))

    # --- MATLAB side
    epochs = sorted(s.matlab_dir.glob(s.epoch_glob))
    frames = [pd.read_csv(p) for p in epochs]
    missing = [(p.name, EPOCH_COLS - set(f.columns)) for p, f in zip(epochs, frames)
               if EPOCH_COLS - set(f.columns)]
    if missing:
        fail(f"epoch files missing columns, e.g. {missing[0][0]}: {sorted(missing[0][1])}")
        return out
    idx = sorted(int(f["EpochIndex"].iloc[0]) for f in frames)
    cfg = pd.read_csv(s.config_csv)
    summ = pd.read_csv(s.epoch_summary)
    for name, df, cols in (("config", cfg, CONFIG_COLS), ("summary", summ, SUMMARY_COLS)):
        if cols - set(df.columns):
            fail(f"{name} missing columns {sorted(cols - set(df.columns))}")
            return out
    if idx != list(range(1, len(idx) + 1)):
        warn(f"epoch indices are not 1..N: {idx}")
    if set(idx) - set(cfg["epoch"]):
        fail(f"epochs {sorted(set(idx) - set(cfg['epoch']))} have no REST_DURATION in the config")
    if len(idx) != len(summ):
        warn(f"{len(idx)} epoch files but {len(summ)} summary rows")
    firsts = pd.concat([f.iloc[:1] for f in frames])
    conds = firsts["Condition"].value_counts().to_dict()
    freqs = sorted(float(v) for v in firsts["waveFreq"].unique())
    ok(f"{len(idx)} epochs  {conds}  waveFreq {freqs}")
    if min(freqs) <= 1.0:
        warn("waveFreq 1.0 epochs cannot be clock-solved by shape (one cycle per trial); "
             "refit_offsets.py uses the session model for them")

    # --- Neon side
    need = ["gaze.csv", "world_timestamps.csv", "scene_camera.json", "events.csv"]
    absent = [n for n in need if not s.neon(n).exists()]
    if absent:
        fail(f"Neon export missing {absent}")
        return out
    gz_cols = set(pd.read_csv(s.neon("gaze.csv"), nrows=0).columns)
    if GAZE_COLS - gz_cols:
        fail(f"gaze.csv missing columns {sorted(GAZE_COLS - gz_cols)}")
        return out
    cam = json.loads(s.neon("scene_camera.json").read_text())
    nd = len(np.ravel(cam["distortion_coefficients"]))
    ok(f"scene camera: {nd} distortion coefficients")

    wts = pd.read_csv(s.neon("world_timestamps.csv"))["timestamp [ns]"].to_numpy()
    if s.scene_video is None or not s.scene_video.exists():
        warn("no scene video (.mp4) -- fine for the fit stages, but the two "
             "interactive stages (corners, track) need it")
    else:
        cap = cv2.VideoCapture(str(s.scene_video))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        # world_timestamps row i is video frame i; a mismatch means the index
        # lookup the interactive tools rely on is wrong.
        (ok if abs(n - len(wts)) <= 2 else warn)(
            f"scene video {w}x{h}, {n} frames vs {len(wts)} world timestamps")

    # --- clocks: timezone, then MATLAB-vs-Neon offset
    starts = (pd.concat([f[["EpochIndex", "EpochStart"]].iloc[:1] for f in frames])
                .set_index("EpochIndex")["EpochStart"])
    local = pd.to_datetime(starts, format="%Y-%m-%d %H:%M:%S.%f")
    try:
        utc = local.dt.tz_localize(s.timezone).dt.tz_convert("UTC").dt.as_unit("ns").astype("int64")
    except Exception as e:  # bad tz name
        fail(f"timezone '{s.timezone}': {e}")
        return out
    ev = pd.read_csv(s.neon("events.csv"))
    t_begin = int(ev.loc[ev["name"] == "recording.begin", "timestamp [ns]"].iloc[0])
    t_end = int(ev.loc[ev["name"] == "recording.end", "timestamp [ns]"].iloc[0])
    inside = ((utc > t_begin) & (utc < t_end)).mean()
    if inside < 1.0:
        # Infer the UTC offset the MATLAB box must have been on.
        naive_ns = local.astype("int64").iloc[0]
        hours = round((naive_ns - t_begin) / 3.6e12 * 4) / 4
        fail(f"only {inside:.0%} of epoch starts fall inside the Neon recording with "
             f"timezone {s.timezone}. The MATLAB clock looks like UTC{hours:+g} h -- "
             f"set \"timezone\" in session.json")
        return out
    ok(f"all epoch starts inside the Neon recording (timezone {s.timezone})")

    # Trial markers, if the stimulus sent them. They measure the MATLAB->Neon offset
    # directly. Only the START markers are used: END markers land ~250 ms after the
    # last logged sample (feedback animation), so they do not bound the trial.
    marks = ev[ev["name"].str.endswith("_START") & ~ev["name"].str.startswith("recording")]
    if len(marks) == len(utc):
        d = (marks["timestamp [ns]"].to_numpy() - utc.to_numpy()) / 1e6
        slope = np.polyfit(np.arange(len(d)), d, 1)[0]
        msg = (f"trial markers: Neon - MATLAB = {np.median(d):+.1f} ms median "
               f"(range {d.min():+.1f}..{d.max():+.1f}, drift {slope:+.2f} ms/epoch)")
        # fit_from_track sweeps the offset over +-2 s, so it must start inside that.
        (ok if abs(np.median(d)) < 1500 else warn)(
            msg if abs(np.median(d)) < 1500 else msg + " -- outside the +-2 s offset search")
        out.append(("info", "  markers may be stamped by MATLAB itself, so they are not proof "
                            "the clocks agree -- compare with fit_from_track's solved offsets"))
    elif len(marks):
        warn(f"{len(marks)} trial markers for {len(utc)} epochs -- not used")
    else:
        out.append(("info", "no trial markers in events.csv; the clock offset is solved "
                            "from the data alone (as for the first session)"))

    gaze = pd.read_csv(s.neon("gaze.csv"), usecols=["worn"])
    ok(f"gaze: {len(gaze)} samples, worn {gaze['worn'].mean():.0%} "
       f"(worn is not required -- see sync.load_gaze)")
    return out
