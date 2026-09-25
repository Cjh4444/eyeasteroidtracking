#!/usr/bin/env bash
# METHOD 2 -- track the asteroid in the scene video.
#
# Locates the asteroid in raw scene-camera pixels every frame. Gaze is also in
# scene-camera pixels, so the gaze-minus-asteroid difference is head-motion
# invariant BY CONSTRUCTION -- no homography in the critical path, nothing to
# interpolate between anchors. Head movement cancels instead of being modelled.
#
# Stage T1 is interactive (supervised tracking: watch, pause, correct) and runs first:
#   LB_OUT=out_track ../.venv/bin/python track_live.py
set -euo pipefail
cd "$(dirname "$0")"
export LB_OUT="${LB_OUT:-out_track}"
PY=../.venv/bin/python
echo "== method 2 (asteroid tracking) -> $LB_OUT =="
$PY build_timeline.py
$PY sync.py
if [ ! -f "$LB_OUT/asteroid_track.csv" ]; then
  echo "!! $LB_OUT/asteroid_track.csv missing -- run track_live.py first" >&2; exit 1
fi
$PY fit_from_track.py
$PY plot.py
echo "done -- figures in analysis/$LB_OUT/figures/"
