#!/usr/bin/env bash
# ============================================================================
# METHOD 4 -- differential mapping with an INDEPENDENT Jacobian.
#
#   gaze_game(tau) = asteroid_matlab(tau + delta) + J . (gaze_px - ast_px)
#
# Maps only the small gaze-to-asteroid OFFSET through the mapping's local
# Jacobian, never gaze's absolute position. The plotted gap between the gaze and
# asteroid curves is therefore the measured tracking error by construction.
#
# J comes from method 1's rest-period corner homographies, NOT from the epoch's
# own asteroid track (that is method 3, and its J is fitted across a 14 s track
# so mid-trial head rotation corrupts its scale -- the very contamination the
# differential form was meant to dodge). Each rest homography is built from four
# corners clicked in a SINGLE frame, where head motion cannot accumulate, and J
# discards translation entirely.
#
# Anisotropy 1.03 (DataAspectRatio [1 1 1] forces ~1.0); a 200 ms clock error
# moves the answer by 0.001 units. Best method for tracking-error plots.
#
# Both interactive upstream stages are ALREADY DONE and their outputs are here:
#   out_frame_annotate_method/corners.csv   <- annotate_corners.py  (method 1)
#   out_track/asteroid_track.csv            <- track_live.py        (method 2)
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"
export LB_OUT="${LB_OUT:-out_hybrid2}"
PY=../../.venv/bin/python
export UV_CACHE_DIR="${UV_CACHE_DIR:-$(cd ../.. && pwd)/.uv-cache}"

echo "== method 4 (differential, rest-corner Jacobian) -> method4/$LB_OUT =="
if [ ! -f out_track/track_mapping_report.csv ]; then
  echo "!! out_track/ inputs missing -- method 4 builds on method 2's track" >&2; exit 1
fi
if [ ! -f out_frame_annotate_method/corners.csv ]; then
  echo "!! out_frame_annotate_method/corners.csv missing -- run annotate_corners.py" >&2; exit 1
fi

# ./clean.sh --deep drops the two 4 MB timeline.csv copies. They are a pure
# function of the MATLAB logs and gaze.csv, so rebuild them if they are absent.
for d in out_track out_frame_annotate_method; do
  if [ ! -f "$d/timeline.csv" ]; then
    echo "-- restoring $d/timeline.csv (cleaned with --deep)"
    LB_OUT="$d" $PY build_timeline.py >/dev/null
    LB_OUT="$d" $PY sync.py >/dev/null
  fi
done

$PY fit_hybrid2.py             # H1  differential map, J from rest corners
$PY plot.py                    # H2  86 figures
echo "done -- figures in analysis/method4/$LB_OUT/figures/"
echo
echo "score it with:                 $PY compare_differential.py"
echo "re-solve the clock model with: $PY refit_offsets.py"
echo "then rerun on that timing:     LB_OFFSETS=offsets_refit.csv ./run.sh"
