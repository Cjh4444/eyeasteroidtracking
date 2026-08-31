# Method 4 — differential mapping with an independent Jacobian

**Self-contained flow.** Everything this method needs is in this folder, except the
raw data (`../../test_sarah_LB/`, `../../test_sarah_lb_eyetracking-9355240b/`) and the
Python env (`../../.venv/`).

```bash
cd analysis/method4
./run.sh
```

**Use this method for tracking-error plots.** It is the best method in the project.
For stimulus-independent screen positions and the headline degrees-off-target number
use [method 2](../method2/).

---

## The idea

    gaze_game(τ) = asteroid_matlab(τ + Δ) + J · (gaze_px − ast_px)

Map only the small gaze-to-asteroid **offset** through the mapping's local Jacobian
`J`, never gaze's absolute position. Consequence: the plotted gap between the gaze and
asteroid curves **is** the measured tracking error, by construction.

That formula is method 3's. The difference is **where `J` comes from**: method 1's
rest-period corners, not the epoch's own asteroid track.

**Why the rest corners are the right source.** Each rest homography is built from four
corners clicked in a **single video frame**, so head motion is frozen and cannot
accumulate over a 14 s track. And method 1's own fatal weakness — interpolating
*absolute position* between anchors 25–100 px apart — is structurally irrelevant here,
because `J` discards translation entirely. The two failure modes are complementary:
method 1 is bad at translation and good at scale, the track-fitted homography is the
reverse. Method 3 takes the wrong half from each.

Measured from `corners.csv`, between consecutive rests: the screen centre moves a
median **27.5 px** (max 112.8) — what method 1 had to interpolate — while `J` itself
changes a median **1.54 %** (max 8.83) — all method 4 interpolates.

**The free falsification test.** `DataAspectRatio [1 1 1]` (`Lunar_Blast_v4.m:52-53`)
forces the mapping to be isotropic, so `J`'s singular-value ratio must be ~1.0. Method
3's `J` violates it on **20/42** epochs (max 7.04); method 4's on **0/42** (max 1.076).

## Shipping this as code

`clean.sh` removes everything `run.sh` can rebuild, so the flow can be sent as source
and the recipient regenerates the pictures themselves.

```bash
./clean.sh            # show the plan, confirm, delete
./clean.sh --deep     # also the two 4 MB timeline.csv copies  (43 MB -> 1.4 MB)
./clean.sh --figures  # only the 86 figures
./clean.sh -n         # dry run
./run.sh              # get it all back
```

It **refuses to touch** the two artefacts that came from an interactive session and
cannot be rebuilt — `out_track/asteroid_track.csv` (`track_live.py`) and
`out_frame_annotate_method/corners.csv` (`annotate_corners.py`). Unlike method 2, this
flow consumes both as *inputs* and has no way to regenerate them, so the guard matters
more here; if either is already missing, `clean.sh` aborts. `--deep` is safe because
`run.sh` rebuilds the timelines from the MATLAB logs when it finds them absent.
Verified by a full `--deep` clean-and-rebuild round trip: 110,257 samples, 93.4 % in
the game area, anisotropy 1.027 median / 1.076 max / 0-of-42 rejected, all 86 figures.

Note that `corners.csv` and `asteroid_track.csv` are also duplicated in
[`../method2/`](../method2/) and in [`../`](..) itself — three copies of the only
irreplaceable files in the project. Keep it that way.

## Stages

Both interactive upstream stages are **already done** and their outputs ship in this
folder, so `run.sh` is fully non-interactive.

| | Script | Does | Reads / writes |
|---|---|---|---|
| **U1** | *(method 1)* `annotate_corners.py` *(interactive)* | Click the gray rest rectangle's four corners at each of the 41 rests | → `out_frame_annotate_method/corners.csv` |
| **U2** | *(method 2)* `track_live.py` *(interactive)* | Supervised asteroid tracking in raw scene pixels | → `out_track/asteroid_track.csv` |
| **H1** | `fit_hybrid2.py` | Interpolate the rest-corner quads onto each tracked frame's timestamp and rebuild `H` per frame; compute `J` at the asteroid's pixel position; reject the epoch if `J`'s anisotropy exceeds `ANISOTROPY_MAX` (1.15); interpolate `J`'s four entries onto the 200 Hz gaze clock (`J` varies slowly — 1.5 % median change between consecutive rests — so this is far cheaper than a homography per gaze sample and just as accurate); then `gaze_game = MATLAB asteroid path + J · measured pixel offset` | → `out_hybrid2/gaze_mapped.csv`, `jacobian_report.csv` |
| **H2** | `plot.py` | 86 figures: per-epoch Y overlay, X overlay, plus two contact sheets | → `out_hybrid2/figures/` |

