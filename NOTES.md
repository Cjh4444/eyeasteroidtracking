# Notes — Lunar Blast × Pupil Neon

Everything behind the three commands in [`README.md`](README.md): what the project
is, how the code is laid out, how to run the other methods, what was found, and the
traps. **Read the traps before changing anything.** Per-method detail is in
[`analysis/METHODS.md`](analysis/METHODS.md); the running log is
[`analysis/README.md`](analysis/README.md).

## What the quick start produces

86 figures land in `analysis/method4/out_hybrid2/figures/` — per epoch a vertical
and a horizontal asteroid-vs-gaze overlay, plus two contact sheets across all 42.

Needs three raw files, all already in the repo: `test_sarah_LB/*_epoch_summary.csv`,
`test_sarah_lb_eyetracking-9355240b/gaze.csv`, and `.../scene_camera.json`. The
scene video is **not** required — the interactive stages that read it are already
done and their output is committed.

Optional, after a run: `../../.venv/bin/python compare_differential.py` scores it
against methods 2 and 3. `./clean.sh` removes everything `run.sh` can rebuild.


## The goal

Sarah ran 42 epochs of a MATLAB game ("Lunar Blast") while wearing Pupil Labs Neon
eye-tracking glasses. An asteroid traverses the screen on a known mathematical path;
she either watches it or actively tracks it with a dial-aimed laser.

**The deliverable is `sample_graph.png` reproduced from real data**: per epoch, the
asteroid's vertical position overlaid with where she was actually looking. That, plus
quantifying how well she tracked.

**Status: complete.** Three methods are implemented, validated against each other,
and produce figures. Findings are in [`NOTES.md`](NOTES.md).

## Why this was hard

The Neon export is **raw only** (`enrichment_info.txt` → `raw-data-exporter`), so
`gaze.csv` gives gaze in **scene-camera pixels** — head-relative, not screen-relative.
Every time the head moves, a fixed screen point lands on a different scene pixel.

Pupil's supported fix is a **Marker Mapper** enrichment, which requires AprilTags
physically taped around the monitor during recording. They weren't there, and it
cannot be run retroactively. So the screen mapping had to be reconstructed from the
data itself.

`events.csv` contains only `recording.begin` / `recording.end` — no trial markers.
Alignment had to come from wall clocks plus signal alignment.

## Key facts about the data

Established at real cost; don't re-derive them.

- **Game coordinates** are `XLim [0,100]`, `YLim [0,65]`, `DataAspectRatio [1 1 1]`
  (`Lunar_Blast_v4.m:52-53,148-158`).
- **Asteroid path**: `ast_y = 30 + 30·-(2/π)·asin(sin(2π·f·t))`,
  `ast_x = 50 + (earthX−50)·t`, with `t = elapsed/14` (`:918-921`). A triangle wave,
  period `14/waveFreq` seconds, spanning the full 0–60 range.
- **The rest screen** is a gray fill spanning *exactly* `[0,100]×[0,65]` (`:439`) with
  the fixation cross at game **(50, 32.5)** (`:441`). This is what makes method 1
  possible.
- **`epochStartClock = now`** is stamped the instant asteroid motion begins (`:901`);
  `Time_s` is relative to it. Every log row therefore has an absolute wall-clock time.
- **Countdown** before every epoch is exactly 3.0 s (`ALIGN_COUNTDOWN_DUR`, `:48`).
- Recording spans 17:02:48.6–17:21:26.8 EDT (America/New_York); epochs run
  17:03:02–17:21:11. All Neon `timestamp [ns]` are **UTC Unix epoch nanoseconds** on
  one shared clock.
- Scene camera is **1600×1200 @ 29.973 fps**, 33517 frames, gaze origin top-left,
  y down, in *distorted* image space. `scene_camera.json` carries **8 distortion
  coefficients** (rational model) — pass all 8 to `cv2.undistortPoints`, not 5.

## ⚠ The traps

Each of these produces a confident, plausible-looking wrong answer. All were hit
during development.

