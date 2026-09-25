## Quick start — method 4 (the best one)

```bash
python3 -m venv .venv                        # must be at the repo root, named .venv
.venv/bin/pip install -r requirements.txt
cd analysis/method4 && ./run.sh
```

figures stored in analysis/method4/out_hybrid2/figures

## Any other session — `analysis/pipeline/`

```bash
cd analysis/pipeline
../../.venv/bin/python lb.py init <name> --matlab ../../<matlab folder> --neon ../../<neon export>
../../.venv/bin/python lb.py run <name>     # repeat after each interactive stage
```

Method 4, generalized: it finds the files itself, checks the data, and walks you
through the two interactive stages. See [`analysis/pipeline/README.md`](analysis/pipeline/README.md).
