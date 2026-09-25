# Four methods, four flows

The problem all four solve: the Neon export gives gaze only in **scene-camera
pixels** (head-relative). To plot gaze against the asteroid we need it in the game's
coordinate system, `[0,100] × [0,65]`.

Shared prerequisites:

| | |
|---|---|
| `build_timeline.py` | MATLAB epoch CSVs → absolute UTC ns. Derives the 41 rest windows as `next_epoch_start − 3 s − REST_DURATION`. |
| `sync.py` | Gaze-vs-stimulus offset by cross-correlation. Diagnostic only — it is confounded with pursuit lag. |
| `refit_offsets.py` | Clock offset re-solved from the tracked trajectory's *shape* — no homography, no gaze. Writes a global 3-parameter model by default (`LB_OFFSET_MODE=per-epoch` for per-epoch solves). Feed it to method 4 with `LB_OFFSETS`. |

### The clock offset — what it is and how to get it

The two clocks differ by a **rate**, not a fixed offset: **+602 ppm**, which accumulates
to 645 ms across the 18-minute session. A rate difference is physically required to be
linear in time, so the right estimator is one global model rather than 42 independent
solves:

    δ(t) = −1049.9 ms  +  602.0 ppm · t  +  29.8 ms · [condition is TRACKING]

Fitted on the 34 epochs where the shape match localises δ (r ≥ 0.90); standard error of
the fitted line ~5.3 ms, against ~31 ms of per-epoch scatter. It also supplies an offset
for the eight waveFreq 1.0 epochs, where one cycle in 14 s leaves δ unlocalisable at any
precision.

**The third term is not a clock term.** The residual about a two-parameter line splits by
condition — TRACKING epochs land **+29.8 ms** later and scatter 2.5× more — and no clock
can know the condition. The cause is `Lunar_Blast_v4.m:890`, which renders with
`drawnow limitrate`: MATLAB throttles and skips frames when the renderer is busy, and
TRACKING draws the laser as well as the asteroid, so the display lags the log further.
Modelling it explicitly is what lets the rate be estimated from all 34 epochs instead of
only the 17 laser-free ones.

**602 ppm is far too large to be the Neon.** Pupil's own figures are <10 ms for a freshly
synced pair, ≤1.5 ms/hour of measured relative drift, and 700 ms–1 s after a *full day*
without sync (≈8 ppm). Ours is ~70× the latter. All Neon streams share one clock — the
Companion phone's NTP-disciplined UTC — so gaze, scene video and IMU are mutually
consistent and need no correction. The drift is almost certainly the **stimulus PC's
`now` clock** free-running rather than NTP-disciplined. See
<https://docs.pupil-labs.com/neon/data-collection/time-synchronization/>.

**There is no post-hoc fix from Pupil.** Cloud stores no device-to-UTC offset, no NTP
sync state, and no sync age; Native Recording Data's `.time` files carry the same UTC
nanoseconds as the CSVs, with no extra precision. Time Echo
(`device.estimate_time_offset()`, ~1 ms), LSL, NTP pre-sync and anchor events all have to
be set up before or during recording. Post-hoc events *can* be added in Pupil Cloud
(they appear as `type = cloud` in `events.csv`) but are placed by timeline scrubber, with
no documented precision.

**How much does it matter?** For methods 3 and 4, almost not at all — a forced 200 ms
error moves method 4's reconstructed separation by 0.001 game units, because the
separation *is* the measured offset by construction. δ sets where the curves sit on the
time axis, and it matters to method 2's absolute mapping. Switching from per-epoch solves
to the global model moves the offsets by a median 14 ms (max 94 ms, on epoch 33), which
slides the plotted curves about 0.43 game units.

---

## Method 1 — rest-rectangle corners

`./run_corners.sh` → `out_frame_annotate_method/`

During every rest the game draws a gray fill spanning *exactly* game `[0,100]×[0,65]`
(`Lunar_Blast_v4.m:439`). Clicking its corners gives a homography from scene pixels
to game units, with no need for screen size, resolution, viewing distance or window
geometry — they all cancel.

**Flow**

1. `annotate_corners.py` *(interactive)* — for each of 41 rests, click the gray
   rectangle's 4 corners plus the fixation cross. Previous shape propagates; drag it
   onto the new frame.