1. **The two clocks drift apart at ~15.4 ms per epoch** — −1050 ms at session start
   to −402 ms at the end (633 ms total, ~570 ppm). **There is no single clock offset.**
   Any analysis assuming one produces artifacts. This is the single most important
   fact in the project.
2. **Gaze *anticipates* the target; it does not lag.** Measure it from gaze vs. the
   *tracked asteroid*, since both are on the Neon clock. Never derive it by
   subtracting offsets measured on two different clocks — that yields +2 s, which no
   eye can do.
3. **Plotting gaze (Neon clock) against the stimulus log (MATLAB clock) without
   converting** makes the ~750 ms clock offset look like eye lag. It swamps the real
   ~200 ms lead and flips its sign. `plot.py` applies the per-epoch offset.
4. **`Asteroid_X` cannot measure lag by correlation.** It is a linear ramp, and
   correlation is invariant to shifting a linear signal — r ≈ 1 at any offset.
5. **`Asteroid_Y` aliases.** Shifting a triangle wave by half a period inverts it,
   and a homography absorbs that by flipping its y-scale — numerically excellent,
   physically impossible. Reject fits where image-y-down doesn't map to game-y-up.
6. **A tracker's match score cannot detect a stuck tracker.** Locked onto static art
   it template-matches itself and reports 1.00 forever. Test actual displacement.
7. **Neon's `worn` flag goes falsely false in epochs 38–42** (43 % of epoch 41),
   which is what put the visible gaps in those epochs' plots across every method.
   The asteroid tracker is not involved — it never drops out. Those samples track
   the asteroid as well as the `worn == 1` ones, so `sync.py` no longer requires
   `worn`; `LB_REQUIRE_WORN=1` restores the old behaviour and every published number.
8. **A per-epoch clock offset solved from a head-motion-damaged homography is
   arbitrary.** `drift_model` drops those epochs from the drift line but then
   re-solves them against the same broken residual. Use `refit_offsets.py`, which
   solves the offset from the tracked trajectory's shape alone.
9. **`sync.py`'s offset is confounded with pursuit lag** — it rises with stimulus
   frequency and differs by condition, which no real clock offset can do. Diagnostic
   only.

## Results

From the clock-free, homography-free pixel measurement that all three methods share:

- **Gaze-to-asteroid error: median 3.23°**; **1.68°** after removing a constant
  **2.7°** calibration bias (dx −2.00°, dy −1.81°). 1.68° is inside Neon's
  calibration-free spec — a strong independent check that the pipeline is sound.
- **Gaze anticipates the asteroid by 195 ms.** Anticipation scales with
  predictability: −280 ms at waveFreq 1.0 → −112 ms at 3.5. Much stronger when
  passively watching (−315 ms) than when actively aiming the laser (−60 ms).
- **Error rises monotonically with target speed** (2.80° → 3.57°), matching the
  game's own laser-contact rates (69% → 21%) from a completely independent
  measurement. This is the falsification test, and it passes.

## The three methods

Full detail in `analysis/METHODS.md`. Each writes to its own directory via `LB_OUT`.

| | Approach | Held-out error | Status |
|---|---|---|---|
| **1** `run_corners.sh` | Homography from the gray rest rectangle's corners, re-anchored at 41 rests | 3.33 game units | Retired; kept for comparison |
| **2** `run_track.sh` | Asteroid tracked in scene pixels; head motion cancels by construction | **0.83 units** | General-purpose mapping |
| **3** `run_hybrid.sh` | Measured gaze-asteroid offset placed onto the log path via local Jacobian | 2–3× less clock-sensitive | Superseded by 4 |
| **4** `run_hybrid2.sh` | Method 3's formula, but `J` from method 1's rest corners instead of the track | anisotropy 1.03; clock-error 200 ms → 0.001 units | **Best for tracking-error plots** |

Method 2 beat method 1 on **42/42 epochs**. Method 1's clicking was never the problem
(3.9 px corner accuracy) — interpolating between anchors 25–100 px apart was.

