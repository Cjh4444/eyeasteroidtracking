> **Historical development log.** This is how methods 1–4 were built and compared on
> Sarah's session. The scripts, `run_*.sh` files and `method2/`/`method4/` flow folders
> it mentions were removed in the repo cleanup; recover them with
> `git show a10f121:analysis/<file>` or `git checkout a10f121 -- analysis/`. The current
> analysis is `analysis/lb.py` (method 4); see [`METHOD.md`](METHOD.md).

# Lunar Blast × Neon gaze analysis

Plots where Sarah looked against where the asteroid was, per epoch.

## Two packaged flows

The four methods share most of their code, and the flat layout in this directory is
where the real files live and where development happened. Two of them are packaged as
**self-contained flow folders**, each with its own copy of every script it needs, its
own inputs, its own outputs, and a `FLOW.md` explaining the method end to end:

| Folder | Method | Run | Use it for |
|---|---|---|---|
| [`method2/`](method2/FLOW.md) | 2 — asteroid tracking | `cd method2 && ./run.sh` | Stimulus-independent screen positions; the headline degrees-off-target number |
| [`method4/`](method4/FLOW.md) | 4 — differential, rest-corner Jacobian | `cd method4 && ./run.sh` | **Tracking-error plots.** The best method here |

The files overlap heavily — method 4 transitively needs most of method 2's code plus
method 1's corner annotation — and each folder carries its own copies on purpose, so
either can be handed over on its own. Edits made in a flow folder do **not** propagate
back to this directory, or to the other flow.

Each flow also has a **`clean.sh`** that removes everything its `run.sh` can rebuild,
for shipping the code without the ~30 MB of figures. It prints the plan and asks first,
and refuses to delete `asteroid_track.csv` or `corners.csv` — the interactive artefacts
nothing can regenerate.

The originals, the `run_*.sh` scripts, and methods 1 and 3 are untouched and still
work exactly as before.

## The coordinate problem

The Neon export is raw only (`enrichment_info.txt` → `raw-data-exporter`), so gaze
lives in **scene-camera pixels** — head-relative, not screen-relative. No AprilTags
were present during recording, so Pupil's Marker Mapper enrichment cannot be run
retroactively. The mapping has to be recovered from the data.

**The trick:** during every rest period the game draws a gray fill spanning
*exactly* game `[0,100] × [0,65]` (`Lunar_Blast_v4.m:439`), with the fixation cross
at game `(50, 32.5)` on top of it (`:441`). Clicking those four corners in a video
frame gives a homography from scene pixels straight to game units — no screen size,
resolution, viewing distance or MATLAB window geometry needed, because they all
cancel. There are 41 rest periods, so the mapping is re-anchored roughly every 25 s,
which tracks slow head drift.

## Two methods, kept side by side

Both are intact and write to their own output directory (`LB_OUT`), so results
can be compared.

### Method 1 — rest-rectangle corners (`run_corners.sh` → `out_frame_annotate_method/`)

Homography from the gray rest rectangle's four corners, re-anchored at each of the
41 rests and interpolated between them.

| Script | Role |
|---|---|
| `annotate_corners.py` | **interactive** — click corners + cross per rest |
| `fit_mapping.py` | per-rest homography, undistortion, validation |
| `detect_asteroid.py` | clock offset by warping frames into game space |

**Where it falls down.** Its own diagnostics: corner clicks are accurate (3.9 px ≈
0.64 game units), but the screen centre moves up to **100 px between consecutive
rests** (median 25 px) and the screen width changes 16 % across the session. Whatever
the head does *mid-trial* is invisible to an interpolation between anchors, and the
end-to-end residual lands at 8.1 game units — 12× the click accuracy.

### Method 2 — asteroid tracking (`run_track.sh` → `out_track/`)

| Script | Role |
|---|---|
| `track_live.py` | **the only tracker** — supervised: watch, pause, rewind, re-seed, continue. Owns the tracking primitives (template match, motion gate, seed proposal) |
| `fit_from_track.py` | gaze vs asteroid in px and degrees; per-epoch homography from the track |