2. `fit_mapping.py` — undistort clicks (8-coefficient rational model), fit a
   5-point homography per rest.
3. Interpolate the four **corner positions** between rests, rebuild `H` per
   timestamp. *(Corners interpolate sensibly; matrix entries do not.)*
4. Map all gaze through `H(t)` → `gaze_mapped.csv`.
5. `plot.py` → figures.

**Validation.** The cross sits at game (50, 32.5) on the same rectangle, so it checks
the mapping independently of the clicks.

**Weakness.** The screen centre moves up to **100 px between consecutive rests**
(median 25 px) and screen width changes 16% across the session. Mid-trial head motion
is invisible to an interpolation between anchors. Held-out error **3.33 game units**.

---

## Method 2 — asteroid tracking

`./run_track.sh` → `out_track/`

Track the asteroid in raw scene pixels. Gaze is *already* in raw scene pixels on the
**same Neon clock**, so their difference is head-motion invariant by construction —
if the head turns, both move together in the image.

**Flow**

1. `track_live.py` *(interactive)* — play each epoch, watch the box follow the
   asteroid, pause/rewind/re-seed where it drifts. Auto-pauses on low confidence or
   a parked box. → `asteroid_track.csv`
2. `fit_from_track.py`, part A — undistort both, interpolate the asteroid (30 Hz)
   onto each gaze timestamp (200 Hz), take `gaze_px − ast_px`, convert to degrees.
   **This is the primary result and uses no MATLAB data at all.**
3. Part B — solve the per-epoch clock offset by sweeping it and keeping the value
   that minimises homography reprojection residual. Reject y-flipped fits.
4. Fit the drift line across epochs, re-solve each epoch in a narrow window around
   it. *(The clocks drift +15.4 ms/epoch; no single offset exists.)*
5. Per-epoch homography from tracked pixels ↔ MATLAB game coords — the asteroid is a
   moving calibration target sweeping the screen. Map gaze through it.
6. `plot.py` → figures, with the per-epoch offset applied to gaze timestamps.

**Result.** Held-out error **0.83 game units** — better than method 1 on 42/42
epochs. Gaze-to-asteroid error 3.23° (1.68° after removing the 2.7° calibration bias).

---

## Method 3 — differential

`./run_hybrid.sh` → `out_hybrid/` *(requires method 2 first)*

    gaze_game(τ) = asteroid_matlab(τ + Δ) + J · (gaze_px − ast_px)

Map only the small gaze-to-asteroid **offset** through the mapping's local Jacobian
`J`, never gaze's absolute position.

**Flow**

1. Reuse `asteroid_track.csv` and the per-epoch offsets from method 2.
2. `fit_hybrid.py` — refit `H` per epoch at the known offset.
3. Compute `J`, the 2×2 local derivative of `H` **at the asteroid's pixel position**.
4. `gaze_game = MATLAB asteroid path + J · (measured pixel offset)`.
5. `plot.py` → figures.

**Why it helps.** The gap between the plotted gaze and asteroid curves *is* the
measured offset, so a clock error slides the curve along time without corrupting the
tracking error it depicts. Under a forced 100 ms error the separation shifts 1.02
units vs method 2's 2.37. `H`'s translation cancels exactly, and screen perspective
stops mattering since gaze sits within a few degrees of the asteroid.

**Limits.** `J` is a local linearisation — degrades when gaze is far from the
asteroid (fine here, median separation 9 units). Gaze is reconstructed *relative to
the target*, so it answers "how far off target" rather than "where on screen",
independent of the stimulus.

**The flaw method 4 fixes.** Step 2 refits `H` across the *whole epoch's track*, and
that is precisely what mid-trial head rotation corrupts — so the contamination the
differential form was built to dodge comes back in through the scale factor. `J` is
unphysically anisotropic on **20 of 42 epochs**. Superseded by method 4.

---

## Method 4 — differential with an independent Jacobian

`./run_hybrid2.sh` → `out_hybrid2/` *(requires methods 1 and 2 first)*

    gaze_game(τ) = asteroid_matlab(τ + Δ) + J · (gaze_px − ast_px)

Method 3's formula unchanged. The difference is **where `J` comes from**: method 1's
rest-period corners, not the asteroid track.

