"""Stage 3 -- annotate the gray rest rectangle and its fixation cross.

During every rest period the game draws a gray fill spanning exactly game
[0,100]x[0,65] (Lunar_Blast_v4.m:439), with the white fixation cross at (50,32.5)
on top of it (:441). Clicking that rectangle's corners gives a homography from
scene-camera pixels straight to game units -- no screen size, resolution,
viewing distance or MATLAB window geometry required, because all of those cancel.

The cross is clicked too. Corner clicks are never perfectly exact, and the cross
is a direct, independent observation of where game (50,32.5) lands in the scene
image, so it is worth recording alongside the quad.

Run:  .venv/bin/python analysis/annotate_corners.py

Corner order is by ON-SCREEN position: lower-left, lower-right, upper-right,
upper-left. (Game y is up and image y is down; the flip is handled in code.)

Head motion between consecutive rests is mostly a small translation, so after
the first annotated rest the previous accepted shape is propagated onto the new
frame. The usual interaction is then two actions: drag the red cross onto the
white cross (the whole quad follows rigidly), then press 'a'. The previous,
un-nudged quad stays on screen as a dim dashed outline so the shift is visible,
with the (dx,dy) printed in the HUD.

Keys
  drag red cross   move the whole shape rigidly (corners keep their order)
  drag a corner    fine-tune one corner (scale / rotation / lean changes)
  left click       from-scratch mode: place next corner, then the cross
  u                undo the last placed point (from-scratch mode)
  r                restart this frame from scratch (discard the shape)
  p                re-propagate the previous accepted shape onto this frame
  c                clear the cross; the next left click re-places it
  a / enter        accept and save (needs 4 corners; cross optional)
  s                skip this rest
  . / ,            step one video frame forward / back within the rest window
  j / k            jump to next / previous rest without saving
  q / esc          save and quit

Progress is appended to out/corners.csv after every accept, so quitting midway
loses nothing and re-running resumes where you left off.
"""
import sys

import cv2
import numpy as np
import pandas as pd

from common import (CORNER_GAME_XY, CORNER_SCREEN_NAMES, CROSS_XY, GAME_XLIM,
                    GAME_YLIM, OUT, SCENE_VIDEO)

CORNERS_CSV = OUT / "corners.csv"
WIN = "annotate corners  |  drag the red cross, tweak corners, 'a' to accept"

DISPLAY_SCALE = 0.75      # 1600x1200 -> 1200x900
MAG_SIZE = 180            # magnifier inset, display px
MAG_ZOOM = 6

DOT_R = 5                 # drawn corner dot radius, display px
HIT_R = 16                # grab radius, display px -- deliberately > DOT_R

DOT_COLORS = [(80, 200, 255), (80, 255, 140), (255, 160, 80), (200, 120, 255)]
CROSS_COLOR = (0, 0, 255)        # observed cross handle: the thing you drag
PRED_COLOR = (0, 220, 255)       # cross predicted by the homography (a check)
PREV_COLOR = (130, 130, 130)     # previous, un-nudged quad

CSV_COLUMNS = (["rest_id", "after_epoch", "before_epoch", "ts_ns", "frame_idx"]
               + [f"{a}{n}" for n in range(4) for a in ("x", "y")]
               + ["cross_x", "cross_y"])


# --- geometry -----------------------------------------------------------------
# Everything below works in ORIGINAL frame pixels unless a name says "disp".
# The display is only a scaled view; nothing scaled is ever saved.

def to_disp(pt, scale):
    """Original frame px -> display px."""
    return (pt[0] * scale, pt[1] * scale)


def to_frame(pt, scale):
    """Display px -> original frame px."""
    return (pt[0] / scale, pt[1] / scale)


def translate_pts(pts, dx, dy):
    """Rigid shift of a point list. Order is preserved, which matters a lot."""
    return [(x + dx, y + dy) for x, y in pts]


