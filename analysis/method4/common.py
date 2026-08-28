"""Shared paths and constants for the Lunar Blast / Neon gaze analysis."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
# This flow folder. Sibling scripts read their inputs and write their outputs
# relative to FLOW, so the flow is self-contained.
FLOW = Path(__file__).resolve().parent
MATLAB_DIR = ROOT / "test_sarah_LB"
NEON_DIR = ROOT / "test_sarah_lb_eyetracking-9355240b"
# Each analysis method writes to its own output directory, so the two can be
# compared side by side. Set LB_OUT to switch; the run_*.sh scripts do this.
OUT = Path(__file__).resolve().parent / os.environ.get("LB_OUT", "out")

CONFIG_CSV = MATLAB_DIR / "USED_20260818_170256_lunar_config.csv"
EPOCH_SUMMARY = MATLAB_DIR / "test_sarah_LB_20260818_170256_epoch_summary.csv"
EPOCH_GLOB = "test_sarah_LB_*_epoch_0*_waveFreq_*.csv"

GAZE_CSV = NEON_DIR / "gaze.csv"
WORLD_TS_CSV = NEON_DIR / "world_timestamps.csv"
SCENE_CAM_JSON = NEON_DIR / "scene_camera.json"
IMU_CSV = NEON_DIR / "imu.csv"
FIXATIONS_CSV = NEON_DIR / "fixations.csv"
SCENE_VIDEO = NEON_DIR / "18795319_0.0-1118.231.mp4"

# Wall clock the MATLAB box was running on. Neon stamps UTC ns.
MATLAB_TZ = "America/New_York"

# --- Game geometry, from Lunar_Blast_v4.m -------------------------------------
# Axes limits (:52-53). The rest-screen fill (:439) spans exactly this region,
# which is what makes it usable as a calibration target.
GAME_XLIM = (0.0, 100.0)
GAME_YLIM = (0.0, 65.0)

# Fixation cross centre (:441).
CROSS_XY = (50.0, 32.5)

# Asteroid spawn (:59-60) and trajectory constants (:918-921).
AST_SPAWN_X = 50.0
AST_SPAWN_Y = 30.0
OSC_AMP = 30.0

# Countdown shown before every epoch (:48). The rest screen is hidden when this
# starts, so it bounds the trailing edge of each rest window.
ALIGN_COUNTDOWN_DUR = 3.0

# Trim off each end of a derived rest window so we never sample a frame during
# the fade in/out of the rest overlay.
REST_MARGIN = 0.5

# Corner order used everywhere: game (0,0), (100,0), (100,65), (0,65).
# Game y is up, image y is down, so game (0,0) is the LOWER-left on screen.
CORNER_GAME_XY = [
    (GAME_XLIM[0], GAME_YLIM[0]),  # lower-left  on screen
    (GAME_XLIM[1], GAME_YLIM[0]),  # lower-right on screen
    (GAME_XLIM[1], GAME_YLIM[1]),  # upper-right on screen
    (GAME_XLIM[0], GAME_YLIM[1]),  # upper-left  on screen
]
CORNER_SCREEN_NAMES = ["LOWER-LEFT", "LOWER-RIGHT", "UPPER-RIGHT", "UPPER-LEFT"]
