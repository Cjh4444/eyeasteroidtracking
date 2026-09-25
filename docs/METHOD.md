# Method 4: differential mapping with an independent Jacobian

This is the method `analysis/lb.py` runs. See [`../analysis/README.md`](../analysis/README.md)
for how to run it on a session, and [`HISTORY.md`](HISTORY.md) for the methods it
replaced.

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

## Stages

`lb.py run <session>` runs these in order, stopping at each unfinished interactive stage.

| | Script | Does | Reads / writes |
|---|---|---|---|
| **U1** | `annotate_corners.py` *(interactive, `lb.py corners`)* | Click the gray rest rectangle's four corners at every rest | → `out_frame_annotate_method/corners.csv` |
| **U2** | `track_live.py` *(interactive, `lb.py track`)* | Supervised asteroid tracking in raw scene pixels | → `out_track/asteroid_track.csv` |
| **T** | `fit_from_track.py`, `refit_offsets.py` | Gaze vs tracked asteroid in degrees; per-epoch clock offsets, then the session clock model | → `out_track/`, `offsets_refit.csv` |
| **H1** | `fit_hybrid2.py` | Interpolate the rest-corner quads onto each tracked frame's timestamp and rebuild `H` per frame; compute `J` at the asteroid's pixel position; reject the epoch if `J`'s anisotropy exceeds `ANISOTROPY_MAX` (1.15); interpolate `J`'s four entries onto the 200 Hz gaze clock (`J` varies slowly, 1.5 % median change between consecutive rests, so this is far cheaper than a homography per gaze sample and just as accurate); then `gaze_game = MATLAB asteroid path + J · measured pixel offset` | → `out_hybrid2/gaze_mapped.csv`, `jacobian_report.csv` |
| **H2** | `plot.py` | Per-epoch Y overlay, X overlay, plus two contact sheets | → `out_hybrid2/figures/` |

All outputs are under `analysis/sessions/<name>/`.

## The clock offset

`lb.py fit` re-solves Δ from the tracked trajectory's **shape** alone (no homography,
no gaze) with `refit_offsets.py`, and maps with that. If a session can't support the
model, it falls back to the per-epoch solves in `out_track/track_mapping_report.csv`
(`--offsets method2` forces that).

The figures below are Sarah's session:

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

How method 4 was chosen, on Sarah's session. The scoring script
(`compare_differential.py`) was retired with the old methods and is in git history at
`a10f121`. The yardstick couldn't be `compare_methods.py`: its held-out task is *absolute*, and a differential method
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

## Traps this flow is exposed to

Full list in [`NOTES.md`](NOTES.md). The ones that bite here:

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
