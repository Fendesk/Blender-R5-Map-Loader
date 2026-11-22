"""R5 Map Loader EX: the whole interface in a separate window, drawn like a terminal system monitor (btop style).

Only in the EX build (the build script leaves this file out of the regular zip). The window is a Text Editor area
in its own Blender window; a POST_PIXEL draw handler paints a character grid over it (mono font, box borders,
braille graphs, gradient meters) and a modal operator turns mouse / keys into actions:
  click            toggle, cycle, press a button, open the Blender field of a value (to type it)
  drag / wheel     change a number          right-click   Blender field of any value
  wheel elsewhere  scroll                   Ctrl+wheel / + -   text size        1-9   show / hide a box
"""
import ctypes
import math
import os
import re
import sys
import textwrap
import time
from collections import deque

import blf
import bpy
import gpu
from bpy.props import BoolProperty, IntProperty, StringProperty
from gpu_extras.batch import batch_for_shader

from . import ui

# ==========================================================================
# Theme: Blender's own colours. Each box has the colour of a Properties tab icon (Object orange, Object Data
# teal, Modifier blue, Material pink, Folder yellow) or of a collection colour tag, read from the user's theme.
# ==========================================================================


def _hex(h, a=1.0):
    return (int(h[1:3], 16) / 255.0, int(h[3:5], 16) / 255.0, int(h[5:7], 16) / 255.0, a)


def _mix(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t, 1.0)


BG = _hex("#1D1D1D")             # window background (Blender's dark editor grey)
TITLE = _hex("#F2F2F2")          # box titles
FG = _hex("#D6D6D6")             # main text
STRONG = _hex("#FFFFFF")         # values, text under the mouse
GRAPH_TEXT = _hex("#9C9C9C")     # secondary text, column headers
FAINT = _hex("#4A4A4A")          # empty gauge cells, dividers, empty ticks
INACTIVE = _hex("#6E6E6E")       # disabled items
HOVER_BG = _hex("#2B2B2B")       # row / button under the mouse
BTN_BG = _hex("#262626")
WARN = _hex("#E2605B")           # errors, warnings (Blender's red tag)
METER_BG = FAINT
DIV = _hex("#3A3A3A")
SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹"
PRIMARY = {"r5.import_map", "r5.camera_from_view", "r5.lights_build", "r5.setup_sky"}   # filled buttons

# Fallbacks = Blender's default theme
_DEFAULT_ICONS = {"icon_scene": "#E6E6E6", "icon_object": "#E96A00", "icon_object_data": "#00D4A3",
                  "icon_modifier": "#84B8FF", "icon_shading": "#EA7581", "icon_folder": "#E2C37B"}
_DEFAULT_TAGS = ["#E2605B", "#F1A355", "#E3D45C", "#82B55F", "#4FA1E5", "#9B6AE3", "#D564A7", "#A47F62"]
# box -> ("icon", attribute) or ("tag", index of the collection colour tag)
# one hue per box: grey, red, orange, blue, teal, violet, yellow, green, pink (0 = footer, 10 = import bar)
BOX_THEME = {0: ("icon", "icon_modifier"), 1: ("icon", "icon_scene"), 2: ("tag", 0),
             3: ("icon", "icon_object"), 4: ("icon", "icon_modifier"), 5: ("icon", "icon_object_data"),
             6: ("tag", 5), 7: ("tag", 2), 8: ("tag", 3), 9: ("tag", 6), 10: ("tag", 4)}


def box_color(n):
    kind, key = BOX_THEME.get(n, ("icon", "icon_scene"))
    try:
        th = bpy.context.preferences.themes[0]
        if kind == "icon":
            c = getattr(th.user_interface, key)
        else:
            c = th.collection_color[key].color
        return (c[0], c[1], c[2], 1.0)
    except Exception:  # noqa: BLE001
        return _hex(_DEFAULT_ICONS[key] if kind == "icon" else _DEFAULT_TAGS[key])


# Colours of the box being drawn (set by use_box): its accent, the selected background, the gauge gradient
HI = SEL_BG = BTN_HOVER = MISC = BOX = None
GAUGE = None


def use_box(n):
    global HI, SEL_BG, BTN_HOVER, MISC, BOX, GAUGE
    c = box_color(n)
    HI = MISC = BOX = c
    SEL_BG = _mix(BG, c, 0.28)
    BTN_HOVER = _mix(BG, c, 0.38)
    GAUGE = (_mix(BG, c, 0.35), c, _mix(c, STRONG, 0.55))


use_box(0)


def _version():
    try:
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("version"):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "?"


VERSION = _version()


LOAD_GRAD = (_hex("#82B55F"), _hex("#E3D45C"), _hex("#E2605B"))     # CPU / RAM graphs: green, yellow, red


def grad(name, t):
    """Gauge / graph colour at t (0..1): the box's own gradient, or the load gradient ("cpu", "used")."""
    s, m, e = LOAD_GRAD if name in ("cpu", "used") else GAUGE
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return _mix(s, m, t * 2) if t < 0.5 else _mix(m, e, (t - 0.5) * 2)


def tick(g, x, y, on, dim=False):
    """Check box: [x] in the box colour, or [ ]."""
    g.put(x, y, "[", GRAPH_TEXT if on else FAINT)
    g.put(x + 1, y, "x" if on else " ", INACTIVE if dim else HI)
    g.put(x + 2, y, "]", GRAPH_TEXT if on else FAINT)
    return x + 3


def fit(text, n, left=False):
    """Cut text to n cells with an ellipsis (left=True keeps the end: paths)."""
    if n <= 0:
        return ""
    if len(text) <= n:
        return text
    return ("…" + text[-(n - 1):]) if left else (text[:n - 1] + "…")


def num(n):
    return f"{n:,}".replace(",", " ")


def human(b):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if b < 1024 or unit == "TiB":
            return f"{b:.1f} {unit}" if unit != "B" else f"{b:.0f} B"
        b /= 1024.0
    return ""


# ==========================================================================
# State
# ==========================================================================
OURS = set()                     # as_pointer() of the EX areas
RUNNING = set()                  # areas with a live modal operator
FONT = {"id": 0, "path": ""}
UI = {
    "scroll": 0, "mouse": (-1, -1), "msg": ("", 0.0), "tabs": {}, "tables": {}, "light_sel": 0,
    "interval": 1.0, "last": {}, "hover_tip": "", "hidden": "", "font": 13,
}
_HANDLE = []


def _prefs():
    a = bpy.context.preferences.addons.get(__package__)
    return a.preferences if a is not None else None


def _hidden_boxes():
    p = _prefs()
    return {int(c) for c in (p.hidden_boxes if p is not None else UI["hidden"]) if c.isdigit()}


def _set_hidden_boxes(hidden):
    text = "".join(str(i) for i in sorted(hidden))
    p = _prefs()
    if p is not None:
        p.hidden_boxes = text
    UI["hidden"] = text


def _font_size():
    p = _prefs()
    return p.font_size if p is not None else UI["font"]


def flash(msg):
    UI["msg"] = (msg, time.monotonic())
    redraw()


# ==========================================================================
# Live stats (CPU / RAM of Blender, props drawn)
# ==========================================================================
class _PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


class _MSX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _memory():
    """(Blender resident memory, total RAM, available RAM) in bytes; None where the system does not say."""
    try:
        if sys.platform == "win32":
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi = ctypes.windll.psapi
            psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC), ctypes.c_ulong]
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
            ms = _MSX()
            ms.dwLength = ctypes.sizeof(_MSX)
            k32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return pmc.WorkingSetSize, ms.ullTotalPhys, ms.ullAvailPhys
        if os.path.exists("/proc/self/statm"):
            with open("/proc/self/statm") as f:
                rss = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k] = int(v.split()[0]) * 1024
            return rss, info.get("MemTotal"), info.get("MemAvailable")
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return rss, total, None
    except Exception:  # noqa: BLE001
        return None, None, None


STATS = {"cpu": deque([0.0] * 400, maxlen=400), "ram": deque([0.0] * 400, maxlen=400),
         "drawn": deque([0.0] * 400, maxlen=400), "t": None, "pt": None, "cpu_now": 0.0,
         "rss": None, "total": None, "avail": None, "lib": (0, 0, 0), "lib_t": 0.0}


def sample():
    now, pt = time.monotonic(), time.process_time()
    if STATS["t"] is not None and now > STATS["t"]:
        cpu = 100.0 * (pt - STATS["pt"]) / (now - STATS["t"]) / max(os.cpu_count() or 1, 1)
        STATS["cpu_now"] = min(max(cpu, 0.0), 100.0)
        STATS["cpu"].append(STATS["cpu_now"] / 100.0)
    STATS["t"], STATS["pt"] = now, pt
    rss, total, avail = _memory()
    STATS["rss"], STATS["total"], STATS["avail"] = rss, total, avail
    STATS["ram"].append((rss / total) if rss and total else 0.0)
    try:
        from . import lod_system
        h = lod_system.HIDDEN
        STATS["drawn"].append(h["drawn"] / h["total"] if h.get("valid") and h.get("total") else 0.0)
    except Exception:  # noqa: BLE001
        STATS["drawn"].append(0.0)
    if now - STATS["lib_t"] > 3.0:
        try:
            from . import optimize
            STATS["lib"] = optimize.library_stats()
        except Exception:  # noqa: BLE001
            pass
        STATS["lib_t"] = now


