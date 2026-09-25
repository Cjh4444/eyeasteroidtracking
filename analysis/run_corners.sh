#!/usr/bin/env bash
# METHOD 1 -- rest-rectangle corner annotation.
#
# Maps gaze to game coords via a homography from the gray rest rectangle's
# corners, re-anchored at each of the 41 rest periods and interpolated in
# between. Accurate at the anchors (corner clicks land within ~0.6 game units)
# but the interpolation is the weak link: the screen centre moves up to 100 px
# BETWEEN consecutive rests, so mid-trial head motion is not captured.
#
# Stage 3 is interactive and must be run by hand first:
#   LB_OUT=out_frame_annotate_method ../.venv/bin/python annotate_corners.py
set -euo pipefail
cd "$(dirname "$0")"
export LB_OUT="${LB_OUT:-out_frame_annotate_method}"
PY=../.venv/bin/python
echo "== method 1 (corner annotation) -> $LB_OUT =="
$PY build_timeline.py
$PY sync.py
if [ ! -f "$LB_OUT/corners.csv" ]; then
  echo "!! $LB_OUT/corners.csv missing -- run annotate_corners.py first" >&2; exit 1
fi
$PY fit_mapping.py
$PY detect_asteroid.py
$PY plot.py
echo "done -- figures in analysis/$LB_OUT/figures/"
