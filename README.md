# Lunar Blast × Pupil Neon gaze analysis

Where participants looked versus where the asteroid was, per epoch, from Pupil Labs
Neon recordings of the Lunar Blast task.

## Quick start

```bash
python3 -m venv .venv                        # must be at the repo root, named .venv
.venv/bin/pip install -r requirements.txt

cd analysis
../.venv/bin/python lb.py list               # sessions and their progress
../.venv/bin/python lb.py fit pilot_3170     # rebuild a session's figures
```

Figures land in `analysis/sessions/<name>/out_hybrid2/figures/`.

## A new recording

```bash
cd analysis
../.venv/bin/python lb.py init <name> --matlab ../data/<matlab folder> --neon ../data/<neon export>
../.venv/bin/python lb.py run  <name>        # repeat after each interactive stage
```

`init` finds the files and checks the data. `run` walks you through the two
interactive stages (rest-corner annotation, supervised asteroid tracking) and then
fits and plots. Details: [`analysis/README.md`](analysis/README.md).

## Layout

| Path | What |
|---|---|
| `analysis/` | The pipeline (`lb.py` + stage scripts) and `sessions/<name>/`: each session's config, hand annotations and outputs |
| `data/` | Raw recordings, one MATLAB folder + one Neon export per session. The large files (scene video, `imu.csv`, `3d_eye_states.csv`) are not in git |
| `docs/METHOD.md` | How the gaze mapping works and why (method 4) |
| `docs/NOTES.md` | Key facts about the data, traps, results, next steps |
| `docs/HISTORY.md` | Development log of the four methods tried |
| `docs/Lunar_Blast_v4.m` | Stimulus program the geometry constants were read from. An older version than the one used for pilot 3170 |
| `docs/sample_graph.png` | The target figure format |

Sessions: `sarah_20260818` (42 epochs) and `pilot_3170` (26 epochs, with LSL trial markers).
