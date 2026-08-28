#!/usr/bin/env bash
# ============================================================================
# Remove everything ./run.sh can regenerate, so the flow can be shipped as code.
#
#   ./clean.sh              show the plan, ask, then delete this flow's output
#   ./clean.sh --figures    only the 129 figures, keep the derived CSVs
#   ./clean.sh --deep       also drop the two 4 MB timeline.csv copies; ./run.sh
#                           rebuilds them from the MATLAB logs on next run
#   ./clean.sh -n           dry run -- print the plan and stop
#   ./clean.sh -y           skip the confirmation
#
# NEVER deletes the two things that cannot be regenerated without redoing an
# interactive session by hand:
#
#   out_track/asteroid_track.csv           <- track_live.py, supervised tracking
#   out_frame_annotate_method/corners.csv  <- annotate_corners.py, 41 rests clicked
#
# Those are the actual work, and this flow has no way to rebuild them: unlike
# method 2, ./run.sh here consumes them as inputs. Everything else is a pure
# function of them plus the raw data.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

OUT="${LB_OUT:-out_hybrid2}"
M1=out_frame_annotate_method
M2=out_track
MODE=all; DEEP=0; ASSUME_YES=0; DRY=0
for a in "$@"; do
  case "$a" in
    --figures) MODE=figures ;;
    --deep) DEEP=1 ;;
    -n|--dry-run) DRY=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

# --- the protected set: interactive output, hours of work, not reproducible ---
KEEP=("$M2/asteroid_track.csv" "$M1/corners.csv")
missing=()
for k in "${KEEP[@]}"; do [ -e "$k" ] || missing+=("$k"); done
if [ ${#missing[@]} -gt 0 ]; then
  echo "!! refusing to clean -- these are already missing, so ./run.sh could not" >&2
  echo "   rebuild what I am about to delete:" >&2
  printf '     %s\n' "${missing[@]}" >&2
  exit 1
fi

# --- the removable set ---------------------------------------------------------
if [ "$MODE" = figures ]; then
  TARGETS=("$OUT/figures")
else
  # the whole output dir is written by fit_hybrid2.py (which copies its inputs
  # forward into it) and plot.py -- nothing original lives there
  TARGETS=("$OUT" "offsets_refit.csv" "differential_comparison.csv")
fi
if [ "$DEEP" -eq 1 ]; then
  # regenerable from the MATLAB logs + gaze.csv, but by build_timeline.py/sync.py
  # rather than by this flow's own stages -- ./run.sh restores them if absent
  for d in "$M1" "$M2"; do
    TARGETS+=("$d/timeline.csv" "$d/rest_windows.csv" "$d/sync_per_epoch.csv" "$d/sync.json")
  done
fi

present=(); for t in "${TARGETS[@]}"; do [ -e "$t" ] && present+=("$t"); done
if [ ${#present[@]} -eq 0 ]; then echo "already clean -- nothing to remove."; exit 0; fi

echo "will DELETE (all of it rebuilt by ./run.sh):"
for t in "${present[@]}"; do
  printf '  %8s  %s\n' "$(du -sh "$t" | cut -f1)" "$t"
done
echo
echo "will KEEP (interactive, not reproducible):"
for k in "${KEEP[@]}"; do printf '  %8s  %s\n' "$(du -sh "$k" | cut -f1)" "$k"; done
[ "$DEEP" -eq 0 ] && echo "  (--deep also drops the two 4 MB timeline.csv copies)"
echo
printf 'frees %s\n' "$(du -sch "${present[@]}" | tail -1 | cut -f1)"

[ "$DRY" -eq 1 ] && { echo; echo "dry run -- nothing deleted."; exit 0; }

if [ "$ASSUME_YES" -eq 0 ]; then
  printf '\nproceed? [y/N] '
  read -r reply </dev/tty
  case "$reply" in [yY]*) ;; *) echo "aborted."; exit 0 ;; esac
fi

# Sweep .DS_Store and __pycache__ FIRST. On macOS, Finder rewrites .DS_Store into a
# directory it has open; if that lands after rm -rf has enumerated the directory but
# before it calls rmdir, rmdir fails with ENOTEMPTY even under -rf. Same for a stale
# __pycache__ being repopulated. Clearing them up front closes most of that window.
find . -name .DS_Store -delete 2>/dev/null || true
find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

failed=()
for t in "${present[@]}"; do
  rm -rf "$t" 2>/dev/null || true
  if [ -e "$t" ]; then                     # lost the race -- resweep and retry once
    find "$t" -name .DS_Store -delete 2>/dev/null || true
    rm -rf "$t" 2>/dev/null || true
    [ -e "$t" ] && failed+=("$t")
  fi
done

if [ ${#failed[@]} -gt 0 ]; then
  echo >&2
  echo "!! removed everything else, but these survived:" >&2
  printf '     %s\n' "${failed[@]}" >&2
  echo "   something is writing into them -- a Finder window on the folder is the" >&2
  echo "   usual cause. Close it and rerun; nothing protected was touched." >&2
  exit 1
fi

echo "cleaned. regenerate the figures with:  ./run.sh"
