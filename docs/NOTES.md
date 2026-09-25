# Notes — Lunar Blast × Pupil Neon

What the project is, key facts about the data, the traps, and what was found. **Read
the traps before changing anything.** How to run the analysis is in
[`../analysis/README.md`](../analysis/README.md), the method itself in
[`METHOD.md`](METHOD.md), and the development log of the retired methods in
[`HISTORY.md`](HISTORY.md).

## The goal

Sarah ran 42 epochs of a MATLAB game ("Lunar Blast") while wearing Pupil Labs Neon
eye-tracking glasses. An asteroid traverses the screen on a known mathematical path;
she either watches it or actively tracks it with a dial-aimed laser.

**The deliverable is `sample_graph.png` reproduced from real data**: per epoch, the
asteroid's vertical position overlaid with where she was actually looking. That, plus
quantifying how well she tracked.

**Status:** four methods were built and compared on Sarah's session. Method 4 won, and
it's what `analysis/lb.py` runs, on any session. A second session (pilot 3170) has been
analysed with it.

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

## How we got here

Four mapping methods were built and scored against each other on Sarah's session:

| | Approach | Outcome |
|---|---|---|
| **1** | Homography from the gray rest rectangle's corners, re-anchored at each rest | 3.33 game units held-out error. Interpolating *position* between rests fails under head motion |
| **2** | Asteroid tracked in scene pixels; head motion cancels by construction | 0.83 units. Its per-epoch homography is corrupted by mid-trial head rotation |
| **3** | Measured gaze-asteroid offset placed on the log path via a local Jacobian | Jacobian unphysical (anisotropic) on 20/42 epochs |
| **4** | Method 3's formula, with `J` from method 1's single-frame rest corners | Anisotropy 1.03; a 200 ms clock error moves the answer by 0.001 units. **Kept** |

Method 4 takes the right half of each: method 1 is bad at translation but good at
scale, and the track-fitted homography is the reverse. The code for all four is in git
history at commit `a10f121`; [`HISTORY.md`](HISTORY.md) is the full log.

### Do not read the scene video for its imagery

The `.mp4` is 553 MB. Loading frames into an agent's context wastes enormous tokens
for no benefit. **Programmatic frame processing is expected and fine**, since that's
what the tracker does. Just never render frames into context.

## If you continue this work

- **Epoch 33 was a mapping artifact, not bad tracking.** Method 3's Jacobian is
  fitted across the whole asteroid track, so mid-trial head rotation corrupts its
  scale — the very contamination the differential form was meant to dodge. Epoch 33
  carries the session's largest head yaw (−19.7°, confirmed against `imu.csv`, which
  no method uses), which cancels most of the asteroid's apparent horizontal motion.
  Method 4 sources `J` from method 1's single-frame rest corners instead and brings
  epoch 33 back to 7.95 units, in line with clean epochs. **Check `J`'s anisotropy**:
  `DataAspectRatio [1 1 1]` forces it to ~1.0, and it is unphysical on 20/42 epochs
  under method 3. See [`HISTORY.md`](HISTORY.md).

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
  There is no post-hoc fix available from Pupil.

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
  3. ~~Send Neon events at epoch onset.~~ **Done from pilot 3170 on**, via LSL:
     `events.csv` carries `OBSERVE_START`/`TRACK_START` markers, which sat +4 ms from
     the MATLAB log with no drift on that session. `lb.py check` reports them.
  4. Replace `drawnow limitrate` with a vsync-locked present, or log the actual flip
     time — that removes the 29.8 ms condition bias at source.
  5. **AprilTags around the monitor.** Tried on pilot 3170, but the tags were too blurry
     at that viewing distance and resolution to detect, so there's no AprilTag pipeline
     yet. Working tags would give a per-frame screen mapping, which removes the manual
     corner annotation and the mid-trial head-roll blind spot. That likely needs the
     subject closer to the screen and head stabilisation (e.g. a chin rest).
- The gaze-overlay export at the repo root (`lunar_blast_-_prelim_GAZE-OVERLAY_*`,
  untracked) holds a Cloud-rendered gaze-overlay video of Sarah's session.
