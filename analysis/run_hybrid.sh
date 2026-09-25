#!/usr/bin/env bash
# METHOD 3 -- differential: measured gaze-asteroid offset placed onto the log path.
#
#   gaze_game(tau) = asteroid_matlab(tau + delta) + J . (gaze_px - ast_px)
#
# Uses method 2's asteroid track and per-epoch offsets, but maps only the small
# gaze-to-asteroid OFFSET through the mapping's local Jacobian, never gaze's
# absolute position. Consequence: the plotted gap between the gaze and asteroid
# curves is the measured tracking error by construction, and a clock error
# perturbs it 2-3x less than method 2's absolute mapping.
#
# Requires method 2 to have been run first (needs out_track/asteroid_track.csv
# and out_track/track_mapping_report.csv).
set -euo pipefail
cd "$(dirname "$0")"
export LB_OUT="${LB_OUT:-out_hybrid}"
PY=../.venv/bin/python
echo "== method 3 (differential) -> $LB_OUT =="
if [ ! -f out_track/track_mapping_report.csv ]; then
  echo "!! run ./run_track.sh first -- method 3 builds on method 2's track" >&2; exit 1
fi
$PY fit_hybrid.py
$PY plot.py
echo "done -- figures in analysis/$LB_OUT/figures/"
