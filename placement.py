"""Prop placement readers (.mprt / .ent / .json / .csv).

.ent format (R5Reloaded / Source):
    {
    "classname" "prop_dynamic"
    "model" "mdl/props/foo/bar.rmdl"
    "origin" "100 200 30"
    "angles" "0 90 0"
    "modelscale" "1"
    }

.json format:
    [ {"model": "mdl/...rmdl", "origin": [x,y,z], "angles": [pitch,yaw,roll], "scale": 1.0}, ... ]
    (or {"props": [...]})

.csv format:  model,x,y,z,pitch,yaw,roll,scale
"""

import csv
import json
import os
import re
import struct
from dataclasses import dataclass, field

_KV_RE = re.compile(r'"([^"]*)"\s+"([^"]*)"')
_ZIP_RE = re.compile(r"_zipline_rest_point_(\d+)$")


@dataclass
class Placement:
    model: str            # raw path ("mdl/props/foo/bar.rmdl")
    origin: tuple         # game units
    angles: tuple         # pitch, yaw, roll (degrees)
    scale: float = 1.0
    classname: str = ""
    name: str = ""


@dataclass
class Zone:
    """A point of interest read from the entities (game units)."""
    name: str
    origin: tuple
    radius: float          # game units (0 = unknown)
    kind: str = ""


@dataclass
class EntData:
    placements: list = field(default_factory=list)
    ziplines: list = field(default_factory=list)       # [[(x,y,z), ...], ...]
    zones: list = field(default_factory=list)          # [Zone]
    sun: dict = field(default_factory=dict)            # light_environment keys, if present
    sky: dict = field(default_factory=dict)            # sky_camera / skybox keys, if present


def model_key(model_path: str) -> str:
    """'mdl/props/foo/Bar_01.rmdl' -> 'bar_01' (lookup key in the library)."""
    base = model_path.replace("\\", "/").rsplit("/", 1)[-1]
    base = os.path.splitext(base)[0]
    return base.lower()


def _vec(text, n=3, default=0.0):
    if isinstance(text, (list, tuple)):
        vals = [float(v) for v in text]
    else:
        vals = []
        for p in str(text).replace(",", " ").split():
            try:
                vals.append(float(p))
            except ValueError:
                pass
    vals = (vals + [default] * n)[:n]
    return tuple(vals)


def _float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _iter_entities(text):
    ent = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("{"):
            ent = {}
            continue
        if s.startswith("}"):
            if ent:
                yield ent
            ent = None
            continue
        if ent is not None:
            m = _KV_RE.match(s)
            if m:
                ent[m.group(1).lower()] = m.group(2)


def parse_ent_full(text):
    """Read an entity file: props, zipline cables, points of interest, sun and sky info."""
    data = EntData()
    for ent in _iter_entities(text):
        p = _from_dict(ent)
        if p:
            data.placements.append(p)
        cls = ent.get("classname", "")
        if cls.startswith("zipline") and "_zipline_rest_point_0" in ent:
            pts = sorted((int(_ZIP_RE.search(k).group(1)), _vec(v))
                         for k, v in ent.items() if _ZIP_RE.search(k))
            if len(pts) >= 2:
                data.ziplines.append([p_ for _i, p_ in pts])
        ed = ent.get("editorclass", "")
        if ent.get("zone_name"):
            data.zones.append(Zone(ent["zone_name"], _vec(ent.get("origin", "0 0 0")),
                                   _float(ent.get("script_radius"), 0.0), "named"))
        elif ed == "info_survival_loot_zone":
            data.zones.append(Zone(ent.get("zone_class", "loot_zone"), _vec(ent.get("origin", "0 0 0")),
                                   _float(ent.get("script_radius"), 0.0), "loot"))
        if cls == "light_environment":
            data.sun = dict(ent)
        if cls in ("sky_camera", "skybox_cam", "env_skybox") or "skyname" in ent:
            data.sky.update(ent)
    return data


def parse_ent_text(text):
    """Compatibility: return (placements, ziplines)."""
    d = parse_ent_full(text)
    return d.placements, d.ziplines