`compare_methods.py` scores 1 vs 2 on the same held-out task (predict the asteroid's
game coords from its measured pixel position): out-of-sample for method 1,
cross-validated for method 2.

## Directory layout

| Path | What it is |
|---|---|
| `Lunar_Blast_v4.m` | The MATLAB stimulus program. **Read-only reference** — it defines the coordinate system and asteroid path. Not runnable here (no MATLAB). |
| `test_sarah_LB/` | Per-epoch stimulus logs (42 CSVs), `USED_*_lunar_config.csv` (per-epoch settings), `*_epoch_summary.csv` (per-epoch outcomes incl. `ContactPct`). |
| `test_sarah_lb_eyetracking-9355240b/` | Neon raw export: `gaze.csv` (200 Hz), `fixations/saccades/blinks.csv`, `imu.csv`, `3d_eye_states.csv`, `world_timestamps.csv` (one row per video frame), `scene_camera.json` (intrinsics), `events.csv`, and the scene video `18795319_0.0-1118.231.mp4`. |
| `analysis/` | All code and results. The flat layout here is where development happened and where methods 1 and 3 live. |
| `analysis/method2/` | **Self-contained flow for method 2.** Own copy of every script, own inputs/outputs, `FLOW.md`. `./run.sh`. |
| `analysis/method4/` | **Self-contained flow for method 4.** Same deal. `./run.sh`. The best method here. |
| `sample_graph.png` | The target output format. |
| `sections.csv`, `enrichment_info.txt` | Neon export metadata. |
| `lunar_blast_-_prelim_GAZE-OVERLAY_*.zip` | 553 MB Pupil Cloud gaze-overlay export. **Unexamined** — not used by any pipeline. |
| `.venv/` | Python env (`uv`). Set `UV_CACHE_DIR=$PWD/.uv-cache`; the default `~/.cache/uv` is sandbox-blocked. |

### Do not read the scene video for its imagery

The `.mp4` is 553 MB. Loading frames into an agent's context wastes enormous tokens
for no benefit. **Programmatic frame processing is expected and fine** — that is what
the tracker does. Just never render frames into context.

## Running it

### The two packaged flows

Methods 2 and 4 — the two worth using — are each packaged as a **self-contained flow
folder** with its own copy of every script it needs, its own inputs and outputs, and a
`FLOW.md` that explains the method end to end.

```bash
cd analysis/method2 && ./run.sh    # method 2 -> method2/out_track/figures/
cd analysis/method4 && ./run.sh    # method 4 -> method4/out_hybrid2/figures/
```

| Folder | Use it for |
|---|---|
| [`analysis/method2/`](analysis/method2/FLOW.md) | Stimulus-independent screen positions; the headline degrees-off-target number |
| [`analysis/method4/`](analysis/method4/FLOW.md) | **Tracking-error plots.** The best method here |

They overlap on purpose — method 4 transitively needs most of method 2's code plus
method 1's corner annotation — and each carries its own copies so either can be handed
over alone. Both are verified to reproduce every published number. Edits in a flow
folder do **not** propagate back to `analysis/` or to the other flow.

Each has a **`clean.sh`** that strips everything `run.sh` can rebuild, so the flow can
be sent as source and the recipient regenerates the figures themselves — 62 MB → 1.4 MB
for method 2, 43 MB → 1.4 MB for method 4 with `--deep`. It shows the plan and asks
first (`-n` for a dry run, `-y` to skip), and it **refuses** to delete the interactive
artefacts `asteroid_track.csv` and `corners.csv`, aborting if either is already gone.
Both directions are verified by full clean-and-rebuild round trips.

### The original flat layout

Still intact, still works; this is where development happened and where methods 1 and
3 live.

```bash
cd analysis
./run_corners.sh    # method 1 — needs annotate_corners.py run first (interactive)
./run_track.sh      # method 2 — needs track_live.py run first (interactive)
./run_hybrid.sh     # method 3 — needs method 2 to have run
./run_hybrid2.sh    # method 4 — needs methods 1 and 2 to have run

../.venv/bin/python refit_offsets.py           # global clock model (LB_OFFSET_MODE=per-epoch)
LB_OFFSETS=offsets_refit.csv ./run_hybrid2.sh  # method 4 with the re-solved timing
```

