#!/usr/bin/env bash
# ============================================================================
# METHOD 2 -- absolute mapping from a tracked asteroid.
#
# Locates the asteroid in raw scene-camera pixels every frame. Gaze is also in
# scene-camera pixels, so the gaze-minus-asteroid difference is head-motion
# invariant BY CONSTRUCTION -- no homography in the critical path, nothing to
# interpolate between anchors. Head movement cancels instead of being modelled.
#
# Held-out error 0.83 game units; beat method 1 on 42/42 epochs.
# This is the general-purpose gaze -> game-coordinate mapping.
#
# Stage T1 (track_live.py) is interactive -- supervised tracking, you watch and
# correct it. It is ALREADY DONE; out_track/asteroid_track.csv is committed.
# Everything below stage T1 is non-interactive and safe to re-run.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"
export LB_OUT="${LB_OUT:-out_track}"
PY=../../.venv/bin/python
export UV_CACHE_DIR="${UV_CACHE_DIR:-$(cd ../.. && pwd)/.uv-cache}"

echo "== method 2 (asteroid tracking) -> method2/$LB_OUT =="
$PY build_timeline.py          # S1  epoch timeline on both clocks
$PY sync.py                    # S2  diagnostic clock offsets (see FLOW.md caveat)
if [ ! -f "$LB_OUT/asteroid_track.csv" ]; then
  echo "!! $LB_OUT/asteroid_track.csv missing -- run stage T1 first:" >&2
  echo "   LB_OUT=$LB_OUT $PY track_live.py" >&2
  exit 1
fi
$PY fit_from_track.py          # S3  pixel->game homography per epoch, map gaze
$PY plot.py                    # S4  129 figures
echo "done -- figures in analysis/method2/$LB_OUT/figures/"
echo "score it against method 1 with:  $PY compare_methods.py"