**Why it is better.** The asteroid is located in raw scene-camera pixels, and gaze is
*already* in raw scene-camera pixels, on the **same Neon UTC clock**. So the
gaze-minus-asteroid difference is head-motion invariant by construction — if the head
turns, both move together in the image and the difference is unchanged. No homography
in the critical path, and no cross-clock alignment at all: the MATLAB log is needed
only to label which epoch a frame belongs to.

## Shared stages

`common.py` (paths/constants, `LB_OUT`), `build_timeline.py` (MATLAB log → UTC ns,
derives the 41 rest windows), `sync.py`, `plot.py`.

## Traps worth remembering

- **`sync.py`'s +555 ms is mostly pursuit lag, not clock error.** It rises with
  stimulus frequency (+160 ms at waveFreq 1.0 → +603 ms at 3.5) and splits by
  condition (WATCHING +430, TRACKING +700). A clock offset cannot do either.
- **`Asteroid_X` cannot measure lag by correlation.** It is a linear ramp, and
  correlation is invariant to shifting a linear signal — r ≈ 1 at any offset.
- **`Asteroid_Y` aliases.** It is a triangle wave with period `14/waveFreq`, so
  correlation has multiple peaks (4 s apart at waveFreq 3.5).
- **A tracker's match score cannot detect a stuck tracker.** Locked onto static art
  it template-matches itself and reports 1.00 forever. `track_live.stuck_at()` tests
  actual displacement instead; on epoch 31 it flags from frame 45, while score says
  100 % confident.
- **Method 1's `detect_asteroid.py` clock offset (−775 ms → 1.33 s pursuit lag) is
  not trustworthy** — it warps frames through the homography that is itself unreliable
  mid-trial.


## Verdict — measured, not assumed

`compare_methods.py` scores both on the same held-out task: given the asteroid's
measured pixel position in a frame, predict its game coordinates, checked against
the MATLAB log. Fully out-of-sample for method 1 (its homography comes from the
rest periods and never saw a mid-trial frame); 2-fold cross-validated for method 2.
Each method gets its own best per-epoch time alignment, so neither is penalised for
the clock drift.

| | median | p90 | max |
|---|---|---|---|
| method 1 — rest corners | 3.33 units (1.21°) | 11.53 | 16.99 |
| method 2 — asteroid track | **0.83 units (0.30°)** | **2.28** | **10.56** |

**Method 2 wins on 42/42 epochs, 4× better median and 5× better p90.** Method 1
degrades most where head motion is least forgiving; method 2 is roughly flat across
waveFreq.

### Headline result (method 2, no homography anywhere)

Gaze-to-asteroid error over 107,298 samples: **median 3.23°**, with a constant bias
of (−2.00°, −1.81°). Removing that bias leaves **1.68°** — squarely in Neon's
calibration-free accuracy spec, which is a strong independent sanity check. The bias
is Sarah's gaze calibration offset, and method 1 independently measured the same
thing (~50 px ≈ 3.2°) from the rest fixation cross.

Error rises monotonically with stimulus speed — 2.80° at waveFreq 1.0 to 3.57° at
3.5 — matching `epoch_summary.csv`'s laser-contact rates (69% → 21%) from a
completely independent measurement.



### Method 3 — differential (`run_hybrid.sh` → `out_hybrid/`)

    gaze_game(tau) = asteroid_matlab(tau + delta)  +  J . (gaze_px - ast_px)

Builds on method 2's track, but maps only the small gaze-to-asteroid **offset**
through the mapping's local Jacobian `J`, never gaze's absolute position.

**Does the clock drift break it?** No — and this was the motivating worry. The
separation between the plotted gaze and asteroid curves is the measured offset *by
construction*, so a clock error slides the curve along the time axis without
changing the tracking error it depicts. Measured, under a forced clock error:

| forced error | method 2 | method 3 |
|---|---|---|
| 50 ms | 1.34 units | **0.66** |
| 100 ms | 2.37 | **1.02** |
| 200 ms | 4.23 | **1.35** |

