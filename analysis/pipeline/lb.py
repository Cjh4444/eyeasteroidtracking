#!/usr/bin/env python3
"""One driver for the whole method-4 analysis, on any Lunar Blast + Neon session.

    python lb.py init <name> --matlab <dir> --neon <dir>   register a session, check it
    python lb.py run  <name>                               do the next thing that needs doing

`run` is safe to repeat. It does every automatic stage whose inputs exist, and
stops at the first interactive stage that is not finished, telling you the command
to run. Finish that stage, run `run` again, and it carries on.

The stages, in order:

    prep      build_timeline + sync, for both interactive stages       automatic
    corners   click the rest rectangle's corners at every rest         INTERACTIVE
    track     supervised asteroid tracking, every epoch                INTERACTIVE
    fit       fit_from_track -> refit_offsets -> fit_hybrid2 -> plot   automatic

Other commands: `list`, `check <name>`, `status <name>`, and `score <name>`
(compare_differential.py: methods 2/3/4 on this session). `corners`, `track`,
`prep` and `fit` also run on their own.

Every stage script is the method-4 script, pointed at the session through
LB_SESSION and LB_OUT -- nothing in them knows which session they are on.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import session as S

HERE = Path(__file__).resolve().parent
M1, M2, M4 = "out_frame_annotate_method", "out_track", "out_hybrid2"


def stage(sess: S.Session, script: str, out: str, *args: str, env: dict | None = None) -> None:
    e = dict(os.environ, LB_SESSION=sess.name, LB_OUT=out, **(env or {}))
    print(f"\n── {script} {' '.join(args)}   [{sess.name} → {out}]", flush=True)
    r = subprocess.run([sys.executable, str(HERE / script), *args], env=e, cwd=HERE)
    if r.returncode:
        sys.exit(f"!! {script} failed (exit {r.returncode})")


# --- progress ---------------------------------------------------------------------

def progress(sess: S.Session) -> dict:
    """What exists for this session, and how complete the interactive parts are."""
    import pandas as pd

    d = sess.dir
    p = dict(prep=all((d / o / f).exists() for o in (M1, M2)
                      for f in ("timeline.csv", "rest_windows.csv")))
    rests = epochs = None
    if p["prep"]:
        rests = len(pd.read_csv(d / M1 / "rest_windows.csv"))
        epochs = pd.read_csv(d / M2 / "timeline.csv", usecols=["EpochIndex"])["EpochIndex"].nunique()
    c = d / M1 / "corners.csv"
    t = d / M2 / "asteroid_track.csv"
    p["corners"] = (len(pd.read_csv(c)), rests) if c.exists() else (0, rests)
    p["track"] = ((pd.read_csv(t, usecols=["epoch"])["epoch"].nunique(), epochs)
                  if t.exists() else (0, epochs))
    p["fit"] = (d / M4 / "gaze_mapped.csv").exists()
    p["figures"] = len(list((d / M4 / "figures").glob("*.png")))
    return p


def show_status(sess: S.Session) -> dict:
    p = progress(sess)
    mark = lambda done: "✓" if done else "·"
    c_done, c_all = p["corners"]
    t_done, t_all = p["track"]
    print(f"session {sess.name}   ({sess.dir.relative_to(S.ROOT)})")
    print(f"  {mark(p['prep'])} prep      timelines built")
    print(f"  {mark(c_all and c_done >= c_all)} corners   {c_done}/{c_all or '?'} rests annotated")
    print(f"  {mark(t_all and t_done >= t_all)} track     {t_done}/{t_all or '?'} epochs tracked")
    print(f"  {mark(p['fit'])} fit       {p['figures']} figures in {M4}/figures/")
    return p


# --- commands ---------------------------------------------------------------------

def cmd_init(a) -> None:
    dst = S.SESSIONS / a.name
    if (dst / "session.json").exists() and not a.force:
        sys.exit(f"session '{a.name}' already exists ({dst}); --force to rediscover its inputs "
                 f"(annotations are kept)")
    cfg = S.discover(Path(a.matlab), Path(a.neon), a.tz)
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "session.json").write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"wrote {dst / 'session.json'}:")
    for k, v in cfg.items():
        print(f"  {k:14s} {v}")

    if a.reuse_annotations:
        # For a session whose interactive work was already done in a method flow
        # folder. Copies, never moves -- the source keeps its copy.
        src = Path(a.reuse_annotations).resolve()
        for sub, f in ((M1, "corners.csv"), (M2, "asteroid_track.csv")):
            if (src / sub / f).exists():
                (dst / sub).mkdir(parents=True, exist_ok=True)
                if (dst / sub / f).exists():
                    print(f"  kept existing {sub}/{f}")
                else:
                    shutil.copy2(src / sub / f, dst / sub / f)
                    print(f"  copied {sub}/{f} from {src}")
    print()
    cmd_check(a)


def cmd_check(a) -> None:
    sess = S.load(a.name)
    res = S.validate(sess)
    for level, msg in res:
        print(f"  {level:4s}  {msg}")
    if any(l == "FAIL" for l, _ in res):
        sys.exit("\n!! fix the FAIL lines above (session.json can be edited by hand)")


def cmd_prep(a) -> None:
    sess = S.load(a.name)
    for out in (M1, M2):
        stage(sess, "build_timeline.py", out)
        stage(sess, "sync.py", out)


def cmd_corners(a) -> None:
    sess = S.load(a.name)
    if not progress(sess)["prep"]:
        cmd_prep(a)
    stage(sess, "annotate_corners.py", M1)


def cmd_track(a) -> None:
    sess = S.load(a.name)
    if not progress(sess)["prep"]:
        cmd_prep(a)
    extra = []
    if a.epochs:
        extra += ["--epochs", a.epochs]
    if a.algo:
        extra += ["--algo", a.algo]
    stage(sess, "track_live.py", M2, *extra)


def cmd_fit(a) -> None:
    sess = S.load(a.name)
    stage(sess, "fit_from_track.py", M2)
    # The re-solved clock offsets are the better input (FLOW.md, "The clock
    # offset"), but the global model needs enough cleanly-fitted epochs. If it
    # cannot be built, fall back to method 2's per-epoch solves and say so.
    env = {}
    if a.offsets == "refit":
        e = dict(os.environ, LB_SESSION=sess.name, LB_OUT=M2)
        print(f"\n── refit_offsets.py   [{sess.name}]", flush=True)
        r = subprocess.run([sys.executable, str(HERE / "refit_offsets.py")], env=e, cwd=HERE)
        if r.returncode == 0:
            env["LB_OFFSETS"] = "offsets_refit.csv"
        else:
            print("!! refit_offsets.py could not build a clock model for this session; "
                  "using method 2's per-epoch offsets instead")
    stage(sess, "fit_hybrid2.py", M4, env=env)
    stage(sess, "plot.py", M4, env=env)
    print(f"\nfigures: {(sess.dir / M4 / 'figures').relative_to(S.ROOT)}/")
    print(f"check {(sess.dir / M4 / 'jacobian_report.csv').relative_to(S.ROOT)} before reading "
          f"any figure: epochs with ok=False were rejected as anisotropic")


def cmd_run(a) -> None:
    sess = S.load(a.name)
    p = progress(sess)
    if not p["prep"]:
        cmd_check(a)
        cmd_prep(a)
        p = progress(sess)

    c_done, c_all = p["corners"]
    t_done, t_all = p["track"]
    py = os.path.relpath(sys.executable, HERE)
    if a.partial and (c_done < c_all or t_done < t_all):
        print(f"--partial: fitting with {c_done}/{c_all} rests and {t_done}/{t_all} epochs")
    if c_done < c_all and not (a.partial and c_done):
        show_status(sess)
        sys.exit(f"\nnext: annotate the rest corners ({c_done}/{c_all} done) -- interactive:\n"
                 f"  {py} lb.py corners {a.name}\n"
                 f"then run `lb.py run {a.name}` again "
                 f"(or `run --partial` to go ahead with the rests done so far)")
    if t_done < t_all and not (a.partial and t_done):
        show_status(sess)
        sys.exit(f"\nnext: track the asteroid ({t_done}/{t_all} epochs done) -- interactive:\n"
                 f"  {py} lb.py track {a.name}\n"
                 f"then run `lb.py run {a.name}` again")
    cmd_fit(a)
    print()
    show_status(sess)


def cmd_score(a) -> None:
    sess = S.load(a.name)
    e = {"LB_OFFSETS": "offsets_refit.csv"} if (sess.dir / "offsets_refit.csv").exists() else {}
    stage(sess, "compare_differential.py", M2, env=e)


def cmd_status(a) -> None:
    show_status(S.load(a.name))


def cmd_list(a) -> None:
    names = S.session_names()
    if not names:
        print("no sessions yet -- python lb.py init <name> --matlab DIR --neon DIR")
    for n in names:
        show_status(S.load(n))
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="register a session and validate its data")
    p.add_argument("name", help="short name for this session, e.g. pilot_3170")
    p.add_argument("--matlab", required=True, help="folder with the MATLAB epoch CSVs")
    p.add_argument("--neon", required=True, help="Neon raw-data export (or a parent of it)")
    p.add_argument("--tz", default=S.DEFAULT_TZ, help=f"MATLAB PC timezone (default {S.DEFAULT_TZ})")
    p.add_argument("--reuse-annotations", metavar="FLOW_DIR",
                   help="copy corners.csv / asteroid_track.csv from an existing flow folder")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    for name, fn, h in (("check", cmd_check, "validate a session's data"),
                        ("status", cmd_status, "show what is done"),
                        ("prep", cmd_prep, "build timelines (automatic)"),
                        ("corners", cmd_corners, "INTERACTIVE: annotate rest corners"),
                        ("score", cmd_score, "compare methods 2/3/4 on this session"),
                        ("run", cmd_run, "do the next thing that needs doing"),
                        ("fit", cmd_fit, "fit + figures (automatic)"),
                        ("track", cmd_track, "INTERACTIVE: supervised asteroid tracking")):
        p = sub.add_parser(name, help=h)
        p.add_argument("name")
        if name in ("run", "fit"):
            p.add_argument("--offsets", choices=("refit", "method2"), default="refit",
                           help="clock offsets for the mapping (default: refit, the "
                                "trajectory-shape solve; falls back to method2)")
        if name == "run":
            p.add_argument("--partial", action="store_true",
                           help="fit even though some rests/epochs were skipped")
        if name == "track":
            p.add_argument("--epochs", default="", help="only these, e.g. 3,7")
            p.add_argument("--algo", default="", help="starting algorithm (fast/accurate/blob)")
        p.set_defaults(fn=fn)
    sub.add_parser("list", help="all sessions and their progress").set_defaults(fn=cmd_list)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