# ==========================================================================
# Character grid
# ==========================================================================
class Grid:
    """Rows of characters with a colour per cell, background spans and clickable regions."""

    def __init__(self, w, h=0):
        self.w = w
        self.chars, self.cols = [], []
        self.bgs = []                # (x, y, n, colour)
        self.hits = []               # (x, y, w, h, handlers)
        for _ in range(h):
            self.row()

    @property
    def h(self):
        return len(self.chars)

    def row(self):
        self.chars.append([" "] * self.w)
        self.cols.append([None] * self.w)
        return len(self.chars) - 1

    def put(self, x, y, text, color=FG, bg=None):
        while y >= len(self.chars):
            self.row()
        ch, co = self.chars[y], self.cols[y]
        for i, c in enumerate(text):
            if 0 <= x + i < self.w:
                ch[x + i] = c
                co[x + i] = color
        if bg is not None and text:
            x0 = max(x, 0)
            self.bgs.append((x0, y, max(min(x + len(text), self.w) - x0, 0), bg))
        return x + len(text)

    def hit(self, x, y, n, h=1, **hd):
        self.hits.append((x, y, n, h, hd))

    def blit(self, g, ox, oy):
        for y in range(g.h):
            for x in range(g.w):
                c = g.chars[y][x]
                if c != " " or g.cols[y][x] is not None:
                    tx = ox + x
                    if 0 <= tx < self.w:
                        while oy + y >= len(self.chars):
                            self.row()
                        self.chars[oy + y][tx] = c
                        self.cols[oy + y][tx] = g.cols[y][x]
        self.bgs += [(x + ox, y + oy, n, c) for x, y, n, c in g.bgs]
        self.hits += [(x + ox, y + oy, n, h, hd) for x, y, n, h, hd in g.hits]


# ==========================================================================
# Data access
# ==========================================================================
S = "r5_settings"


def resolve(owner):
    scene = bpy.context.scene
    if owner == "":
        return scene
    if owner.startswith("OB:"):
        return bpy.data.objects.get(owner[3:])
    try:
        return scene.path_resolve(owner)
    except (ValueError, AttributeError):
        return None


def _view3d():
    """(window, area, region) of a 3D view, preferring the main window: operators of the add-on run there."""
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                reg = next((r for r in area.regions if r.type == "WINDOW"), None)
                if reg is not None:
                    return win, area, reg
    return None, None, None


def redraw(all_views=False):
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.as_pointer() in OURS or (all_views and area.type in {"VIEW_3D", "PROPERTIES", "OUTLINER"}):
                area.tag_redraw()


def op_desc(idname):
    try:
        mod, name = idname.split(".")
        return getattr(getattr(bpy.ops, mod), name).get_rna_type().description
    except Exception:  # noqa: BLE001
        return ""


def run_op(idname, **kw):
    mod, name = idname.split(".")
    op = getattr(getattr(bpy.ops, mod), name)
    win, area, reg = _view3d()
    try:
        if win is not None:
            with bpy.context.temp_override(window=win, area=area, region=reg):
                op("INVOKE_DEFAULT", **kw)
        else:
            op("INVOKE_DEFAULT", **kw)
    except Exception as exc:  # noqa: BLE001
        flash(f"{idname}: {str(exc).strip().splitlines()[-1] if str(exc).strip() else exc}")
    redraw(all_views=True)


def undo_push(msg):
    try:
        bpy.ops.ed.undo_push(message=msg)
    except Exception:  # noqa: BLE001
        pass


def set_value(owner, prop, value, push=True):
    o = resolve(owner)
    if o is None:
        return
    try:
        setattr(o, prop, value)
    except Exception as exc:  # noqa: BLE001
        flash(str(exc))
        return
    if push:
        undo_push(f"R5: {prop}")
    redraw(all_views=True)


def popup(owner, prop):
    try:
        bpy.ops.r5.ex_edit("INVOKE_DEFAULT", owner=owner, prop=prop)
    except Exception as exc:  # noqa: BLE001
        flash(str(exc))


# ==========================================================================
# Widgets (write into a box's Grid, one or more rows each)
# ==========================================================================
def _num_info(o, prop, rna):
    v = getattr(o, prop)
    lo, hi = rna.hard_min, rna.hard_max
    if rna.type == "INT":
        step = max(1, 10 ** max(int(math.log10(abs(v))) - 1, 0)) if v else 1
    else:
        step = rna.step / 100.0 if rna.step else 0.1
        if rna.subtype == "ANGLE":
            step = math.radians(1.0)
        elif abs(v) >= 1e-6:
            step = max(step, 10 ** (math.floor(math.log10(abs(v))) - 1))
    return v, lo, hi, step


def fmt_value(o, prop, rna):
    v = getattr(o, prop)
    t, sub = rna.type, rna.subtype
    if t == "BOOLEAN":
        return "on" if v else "off"
    if t == "INT":
        return num(v) + (" %" if sub == "PERCENTAGE" else "")
    if t == "FLOAT":
        if getattr(rna, "is_array", False):
            if sub in {"COLOR", "COLOR_GAMMA"}:
                return "#" + "".join(f"{int(max(0, min(1, c)) ** (1 / 2.2 if sub == 'COLOR' else 1) * 255):02x}"
                                     for c in tuple(v)[:3])
            return " / ".join(f"{c:g}" for c in v)
        prec = min(max(rna.precision, 0), 5)
        if sub == "ANGLE":
            return f"{math.degrees(v):.1f}°"
        if sub == "DISTANCE" or rna.unit == "LENGTH":
            return f"{v:.{min(prec, 3)}f} m"
        if sub == "PERCENTAGE":
            return f"{v:.{prec}f} %"
        if rna.unit == "POWER":
            return f"{v:.0f} W"
        return f"{v:.{prec}f}"
    if t == "ENUM":
        if rna.is_enum_flag:
            return ", ".join(sorted(v)) or "—"
        item = rna.enum_items.get(v)
        return item.name if item else str(v)
    if t == "STRING":
        return v or "—"
    if t == "POINTER":
        return v.name if v is not None else "—"
    return str(v)