Method 2 degrades linearly; method 3 saturates. Its residual sensitivity is not
from the time base at all — refitting the homography at a wrong offset slightly
changes `J`.

Against the direct pixel measurement (no clock, no homography, the most trustworthy
reference available), method 3 deviates **0.98** game units vs method 2's **1.45**.

**Also more robust to mapping error**: `H`'s translation cancels exactly, and
perspective variation across the screen stops mattering because gaze sits within a
few degrees of the asteroid.

**Limitations.** `J` is a local linearisation, so accuracy degrades when gaze is far
from the asteroid — fine here (median separation 9 game units) but not a
general-purpose screen mapping. And gaze is reconstructed *relative to the target*,
so this answers "how far off target, moment to moment" rather than "where on the
screen was she looking" independent of the stimulus. For this experiment that is the
question anyway. Use method 2 if you need stimulus-independent screen positions.

### Pursuit lag — measured without any clock model

Gaze and the tracked asteroid are both timestamped by the Neon clock, so shifting
one against the other measures eye behaviour directly (`pursuit_lag.csv`). All 42
epochs give r > 0.8.

**Median −195 ms: gaze ANTICIPATES the asteroid.** The triangle wave is perfectly
predictable, so the eyes lead rather than follow. Anticipation scales with how
predictable the target is:

| waveFreq | 1.0 | 1.5 | 2.5 | 3.0 | 3.5 |
|---|---|---|---|---|---|
| lead | −280 ms | −235 ms | −197 ms | −182 ms | −112 ms |

and it is much stronger when merely watching (−315 ms) than when actively aiming
the laser (−60 ms, i.e. near-synchronous).

Do NOT derive this by subtracting a clock offset from `sync.py`'s gaze-vs-stimulus
offset. Composing two offsets measured on different clocks, with method 1's ~675 ms
drift scatter, yields +2 s — physiologically impossible.

### A trap in the overlay plots

Gaze is timestamped by Neon; the stimulus log by MATLAB. Plotting one against the
other **without converting** makes the ~750 ms clock offset look like eye-movement
lag, and it swamps the real ~200 ms anticipatory lead — it even flips its sign.
`plot.py` now applies the per-epoch offset from `track_mapping_report.csv` before
plotting. Method 1 has no trustworthy per-epoch offset, so its figures remain
uncorrected and their apparent lag should not be read as behaviour.

### The clock drift

The two clocks drift apart at **+15.4 ms per epoch**, −1050 ms at the start to
−402 ms at the end (633 ms, ~570 ppm, r = 0.90 vs epoch index). There was never a
single offset to find, which is why method 1's `detect_asteroid.py` produced a
1.33 s "pursuit lag". `fit_from_track.py` solves the offset per epoch, then fits the
drift line and re-solves in a narrow window around it.

Watch for **half-period sign flips**: shifting a triangle wave by half a period
inverts it, and a homography absorbs that by flipping its y-scale — a numerically
excellent, physically impossible fit that captured 12 epochs. `y_flipped()` rejects
them, since image-y-down must map to game-y-up.

## Validation

- Behavioural falsification: `epoch_summary.csv` reports `ContactPct` ≈ 66–69 % at
  waveFreq 1.0 vs ≈ 20 % at 3.5. Plots must show clean pursuit on slow epochs and
  ragged, saccadic tracking on fast ones.
- Epoch 15 fits sync r = 0.30 (all others > 0.85) and is flagged in plot titles.

### Method 4 — differential with an independent Jacobian (`run_hybrid2.sh` → `out_hybrid2/`)

Same formula as method 3:

    gaze_game(tau) = asteroid_matlab(tau + delta)  +  J . (gaze_px - ast_px)

**but J comes from method 1's rest-period corners, not from the asteroid track.**