def nearest_handle(pts, cross, pt, radius=HIT_R):
    """Which handle is under `pt`? -> ("cross", -1) | ("corner", i) | (None, -1).

    The cross wins ties: it is the handle grabbed on nearly every frame, and it
    sits in the middle of the quad where no corner should be anyway.
    """
    best_kind, best_i, best_d2 = None, -1, radius * radius
    if cross is not None:
        d2 = (cross[0] - pt[0]) ** 2 + (cross[1] - pt[1]) ** 2
        if d2 <= best_d2:
            best_kind, best_i, best_d2 = "cross", -1, d2
    for i, (x, y) in enumerate(pts):
        d2 = (x - pt[0]) ** 2 + (y - pt[1]) ** 2
        if d2 < best_d2:
            best_kind, best_i, best_d2 = "corner", i, d2
    return best_kind, best_i


def homography_from_clicks(pts_img):
    src = np.array(pts_img, dtype=np.float32)
    dst = np.array(CORNER_GAME_XY, dtype=np.float32)
    return cv2.getPerspectiveTransform(src, dst)


def game_to_img(H, pts_game):
    """Map game coords -> image px using the inverse of H."""
    Hinv = np.linalg.inv(H)
    pts = np.array(pts_game, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, Hinv).reshape(-1, 2)


# --- drawing ------------------------------------------------------------------

def draw_dashed_poly(disp, pts_disp, color, dash=9, gap=7):
    """Closed dashed outline -- used for the previous quad, so it reads as 'old'."""
    n = len(pts_disp)
    for i in range(n):
        p = np.array(pts_disp[i], dtype=np.float64)
        q = np.array(pts_disp[(i + 1) % n], dtype=np.float64)
        seg = np.linalg.norm(q - p)
        if seg < 1e-6:
            continue
        u = (q - p) / seg
        t = 0.0
        while t < seg:
            a = p + u * t
            b = p + u * min(t + dash, seg)
            cv2.line(disp, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])),
                     color, 1, cv2.LINE_AA)
            t += dash + gap


def draw_prev_quad(disp, prev_pts, scale):
    """The propagated shape before the user nudged it, as a dim dashed box."""
    if not prev_pts:
        return
    draw_dashed_poly(disp, [to_disp(p, scale) for p in prev_pts], PREV_COLOR)


def draw_overlay(disp, pts, scale):
    """Once 4 corners are down, reproject a game grid so bad clicks are obvious."""
    if len(pts) < 4:
        return
    H = homography_from_clicks(pts)

    def grid_disp(pts_game):
        return (game_to_img(H, pts_game) * scale).astype(np.int32)

    # Grid every 10 game units in x, 6.5 in y.
    for gx in np.linspace(*GAME_XLIM, 11):
        line = grid_disp([(gx, GAME_YLIM[0]), (gx, GAME_YLIM[1])])
        cv2.line(disp, tuple(line[0]), tuple(line[1]), (60, 90, 60), 1, cv2.LINE_AA)
    for gy in np.linspace(*GAME_YLIM, 11):
        line = grid_disp([(GAME_XLIM[0], gy), (GAME_XLIM[1], gy)])
        cv2.line(disp, tuple(line[0]), tuple(line[1]), (60, 90, 60), 1, cv2.LINE_AA)

    # Outline.
    cv2.polylines(disp, [grid_disp(CORNER_GAME_XY)], True, (0, 230, 0), 2, cv2.LINE_AA)

    # Where the corner homography *predicts* the fixation cross lands. If it
    # drifts off the red handle, a corner needs tweaking.
    (px, py) = grid_disp([CROSS_XY])[0]
    cv2.drawMarker(disp, (int(px), int(py)), PRED_COLOR,
                   cv2.MARKER_TILTED_CROSS, 26, 1, cv2.LINE_AA)


def draw_handles(disp, pts, cross, scale, hover):
    """Corner dots and the draggable red cross. Hovered handle is fattened."""
    hkind, hidx = hover
    for n, p in enumerate(pts):
        x, y = (int(v) for v in to_disp(p, scale))
        hot = (hkind == "corner" and hidx == n)
        cv2.circle(disp, (x, y), DOT_R + (3 if hot else 0), DOT_COLORS[n], -1, cv2.LINE_AA)
        cv2.circle(disp, (x, y), HIT_R if hot else DOT_R + 4,
                   (255, 255, 255) if hot else (20, 20, 20), 1, cv2.LINE_AA)

    if cross is not None:
        x, y = (int(v) for v in to_disp(cross, scale))
        hot = (hkind == "cross")
        cv2.drawMarker(disp, (x, y), CROSS_COLOR, cv2.MARKER_CROSS,
                       40 if hot else 34, 3 if hot else 2, cv2.LINE_AA)
        cv2.circle(disp, (x, y), HIT_R if hot else 20, CROSS_COLOR,
                   2 if hot else 1, cv2.LINE_AA)


