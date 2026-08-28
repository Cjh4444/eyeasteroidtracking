"""Stage 5 -- the deliverables.

Three views per epoch, all in game coordinates:
  (a) Y overlay   Asteroid_Y(t) vs gaze vertical position   -- the sample_graph.png view
  (b) X overlay   Asteroid_X(t) vs gaze horizontal position -- doubles as a mapping check
  (c) scanpath    the [0,100]x[0,65] game area with both paths drawn on it

Both series in every overlay share one axis and one unit (game units), so there is
no second y-scale anywhere. Gaze is broken (not interpolated) across blinks and
not-worn stretches, so gaps read as missing data rather than as straight lines.
"""
import argparse
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import matplotlib.patheffects as pe
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.colors import LinearSegmentedColormap

from common import (CROSS_XY, EPOCH_SUMMARY, GAME_XLIM, GAME_YLIM, OUT)

FIGS = OUT / "figures"

# Categorical slots 1 and 2 from the reference palette; validated as a pair
# (all-pairs CVD dE 24.7 protan, normal-vision 33.6, both >= 3:1 on surface).
C_AST = "#2a78d6"    # slot 1, blue   -- the stimulus
C_GAZE = "#eb6834"   # slot 2, orange -- where she actually looked
C_SURFACE = "#fcfcfb"
C_INK = "#0b0b0b"
C_INK2 = "#52514e"
C_GRID = "#e3e2df"

# Sequential ramp for scanpath time: ONE hue, light -> dark, and it is the GAZE
# hue -- colour follows the entity, so gaze reads orange in every view and the
# blue asteroid path stays legible where the scanpath crosses it.
CMAP_TIME = LinearSegmentedColormap.from_list("oranges1", ["#f7d3c0", "#eb6834", "#8c3208"])


def style():
    plt.rcParams.update({
        "figure.facecolor": C_SURFACE, "axes.facecolor": C_SURFACE,
        "savefig.facecolor": C_SURFACE,
        "axes.edgecolor": C_GRID, "axes.linewidth": 0.8,
        "axes.labelcolor": C_INK2, "text.color": C_INK,
        "xtick.color": C_INK2, "ytick.color": C_INK2,
        "xtick.labelsize": 8, "ytick.labelsize": 8,
        "axes.labelsize": 9, "axes.titlesize": 10,
        "grid.color": C_GRID, "grid.linewidth": 0.7,
        "legend.frameon": False, "legend.fontsize": 8,
        "font.size": 9,
    })