**The bug this fixes.** Method 3's differential form is right — `gaze_px - ast_px`
really is head-motion invariant. But the offset is in *pixels*, and converting it
to game units needs `J`, which `fit_hybrid.epoch_H` fits across the **whole
epoch's track**. That is precisely what mid-trial head rotation destroys, so the
contamination comes straight back in through the scale factor.

Epoch 33 is the worst case: Sarah yaws **−19.7°** (the largest head motion in the
session) while the asteroid travels +40 game units right. The rotation cancels most
of the asteroid's apparent horizontal motion — the track sweeps **85 px of x where
a still head gives ~205 px** — and the homography absorbs it by inflating its
x scale. Confirmed against `imu.csv`, which no method uses: the three largest head
yaws (epochs 33, 37, 20) are exactly the three worst `resid_median`, Spearman 0.57–0.72.
De-rotating the track by IMU yaw restores epoch 33's aspect ratio and is a **no-op on
clean epochs**, which is the control that matters.

**Why the rest corners are the right source for J.** Each rest homography is built
from four corners clicked in a **single video frame**, so head motion is frozen and
cannot accumulate over 14 s. And method 1's own fatal flaw — interpolating *absolute
position* between anchors up to 100 px apart — is structurally irrelevant, because
`J` discards translation entirely. Scale at screen centre changes a median of
**1.2 % between consecutive rests**, so interpolating `J` is safe where interpolating
position was not. The failure modes are complementary: method 1 is bad at translation
and good at scale, the track-fitted homography is the reverse. Method 3 takes the
wrong half from each.

**A new free falsification test.** `DataAspectRatio [1 1 1]` forces the mapping to be
isotropic, so `J`'s singular-value ratio must be ~1.0. This is free to check and it
turns out method 3's `J` is unphysical on **20 of 42 epochs** (max 7.04, epoch 28) —
far more than the residual diagnostics ever flagged. `ANISOTROPY_MAX = 1.15` rejects them.

#### Scoring — `compare_differential.py`