def prop_line(g, owner, prop, label=None, dim=False):
    """One row: label on the left, value on the right; a meter for bounded numbers."""
    o = resolve(owner)
    y = g.row()
    if o is None or prop not in o.bl_rna.properties:
        g.put(0, y, f"? {prop}", WARN)
        return
    rna = o.bl_rna.properties[prop]
    name = label if label is not None else rna.name
    tip = rna.description or rna.name
    lab_c = INACTIVE if dim else FG
    t = rna.type
    arr = getattr(rna, "is_array", False)
    rclick = (lambda o_=owner, p_=prop: popup(o_, p_))
    if t == "BOOLEAN" and not arr:
        v = getattr(o, prop)
        tick(g, 0, y, v, dim)
        g.put(4, y, fit(name, g.w - 4), lab_c)
        g.hit(0, y, g.w, click=lambda o_=owner, p_=prop: set_value(o_, p_, not getattr(resolve(o_), p_)),
              rclick=rclick, tip=tip, hl=HOVER_BG)
        return
    text = fmt_value(o, prop, rna)
    if t == "ENUM" and not rna.is_enum_flag and len(rna.enum_items) > 1:
        vw = min(len(text) + 4, max(g.w // 2, 12))
        x0 = g.w - vw
        g.put(0, y, fit(name, x0 - 1), lab_c)
        g.put(x0, y, "‹", HI)
        g.put(x0 + 2, y, fit(text, vw - 4), INACTIVE if dim else STRONG)
        g.put(g.w - 1, y, "›", HI)
        ids = [i.identifier for i in rna.enum_items]

        def cyc(d, o_=owner, p_=prop, ids_=ids):
            cur = getattr(resolve(o_), p_)
            i = ids_.index(cur) if cur in ids_ else 0
            set_value(o_, p_, ids_[(i + d) % len(ids_)])

        g.hit(0, y, x0 + 2, click=lambda c=cyc: c(-1), wheel=cyc, rclick=rclick, tip=tip, hl=HOVER_BG, enum=(owner, prop))
        g.hit(x0 + 2, y, vw - 2, click=lambda c=cyc: c(1), wheel=cyc, rclick=rclick, tip=tip, hl=HOVER_BG, enum=(owner, prop))
        return
    if t in {"INT", "FLOAT"} and not arr:
        v, lo, hi, step = _num_info(o, prop, rna)
        smin, smax = rna.soft_min, rna.soft_max
        bar = (smax - smin) <= 100.0 and smin >= 0.0 and rna.subtype not in {"DISTANCE", "ANGLE"} \
            and g.w >= 34
        vw = len(text)
        lw = min(len(name), max(g.w - vw - 1 - (12 if bar else 0), 4))
        g.put(0, y, fit(name, lw), lab_c)
        if bar:
            bx, bw = lw + 1, g.w - vw - lw - 3
            frac = (v - smin) / (smax - smin) if smax > smin else 0.0
            meter(g, bx, y, bw, frac, "proc" if not dim else None)
        g.put(g.w - vw, y, text, INACTIVE if dim else STRONG)
        g.hit(0, y, g.w, num=(owner, prop), click=rclick, rclick=rclick, tip=tip + "  (drag / wheel; click to type)",
              hl=HOVER_BG)
        return
    # strings, paths, colours, vectors, pointers: shown, edited in a Blender field
    if t == "FLOAT" and arr and rna.subtype in {"COLOR", "COLOR_GAMMA"}:
        c = tuple(getattr(o, prop))[:3]
        g.put(0, y, fit(name, g.w - 14), lab_c)
        g.put(g.w - 12, y, "████", (c[0], c[1], c[2], 1.0))
        g.put(g.w - 7, y, text, GRAPH_TEXT)
    else:
        lw = min(len(name), max(g.w // 3, 8))
        g.put(0, y, fit(name, lw), lab_c)
        vt = fit(text, g.w - lw - 2, left=(rna.subtype in {"DIR_PATH", "FILE_PATH", "FILE_NAME"}))
        g.put(g.w - len(vt), y, vt, STRONG if text != "—" else INACTIVE)
    g.hit(0, y, g.w, click=rclick, rclick=rclick, tip=tip + "  (click to edit)", hl=HOVER_BG)


def meter(g, x, y, w, frac, gname="proc"):
    filled = int(round(max(0.0, min(1.0, frac)) * w))
    for i in range(max(w, 0)):
        if i < filled and gname:
            g.put(x + i, y, "■", grad(gname, i / max(w - 1, 1)))
        else:
            g.put(x + i, y, "■", METER_BG)


def label(g, text, color=GRAPH_TEXT, wrap=True):
    for line in (textwrap.wrap(text, g.w) if wrap else [fit(text, g.w)]) or [""]:
        g.put(0, g.row(), line, color)


def divider(g, text=""):
    y = g.row()
    g.put(0, y, "─" * g.w, DIV)
    if text:
        g.put(1, y, f" {text} ", GRAPH_TEXT)


def buttons(g, items):
    """items: (label, idname, kwargs[, tip]) or (label, callable, None[, tip]); wraps to the next row."""
    y, x = g.row(), 0
    for it in items:
        lab, act, kw = it[0], it[1], it[2]
        tip = it[3] if len(it) > 3 else (op_desc(act) if isinstance(act, str) else "")
        text = f"[{lab}]"
        if x and x + len(text) > g.w:
            y, x = g.row(), 0
        main = isinstance(act, str) and act in PRIMARY
        g.put(x, y, "[", HI, bg=SEL_BG if main else None)
        g.put(x + 1, y, lab, STRONG if main else FG)
        g.put(x + 1, y, lab[:1], STRONG if main else HI)
        g.put(x + len(text) - 1, y, "]", HI)
        cb = (lambda a=act, k=kw: run_op(a, **(k or {}))) if isinstance(act, str) else act
        g.hit(x, y, len(text), click=cb, tip=tip, hl=BTN_HOVER)
        x += len(text) + 1


def tabs(g, key, names):
    cur = UI["tabs"].get(key, names[0])
    if cur not in names:
        cur = names[0]
    y, x = g.row(), 0
    for n in names:
        text = f" {n} "
        if x and x + len(text) > g.w:
            y, x = g.row(), 0
        sel = n == cur
        g.put(x, y, text, STRONG if sel else GRAPH_TEXT, bg=SEL_BG if sel else None)
        g.put(x + 1, y, n[:1], HI)
        g.hit(x, y, len(text), click=lambda k=key, n_=n: (UI["tabs"].__setitem__(k, n_), redraw()),
              tip=f"Show the {n} settings", hl=HOVER_BG)
        x += len(text)
    divider(g)
    return cur


def table(g, key, header, rows, height, sel=None, on_select=None):
    """header: [(title, width or 0 = flexible)]; rows: list of (cells, handlers) where cells = [(text, colour)] and
    handlers maps a column index to a click callback. A scrollbar shows when the rows do not fit."""
    widths = [w for _t, w in header]
    flex = max(g.w - sum(widths) - (len(widths) - 1) - 1, 4)
    widths = [w or flex for w in widths]
    y = g.row()
    x = 0
    for (title, _w), w in zip(header, widths):
        g.put(x, y, fit(title, w), GRAPH_TEXT)
        x += w + 1
    top = UI["tables"].get(key, 0)
    top = max(0, min(top, max(len(rows) - height, 0)))
    UI["tables"][key] = top
    y0 = g.h

    def scroll(d, k=key, n=len(rows), h=height):
        UI["tables"][k] = max(0, min(UI["tables"].get(k, 0) - d * 3, max(n - h, 0)))
        redraw()

    g.hit(0, y0, g.w, height, wheel=scroll)         # first: rows and cells below take precedence
    for i in range(height):
        y = g.row()
        r = top + i
        if r >= len(rows):
            continue
        cells, handlers = rows[r]
        is_sel = sel is not None and r == sel
        if is_sel:
            g.bgs.append((0, y, g.w - 1, SEL_BG))
        if on_select is not None:
            g.hit(0, y, g.w - 1, click=lambda rr=r: on_select(rr), wheel=scroll, hl=HOVER_BG)
        x = 0
        for ci, ((text, col), w) in enumerate(zip(cells, widths)):
            g.put(x, y, fit(text, w), STRONG if is_sel and col == FG else col)
            if ci in handlers:
                g.hit(x, y, w, click=handlers[ci], wheel=scroll, hl=HOVER_BG)
            x += w + 1
    # scrollbar
    if len(rows) > height:
        n = len(rows)
        th = max(1, height * height // n)
        ty = (height - th) * top // max(n - height, 1)
        for i in range(height):
            g.put(g.w - 1, y0 + i, "█" if ty <= i < ty + th else "│", GRAPH_TEXT if ty <= i < ty + th else DIV)
    return top


def braille_graph(g, x, y, w, h, values, gname, inverted=False):
    """Filled area graph, newest value on the right; 2 samples per cell, 4 levels per row."""
    vals = list(values)[-2 * w:]
    vals = [0.0] * (2 * w - len(vals)) + vals
    left_bits = (0x40, 0x04, 0x02, 0x01)
    right_bits = (0x80, 0x20, 0x10, 0x08)
    for cx in range(w):
        a, b = vals[2 * cx], vals[2 * cx + 1]
        la, lb = a * 4 * h, b * 4 * h
        for r in range(h):                       # r = 0 at the bottom
            fa, fb = int(round(min(max(la - 4 * r, 0), 4))), int(round(min(max(lb - 4 * r, 0), 4)))
            if a > 0.005 and r == 0 and fa == 0:
                fa = 1
            if b > 0.005 and r == 0 and fb == 0:
                fb = 1
            bits = 0
            for k in range(fa):
                bits |= left_bits[3 - k] if inverted else left_bits[k]
            for k in range(fb):
                bits |= right_bits[3 - k] if inverted else right_bits[k]
            if bits:
                row = (y + r) if inverted else (y + h - 1 - r)
                g.put(x + cx, row, chr(0x2800 + bits), grad(gname, (r + 0.5) / h))


# ==========================================================================
# Boxes
# ==========================================================================
def _map_name(scene):
    for c in scene.collection.children:
        if c.name.startswith("APX_Map_"):
            return c.name[len("APX_Map_"):]
    return ""


def box_blender(g, s, scene):
    """Graphs and figures of the session (top box)."""
    info_w = min(44, max(g.w // 2, 30))
    gw = g.w - info_w - 1
    h = 6
    for _ in range(h):
        g.row()
    if gw >= 10:
        braille_graph(g, 0, 0, gw, 3, STATS["cpu"], "cpu")
        braille_graph(g, 0, 3, gw, 3, STATS["ram"], "used", inverted=True)
        g.put(0, 0, f"cpu {STATS['cpu_now']:.0f}%", GRAPH_TEXT)
        if STATS["rss"]:
            g.put(0, 5, f"ram {human(STATS['rss'])}", GRAPH_TEXT)
    x = g.w - info_w
    rows = []
    mw = max(info_w - 18, 6)
    cpu = STATS["cpu_now"] / 100.0
    rows.append(("CPU", cpu, "cpu", f"{STATS['cpu_now']:5.1f}%"))
    if STATS["rss"] and STATS["total"]:
        rows.append(("RAM", STATS["rss"] / STATS["total"], "used", human(STATS["rss"])))
    from . import lod_system
    hd = lod_system.HIDDEN
    if hd.get("valid") and hd.get("total"):
        rows.append(("Drawn", hd["drawn"] / hd["total"], "free", f"{100 * hd['drawn'] / hd['total']:.0f}% props"))
    else:
        rows.append(("Drawn", 0.0, None, "not counted"))
    for i, (lab, frac, gn, txt) in enumerate(rows):
        g.put(x, i, lab, TITLE)
        meter(g, x + 6, i, mw, frac, gn)
        g.put(x + 7 + mw, i, fit(txt, info_w - mw - 7), FG)
    models, inst, tris = STATS["lib"]
    from . import motion
    lines = [
        ("Map", _map_name(scene) or "none loaded"),
        ("Props", f"{num(inst)} · {num(models)} models · {tris / 1e6:.1f}M tris"),
        ("Motion", f"movers {'on' if motion.movers_enabled(scene) else 'off'} · "
                   f"shaders {'on' if motion.shaders_enabled() else 'off'}"),
    ]
    for i, (lab, txt) in enumerate(lines):
        y = len(rows) + i
        if y < h:
            g.put(x, y, lab, TITLE)
            g.put(x + 7, y, fit(txt, info_w - 7), FG)


def box_map(g, s, scene):
    tab = tabs(g, "map", ["import", "geometry", "entities", "textures", "meshes", "lods", "placement"])
    from .operators import map_loaded
    if tab == "import":
        prop_line(g, S, "models_dir", "Models")
        prop_line(g, S, "instancing_mode", "Instancing")
        items = [("▶ Import map…", "r5.import_map", {})]
        if map_loaded(scene):
            items.append(("↻ Replace loaded map…", "r5.import_map", {"replace": True}))
        buttons(g, items)
        label(g, ".mprt .cast .ent .vpk .nut · one POI only: choose it in the file browser (N)")
        prop_line(g, S, "setup_viewport_on_import")
    elif tab == "geometry":
        prop_line(g, S, "import_world")
        prop_line(g, S, "world_split", "Split")
        if s.world_split == "TILES":
            prop_line(g, S, "world_tile_size")
        prop_line(g, S, "import_skybox")
        prop_line(g, S, "import_ziplines")
    elif tab == "entities":
        prop_line(g, S, "auto_entities")
        prop_line(g, S, "ent_path", "Entity file")
        prop_line(g, S, "ent_files", "Files")
        label(g, "R5Reloaded: vpk/englishserver_<map>.bsp.pak000_dir.vpk")
    elif tab == "textures":
        prop_line(g, S, "cast_load_textures")
        off = not s.cast_load_textures
        prop_line(g, S, "texture_other_exports", dim=off)
        prop_line(g, S, "cast_alpha_mode", "Alpha", dim=off)
        prop_line(g, S, "cast_spec_to_metal", dim=off)
        prop_line(g, S, "cast_flip_normal_green", dim=off)
        prop_line(g, S, "cast_extra_textures", dim=off)
        prop_line(g, S, "decal_offset")
        prop_line(g, S, "dedupe_on_import")
    elif tab == "meshes":
        prop_line(g, S, "join_meshes")
        prop_line(g, S, "merge_distance")
        prop_line(g, S, "cast_weld")
        prop_line(g, S, "cast_custom_normals")
    elif tab == "lods":
        prop_line(g, S, "max_lods")
        prop_line(g, S, "auto_generate_lods")
        off = not s.auto_generate_lods
        prop_line(g, S, "auto_lod_ratio", dim=off)
        prop_line(g, S, "auto_lod_min_ratio", dim=off)
        prop_line(g, S, "auto_lod_min_faces", dim=off)
        prop_line(g, S, "auto_lod_max_radius", dim=off)
        prop_line(g, S, "auto_lod_skip_names", "Never decimate", dim=off)
    else:
        prop_line(g, S, "unit_scale")
        prop_line(g, S, "axis_correction", "Axis")
        prop_line(g, S, "lift_above_zero")
        prop_line(g, S, "recenter")
        prop_line(g, S, "split_by_category")
        prop_line(g, S, "exclude_patterns")
        prop_line(g, S, "use_region")
        if s.use_region:
            prop_line(g, S, "region_center")
            prop_line(g, S, "region_radius")
    if tab != "import":
        label(g, "Used by the next import")


def _select_model(ob):
    def go():
        s = bpy.context.scene.r5_settings
        idx = bpy.data.objects.find(ob)
        if idx >= 0:
            s.model_index = idx
        o = bpy.data.objects.get(ob)
        vl = bpy.context.view_layer
        if o is not None and o.name in vl.objects:
            for x in vl.objects:
                if x.select_get():
                    x.select_set(False)
            try:
                o.select_set(True)
                vl.objects.active = o
            except RuntimeError:
                pass
        redraw(all_views=True)
    return go


def box_display(g, s, scene):
    from . import visibility
    from .castfile import GROUPS
    counts = visibility.counts()
    for grp, lab in GROUPS:
        y = g.row()
        hidden = getattr(s, f"hide_{grp}")
        tick(g, 0, y, not hidden)
        g.put(4, y, fit(f"{lab.split(' (')[0]} ({counts.get(grp, 0)})", g.w - 21), FG if not hidden else GRAPH_TEXT)
        g.put(g.w - 16, y, "hidden" if hidden else "shown", GRAPH_TEXT)
        g.hit(0, y, g.w - 8, click=lambda k=f"hide_{grp}": set_value(S, k, not getattr(resolve(S), k)),
              rclick=lambda k=f"hide_{grp}": popup(S, k), tip=f"Show / hide: {lab}", hl=HOVER_BG)
        g.put(g.w - 8, y, "[", HI)
        g.put(g.w - 7, y, "select", FG)
        g.put(g.w - 7, y, "s", HI)
        g.put(g.w - 1, y, "]", HI)
        g.hit(g.w - 8, y, 8, click=lambda k=grp: run_op("r5.select_group", group=k),
              tip=op_desc("r5.select_group"), hl=BTN_HOVER)
    divider(g, "prop models")
    prop_line(g, S, "model_filter", "Filter")
    buttons(g, [("Hide matching", "r5.hide_models", {"hide": True}),
                ("Show matching", "r5.hide_models", {"hide": False}),
                ("Show prop materials", "r5.link_materials", {})])
    needle = s.model_filter.strip().lower()
    obs = [o for o in bpy.data.objects if o.get("apx_gn") and (not needle or needle in o["apx_gn"].lower())]
    obs.sort(key=lambda o: o["apx_gn"])
    active = bpy.data.objects[s.model_index].name if 0 <= s.model_index < len(bpy.data.objects) else ""
    rows, sel = [], None
    for i, o in enumerate(obs):
        if o.name == active:
            sel = i
        n = len(o.data.vertices) if o.type == "MESH" else 0
        on_v, on_r = not o.hide_viewport, not o.hide_render
        rows.append(([(o["apx_gn"], FG), (num(n), GRAPH_TEXT), ("[x]" if on_v else "[ ]", HI if on_v else FAINT),
                      ("[x]" if on_r else "[ ]", HI if on_r else FAINT)],
                     {2: lambda n_=o.name: set_value("OB:" + n_, "hide_viewport",
                                                     not bpy.data.objects[n_].hide_viewport),
                      3: lambda n_=o.name: set_value("OB:" + n_, "hide_render",
                                                     not bpy.data.objects[n_].hide_render)}))
    table(g, "models", [("Model", 0), ("Count", 7), ("View", 4), ("Rend", 4)], rows, 10, sel,
          on_select=lambda r, obs_=obs: _select_model(obs_[r].name)() if r < len(obs_) else None)
    label(g, f"{len(obs)} models · count = placements · a row selects every copy (its Material tab edits them all)")


def box_poi(g, s, scene):
    from . import regions
    n = len(regions.get_discs(scene))
    label(g, f"Showing {n} area{'s' if n > 1 else ''} of the map" if n else "Showing the whole map",
          MISC if n else FG, wrap=False)
    rows = []
    for i, p in enumerate(s.pois):
        nm = p.name
        if p.kind == "zone":
            nm = re.sub(r"^zone_\d+_", "", nm).replace("_", " ")
        rows.append(([("[x]" if p.use else "[ ]", HI if p.use else FAINT), (nm, FG), (f"{p.radius:.0f} m", GRAPH_TEXT),
                      (p.kind, GRAPH_TEXT)],
                     {0: lambda i_=i: set_value(f"{S}.pois[{i_}]", "use", not resolve(f"{S}.pois[{i_}]").use)}))
    table(g, "pois", [("", 3), ("POI", 0), ("Radius", 8), ("Kind", 5)], rows, 8, s.poi_index,
          on_select=lambda r: set_value(S, "poi_index", r, push=False))
    buttons(g, [("+ Add at cursor", "r5.poi_add", {}), ("- Remove", "r5.poi_remove", {}),
                ("Go to", "r5.poi_goto", {}), ("All", "r5.poi_check", {"state": True}),
                ("None", "r5.poi_check", {"state": False})])
    if 0 <= s.poi_index < len(s.pois):
        prop_line(g, f"{S}.pois[{s.poi_index}]", "radius", "Radius (selected)")
    prop_line(g, S, "region_scale")
    checked = sum(1 for p in s.pois if p.use)
    buttons(g, [(f"Show {checked} POIs" if checked > 1 else "Show POI", "r5.region_poi", {}),
                ("Whole map", "r5.region_clear", {}), ("Around 3D cursor", "r5.region_cursor", {})])
    divider(g, "grid & new POIs")
    prop_line(g, S, "grid_size")
    buttons(g, [("Add grid sectors", "r5.poi_grid", {})])
    prop_line(g, S, "poi_default_radius")


def box_perf(g, s, scene):
    prop_line(g, S, "use_lod", "LOD system")
    if not s.use_lod:
        label(g, "LODs disabled", WARN)
        prop_line(g, S, "forced_lod")
        buttons(g, [("Update LODs", "r5.update_lods", {})])
        return
    from . import lod_system
    h = lod_system.HIDDEN
    y = g.row()
    if h.get("valid") and h.get("total"):
        hidden = max(h["total"] - h["drawn"], 0)
        g.put(0, y, "Hidden", TITLE)
        meter(g, 7, y, max(g.w - 30, 6), hidden / h["total"], "used")
        g.put(g.w - 22, y, fit(f"{num(hidden)} ({100 * hidden / h['total']:.0f}%)", 22), FG)
        label(g, f"of {num(h['total'])} props, {num(h['drawn'])} drawn", GRAPH_TEXT, wrap=False)
        y = g.h
        for _ in range(2):
            g.row()
        braille_graph(g, 0, y, g.w, 2, STATS["drawn"], "free")
    else:
        g.put(0, y, "Hidden props: not counted yet", GRAPH_TEXT)
    buttons(g, [("↻ Count hidden", "r5.count_hidden", {}), ("Show everything (LOD0)", "r5.show_all", {}),
                ("Update LODs", "r5.update_lods", {})])
    prop_line(g, S, "lod_auto_update")
    tab = tabs(g, "perf", ["lod", "unload", "timing", "tools"])
    if tab == "lod":
        prop_line(g, S, "lod_source", "Source")
        prop_line(g, S, "lod_distances")
        prop_line(g, S, "lod_bias")
        prop_line(g, S, "lod_size_scaling")
        if s.lod_size_scaling:
            prop_line(g, S, "lod_reference_size")
    elif tab == "unload":
        prop_line(g, S, "use_frustum")
        if s.use_frustum:
            prop_line(g, S, "frustum_margin")
        prop_line(g, S, "cull_small_props")
        if s.cull_small_props:
            prop_line(g, S, "min_screen_pct")
            prop_line(g, S, "cull_keep_radius")
        prop_line(g, S, "use_culling")
        if s.use_culling:
            prop_line(g, S, "cull_distance")
        if s.instancing_mode != "GN":
            prop_line(g, S, "use_bounds_level")
            if s.use_bounds_level:
                prop_line(g, S, "bounds_distance")
    elif tab == "timing":
        prop_line(g, S, "update_settle")
        prop_line(g, S, "update_interval")
        prop_line(g, S, "update_move_threshold")
        prop_line(g, S, "update_angle_threshold")
    else:
        prop_line(g, S, "texture_limit", "Textures")
        buttons(g, [("Apply texture limit", "r5.texture_limit", {}), ("Optimize scene", "r5.optimize", {}),
                    ("Set up viewport", "r5.viewport_setup", {}), ("Make real (selection)", "r5.make_real", {})])
        models, inst, tris = STATS["lib"]
        label(g, f"Unique models {num(models)} · instances {num(inst)} · LOD0 triangles {num(tris)}", FG)


def box_sky(g, s, scene):
    tab = tabs(g, "sky", ["sky", "sun", "atmosphere", "fog"])
    proc = s.sky_mode == "PROCEDURAL"
    if tab == "sky":
        prop_line(g, S, "sky_mode", "Mode")
        if not proc:
            prop_line(g, S, "sky_image", {"IMAGE": "Panorama", "DOME": "Dome image"}.get(s.sky_mode, "One face"))
            prop_line(g, S, "sky_image_rotation")
            if s.sky_mode == "DOME":
                prop_line(g, S, "sky_dome_projection", "Projection")
                prop_line(g, S, "sky_dome_horizon")
                prop_line(g, S, "sky_dome_mirror")
            if s.sky_mode == "CUBEMAP":
                prop_line(g, S, "sky_cubemap_y_up")
                prop_line(g, S, "sky_panorama_width")
        prop_line(g, S, "sky_strength")
    elif tab == "sun":
        if proc:
            label(g, "The procedural sky always has its sun", GRAPH_TEXT)
        else:
            prop_line(g, S, "sky_add_sun", "Sun")
        off = not (s.sky_add_sun or proc)
        if s.map_sun_json:
            prop_line(g, S, "sky_use_map_sun", dim=off)
        prop_line(g, S, "sky_sun_elevation", dim=off)
        prop_line(g, S, "sky_sun_rotation", dim=off)
        prop_line(g, S, "sky_sun_strength", dim=off)
        prop_line(g, S, "sky_sun_color", "Colour", dim=off)
        buttons(g, [("Reset sun colour", "r5.sun_color_reset", {})])
    elif tab == "atmosphere":
        if not proc:
            prop_line(g, S, "sky_atmosphere")
            prop_line(g, S, "sky_atmosphere_strength", dim=not s.sky_atmosphere)
        off = not (proc or s.sky_atmosphere)
        prop_line(g, S, "sky_air_density", dim=off)
        prop_line(g, S, "sky_dust_density", dim=off)
        prop_line(g, S, "sky_ozone_density", dim=off)
    else:
        prop_line(g, S, "sky_fog")
        off = not s.sky_fog
        prop_line(g, S, "sky_fog_density", dim=off)
        prop_line(g, S, "sky_fog_color", dim=off)
        prop_line(g, S, "sky_fog_anisotropy", dim=off)
        prop_line(g, S, "sky_fog_distance", dim=off)
    buttons(g, [("Apply sky", "r5.setup_sky", {})])
    label(g, "Then changes apply live" + ("" if s.map_sun_json else " · no map sun (compressed _env.ent)"))


def box_lights(g, s, scene):
    if not hasattr(s, "light_cats"):
        label(g, "Restart Blender to finish the update", WARN)
        return
    buttons(g, [("Find light sources", "r5.lights_scan", {})])
    cats = list(s.light_cats)
    if not cats:
        label(g, "Scan the map first: lamps, signs, screens… are sorted into types")
        return
    sel = min(UI["light_sel"], len(cats) - 1)
    rows = []
    for i, c in enumerate(cats):
        rows.append(([("[x]" if c.enabled else "[ ]", HI if c.enabled else FAINT), (c.label, FG),
                      (num(c.props), GRAPH_TEXT), (num(c.created), FG)],
                     {0: lambda i_=i: set_value(f"{S}.light_cats[{i_}]", "enabled",
                                                not resolve(f"{S}.light_cats[{i_}]").enabled)}))
    table(g, "lights", [("", 3), ("Type", 0), ("Props", 7), ("Lights", 7)], rows, min(len(rows), 7), sel,
          on_select=lambda r: (UI.__setitem__("light_sel", r), redraw()))
    c = cats[sel]
    o = f"{S}.light_cats[{sel}]"
    divider(g, c.label)
    off = not c.enabled
    prop_line(g, o, "light_type", "Light", dim=off)
    if c.light_type == "SPOT":
        prop_line(g, o, "spot_angle", dim=off)
        prop_line(g, o, "spot_blend", dim=off)
    elif c.light_type == "AREA":
        prop_line(g, o, "area_shape", dim=off)
        prop_line(g, o, "spot_angle", "Spread", dim=off)
    prop_line(g, o, "power", dim=off)
    prop_line(g, o, "use_model_color", "Model colour", dim=off)
    prop_line(g, o, "color", dim=off or c.use_model_color)
    prop_line(g, o, "radius", dim=off)
    prop_line(g, o, "shadows", dim=off)
    prop_line(g, o, "emission", dim=off)
    prop_line(g, o, "max_lights", "Max", dim=off)
    buttons(g, [("Create lights", "r5.lights_build", {}), ("Remove lights", "r5.lights_clear", {})])
    label(g, "Lights near the 3D cursor first (Max)")
    from .regions import get_discs
    if len(get_discs(scene)):
        label(g, "Only in the POIs shown (Regions / POI)", MISC)


def box_anim(g, s, scene):
    from . import motion
    mv, sh = motion.movers_enabled(scene), motion.shaders_enabled()
    items = [("■ Stop movers", "r5.movers", {"enable": False}), ("↻", "r5.movers", {"enable": True},
                                                                   "Restart the movers")] if mv else \
        [("▶ Start movers", "r5.movers", {"enable": True})]
    items += [("■ Stop shaders", "r5.anim_shaders", {"enable": False}),
              ("↻", "r5.anim_shaders", {"enable": True}, "Restart the shaders")] if sh else \
        [("▶ Start shaders", "r5.anim_shaders", {"enable": True})]
    buttons(g, items)
    y = g.row()
    g.put(0, y, "movers", GRAPH_TEXT)
    g.put(7, y, "on " if mv else "off", MISC if mv else INACTIVE)
    g.put(12, y, "shaders", GRAPH_TEXT)
    g.put(20, y, "on " if sh else "off", MISC if sh else INACTIVE)
    divider(g, "speeds")
    for p in ("movers_speed", "shader_speed", "speed_water", "speed_lava", "speed_screens", "speed_energy",
              "speed_effects"):
        prop_line(g, S, p)
    divider(g, "foliage wind")
    prop_line(g, S, "anim_foliage")
    prop_line(g, S, "wind_speed", dim=not s.anim_foliage)
    prop_line(g, S, "wind_strength", dim=not s.anim_foliage)
    label(g, "Rotators, fans, rings, Tridents · play the timeline (Space)")


def box_render(g, s, scene):
    buttons(g, [("New camera from view", "r5.camera_from_view", {"new": True}),
                ("Move active camera to view", "r5.camera_from_view", {"new": False})])
    prop_line(g, "", "camera", "Active camera")
    buttons(g, [("Cycles preview", "r5.setup_cycles", {"preset": "PREVIEW"}),
                ("Cycles final", "r5.setup_cycles", {"preset": "FINAL"})])
    divider(g, "cycles presets")
    prop_line(g, S, "cycles_samples_preview")
    prop_line(g, S, "cycles_samples_final")
    prop_line(g, S, "cycles_viewport_samples")
    prop_line(g, S, "cycles_texture_limit", "Textures")
    prop_line(g, S, "cycles_final_lod0")
    divider(g, "props at render time")
    prop_line(g, S, "render_mode", "LOD")
    if s.render_mode == "CAMERA":
        prop_line(g, S, "render_frustum")
        prop_line(g, S, "render_min_screen_pct", "Min. size on screen (%)")
        prop_line(g, S, "lod_fade")
        if s.lod_fade:
            prop_line(g, S, "lod_fade_width")
            prop_line(g, S, "lod_fade_viewport")
    label(g, "Regions / POI: render only part of the map")


BOXES = [
    (1, "blender", box_blender),
    (2, "map", box_map),
    (3, "display", box_display),
    (4, "poi", box_poi),
    (5, "performance", box_perf),
    (6, "sky", box_sky),
    (7, "lights", box_lights),
    (8, "animation", box_anim),
    (9, "camera & render", box_render),
]


def draw_box(cv, x, y, w, h, color, number, title, items_right=(), items_center=()):
    cv.put(x, y, "╭" + "─" * (w - 2) + "╮", color)
    for r in range(1, h - 1):
        cv.put(x, y + r, "│", color)
        cv.put(x + w - 1, y + r, "│", color)
    cv.put(x, y + h - 1, "╰" + "─" * (w - 2) + "╯", color)
    cx = cv.put(x + 1, y, "┐", color)
    if number:
        cx = cv.put(cx, y, SUP[number], HI)
    cx = cv.put(cx, y, title, TITLE)
    cv.put(cx, y, "┌", color)
    for text, hd in items_center:
        cx = x + (w - len(text) - 2) // 2
        cv.put(cx, y, "┐", color)
        cv.put(cx + 1, y, text, TITLE)
        cv.put(cx + 1 + len(text), y, "┌", color)
    rx = x + w - 2
    for spans, hd in items_right:
        n = sum(len(t) for t, _c in spans)
        rx -= n + 2
        cv.put(rx, y, "┐", color)
        px = rx + 1
        for t, c in spans:
            px = cv.put(px, y, t, c)
        cv.put(px, y, "┌", color)
        if hd:
            cv.hit(rx + 1, y, n, **hd)


# ==========================================================================
# Frame: layout of the boxes, scrolling, footer
# ==========================================================================
def build(cols, rows, scene):
    s = scene.r5_settings
    hidden = _hidden_boxes()
    page = Grid(cols)
    if not hasattr(s, "auto_entities"):
        page.put(1, 1, "Restart Blender to finish the update", WARN)
        return page
    from . import progress
    if progress.active():
        use_box(10)
        # during an import only the bar is drawn: the boxes read the whole scene, which is being built
        st = progress.STATE
        inner = Grid(cols - 4)
        yy = inner.row()
        inner.put(0, yy, fit(st["title"], cols - 4), HI)
        yy = inner.row()
        meter(inner, 0, yy, cols - 12, st["frac"])
        inner.put(cols - 10, yy, f"{100 * st['frac']:5.1f}%", STRONG)
        label(inner, progress.label(), FG, wrap=False)
        label(inner, "The boxes come back when the import is finished", GRAPH_TEXT, wrap=False)
        draw_box(page, 0, 0, cols, inner.h + 2, BOX, 0, "import")
        page.blit(inner, 2, 1)
        return page
    y0 = 0
    if 1 not in hidden:
        use_box(1)
        inner = Grid(cols - 4)
        box_blender(inner, s, scene)
        h = inner.h + 2
        iv = UI["interval"]
        draw_box(page, 0, 0, cols, h, BOX, 1, "blender",
                 items_right=[([("-", HI), (f" {int(iv * 1000)}ms ", TITLE), ("+", HI)], None),
                              ([(f"v{VERSION}", GRAPH_TEXT)], None)],
                 items_center=[(time.strftime("%H:%M:%S"), None)])
        # interval buttons: "-" and "+"
        n = len(f" {int(iv * 1000)}ms ") + 2
        rx = cols - 2 - n
        page.hit(rx + 1, 0, 1, click=lambda: _set_interval(-1), tip="Slower graph updates")
        page.hit(rx + n, 0, 1, click=lambda: _set_interval(1), tip="Faster graph updates")
        page.blit(inner, 2, 1)
        y0 = h
    ncol = max(1, cols // 56)
    colw = cols // ncol
    tops = [y0] * ncol
    for number, title, fn in BOXES[1:]:
        if number in hidden:
            continue
        c = min(range(ncol), key=lambda i: tops[i])
        x = c * colw
        w = colw if c < ncol - 1 else cols - x
        use_box(number)
        inner = Grid(w - 4)
        try:
            fn(inner, s, scene)
        except Exception as exc:  # noqa: BLE001
            label(inner, f"error: {exc}", WARN)
        h = inner.h + 2
        draw_box(page, x, tops[c], w, h, BOX, number, title)
        page.blit(inner, x + 2, tops[c] + 1)
        tops[c] += h
    return page


def _set_interval(d):
    steps = [0.25, 0.5, 1.0, 2.0, 5.0]
    i = min(range(len(steps)), key=lambda k: abs(steps[k] - UI["interval"]))
    UI["interval"] = steps[max(0, min(len(steps) - 1, i + d))]
    redraw()


def footer(cols):
    use_box(0)
    f = Grid(cols, 1)
    hidden = _hidden_boxes()
    msg, t = UI["msg"]
    text = msg if msg and time.monotonic() - t < 5.0 else UI["hover_tip"]
    # r5mex-style key hints (red key, grey meaning), then the box switches
    hints = [("wheel", "scroll / change"), ("drag", "value"), ("right-click", "field"), ("ctrl+wheel", "size")]
    right = " ".join(str(n) for n, _t, _f in BOXES)
    x_boxes = cols - len(right) - 6
    hint_w = sum(len(k) + len(v) + 3 for k, v in hints)
    x = 0
    if msg and text == msg:
        f.put(0, 0, fit(text, max(x_boxes - 2, 0)), HI)
    elif text:
        f.put(0, 0, fit(text, max(x_boxes - 2, 0)), GRAPH_TEXT)
    elif hint_w < x_boxes - 2:
        for k, v in hints:
            x = f.put(x, 0, k, HI)
            x = f.put(x + 1, 0, v, GRAPH_TEXT) + 2
    x = f.put(x_boxes, 0, "boxes", GRAPH_TEXT) + 1
    for n, title, _f in BOXES:
        f.put(x, 0, str(n), FAINT if n in hidden else box_color(n))
        f.hit(x, 0, 1, click=lambda n_=n: toggle_box(n_), tip=f"Show / hide {title} (key {n})")
        x += 2
    return f


def toggle_box(n):
    h = _hidden_boxes()
    h ^= {n}
    _set_hidden_boxes(h)
    redraw()


# ==========================================================================
# Drawing
# ==========================================================================
_SHADER = []


def _shader():
    if not _SHADER:
        _SHADER.append(gpu.shader.from_builtin("UNIFORM_COLOR"))
    return _SHADER[0]


def _rects(rects, color):
    if not rects:
        return
    sh = _shader()
    pos, idx = [], []
    for x0, y0, x1, y1 in rects:
        k = len(pos)
        pos += [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        idx += [(k, k + 1, k + 2), (k, k + 2, k + 3)]
    b = batch_for_shader(sh, "TRIS", {"pos": pos}, indices=idx)
    sh.uniform_float("color", color)
    b.draw(sh)


def metrics(region):
    fid = FONT["id"]
    size = max(6, round(_font_size() * bpy.context.preferences.system.ui_scale))
    blf.size(fid, size)
    cw = blf.dimensions(fid, "M" * 40)[0] / 40.0 or size * 0.6
    ch = max(round(size * 1.18), 1)
    cols = max(int((region.width - 4) // cw), 20)
    rows = max(int((region.height - 4) // ch), 5)
    ox = (region.width - cols * cw) / 2.0
    oy = (region.height - rows * ch) / 2.0
    return size, cw, ch, cols, rows, ox, oy


def _draw():
    area, region = bpy.context.area, bpy.context.region
    if area is None or area.as_pointer() not in OURS:
        return
    try:
        _draw_ui(area, region)
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()


def _draw_ui(area, region):
    scene = bpy.context.scene
    size, cw, ch, cols, rows, ox, oy = metrics(region)
    gpu.state.blend_set("ALPHA")
    _rects([(0, 0, region.width, region.height)], BG)
    page = build(cols, rows, scene)
    view_rows = rows - 1
    UI["scroll"] = max(0, min(UI["scroll"], max(page.h - view_rows, 0)))
    sc = UI["scroll"]
    foot = footer(cols)
    # hover highlight
    mx, my = UI["mouse"]
    hov = None
    if 0 <= my < view_rows:
        hov = _find(page.hits, mx, my + sc)
    elif my == rows - 1:
        hov = _find(foot.hits, mx, 0)
    UI["hover_tip"] = hov[4].get("tip", "") if hov else ""
    if hov is not None and hov[4].get("hl") is not None:
        target = page if my < view_rows else foot
        target.bgs.append((hov[0], hov[1], hov[2], hov[4]["hl"]))
    top = region.height - oy

    def cell_rect(x, y, n, h=1):
        return (ox + x * cw, top - (y + h) * ch, ox + (x + n) * cw, top - y * ch)

    by_color = {}
    for x, y, n, c in page.bgs:
        if sc <= y < sc + view_rows:
            by_color.setdefault(c, []).append(cell_rect(x, y - sc, n))
    for x, y, n, c in foot.bgs:
        by_color.setdefault(c, []).append(cell_rect(x, rows - 1, n))
    for c, rs in by_color.items():
        _rects(rs, c)
    fid = FONT["id"]
    base = round(ch * 0.26)

    def text_row(chars, colors, srow):
        y = top - (srow + 1) * ch + base
        run, rc, rx = [], None, 0
        for x, (c, col) in enumerate(zip(chars, colors)):
            if c == " " and col is None:
                if run:
                    run.append(" ")
                continue
            if col is None:
                col = FG
            if rc is not None and col != rc:
                _flush(fid, run, rc, ox + rx * cw, y)
                run = []
            if not run:
                rx = x
            rc = col
            run.append(c)
        _flush(fid, run, rc, ox + rx * cw, y)

    for srow in range(view_rows):
        prow = srow + sc
        if prow < page.h:
            text_row(page.chars[prow], page.cols[prow], srow)
    text_row(foot.chars[0], foot.cols[0], rows - 1)
    if page.h > view_rows:                       # page scrollbar
        th = max(1, view_rows * view_rows // page.h)
        ty = (view_rows - th) * sc // max(page.h - view_rows, 1)
        _rects([(region.width - 3, top - (ty + th) * ch, region.width, top - ty * ch)], GRAPH_TEXT)
    UI["last"][area.as_pointer()] = {"page": page, "foot": foot, "cw": cw, "ch": ch, "cols": cols, "rows": rows,
                                     "ox": ox, "oy": oy, "scroll": sc}
    gpu.state.blend_set("NONE")


def _flush(fid, run, color, x, y):
    text = "".join(run).rstrip()
    if not text:
        return
    blf.color(fid, *color)
    blf.position(fid, x, y, 0)
    blf.draw(fid, text)


def _find(hits, x, y):
    for h in reversed(hits):
        hx, hy, hw, hh, hd = h
        if hx <= x < hx + hw and hy <= y < hy + hh:
            return h
    return None


# ==========================================================================
# Window and events
# ==========================================================================
def ex_areas():
    out = []
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.as_pointer() in OURS:
                out.append((win, area))
    return out


def _adopt():
    """After loading a file: text editors alone in a window, without header or text, are EX windows."""
    for win in bpy.context.window_manager.windows:
        areas = win.screen.areas
        if len(areas) == 1 and areas[0].type == "TEXT_EDITOR":
            sp = areas[0].spaces.active
            if sp.text is None and not sp.show_region_header:
                OURS.add(areas[0].as_pointer())


def _start_modal(win, area):
    reg = next((r for r in area.regions if r.type == "WINDOW"), None)
    if reg is None:
        return
    try:
        with bpy.context.temp_override(window=win, area=area, region=reg):
            bpy.ops.r5.ex_ui("INVOKE_DEFAULT")
    except Exception as exc:  # noqa: BLE001
        print(f"[R5 EX] could not start the window: {exc}")


def _tick():
    areas = ex_areas()
    if not areas:
        return 1.0
    sample()
    for win, area in areas:
        if area.as_pointer() not in RUNNING:
            _start_modal(win, area)
        area.tag_redraw()
    return UI["interval"]


def _cell(area, region, event):
    m = UI["last"].get(area.as_pointer())
    if m is None:
        return None, None, None
    mx = event.mouse_x - region.x
    my = event.mouse_y - region.y
    top = region.height - m["oy"]
    col = int((mx - m["ox"]) // m["cw"])
    row = int((top - my) // m["ch"])
    return col, row, m


def hit_at(area, col, row):
    m = UI["last"].get(area.as_pointer())
    if m is None:
        return None
    if row == m["rows"] - 1:
        return _find(m["foot"].hits, col, 0)
    return _find(m["page"].hits, col, row + m["scroll"])


class R5_OT_ex_ui(bpy.types.Operator):
    """Runs the EX window (mouse and keys)"""
    bl_idname = "r5.ex_ui"
    bl_label = "R5 EX window"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        area = context.area
        if area is None or area.as_pointer() not in OURS or area.as_pointer() in RUNNING:
            return {"CANCELLED"}
        self.ptr = area.as_pointer()
        self.drag = None
        RUNNING.add(self.ptr)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _area(self, context):
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.as_pointer() == self.ptr:
                    return area
        return None

    def _finish(self):
        RUNNING.discard(self.ptr)
        OURS.discard(self.ptr)
        return {"FINISHED"}

    def modal(self, context, event):
        area = self._area(context)
        if area is None:
            return self._finish()
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is None:
            return {"PASS_THROUGH"}
        try:
            return self._event(context, area, region, event)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            flash(f"error: {exc}")
            return {"RUNNING_MODAL"}

    def _event(self, context, area, region, event):
        col, row, m = _cell(area, region, event)
        t, v = event.type, event.value
        inside = 0 <= event.mouse_x - region.x < region.width and 0 <= event.mouse_y - region.y < region.height
        if t == "MOUSEMOVE":
            if self.drag is not None:
                d = self.drag
                dx = event.mouse_x - d["mx"]
                if abs(dx) > 3:
                    d["moved"] = True
                if d["moved"]:
                    o = resolve(d["owner"])
                    if o is not None:
                        rna = o.bl_rna.properties[d["prop"]]
                        fine = 0.1 if event.shift else 1.0
                        nv = d["v0"] + (dx / m["cw"]) * d["step"] * fine
                        nv = max(rna.hard_min, min(rna.hard_max, nv))
                        set_value(d["owner"], d["prop"], int(round(nv)) if rna.type == "INT" else nv, push=False)
                return {"RUNNING_MODAL"}
            prev = UI["mouse"]
            UI["mouse"] = (col, row) if inside and m is not None else (-1, -1)
            if UI["mouse"] != prev:
                area.tag_redraw()
            return {"PASS_THROUGH"}
        if not inside or m is None:
            return {"PASS_THROUGH"}
        if event.ctrl and t in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and v == "PRESS":
            self._zoom(1 if t == "WHEELUPMOUSE" else -1)
            return {"RUNNING_MODAL"}
        if event.ctrl or event.oskey or event.alt:
            return {"PASS_THROUGH"}           # Ctrl+Z, Ctrl+S... keep working
        if t == "LEFTMOUSE":
            if v == "PRESS":
                h = hit_at(area, col, row)
                if h is None:
                    return {"RUNNING_MODAL"}
                hd = h[4]
                if "num" in hd:
                    owner, prop = hd["num"]
                    o = resolve(owner)
                    if o is not None:
                        rna = o.bl_rna.properties[prop]
                        val, _lo, _hi, step = _num_info(o, prop, rna)
                        self.drag = {"owner": owner, "prop": prop, "v0": val, "step": step,
                                     "mx": event.mouse_x, "moved": False, "click": hd.get("click")}
                    return {"RUNNING_MODAL"}
                if "click" in hd:
                    hd["click"]()
                    area.tag_redraw()
                return {"RUNNING_MODAL"}
            if v == "RELEASE" and self.drag is not None:
                d, self.drag = self.drag, None
                if d["moved"]:
                    undo_push(f"R5: {d['prop']}")
                elif d["click"]:
                    d["click"]()
                area.tag_redraw()
            return {"RUNNING_MODAL"}
        if t == "RIGHTMOUSE" and v == "PRESS":
            h = hit_at(area, col, row)
            if h is not None and "rclick" in h[4]:
                h[4]["rclick"]()
            return {"RUNNING_MODAL"}
        if t in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            d = 1 if t == "WHEELUPMOUSE" else -1
            h = hit_at(area, col, row)
            if h is not None and "num" in h[4]:
                owner, prop = h[4]["num"]
                o = resolve(owner)
                if o is not None:
                    rna = o.bl_rna.properties[prop]
                    val, lo, hi, step = _num_info(o, prop, rna)
                    nv = max(lo, min(hi, val + d * step * (10 if event.shift else 1)))
                    set_value(owner, prop, int(round(nv)) if rna.type == "INT" else nv)
            elif h is not None and "wheel" in h[4]:
                h[4]["wheel"](d)
            else:
                UI["scroll"] = max(0, UI["scroll"] - d * 3)
            area.tag_redraw()
            return {"RUNNING_MODAL"}
        if v == "PRESS":
            digits = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8,
                      "NINE": 9, "NUMPAD_1": 1, "NUMPAD_2": 2, "NUMPAD_3": 3, "NUMPAD_4": 4, "NUMPAD_5": 5,
                      "NUMPAD_6": 6, "NUMPAD_7": 7, "NUMPAD_8": 8, "NUMPAD_9": 9}
            if t in digits:
                toggle_box(digits[t])
            elif t in {"NUMPAD_PLUS", "EQUAL"}:
                self._zoom(1)
            elif t in {"NUMPAD_MINUS", "MINUS"}:
                self._zoom(-1)
            elif t == "HOME":
                UI["scroll"] = 0
            elif t in {"PAGE_DOWN", "DOWN_ARROW"}:
                UI["scroll"] += (m["rows"] - 2) if t == "PAGE_DOWN" else 1
            elif t in {"PAGE_UP", "UP_ARROW"}:
                UI["scroll"] = max(0, UI["scroll"] - ((m["rows"] - 2) if t == "PAGE_UP" else 1))
            elif t == "ESC" and self.drag is not None:
                d, self.drag = self.drag, None
                set_value(d["owner"], d["prop"], d["v0"], push=False)
            area.tag_redraw()
        if t.startswith("TIMER") or t in {"WINDOW_DEACTIVATE", "NONE"}:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    @staticmethod
    def _zoom(d):
        p = _prefs()
        size = max(8, min(40, _font_size() + d))
        if p is not None:
            p.font_size = size
        UI["font"] = size
        redraw()


class R5_OT_ex_edit(bpy.types.Operator):
    """Edit this value with a Blender field"""
    bl_idname = "r5.ex_edit"
    bl_label = "Edit value"
    bl_options = {"INTERNAL"}

    owner: StringProperty(options={"SKIP_SAVE"})
    prop: StringProperty(options={"SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=round(360 * context.preferences.system.ui_scale))

    def draw(self, context):
        lay = self.layout
        o = resolve(self.owner)
        if o is None or self.prop not in o.bl_rna.properties:
            lay.label(text="Not available any more")
            return
        rna = o.bl_rna.properties[self.prop]
        lay.label(text=rna.name)
        if getattr(rna, "is_array", False) and rna.subtype not in {"COLOR", "COLOR_GAMMA"}:
            lay.column(align=True).prop(o, self.prop, text="")
        else:
            lay.prop(o, self.prop, text="")
        if rna.description:
            col = lay.column(align=True)
            col.scale_y = 0.8
            for line in textwrap.wrap(rna.description, 56):
                col.label(text=line)

    def execute(self, context):
        return {"FINISHED"}


class R5_OT_ex_open(bpy.types.Operator):
    """Open the R5 Map Loader interface in its own window (place it on another screen, resize it freely)"""
    bl_idname = "r5.ex_open"
    bl_label = "R5 Map Loader window"

    def execute(self, context):
        if ex_areas():
            self.report({"INFO"}, "The R5 window is already open")
            redraw()
            return {"FINISHED"}
        win = context.window
        src = context.area if context.area is not None else max(win.screen.areas, key=lambda a: a.width * a.height)
        wm = context.window_manager
        before = {w.as_pointer() for w in wm.windows}
        with context.temp_override(window=win, area=src):
            bpy.ops.screen.area_dupli("INVOKE_DEFAULT")
        new = [w for w in wm.windows if w.as_pointer() not in before]
        if not new:
            self.report({"ERROR"}, "Blender did not open a new window")
            return {"CANCELLED"}
        nw = new[0]
        area = nw.screen.areas[0]
        area.type = "TEXT_EDITOR"
        sp = area.spaces.active
        sp.text = None
        for attr in ("show_region_header", "show_region_footer", "show_region_ui"):
            if hasattr(sp, attr):
                setattr(sp, attr, False)
        OURS.add(area.as_pointer())
        sample()
        _start_modal(nw, area)
        return {"FINISHED"}


def _header_button(self, context):
    self.layout.operator(R5_OT_ex_open.bl_idname, text="R5", icon="WINDOW")


def _window_menu(self, context):
    self.layout.separator()
    self.layout.operator(R5_OT_ex_open.bl_idname, icon="WINDOW")


def _sidebar_changed(self, context):
    ui.SIDEBAR[0] = self.show_sidebar
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            area.tag_redraw()


class R5_EX_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    show_sidebar: BoolProperty(name="Also show the sidebar (N) panels", default=False, update=_sidebar_changed,
                               description="The EX version puts its interface in a separate window; this brings "
                                           "the classic panels back in 3D View > Sidebar (N) as well")
    font_size: IntProperty(name="Window text size", default=13, min=8, max=40,
                           description="Size of the text in the R5 window (also Ctrl+wheel or + / - in it)")
    hidden_boxes: StringProperty(name="Hidden boxes", default="")

    def draw(self, context):
        col = self.layout.column()
        col.operator(R5_OT_ex_open.bl_idname, icon="WINDOW")
        col.prop(self, "font_size")
        col.prop(self, "show_sidebar")


@bpy.app.handlers.persistent
def _load_post(_dummy):
    OURS.clear()
    RUNNING.clear()
    UI["last"].clear()
    _adopt()


CLASSES = (R5_EX_Preferences, R5_OT_ex_ui, R5_OT_ex_edit, R5_OT_ex_open)


def register():
    path = os.path.join(bpy.utils.system_resource("DATAFILES", path="fonts") or "", "DejaVuSansMono.woff2")
    fid = blf.load(path) if os.path.exists(path) else -1
    FONT["id"], FONT["path"] = (fid if fid >= 0 else 0), path
    for c in CLASSES:
        bpy.utils.register_class(c)
    p = _prefs()
    ui.SIDEBAR[0] = bool(p.show_sidebar) if p is not None else False
    _HANDLE.append(bpy.types.SpaceTextEditor.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL"))
    bpy.types.VIEW3D_HT_header.append(_header_button)
    bpy.types.TOPBAR_MT_window.append(_window_menu)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=1.0, persistent=True)
    _adopt()


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    for fn, menu in ((_header_button, bpy.types.VIEW3D_HT_header), (_window_menu, bpy.types.TOPBAR_MT_window)):
        try:
            menu.remove(fn)
        except Exception:  # noqa: BLE001
            pass
    while _HANDLE:
        try:
            bpy.types.SpaceTextEditor.draw_handler_remove(_HANDLE.pop(), "WINDOW")
        except Exception:  # noqa: BLE001
            pass
    ui.SIDEBAR[0] = True
    for c in reversed(CLASSES):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
    if FONT["id"] and FONT["path"]:
        try:
            blf.unload(FONT["path"])
        except Exception:  # noqa: BLE001
            pass
    OURS.clear()
    RUNNING.clear()