def recessive(ax):
    ax.grid(True, axis="y", alpha=0.9)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def smooth(v, k=5):
    """Light median filter (25 ms at 200 Hz) -- kills sensor jitter without
    blunting real saccades, which we want to stay visible."""
    v = np.asarray(v, dtype=float)
    if k < 2 or len(v) < k:
        return v
    w = np.lib.stride_tricks.sliding_window_view(np.pad(v, k // 2, mode="edge"), k)
    return np.nanmedian(w, axis=1)


def break_invalid(t, v, valid, max_gap_s=0.15):
    """NaN out invalid samples and any bridge across a real time gap."""
    v = np.asarray(v, dtype=float).copy()
    v[~np.asarray(valid)] = np.nan
    gap = np.diff(t, prepend=t[0]) > max_gap_s
    v[gap] = np.nan
    return v


def epoch_slice(gaze, t0_ns, t1_ns, offset_s=0.0):
    """Gaze samples for one epoch, on the STIMULUS time base.

    Gaze is timestamped by the Neon clock and the stimulus log by the MATLAB
    clock, and the two drift apart by ~15 ms per epoch (-1050 ms at the start of
    the session to -400 ms at the end). Plotting one against the other without
    converting makes the clock offset look like eye-movement lag -- and it swamps
    it, since the real pursuit lead is ~200 ms.
    """
    off = int(offset_s * 1e9)
    lo, hi = t0_ns - off, t1_ns - off
    m = gaze[(gaze["ts_ns"] >= lo) & (gaze["ts_ns"] <= hi)].copy()
    m["t"] = (m["ts_ns"] + off - t0_ns) / 1e9
    return m


def panel_overlay(ax, ep, stim, g, axis, compact=False):
    """One time-series overlay. axis is 'y' or 'x'."""
    col = "Asteroid_Y" if axis == "y" else "Asteroid_X"
    gcol = "game_y" if axis == "y" else "game_x"
    lim = GAME_YLIM if axis == "y" else GAME_XLIM

    ax.plot(stim["t"], stim[col], color=C_AST, lw=3.0, solid_capstyle="round",
            label="asteroid", zorder=2)
    if len(g):
        ax.plot(g["t"], break_invalid(g["t"].to_numpy(), smooth(g[gcol]), g["valid"]),
                color=C_GAZE, lw=1.1, solid_capstyle="round", label="gaze", zorder=3)
    ax.set_xlim(0, max(stim["t"].max(), 14))
    ax.set_ylim(lim[0] - 2, lim[1] + 2)
    recessive(ax)
    if not compact:
        ax.set_xlabel("time in trial (s)")
        ax.set_ylabel(f"{'vertical' if axis=='y' else 'horizontal'} position (game units)")
        ax.legend(loc="upper right", ncols=2)


def panel_scan(ax, ep, stim, g, compact=False):
    """The game area seen from the front, with both paths on it."""
    ax.add_patch(plt.Rectangle((GAME_XLIM[0], GAME_YLIM[0]),
                               GAME_XLIM[1], GAME_YLIM[1],
                               facecolor="#f3f2ef", edgecolor=C_GRID, lw=1.0, zorder=0))
    v = g[g["valid"]] if len(g) else g
    if len(v) > 1:
        pts = np.c_[v["game_x"], v["game_y"]].reshape(-1, 1, 2)
        segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
        # Drop segments that bridge a blink -- they are not real eye movements.
        dt = np.diff(v["t"].to_numpy())
        keep = dt < 0.15
        lc = LineCollection(segs[keep], cmap=CMAP_TIME, lw=1.0, alpha=0.9, zorder=2)
        lc.set_array(v["t"].to_numpy()[:-1][keep])
        ax.add_collection(lc)

    # Asteroid drawn last with a surface-coloured halo, so the reference path
    # stays readable wherever the scanpath crosses it.
    ax.plot(stim["Asteroid_X"], stim["Asteroid_Y"], color=C_AST, lw=2.4,
            solid_capstyle="round", label="asteroid path", zorder=4,
            path_effects=[pe.Stroke(linewidth=4.8, foreground=C_SURFACE), pe.Normal()])

    ax.plot(*CROSS_XY, marker="+", color=C_INK2, ms=9, mew=1.2, zorder=4)
    ax.set_xlim(GAME_XLIM[0] - 3, GAME_XLIM[1] + 3)
    ax.set_ylim(GAME_YLIM[0] - 3, GAME_YLIM[1] + 3)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    if not compact:
        ax.set_xlabel("gaze shaded light \u2192 dark over the 14 s trial")
        ax.legend(handles=[Line2D([], [], color=C_AST, lw=2.4, label="asteroid path"),
                           Line2D([], [], color=C_GAZE, lw=1.6, label="gaze")],
                  loc="upper left", ncols=1, borderpad=0.9, labelspacing=0.6)


def title_for(ep, meta, compact=False):
    s = f"epoch {ep}  ·  {meta['cond']}  ·  waveFreq {meta['freq']:.1f}"
    if not compact and meta.get("contact_pct") is not None:
        s += f"  ·  laser contact {meta['contact_pct']:.0f}%"
    if meta.get("flag"):
        s += f"   [{meta['flag']}]"
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=str, default="", help="e.g. 1,5,15 (default all)")
    args = ap.parse_args()

    style()
    FIGS.mkdir(parents=True, exist_ok=True)
    timeline = pd.read_csv(OUT / "timeline.csv")
    gaze = pd.read_csv(OUT / "gaze_mapped.csv")
    summary = pd.read_csv(EPOCH_SUMMARY).set_index("EpochIndex")
    per_sync = pd.read_csv(OUT / "sync_per_epoch.csv").set_index("epoch")

    # Per-epoch clock offset, when the method that produced this mapping solved
    # one. Method 1 has no trustworthy per-epoch value, so it plots uncorrected.
    offs = {}
    rep_path = OUT / "track_mapping_report.csv"
    if rep_path.exists():
        tr = pd.read_csv(rep_path)
        if "offset_s" in tr:
            offs = dict(zip(tr["epoch"], tr["offset_s"]))
            print(f"applying per-epoch clock offset to gaze "
                  f"({min(offs.values())*1000:+.0f}..{max(offs.values())*1000:+.0f} ms)")

    wanted = [int(x) for x in args.epochs.split(",")] if args.epochs else None
    eps = sorted(timeline["EpochIndex"].unique())
    if wanted:
        eps = [e for e in eps if e in wanted]

    panels = {"y": [], "x": [], "scan": []}
    for ep in eps:
        d = timeline[timeline["EpochIndex"] == ep].sort_values("t_utc_ns")
        t0, t1 = d["t_utc_ns"].iloc[0], d["t_utc_ns"].iloc[-1]
        stim = d.assign(t=(d["t_utc_ns"] - t0) / 1e9)
        g = epoch_slice(gaze, t0, t1, offs.get(ep, 0.0))
        meta = dict(cond=d["Condition"].iloc[0], freq=float(d["waveFreq"].iloc[0]),
                    contact_pct=float(summary.loc[ep, "ContactPct"]) if ep in summary.index else None,
                    flag=("low sync r" if per_sync.loc[ep, "r"] < 0.5 else "")
                    if ep in per_sync.index else "")
        panels["y"].append((ep, stim, g, meta))
        panels["x"].append((ep, stim, g, meta))
        panels["scan"].append((ep, stim, g, meta))

        for kind in ("y", "x", "scan"):
            fig, ax = plt.subplots(figsize=(9, 4.2) if kind != "scan" else (8.4, 6.0))
            if kind == "scan":
                panel_scan(ax, ep, stim, g)
            else:
                panel_overlay(ax, ep, stim, g, kind)
            ax.set_title(title_for(ep, meta), loc="left", color=C_INK, pad=10)
            fig.tight_layout()
            fig.savefig(FIGS / f"epoch_{ep:02d}_{kind}.png", dpi=140)
            plt.close(fig)
        print(f"  epoch {ep:2d} ok ({len(g)} gaze samples)")

    # Contact sheets: 7 rows x 6 cols across all 42.
    for kind in ("y", "x", "scan"):
        nrow, ncol = 7, 6
        fs = (22, 17) if kind == "scan" else (24, 18)
        fig, axes = plt.subplots(nrow, ncol, figsize=fs)
        for ax in axes.ravel():
            ax.axis("off")
        for i, (ep, stim, g, meta) in enumerate(panels[kind]):
            ax = axes.ravel()[i]
            ax.axis("on")
            if kind == "scan":
                panel_scan(ax, ep, stim, g, compact=True)
            else:
                panel_overlay(ax, ep, stim, g, kind, compact=True)
            ax.set_title(title_for(ep, meta, compact=True), loc="left",
                         fontsize=8, color=C_INK, pad=4)
        label = {"y": "vertical position", "x": "horizontal position",
                 "scan": "gaze scanpath over the game area"}[kind]
        fig.suptitle(f"Lunar Blast — asteroid vs gaze, {label}  (blue = asteroid, orange = gaze)",
                     fontsize=15, color=C_INK, x=0.01, ha="left")
        fig.tight_layout(rect=[0, 0, 1, 0.975])
        fig.savefig(FIGS / f"contact_sheet_{kind}.png", dpi=110)
        plt.close(fig)
        print(f"contact sheet: {kind}")

    print(f"\nwrote {len(eps)*3 + 3} figures to {FIGS}")


if __name__ == "__main__":
    main()