`run.sh` runs H1 → H2.

To re-do either interactive upstream stage from scratch:

```bash
LB_OUT=out_frame_annotate_method ../../.venv/bin/python annotate_corners.py   # U1
LB_OUT=out_track                 ../../.venv/bin/python track_live.py         # U2
```

`build_timeline.py` and `sync.py` are included because those stages depend on them;
`fit_from_track.py` and `fit_hybrid.py` are included because `fit_hybrid2.py` imports
`MIN_SCORE` and `jacobian()` from them.

## The clock offset

By default `fit_hybrid2.py` takes Δ from method 2's `out_track/track_mapping_report.csv`.
Better: re-solve it from the tracked trajectory's **shape** alone — no homography, no
gaze — and feed that in.

```bash
../../.venv/bin/python refit_offsets.py            # → offsets_refit.csv
LB_OFFSETS=offsets_refit.csv ./run.sh              # method 4 on the re-solved timing
```

The two clocks differ by a **rate**, not a fixed offset: **+602 ppm**, accumulating to
645 ms across the 18-minute session. A rate difference is linear in time, so one global
model beats 42 independent per-epoch solves:

    δ(t) = −1049.9 ms  +  602.0 ppm · t  +  29.8 ms · [condition is TRACKING]

Standard error ~5.3 ms against ~31 ms of per-epoch scatter. `LB_OFFSET_MODE=per-epoch`
gives the per-epoch solves instead.

**The third term is not a clock term** — no clock can know the condition. It is
`Lunar_Blast_v4.m:890` rendering with `drawnow limitrate`: MATLAB skips frames when the
renderer is busy, and TRACKING draws the laser as well as the asteroid. Modelling it
explicitly is what lets the rate be estimated from all 34 usable epochs instead of only
the 17 laser-free ones.

Method 4 barely cares either way — a 200 ms clock error moves the answer by **0.001
game units**, because `J` never touches the offset at all.

## Scoring

```bash
../../.venv/bin/python compare_differential.py     # → differential_comparison.csv
```

Not `compare_methods.py` — its held-out task is *absolute*, and a differential method
puts the asteroid on the log path by construction, so it would score a meaningless
zero. The yardstick is instead method 2's `gaze_vs_asteroid.csv`: separation in degrees
from raw pixels and intrinsics alone.

| | method 2 | method 3 | **method 4** |
|---|---|---|---|
| anisotropy of `J` (must be ~1.0) | — | 1.134 med, 20/42 bad | **1.027 med, 0/42** |
| calibration-bias spread, x (sd / range) | 3.31 / 18.76 | 3.20 / 14.62 | **1.10 / 5.17** |
| units-per-degree consistency (CV) | 35.4 % | 37.6 % | **5.9 %** |
| clock error 200 ms → separation change | 4.24 | 1.298 | **0.001** |
| Spearman(sep, ContactPct) | −0.024 | −0.123 | **−0.328** |

The bias test is the non-circular one: Sarah's calibration offset is a property of her
eyes and how the glasses sat, so it must be near-constant across an 18-minute session.
Method 4 recovers it 3× tighter. The ContactPct test is fully independent — it comes
from the game and never touched any mapping — and methods 2 and 3 have *destroyed* that
signal, while method 4 slightly exceeds the raw-degree yardstick (−0.273), as it should,
since game units account for viewing-distance changes that degrees do not.

## Epoch 33 — the motivating case

Its tracking was never bad. In the raw-degree reference it measures **2.99°**,
indistinguishable from clean epochs 31 (2.98°) and 36 (2.73°). Sarah yaws **−19.7°**
during it — the session's largest head motion, confirmed against `imu.csv`, which no
method uses — and that cancels most of the asteroid's apparent horizontal motion: the
track sweeps 85 px of x where a still head gives ~205 px. Method 3 reported 23.46 game
units of separation. Method 4 gives **7.95**, in line with clean epochs.

## Limits

`J` is a local linearisation, and gaze is reconstructed *relative to the target* — so
this method cannot tell you where she looked when she was not looking near the
asteroid. It also depends on method 1's `corners.csv`, so it cannot run on a recording
with no annotated rests.

**The two unmodelled modes are head tilt and viewing-distance change.** `J` is
re-anchored at every rest, so changes *across* the session are tracked — and they are
large: apparent vertical scale ranges 3.35–6.23 px per game unit, a 54 % swing. *Within*
a trial it is an interpolation, and note that the anisotropy check above cannot see
either mode: a distance change is an isotropic scale and a tilt is a rotation, so both
leave the singular-value ratio at 1.0.