`compare_methods.py` cannot be used here: its held-out task is *absolute* ("given the
asteroid's pixel position, predict its game coords"), and both differential methods put
the asteroid on the log path by construction, so they would score a meaningless zero.
The yardstick instead is `gaze_vs_asteroid.csv` — separation in **degrees**, from raw
pixels and intrinsics alone, no homography, no clock, no MATLAB log.

| test | method 2 | method 3 | method 4 |
|---|---|---|---|
| anisotropy of `J` (must be ~1.0) | — | median 1.134, **20/42 over 1.15** | median 1.027, **0/42** |
| calibration-bias spread, x (sd / range) | 3.31 / 18.76 | 3.20 / 14.62 | **1.10 / 5.17** |
| calibration-bias spread, y (sd / range) | 3.33 / 17.77 | 3.41 / 21.62 | **1.28 / 7.53** |
| units-per-degree consistency (CV) | 35.4 % | 37.6 % | **5.9 %** |
| clock error 200 ms → separation change | 4.24 | 1.298 | **0.001** |
| Spearman(sep, ContactPct) | −0.024 | −0.123 | **−0.328** |

The bias test is the non-circular one: Sarah's calibration offset is a property of her
eyes and how the glasses sat, so it must be near-constant across an 18-minute session.
Method 4 recovers it 3× tighter, and its session median (dx −5.26, dy +4.74 units)
matches the degree measurement (−2.01°, −1.81°) times the correct scale (2.66 units/deg
→ −5.35, +4.82). Methods 2 and 3 overshoot.

Test C is fully independent — `ContactPct` comes from the game and never touched any
mapping. Methods 2 and 3 have **destroyed** the behavioural signal (−0.024, −0.123);
method 4 recovers −0.328, slightly exceeding the raw-degree yardstick (−0.273), as it
should, since game units account for viewing-distance changes that degrees do not.

Clock sensitivity collapses to ~0.001 units because `J` never touches the offset at all
— method 3's residual sensitivity was entirely "refitting `H` at a wrong offset changes
`J`", and method 4 removes that term structurally.

#### What this means for epoch 33

**Epoch 33's tracking was never bad.** In the trustworthy raw-degree reference it
measures **2.99°** — indistinguishable from clean epochs 31 (2.98°) and 36 (2.73°).
Method 3 reported 23.46 game units of separation where clean epochs give ~8.3;
method 4 gives **7.95**. The "bad data" was entirely a mapping artifact, which is why
the video looked fine: the tracker was locked on the right blob the whole time, and
only the accumulated trajectory was corrupted.

| epoch | measured ° | method 3 | method 4 | aniso 3 | aniso 4 |
|---|---|---|---|---|---|
| 33 | 2.99 | 23.46 | **7.95** | 2.71 | 1.02 |
| 37 | 3.00 | 20.07 | **8.49** | 1.78 | 1.02 |
| 20 | 3.63 | 23.37 | **9.68** | 2.83 | 1.03 |
| 31 (clean) | 2.98 | 8.68 | 8.27 | 1.12 | 1.01 |
| 36 (clean) | 2.73 | 8.32 | 7.70 | 1.01 | 1.03 |

### The gaps in epochs 38–42 are a false `worn` negative, not lost data

Those epochs' plots had visible holes across *all* methods. The asteroid tracker is
not the cause — it never drops below `MIN_SCORE` on any epoch. The gaps are Neon's
`worn` flag going false: 43 % of epoch 41, 30 % of 39, 24 % of 42, and `sync.py`
excluded those samples.

It is a **false** negative. Checked against the tracked asteroid — which needs no
mapping and no clock — the `worn == 0` samples track as well as the `worn == 1` ones,
and in four of the five epochs slightly better:

| epoch | worn=1 | worn=0 |
|---|---|---|
| 38 | 3.27° | 2.96° |
| 39 | 3.83° | 3.31° |
| 40 | 2.90° | 2.61° |
| 41 | 3.97° | 3.49° |
| 42 | 3.78° | 4.37° |

Glasses genuinely off a face do not produce gaze landing 3° from a moving target.
`sync.py` now requires only "not blinking"; `LB_REQUIRE_WORN=1` restores the old
behaviour and reproduces every published number exactly. This recovers **3,329
samples** (epoch 41 goes from 53 % to 94 % coverage) and **no metric degrades** —
bias spread, scale consistency and the ContactPct correlation all hold, which is
itself the evidence that the recovered samples are real.

### Fixing the per-epoch clock offset — `refit_offsets.py`

Method 2 solves delta by minimising the reprojection residual of the same
track-fitted homography that head motion corrupts, so on a damaged epoch the offset
is as untrustworthy as the mapping. `drift_model` correctly drops those epochs from
the drift *line*, but then re-solves them inside ±0.25 s of it using the same broken
residual — epoch 33 landed 197 ms off-trend on a clock that drifts 15 ms per epoch.

`refit_offsets.py` re-solves delta from the **shape of the tracked pixel trajectory
alone** — no homography, no gaze. Z-scoring makes it scale-free (so the inflated
x-scale never enters) and removing a linear trend absorbs the slow head drift while
leaving the triangle wave, whose turning points carry the timing. Scatter about the
drift line drops from 50.2 ms to **34.9 ms**; epoch 33 improves from 197 ms
off-trend to 110 ms, epoch 20 from −124 to −25, epoch 37 from +48 to +15.

Two real limits, both handled by falling back rather than guessing:

- **waveFreq 1.0 cannot be solved this way at all.** One cycle in 14 s gives a broad
  correlation peak, so delta is not localised (r falls to 0.25–0.81). Those epochs
  fall back to the drift line, or to method 2's solve where its homography was sound.
- The detrend must be **order 1**. A cubic fits most of a single triangle cycle and
  collapses the correlation on the slow epochs.

```bash
../.venv/bin/python refit_offsets.py          # writes offsets_refit.csv
LB_OFFSETS=offsets_refit.csv ./run_hybrid2.sh # method 4 with the re-solved timing
```

Offset sources across the 42 epochs: 34 shape-solved, 5 drift-line, 3 method 2.