def draw_magnifier(disp, frame, cursor, scale):
    """Zoomed inset under the cursor, so corner clicks can be pixel-accurate."""
    if cursor is None:
        return
    mx, my = cursor
    h, w = disp.shape[:2]
    half = MAG_SIZE // (2 * MAG_ZOOM)
    fx, fy = int(mx / scale), int(my / scale)
    fh, fw = frame.shape[:2]
    x0, y0 = np.clip([fx - half, fy - half], 0, [fw - 2 * half, fh - 2 * half])
    patch = frame[y0:y0 + 2 * half, x0:x0 + 2 * half]
    if patch.size == 0:
        return
    mag = cv2.resize(patch, (MAG_SIZE, MAG_SIZE), interpolation=cv2.INTER_NEAREST)
    c = MAG_SIZE // 2
    cv2.line(mag, (c, 0), (c, MAG_SIZE), (0, 255, 255), 1)
    cv2.line(mag, (0, c), (MAG_SIZE, c), (0, 255, 255), 1)
    cv2.rectangle(mag, (0, 0), (MAG_SIZE - 1, MAG_SIZE - 1), (255, 255, 255), 1)

    # Park the inset in whichever corner the cursor is furthest from.
    px = w - MAG_SIZE - 10 if mx < w // 2 else 10
    py = 10 if my > h // 2 else h - MAG_SIZE - 10
    disp[py:py + MAG_SIZE, px:px + MAG_SIZE] = mag


def shift_text(pts, prev_pts):
    """(dx,dy) of the current quad relative to the propagated one, frame px."""
    if not prev_pts or len(pts) != len(prev_pts):
        return ""
    dx = float(np.mean([p[0] - q[0] for p, q in zip(pts, prev_pts)]))
    dy = float(np.mean([p[1] - q[1] for p, q in zip(pts, prev_pts)]))
    return f"shift dx={dx:+.1f} dy={dy:+.1f} px"


