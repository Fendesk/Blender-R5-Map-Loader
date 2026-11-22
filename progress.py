"""Progress bar of the map import.

Blender does not redraw while an operator runs, so the import refreshes the progress itself: a bar + text in
Blender's status bar, and the EX window when it is open. Every object the import adds tags the 3D views for a
redraw, so a refresh also redraws them and evaluates the scene being built (~0.6 s on a full map): the refreshes
are spaced by 10x their own duration (at least 0.5 s), which keeps them under ~10% of the import time.
"""
import time

import bpy

STATE = {"depth": 0, "title": "", "text": "", "frac": 0.0, "span": (0.0, 1.0), "t0": 0.0, "t_draw": 0.0, "cost": 0.0}
INTERVAL = 0.5          # shortest time between two refreshes (s)
MAX_SHARE = 0.1         # a refresh waits 1 / MAX_SHARE times its own duration: at most ~10% of the import time


def active():
    return STATE["depth"] > 0


def begin(title):
    STATE["depth"] += 1
    if STATE["depth"] == 1:
        STATE.update(title=title, text="Starting", frac=0.0, span=(0.0, 1.0), t0=time.monotonic(), t_draw=0.0,
                     cost=0.0)
        _refresh(force=True)


def stage(text, start, end):
    """A part of the work, from `start` to `end` of the whole bar (0..1)."""
    if not active():
        return
    STATE["span"] = (start, end)
    STATE["text"] = text
    STATE["frac"] = max(STATE["frac"], start)
    print(f"[R5] {text}")
    _refresh(force=True)


def update(f, text=None):
    """f: 0..1 inside the current stage."""
    if not active():
        return
    s, e = STATE["span"]
    STATE["frac"] = max(STATE["frac"], s + (e - s) * min(max(f, 0.0), 1.0))
    if text is not None:
        STATE["text"] = text
    _refresh()


def end():
    if STATE["depth"] == 0:
        return
    STATE["depth"] -= 1
    if STATE["depth"] == 0:
        STATE["frac"] = 1.0
        for win in bpy.context.window_manager.windows:
            _status_text(win, None)


def label():
    el = time.monotonic() - STATE["t0"]
    return f"{STATE['text']}  ·  {100 * STATE['frac']:.0f}%  ·  {el:.0f}s"


# --------------------------------------------------------------------------
def _status_text(win, text):
    """The status bar is a global area (not in screen.areas): setting its text is what makes it redraw."""
    try:
        with bpy.context.temp_override(window=win):
            win.workspace.status_text_set(text)
    except Exception:  # noqa: BLE001
        pass


def _refresh(force=False):
    if bpy.app.background:
        return
    now = time.monotonic()
    if not force and now - STATE["t_draw"] < max(INTERVAL, STATE["cost"] / MAX_SHARE):
        return
    try:
        wm = bpy.context.window_manager
        target = None           # a region away from the 3D views, for the context of the redraw
        for win in wm.windows:
            areas = win.screen.areas
            if any(a.type == "VIEW_3D" for a in areas):
                _status_text(win, f"{STATE['title']}  ▸  {label()}")
            for area in areas:
                if area.type == "VIEW_3D":
                    continue
                for reg in area.regions:
                    if area.type == "TEXT_EDITOR" and reg.type == "WINDOW":
                        reg.tag_redraw()            # the EX window
                        target = (win, area, reg)
                    elif target is None and reg.type == "HEADER":
                        target = (win, area, reg)
        if target is not None:
            win, area, reg = target
            with bpy.context.temp_override(window=win, area=area, region=reg):
                # draws the tagged regions only (status bar, EX window), then swaps the windows
                bpy.ops.wm.redraw_timer(type="DRAW_SWAP", iterations=1)
        # every object the import adds also tags the 3D views: they are drawn too (and the scene evaluated)
        STATE["t_draw"] = time.monotonic()
        STATE["cost"] = STATE["t_draw"] - now
    except Exception as exc:  # noqa: BLE001
        print(f"[R5] progress: {exc}")


def _status_bar(self, context):
    if not active():
        return
    row = self.layout.row(align=True)
    sub = row.row(align=True)
    sub.ui_units_x = 12
    if hasattr(sub, "progress"):            # the step and the time are in the status text next to it
        sub.progress(factor=STATE["frac"], type="BAR", text=f"{100 * STATE['frac']:.0f}%")


def register():
    bpy.types.STATUSBAR_HT_header.prepend(_status_bar)


def unregister():
    try:
        bpy.types.STATUSBAR_HT_header.remove(_status_bar)
    except Exception:  # noqa: BLE001
        pass
    STATE["depth"] = 0