def parse_ent(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_ent_full(f.read()).placements


def _from_dict(d):
    model = d.get("model") or d.get("modelname") or ""
    if not model or model.startswith("*"):   # "*12" = BSP brush, not a model
        return None
    low = model.lower()
    if not (low.endswith(".rmdl") or low.endswith(".mdl") or "/" in low):
        return None
    scale = d.get("modelscale", d.get("scale", 1.0))
    try:
        scale = float(scale if not isinstance(scale, (list, tuple)) else scale[0])
    except (TypeError, ValueError):
        scale = 1.0
    return Placement(
        model=model,
        origin=_vec(d.get("origin", "0 0 0")),
        angles=_vec(d.get("angles", "0 0 0")),
        scale=scale if scale > 0 else 1.0,
        classname=str(d.get("classname", "")),
        name=str(d.get("targetname", d.get("name", ""))),
    )


def parse_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("props") or data.get("entities") or data.get("placements") or []
    out = []
    for d in data:
        if isinstance(d, dict):
            p = _from_dict({k.lower(): v for k, v in d.items()})
            if p:
                out.append(p)
    return out


def parse_csv(path):
    out = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().lower() in ("model", "#") or row[0].startswith("#"):
                continue
            try:
                vals = [float(v) for v in row[1:8]]
            except ValueError:
                continue
            vals += [0.0] * (7 - len(vals))
            out.append(Placement(row[0].strip(), tuple(vals[0:3]), tuple(vals[3:6]),
                                 vals[6] if len(row) > 7 and vals[6] > 0 else 1.0))
    return out


def parse_mprt(path):
    """Legion+ .mprt file (static props of the BSP).

    Header: "mprt", u32 version, u32 count.
    Entry:  name (C string, no folder or extension), origin xyz,
            rotation (roll, pitch, yaw) in degrees, scale.
    """
    with open(path, "rb") as f:
        buf = f.read()
    magic, version, count = struct.unpack_from("<4sII", buf, 0)
    if magic != b"mprt":
        raise ValueError(f"{path} is not an mprt file")
    has_scale = version >= 3
    rec = struct.Struct("<7f" if has_scale else "<6f")
    out = []
    off = 12
    find = buf.index
    for _ in range(count):
        end = find(b"\0", off)
        name = buf[off:end].decode("utf-8", "replace")
        v = rec.unpack_from(buf, end + 1)
        off = end + 1 + rec.size
        scale = v[6] if has_scale and v[6] > 0 else 1.0
        # stored as (roll, pitch, yaw) -> Placement expects (pitch, yaw, roll)
        out.append(Placement(name, (v[0], v[1], v[2]), (v[4], v[5], v[3]), scale, "prop_static"))
    return out


# --------------------------------------------------------------------------
# ReMap / R5Reloaded custom maps (.nut Squirrel scripts)
# --------------------------------------------------------------------------
_NUT_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_NUT_ASSET_RE = re.compile(r'\$\s*"([^"]+\.rmdl)"', re.IGNORECASE)
_NUT_VEC_RE = re.compile(r"<\s*(-?[\d.eE+\-]+)\s*,\s*(-?[\d.eE+\-]+)\s*,\s*(-?[\d.eE+\-]+)\s*>")
# functions that only declare an asset or define the mover logic: not props
_NUT_SKIP = {"PrecacheModel", "SetValueForModelKey", "function", "if", "while", "for"}


def _nut_calls(text):
    """Yield (function name, argument text) for every call, with nested parentheses handled."""
    i, n = 0, len(text)
    while True:
        m = _NUT_CALL_RE.search(text, i)
        if not m:
            return
        depth, j = 1, m.end()
        in_str = False
        while j < n and depth:
            c = text[j]
            if in_str:
                if c == '"' and text[j - 1] != "\\":
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            j += 1
        yield m.group(1), text[m.end(): j - 1]
        i = m.end()


def _split_args(s):
    out, depth, cur, in_str = [], 0, [], False
    for c in s:
        if in_str:
            cur.append(c)
            if c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "(<[":
            depth += 1
        elif c in ")>]":
            depth -= 1
        elif c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(c)
    if cur:
        out.append("".join(cur).strip())
    return out


def parse_nut_text(text):
    """ReMap / map-editor scripts: every call whose first argument is an $"mdl/...rmdl" asset followed by two
    vectors is a prop (MapEditor_CreateProp, MapEditor_CreateMovingProp, CreateEditorProp...); calls with
    "Zipline" in their name give zipline cables. Comments are ignored."""
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    data = EntData()
    for fn, args in _nut_calls(text):
        if fn in _NUT_SKIP:
            continue
        parts = _split_args(args)
        if not parts:
            continue
        asset = _NUT_ASSET_RE.fullmatch(parts[0].strip())
        vecs = [_NUT_VEC_RE.fullmatch(p.strip()) for p in parts]
        if asset and len(parts) >= 3 and vecs[1] and vecs[2]:
            scale = 1.0
            if fn.endswith("CreateProp") and len(parts) >= 7:
                scale = _float(parts[6], 1.0)        # (asset, origin, angles, mantle, fade, realm, scale)
            data.placements.append(Placement(
                model=asset.group(1),
                origin=tuple(float(v) for v in vecs[1].groups()),
                angles=tuple(float(v) for v in vecs[2].groups()),
                scale=scale if scale > 0 else 1.0, classname=fn))
        elif "zipline" in fn.lower():
            pts = [tuple(float(v) for v in m.groups()) for m in vecs if m]
            if len(pts) >= 4:                         # (start, start angles, end, end angles, ...)
                data.ziplines.append([pts[0], pts[2]])
            elif len(pts) >= 2:
                data.ziplines.append([pts[0], pts[1]])
    return data


def parse_nut(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_nut_text(f.read())


def load_placements(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".nut":
        return parse_nut(path).placements
    if ext == ".mprt":
        return parse_mprt(path)
    if ext == ".json":
        return parse_json(path)
    if ext == ".csv":
        return parse_csv(path)
    return parse_ent(path)