**Flow**

1. Reuse `asteroid_track.csv` (method 2) and `corners.csv` (method 1).
2. `fit_hybrid2.py` — interpolate the rest-corner quads onto each tracked frame's
   timestamp and rebuild `H` per frame.
3. Compute `J` at the asteroid's pixel position; check its anisotropy and reject the
   epoch if it exceeds `ANISOTROPY_MAX` (1.15).
4. Interpolate `J`'s four entries onto the 200 Hz gaze clock. *(`J` varies slowly —
   1.2 % median change between consecutive rests — so this is far cheaper than a
   homography per gaze sample, and just as accurate.)*
5. `gaze_game = MATLAB asteroid path + J · (measured pixel offset)`.
6. `plot.py` → figures.

**Why the rest corners are the right source.** Each rest homography comes from four
corners clicked in a **single video frame**, so head motion is frozen and cannot
accumulate over 14 s the way it does across a track. And method 1's own fatal
weakness — interpolating *absolute position* between distant anchors — is
structurally irrelevant, because `J` discards translation entirely. The two failure
modes are complementary: method 1 is bad at translation and good at scale, the
track-fitted homography is the reverse. Method 3 takes the wrong half from each.

**The free falsification test.** `DataAspectRatio [1 1 1]` forces the mapping to be
isotropic, so `J`'s singular-value ratio must be ~1.0. Free to check, and in the same
family as `y_flipped()`. Method 3's `J` violates it on 20/42 epochs (max 7.04);
method 4's on 0/42 (max 1.076).

**Result.** Scored by `compare_differential.py`, not `compare_methods.py` — the
latter's held-out task is *absolute*, and a differential method puts the asteroid on
the log path by construction, so it would score a meaningless zero. The yardstick is
instead `gaze_vs_asteroid.csv`: separation in degrees from raw pixels and intrinsics
alone.

| | method 2 | method 3 | method 4 |
|---|---|---|---|
| anisotropy of `J` (must be ~1.0) | — | 1.134 med, **20/42 bad** | 1.027 med, **0/42** |
| calibration-bias spread, x (sd / range) | 3.31 / 18.76 | 3.20 / 14.62 | **1.10 / 5.17** |
| units-per-degree consistency (CV) | 35.4 % | 37.6 % | **5.9 %** |
| clock error 200 ms → separation change | 4.24 | 1.298 | **0.001** |
| Spearman(sep, ContactPct) | −0.024 | −0.123 | **−0.328** |

The bias test is the non-circular one: Sarah's calibration offset is a property of
her eyes and how the glasses sat, so it must be near-constant across an 18-minute
session. Method 4 recovers it 3× tighter. Test C is fully independent — `ContactPct`
comes from the game and never touched any mapping — and methods 2 and 3 have
*destroyed* that signal, while method 4 slightly exceeds the raw-degree yardstick
(−0.273), as it should, since game units account for viewing-distance changes that
degrees do not.

Clock sensitivity collapses to ~0.001 units because `J` never touches the offset at
all. Method 3's residual sensitivity was entirely "refitting `H` at a wrong offset
changes `J`"; method 4 removes that term structurally.

**Epoch 33.** The motivating case. Its tracking was never bad — in the raw-degree
reference it measures **2.99°**, indistinguishable from clean epochs 31 (2.98°) and
36 (2.73°). Sarah yaws −19.7° during it (the session's largest head motion, confirmed
against `imu.csv`, which no method uses), which cancels most of the asteroid's
apparent horizontal motion: the track sweeps 85 px of x where a still head gives
~205 px. Method 3 reported 23.46 game units of separation; method 4 gives **7.95**.

**Limits.** Inherits method 3's: `J` is a local linearisation, and gaze is
reconstructed relative to the target. Adds a dependency on method 1's `corners.csv`,
so it cannot run on a recording with no annotated rests.

---

## Which to use

- **Method 4** for tracking-error plots.
- **Method 2** for stimulus-independent screen positions, and for the headline
  degrees-off-target number.
- **Methods 1 and 3** are retired; kept for comparison. Method 3's figures should not
  be read on the epochs where its `J` is anisotropic — see `jacobian_report.csv`.

The headline results come from the clock-free pixel measurement all four share, so
they do not depend on this choice.
