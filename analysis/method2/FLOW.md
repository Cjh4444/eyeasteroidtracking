# Method 2 — absolute gaze mapping from a tracked asteroid

**Self-contained flow.** Everything this method needs is in this folder, except the
raw data (`../../test_sarah_LB/`, `../../test_sarah_lb_eyetracking-9355240b/`) and the
Python env (`../../.venv/`).

```bash
cd analysis/method2
./run.sh
```

**Use this method for** stimulus-independent screen positions and the headline
degrees-off-target number. For tracking-error plots use [method 4](../method4/).

---

## The idea

Track the asteroid in **raw scene-camera pixels**. Gaze is already in raw scene-camera
pixels on the **same Neon clock**, so `gaze_px − ast_px` is head-motion invariant *by
construction* — when the head turns, both move together in the image. No homography in
the critical path, nothing to interpolate between anchors. Head movement cancels
instead of being modelled.

That difference — step S3 part A below — uses **no MATLAB data at all** and no clock
offset. It is the primary result and the source of every headline number in the
project.

## Shipping this as code

`clean.sh` removes everything `run.sh` can rebuild, so the flow can be sent as source
and the recipient regenerates the pictures themselves.

```bash
./clean.sh            # show the plan, confirm, delete   (62 MB -> 1.4 MB)
./clean.sh --figures  # only the 129 figures
./clean.sh -n         # dry run
./run.sh              # get it all back
```

It **refuses to touch** the two artefacts that came from an interactive session and
cannot be rebuilt — `out_track/asteroid_track.csv` from `track_live.py`, plus
`out_frame_annotate_method/corners.csv` from method 1's
`annotate_corners.py`. If any of them is already missing it aborts rather than
deleting things it could not restore. Verified by a full clean-and-rebuild round trip:
3.23° median error, −195 ms anticipation, 0.84-unit residual, all 129 figures.

## Stages

| | Script | Does | Writes |
|---|---|---|---|
| **S1** | `build_timeline.py` | MATLAB epoch CSVs → absolute UTC ns; derives the 41 rest windows as `next_epoch_start − 3 s − REST_DURATION` | `out_track/timeline.csv`, `rest_windows.csv` |
| **S2** | `sync.py` | Gaze-vs-stimulus offset by cross-correlation. **Diagnostic only** — confounded with pursuit lag (trap 9) | `out_track/sync_per_epoch.csv`, `sync.json` |
| **T1** | `track_live.py` *(interactive)* | Play each epoch, watch the box follow the asteroid, pause/rewind/re-seed where it drifts. Auto-pauses on low confidence or a parked box. **Already done — output is committed** | `out_track/asteroid_track.csv` |
| **S3** | `fit_from_track.py` | **A.** Undistort both, interpolate the asteroid (30 Hz) onto each gaze timestamp (200 Hz), take `gaze_px − ast_px`, convert to degrees. **B.** Solve the per-epoch clock offset by sweeping it for minimum homography reprojection residual, rejecting y-flipped fits. **C.** Fit the drift line across epochs, re-solve each in a narrow window around it. **D.** Per-epoch homography from tracked pixels ↔ MATLAB game coords — the asteroid is a moving calibration target sweeping the screen — and map gaze through it | `out_track/gaze_vs_asteroid.csv`, `gaze_mapped.csv`, `track_mapping_report.csv`, `pursuit_lag.csv` |
| **S4** | `plot.py` | 129 figures: per-epoch Y overlay, X overlay, 2D scanpath, plus contact sheets. Applies the per-epoch offset to gaze timestamps | `out_track/figures/` |

`run.sh` runs S1 → S2 → S3 → S4. T1 is interactive and is already done; re-running
the rest is safe and reproduces everything.

To re-do the supervised tracking from scratch:

```bash
LB_OUT=out_track ../../.venv/bin/python track_live.py
```

## Scoring

```bash
../../.venv/bin/python compare_methods.py     # → method_comparison.csv
```

Held-out task: predict the asteroid's game coords from its measured pixel position,
out-of-sample for method 1 and cross-validated for method 2. Requires
`out_frame_annotate_method/corners.csv`, which is included here as a reference input
(it is method 1's only artefact this flow touches).

## Results

- **Held-out error 0.83 game units.** Beat method 1 on **42/42** epochs, 4.0× on the
  ratio of medians.
- Gaze-to-asteroid error **median 3.23°**; **1.68°** after removing a constant **2.7°**
  calibration bias (dx −2.00°, dy −1.81°). 1.68° is inside Neon's calibration-free
  spec — an independent check that the pipeline is sound.
- **Gaze anticipates the asteroid by 195 ms.** −280 ms at waveFreq 1.0 → −112 ms at
  3.5; −315 ms passively watching vs −60 ms actively aiming the laser.
- Error rises monotonically with target speed (2.80° → 3.57°), matching the game's own
  laser-contact rates (69 % → 21 %). This is the falsification test, and it passes.

## Limits

The absolute mapping is clock-sensitive: a 200 ms clock error moves the reconstructed
gaze-asteroid separation by 4.24 game units, and the units-per-degree scale varies 35 %
across epochs. That is what method 4 fixes. Method 2's own numbers are unaffected
where they come from the clock-free pixel measurement.

## Files

```
run.sh                  this flow, S1 → S4
common.py               shared paths, game geometry from Lunar_Blast_v4.m
build_timeline.py       S1
sync.py                 S2
track_live.py           T1  (interactive, supervised)
fit_mapping.py          camera intrinsics + undistortion, shared with method 1
fit_from_track.py       S3
plot.py                 S4
compare_methods.py      scoring, method 1 vs method 2
out_track/              this flow's outputs, including figures/
out_frame_annotate_method/corners.csv    reference input for compare_methods.py
method_comparison.csv   scoring output
```

## Traps this flow is exposed to

Full list in [`../README.md`](../README.md). The ones that bite here:

1. **The two clocks drift at ~15.4 ms per epoch** (−1050 ms → −402 ms, ~602 ppm).
   There is no single offset; S3 part C is what handles it.
2. **Gaze anticipates, it does not lag.** Measure it from gaze vs the *tracked*
   asteroid — both on the Neon clock. Subtracting offsets measured on two different
   clocks yields +2 s, which no eye can do.
4. **`Asteroid_X` cannot measure lag by correlation** — a linear ramp gives r ≈ 1 at
   any offset.
5. **`Asteroid_Y` aliases.** Shifting a triangle wave half a period inverts it and a
   homography absorbs that by flipping its y-scale. `y_flipped()` rejects those fits.
6. **A tracker's match score cannot detect a stuck tracker** — locked onto static art
   it template-matches itself and reports 1.00 forever. `track_live.py` tests actual
   displacement.
7. **Neon's `worn` flag goes falsely false in epochs 38–42.** `sync.py` no longer
   requires it; `LB_REQUIRE_WORN=1` restores the old behaviour and every published
   number.
9. **`sync.py`'s offset is confounded with pursuit lag.** Diagnostic only — the
   offsets actually used come from S3.

## Do not read the scene video for its imagery

The `.mp4` is 553 MB. Programmatic frame processing is expected and fine — that is
what the tracker does. Never render frames into an agent's context.
