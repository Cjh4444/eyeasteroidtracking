# Method 4 on any session

`lb.py` runs method 4 (the rest-corner Jacobian, see [`../method4/FLOW.md`](../method4/FLOW.md)
for the method itself) on any Lunar Blast + Neon recording. The stage scripts are
method 4's scripts. The only differences are where they find their data, plus three
fixes listed at the bottom. `../method4/` is left exactly as it was.

## New session: three commands

From `analysis/pipeline/`:

```bash
PY=../../.venv/bin/python

$PY lb.py init pilot_3170 --matlab ../../LB_pilot_3170 --neon ../../eyetracking_3170
$PY lb.py run  pilot_3170      # stops at each interactive stage and prints the command
                               # to run; after you finish it, run this again
```

`run` is safe to repeat. It does every automatic stage it can, then stops at the
first unfinished interactive stage:

| stage | what | how |
|---|---|---|
| prep | `build_timeline.py` + `sync.py`, for both interactive stages | automatic |
| corners | click the gray rest rectangle's corners, every rest | **`lb.py corners <name>`** |
| track | supervised asteroid tracking, every epoch | **`lb.py track <name>`** (`--epochs 3,7` to redo some) |
| fit | `fit_from_track` → `refit_offsets` → `fit_hybrid2` → `plot` | automatic |

Both interactive tools save after every accept and resume where you left off. If you
skipped rests or epochs on purpose, `run --partial` fits with what's there.

Figures land in `sessions/<name>/out_hybrid2/figures/`. **Check
`sessions/<name>/out_hybrid2/jacobian_report.csv` first:** epochs with `ok=False`
were rejected as anisotropic.

Other commands: `lb.py list`, `status <name>`, `check <name>`, `score <name>` (runs
`compare_differential.py`, scoring methods 2/3/4 on that session).

## What `init` finds, and what it checks

`init` finds the inputs by pattern, so it needs no filenames:

- MATLAB folder: `*_epoch_NNN_waveFreq_*.csv`, `*_epoch_summary.csv`, `USED_*lunar_config*.csv`
  (the `_2` suffix is fine). It refuses to continue if epoch files from two runs are mixed.
- Neon folder: the export itself or any parent of it. Pupil Cloud downloads nest it
  one level down. It looks for `gaze.csv` and the single `.mp4` next to it.

Everything it found is written to `sessions/<name>/session.json`. That is the only
place a session's paths live, and you can edit it by hand.

It then checks the data. It **FAILs** on:

- missing columns
- epochs without a rest duration in the config
- epoch starts that fall outside the Neon recording. That means the MATLAB PC's
  timezone is wrong: it prints the UTC offset the data implies. Set `"timezone"` in
  session.json or pass `--tz`.

It **warns** on:

- a video frame count that doesn't match `world_timestamps.csv`
- a missing video (only the interactive stages need it)
- waveFreq 1.0 epochs, which can't be clock-solved by shape

## Layout

```
lb.py            the driver
session.py       discovery, validation, LB_SESSION -> paths
common.py        method 4's, now reading paths from the session
*.py             method 4's stage scripts
sessions/<name>/
  session.json
  out_frame_annotate_method/corners.csv   interactive; in git, can't be regenerated
  out_track/asteroid_track.csv            interactive; in git, can't be regenerated
  out_hybrid2/                            fit + figures (regenerable, ignored)
  offsets_refit.csv                       clock model (regenerable, ignored)
```

## Verified against method 4

`sessions/sarah_20260818` was created with `--reuse-annotations ../method4`, which
copies her `corners.csv` and `asteroid_track.csv`. On it, this pipeline reproduces
method 4 **exactly**: max abs difference 0.0 in `timeline.csv`, `rest_windows.csv`,
`track_mapping_report.csv`, `offsets_refit.csv`, `jacobian_report.csv` and all
110,257 rows of `gaze_mapped.csv`. The global clock model comes out at the documented
−1049.9 ms / +602.0 ppm / +29.8 ms.

## Differences from `../method4/`

1. **Plot alignment matches the mapping.** Method 4's `plot.py` always aligned gaze
   using method 2's per-epoch offsets, even when `LB_OFFSETS=offsets_refit.csv` had
   mapped with the re-solved ones. That misaligned the asteroid half of the
   reconstruction by up to ~110 ms (epoch 33). `fit_hybrid2.py` now writes
   `offsets_used.csv` and `plot.py` reads it.
2. **Re-solved offsets are the default** (`--offsets refit`), as FLOW.md recommends.
   If the session can't support a clock model, it falls back to method 2's offsets
   and says so. `--offsets method2` gives method 4's old default.
3. **Contact sheets size to the session** (6 columns × as many rows as needed), where
   they used to be a fixed 7×6.

## Notes on pilot 3170

- **Some "bad" epochs reflect behaviour, not the mapping.** The raw gaze-to-asteroid
  error in `out_track/gaze_vs_asteroid.csv` uses no mapping and no clock. It's ~2–3°
  median on quiet epochs, but 12° on 24, 9° on 19, 8° on 21 and 5° on 16. No change
  to the mapping can move gaze that wasn't on the target.
- **Epoch 19** is one 11 s fixation (2.1–13.3 s, per `fixations.csv`) at the
  bottom-centre of the screen, where the asteroid bottoms out each cycle. Three
  independent mappings agree on the spot, so the flat line near y ≈ 0 in its figure
  is accurate. She waited at the turning point instead of pursuing, then started
  following at ~13 s.
- **Head roll during a trial** is the one head motion method 4 can't see (FLOW.md,
  "Limits"). It rotates the *direction* of the reconstructed error, not its size. On
  3170 the rests either side of a trial differ by up to 20° (epoch 23). Read the
  per-axis error on high-roll epochs with that in mind.