Score the differential methods (3 and 4) with `compare_differential.py`;
`compare_methods.py` only scores the absolute mappings (1 and 2), since its
held-out task is degenerate for a differential method.

The interactive stages are already done; their outputs (`corners.csv`,
`asteroid_track.csv`) are committed in the output directories. Re-running the
non-interactive stages is safe and reproduces everything.

Each output directory holds `gaze_mapped.csv` (gaze in game units) and
`figures/`: 86 for method 4 (per-epoch Y overlay, X overlay, plus two contact
sheets), 129 for the others, which also draw the 2D scanpath.

## If you continue this work

- **Epoch 33 was a mapping artifact, not bad tracking.** Method 3's Jacobian is
  fitted across the whole asteroid track, so mid-trial head rotation corrupts its
  scale — the very contamination the differential form was meant to dodge. Epoch 33
  carries the session's largest head yaw (−19.7°, confirmed against `imu.csv`, which
  no method uses), which cancels most of the asteroid's apparent horizontal motion.
  Method 4 sources `J` from method 1's single-frame rest corners instead and brings
  epoch 33 back to 7.95 units, in line with clean epochs. **Check `J`'s anisotropy**:
  `DataAspectRatio [1 1 1]` forces it to ~1.0, and it is unphysical on 20/42 epochs
  under method 3. See `analysis/README.md`.

- **The anticipatory-pursuit result is the interesting science.** Whether the
  watching-vs-tracking split (−315 vs −60 ms) holds across subjects is the obvious
  next question.
- **The clocks differ by a RATE, not an offset: +602 ppm.** One global model beats 42
  per-epoch solves — `refit_offsets.py` fits
  `δ(t) = −1049.9 ms + 602.0 ppm·t + 29.8 ms·[TRACKING]`, standard error ~5.3 ms
  against ~31 ms of per-epoch scatter. The TRACKING term is **not** a clock term: it is
  `drawnow limitrate` (`Lunar_Blast_v4.m:890`) skipping frames when the laser is also
  being drawn. 602 ppm is ~70× what Pupil's docs allow even for a device unsynced for a
  full day, so the culprit is almost certainly the **stimulus PC's clock**, not the Neon.
  There is no post-hoc fix available from Pupil — see `analysis/METHODS.md`.

- **The game already emits hardware sync triggers.** `Lunar_Blast_v4.m:904` sends
  `TRIG_EPOCH_OBSERVE`/`TRIG_EPOCH_TRACK` over serial to a LUMO fNIRS laptop at the exact
  instant `epochStartClock = now` is stamped, plus `TRIG_REST`, `TRIG_DEFLECTED`,
  `TRIG_IMPACT`. **No LUMO data is in this repo.** If that recording exists it holds a
  hardware-timestamped marker for every epoch onset — worth asking for, since it would
  settle which machine is drifting.

- **For future recordings, in priority order:**
  1. **Fix the stimulus PC's NTP.** At 602 ppm it dwarfs every other error here.
  2. Run Time Echo (`device.estimate_time_offset()`, ~1 ms) before *and* after each
     session; running it twice measures the residual drift directly.
  3. Send Neon events at epoch onset via the real-time API with offset-corrected
     timestamps (`event_timestamp_unix_ns = local_ns − offset_ns`). Events are discarded
     if no recording is active.
  4. Replace `drawnow limitrate` with a vsync-locked present, or log the actual flip
     time — that removes the 29.8 ms condition bias at source.
  5. **Tape AprilTags around the monitor.** That gives surface-mapped gaze directly and
     removes the coordinate problem at source, making this entire reconstruction
     unnecessary.
- The gaze-overlay zip at the root has never been opened; it may contain a
  Cloud-rendered overlay video worth a look.