What the data says about the residual risk:

- **Tilt is small.** IMU roll within a trial: median **0.7°**, p90 2.5°, max 4.5°. The
  motion that actually happened was yaw (median 4.3°, max 27.2°) and pitch — which
  mostly *slides* the screen rather than rescaling it.
- **No detectable within-trial distance change.** Measuring apparent scale directly
  from the raw track (the asteroid's game-y slope is a known constant, so its pixel
  slope gives px-per-game-unit with no homography and no clock): first half of a trial
  vs second half differs by a median **3.5 %**, against a 6–10 % estimator noise floor,
  with signed mean +3.3 % (no systematic direction) and lag-1 autocorrelation between
  consecutive legs of **+0.03**. A real lean-in/lean-out is slow and smooth and would
  autocorrelate strongly. What residual there is tracks *yaw* (spearman +0.377) more
  than roll (+0.225) — i.e. it is yaw foreshortening, the epoch-33 mechanism, not
  distance.
- **Errors in `J` are multiplicative, not additive.** Since
  `separation = |J · (gaze_px − ast_px)|`, a 6 % scale error gives a 6 % error on the
  reported tracking error — about 0.1° against a 1.68° residual. This is the structural
  reason the method is robust, and it is categorically different from method 1, where a
  100 px *translation* error was additive and swamped a signal of similar size. The
  independent empirical ceiling on total `J` error from all causes is the 5.9 %
  units-per-degree CV in the table above.

So: a mid-trial distance change below ~6 % cannot be ruled out, and if one occurred it
would scale the reported error by that same ~6 %.

## Files

```
run.sh                      this flow, H1 → H2
clean.sh                    strip everything run.sh can rebuild
FLOW.md                     this file
common.py                   shared paths, game geometry from Lunar_Blast_v4.m
fit_hybrid2.py              H1  — the method
plot.py                     H2
refit_offsets.py            clock model re-solved from trajectory shape
compare_differential.py     scoring, methods 2 / 3 / 4

  supporting (imported, or needed to regenerate the upstream inputs)
annotate_corners.py         U1, interactive
track_live.py               U2, interactive
build_timeline.py           needed by U1 / U2
sync.py                     needed by U1 / U2; also supplies load_gaze()
fit_mapping.py              camera intrinsics + undistortion
fit_from_track.py           supplies MIN_SCORE, drift_model
fit_hybrid.py               supplies jacobian(), epoch_H() — method 3's core
compare_methods.py          supplies m1_quads(), m1_H_at()

  data
out_frame_annotate_method/  method 1's corners.csv (+ what U1 needs to re-run)
out_track/                  method 2's asteroid_track.csv and timeline (inputs only)
out_hybrid2/                this flow's outputs, including figures/
offsets_refit.csv           refit_offsets.py output
differential_comparison.csv compare_differential.py output
```

## Traps this flow is exposed to

Full list in [`../README.md`](../README.md). The ones that bite here:

1. **The two clocks drift at ~15.4 ms per epoch.** There is no single offset. Method 4
   is nearly immune, but `refit_offsets.py` is the right way to get Δ.
3. **Plotting gaze (Neon clock) against the stimulus log (MATLAB clock) without
   converting** makes the ~750 ms offset look like eye lag — it swamps the real
   ~200 ms *lead* and flips its sign. `plot.py` applies the per-epoch offset.
5. **`Asteroid_Y` aliases.** Shifting a triangle wave half a period inverts it and a
   homography absorbs that by flipping its y-scale — numerically excellent, physically
   impossible. Reject fits where image-y-down doesn't map to game-y-up.
7. **Neon's `worn` flag goes falsely false in epochs 38–42** (43 % of epoch 41), which
   is what put the visible gaps in those epochs' plots. `LB_REQUIRE_WORN=1` restores
   the old behaviour and every published number.
8. **A per-epoch clock offset solved from a head-motion-damaged homography is
   arbitrary.** `drift_model` drops those epochs from the drift line but then re-solves
   them against the same broken residual. `refit_offsets.py` solves from the tracked
   trajectory's shape alone.
9. **`sync.py`'s offset is confounded with pursuit lag.** Diagnostic only.

Always check `out_hybrid2/jacobian_report.csv` before reading a figure: an epoch whose
`J` is anisotropic has not been mapped correctly.

## Do not read the scene video for its imagery

The `.mp4` is 553 MB. Programmatic frame processing is expected and fine. Never render
frames into an agent's context.