def draw_hud(disp, rest, i, total, pts, cross, prev_pts, frame_idx, msg):
    h, w = disp.shape[:2]
    bar = disp[:96].copy()
    disp[:96] = cv2.addWeighted(bar, 0.25, np.zeros_like(bar), 0.75, 0)

    line1 = (f"rest {rest.rest_id}  ({i+1}/{total})   "
             f"between epoch {rest.after_epoch} and {rest.before_epoch}   "
             f"rest {rest.rest_dur_s:.0f}s   frame {frame_idx}   t={rest.video_t_s:.1f}s")
    if len(pts) < 4:
        line2 = f"click the {CORNER_SCREEN_NAMES[len(pts)]} corner of the gray rectangle"
        color = (80, 220, 255)
    elif cross is None:
        line2 = "click the white fixation cross at the centre"
        color = (80, 220, 255)
    else:
        line2 = "drag the red cross onto the white one, tweak corners -> 'a' accept"
        color = (120, 255, 120)

    cv2.putText(disp, line1, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1, cv2.LINE_AA)
    cv2.putText(disp, line2, (12, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

    note = "   ".join(t for t in (msg, shift_text(pts, prev_pts)) if t)
    if note:
        cv2.putText(disp, note, (12, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 255), 1, cv2.LINE_AA)

    cv2.putText(disp, "drag cross=move all | drag corner=tweak | a accept | u undo | "
                      "r scratch | p re-propagate | c clear cross | s skip | . , frame | "
                      "j k rest | q save+quit",
                (12, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (170, 170, 170), 1, cv2.LINE_AA)


# --- io -----------------------------------------------------------------------

def load_done():
    """Resume from a partial file, tolerating one written before cross_x existed."""
    if CORNERS_CSV.exists():
        done = pd.read_csv(CORNERS_CSV)
        for col in ("cross_x", "cross_y"):
            if col not in done.columns:
                done[col] = np.nan
        return done
    return pd.DataFrame(columns=CSV_COLUMNS)


def make_row(rest, frame_idx, frame_ts_ns, pts, cross):
    """One corners.csv row. Coordinates are original frame px; cross may be None."""
    row = dict(rest_id=int(rest.rest_id), after_epoch=int(rest.after_epoch),
               before_epoch=int(rest.before_epoch), ts_ns=int(frame_ts_ns),
               frame_idx=int(frame_idx))
    for n, (x, y) in enumerate(pts):
        row[f"x{n}"], row[f"y{n}"] = round(float(x), 2), round(float(y), 2)
    row["cross_x"] = round(float(cross[0]), 2) if cross is not None else np.nan
    row["cross_y"] = round(float(cross[1]), 2) if cross is not None else np.nan
    return row


def save_row(done, rest, frame_idx, frame_ts_ns, pts, cross):
    row = make_row(rest, frame_idx, frame_ts_ns, pts, cross)
    done = done[done["rest_id"] != row["rest_id"]]
    done = pd.concat([done, pd.DataFrame([row])], ignore_index=True)
    done = done.sort_values("rest_id").reset_index(drop=True)
    done = done.reindex(columns=CSV_COLUMNS)
    done.to_csv(CORNERS_CSV, index=False)
    return done


def last_annotation(done):
    """Most recently annotated shape (pts, cross) in frame px, for propagation."""
    if done is None or len(done) == 0:
        return None
    row = done.sort_values("rest_id").iloc[-1]
    pts = [(float(row[f"x{n}"]), float(row[f"y{n}"])) for n in range(4)]
    cx, cy = row.get("cross_x", np.nan), row.get("cross_y", np.nan)
    cross = None if pd.isna(cx) or pd.isna(cy) else (float(cx), float(cy))
    return pts, cross


# --- main loop ----------------------------------------------------------------

def main() -> None:
    if not SCENE_VIDEO.exists():
        sys.exit(f"scene video not found: {SCENE_VIDEO}")
    rests_path = OUT / "rest_windows.csv"
    if not rests_path.exists():
        sys.exit("run build_timeline.py first")

    rests = pd.read_csv(rests_path)
    done = load_done()
    cap = cv2.VideoCapture(str(SCENE_VIDEO))
    if not cap.isOpened():
        sys.exit("could not open scene video")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    todo = [i for i, r in rests.iterrows()
            if int(r.rest_id) not in set(done["rest_id"].astype(int))]
    if not todo:
        print(f"all {len(rests)} rests already annotated in {CORNERS_CSV}")
        return
    print(f"{len(done)} already done, {len(todo)} to go")

    # pts/cross/prev_pts are all in ORIGINAL frame px. prev_pts is the shape as
    # propagated in, kept so the dashed reference box and the dx,dy readout have
    # something to compare against.
    state = dict(pts=[], cross=None, prev_pts=[], cursor=None,
                 drag=(None, -1), hover=(None, -1), msg="")
    prev_accepted = last_annotation(done)

    def propagate():
        """Drop the previous accepted shape onto this frame, ready to be nudged."""
        if prev_accepted is None:
            return False
        pts, cross = prev_accepted
        state["pts"] = list(pts)
        state["cross"] = cross
        state["prev_pts"] = list(pts)
        state["drag"] = (None, -1)
        return True

    def clear_shape():
        state.update(pts=[], cross=None, prev_pts=[], drag=(None, -1))

    def on_mouse(event, x, y, flags, _):
        state["cursor"] = (x, y)
        pt = to_frame((x, y), DISPLAY_SCALE)
        pts, cross = state["pts"], state["cross"]

        if event == cv2.EVENT_LBUTTONDOWN:
            if len(pts) < 4:                     # from-scratch: corners first
                pts.append(pt)
            elif cross is None:                  # then the fixation cross
                state["cross"] = pt
            else:
                state["drag"] = nearest_handle(
                    [to_disp(p, DISPLAY_SCALE) for p in pts],
                    to_disp(cross, DISPLAY_SCALE), (x, y))
        elif event == cv2.EVENT_MOUSEMOVE:
            kind, idx = state["drag"]
            if kind == "cross":
                # Rigid: the quad follows the cross by the same delta, so the
                # 0..3 corner order can never change.
                dx, dy = pt[0] - cross[0], pt[1] - cross[1]
                state["pts"] = translate_pts(pts, dx, dy)
                state["cross"] = pt
            elif kind == "corner":
                pts[idx] = pt
            elif len(pts) == 4 and cross is not None:
                state["hover"] = nearest_handle(
                    [to_disp(p, DISPLAY_SCALE) for p in pts],
                    to_disp(cross, DISPLAY_SCALE), (x, y))
        elif event == cv2.EVENT_LBUTTONUP:
            state["drag"] = (None, -1)

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)

    pos = 0
    frame_offset = 0
    frame = None
    need_read = True
    new_rest = True           # shape survives frame stepping, resets per rest

    while 0 <= pos < len(todo):
        rest = rests.iloc[todo[pos]]
        base_idx = int(rest.frame_idx)
        frame_idx = base_idx + frame_offset

        if need_read:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                state["msg"] = f"could not read frame {frame_idx}"
                frame_offset = 0
                need_read = False
                continue
            need_read = False
            if new_rest:
                new_rest = False
                clear_shape()
                if propagate():
                    state["msg"] = "propagated from previous rest"

        disp = cv2.resize(frame, None, fx=DISPLAY_SCALE, fy=DISPLAY_SCALE,
                          interpolation=cv2.INTER_AREA)
        draw_prev_quad(disp, state["prev_pts"], DISPLAY_SCALE)
        draw_overlay(disp, state["pts"], DISPLAY_SCALE)
        draw_handles(disp, state["pts"], state["cross"], DISPLAY_SCALE, state["hover"])
        draw_magnifier(disp, frame, state["cursor"], DISPLAY_SCALE)
        frame_ts = rest.frame_ts_ns + int(frame_offset / fps * 1e9)
        draw_hud(disp, rest, pos, len(todo), state["pts"], state["cross"],
                 state["prev_pts"], frame_idx,
                 state["msg"] + (f"   [{frame_offset:+d} frames]" if frame_offset else ""))
        cv2.imshow(WIN, disp)

        key = cv2.waitKey(20) & 0xFF
        if key in (ord("q"), 27):
            break
        elif key == ord("u"):
            # Undo peels back the cross first, then corners.
            if state["cross"] is not None and len(state["pts"]) == 4:
                state["cross"] = None
            elif state["pts"]:
                state["pts"].pop()
            state["prev_pts"] = []
        elif key == ord("r"):
            clear_shape()
            state["msg"] = "from scratch: click LL, LR, UR, UL, then the cross"
        elif key == ord("p"):
            state["msg"] = ("propagated from previous rest" if propagate()
                            else "nothing to propagate yet")
        elif key == ord("c"):
            state["cross"] = None
            state["msg"] = "click the white cross"
        elif key == ord("s"):
            pos += 1
            frame_offset = 0
            need_read = new_rest = True
        elif key in (ord("a"), 13, 10):
            if len(state["pts"]) < 4:
                state["msg"] = "place all four corners first"
            else:
                done = save_row(done, rest, frame_idx, frame_ts,
                                state["pts"], state["cross"])
                prev_accepted = (list(state["pts"]), state["cross"])
                note = "" if state["cross"] is not None else "  (no cross)"
                print(f"  rest {int(rest.rest_id):2d} saved{note}  ({len(done)}/{len(rests)})")
                pos += 1
                frame_offset = 0
                need_read = new_rest = True
                state["msg"] = ""
        elif key == ord("."):
            frame_offset += 1
            need_read = True
        elif key == ord(","):
            frame_offset -= 1
            need_read = True
        elif key == ord("j"):
            pos += 1
            frame_offset = 0
            need_read = new_rest = True
        elif key == ord("k"):
            pos = max(0, pos - 1)
            frame_offset = 0
            need_read = new_rest = True

    cap.release()
    cv2.destroyAllWindows()
    print(f"\n{len(done)}/{len(rests)} rests annotated -> {CORNERS_CSV}")


if __name__ == "__main__":
    main()
