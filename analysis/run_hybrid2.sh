#!/usr/bin/env bash
# METHOD 4 -- differential with an INDEPENDENT Jacobian.
#
#   gaze_game(tau) = asteroid_matlab(tau + delta) + J . (gaze_px - ast_px)
#
# Same formulation as method 3, but J comes from method 1's rest-period corner
# homographies instead of the epoch's own asteroid-track homography. Method 3's
# J is fitted across a 14 s track, so mid-trial head rotation corrupts its scale
# -- the very contamination the differential form was meant to avoid. Each rest
# homography is built from four corners clicked in a SINGLE frame, where head
# motion cannot accumulate, and J discards translation entirely, so method 1's
# own weakness (interpolating absolute position between distant anchors) does
# not apply.
#
# Requires method 1's corners.csv and method 2's track:
#   out_frame_annotate_method/corners.csv, out_track/asteroid_track.csv
set -euo pipefail
cd "$(dirname "$0")"
export LB_OUT="${LB_OUT:-out_hybrid2}"
PY=../.venv/bin/python
echo "== method 4 (differential, rest-corner Jacobian) -> $LB_OUT =="
if [ ! -f out_track/track_mapping_report.csv ]; then
  echo "!! run ./run_track.sh first -- method 4 builds on method 2's track" >&2; exit 1
fi
if [ ! -f out_frame_annotate_method/corners.csv ]; then
  echo "!! need out_frame_annotate_method/corners.csv -- run annotate_corners.py" >&2; exit 1
fi
$PY fit_hybrid2.py
$PY plot.py
echo "done -- figures in analysis/$LB_OUT/figures/"
echo "score it with:  $PY compare_differential.py"
