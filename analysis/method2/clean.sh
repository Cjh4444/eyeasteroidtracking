#!/usr/bin/env bash
# ============================================================================
# Remove everything ./run.sh can regenerate, so the flow can be shipped as code.
#
#   ./clean.sh              show the plan, ask, then delete figures + derived CSVs
#   ./clean.sh --figures    only the 129 figures, keep the derived CSVs
#   ./clean.sh -n           dry run -- print the plan and stop
#   ./clean.sh -y           skip the confirmation
#
# NEVER deletes the two things that cannot be regenerated without redoing an
# interactive session by hand:
#
#   out_track/asteroid_track.csv          <- track_live.py, supervised
#   out_track/asteroid_seeds.csv          <- legacy, from the retired batch seeder;
#                                            nothing reads it now, kept only because
#                                            no tool can rebuild it
#   out_frame_annotate_method/corners.csv              <- annotate_corners.py
#
# Those are the actual work. Everything else is a pure function of them plus the
# raw data, and `./run.sh` rebuilds it in a few minutes.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

OUT="${LB_OUT:-out_track}"
MODE=all; ASSUME_YES=0; DRY=0
for a in "$@"; do
  case "$a" in
    --figures) MODE=figures ;;
    -n|--dry-run) DRY=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

# --- the protected set: interactive output, hours of work, not reproducible ---
KEEP=("$OUT/asteroid_track.csv" "$OUT/asteroid_seeds.csv"
      "out_frame_annotate_method/corners.csv")

missing=()
for k in "${KEEP[@]}"; do [ -e "$k" ] || missing+=("$k"); done
if [ ${#missing[@]} -gt 0 ]; then
  echo "!! refusing to clean -- these are already missing, so ./run.sh could not" >&2
  echo "   rebuild what I am about to delete:" >&2
  printf '     %s\n' "${missing[@]}" >&2
  exit 1
fi

# --- the removable set: everything ./run.sh writes -----------------------------
TARGETS=("$OUT/figures")
if [ "$MODE" = all ]; then
  TARGETS+=("$OUT/timeline.csv" "$OUT/rest_windows.csv"        # S1 build_timeline.py
            "$OUT/sync_per_epoch.csv" "$OUT/sync.json"         # S2 sync.py
            "$OUT/gaze_vs_asteroid.csv" "$OUT/gaze_mapped.csv" # S3 fit_from_track.py
            "$OUT/track_mapping_report.csv" "$OUT/pursuit_lag.csv"
            "method_comparison.csv")                           # compare_methods.py
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
