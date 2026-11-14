"""Lecture des fichiers de placement de props (.ent / .json / .csv).

Format .ent (R5Reloaded / Source) :
    {
    "classname" "prop_dynamic"
    "model" "mdl/props/foo/bar.rmdl"
    "origin" "100 200 30"
    "angles" "0 90 0"
    "modelscale" "1"
    }

Format .json :
    [ {"model": "mdl/...rmdl", "origin": [x,y,z], "angles": [pitch,yaw,roll], "scale": 1.0}, ... ]
    (ou {"props": [...]})

Format .csv :  model,x,y,z,pitch,yaw,roll,scale
"""

import csv
import json
import os
import re
from dataclasses import dataclass

_KV_RE = re.compile(r'"([^"]*)"\s+"([^"]*)"')


@dataclass
class Placement:
    model: str            # chemin brut ("mdl/props/foo/bar.rmdl")
    origin: tuple         # unités du jeu
    angles: tuple         # pitch, yaw, roll (degrés)
    scale: float = 1.0
    classname: str = ""
    name: str = ""


def model_key(model_path: str) -> str:
    """'mdl/props/foo/Bar_01.rmdl' -> 'bar_01' (clé de recherche dans la bibliothèque)."""
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


_ZIP_RE = re.compile(r"_zipline_rest_point_(\d+)$")


def parse_ent_text(text):
    """Renvoie (placements, tyroliennes). Une tyrolienne = liste de points (unités du jeu)."""
    out, zips = [], []
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
                p = _from_dict(ent)
                if p:
                    out.append(p)
                if ent.get("classname", "").startswith("zipline") and "_zipline_rest_point_0" in ent:
                    pts = sorted((int(_ZIP_RE.search(k).group(1)), _vec(v))
                                 for k, v in ent.items() if _ZIP_RE.search(k))
                    if len(pts) >= 2:
                        zips.append([p_ for _i, p_ in pts])
            ent = None
            continue
        if ent is not None:
            m = _KV_RE.match(s)
            if m:
                ent[m.group(1).lower()] = m.group(2)
    return out, zips


def parse_ent(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_ent_text(f.read())[0]


def ent_ziplines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_ent_text(f.read())[1]


def _from_dict(d):
    model = d.get("model") or d.get("modelname") or ""
    if not model or model.startswith("*"):   # "*12" = brush du BSP, pas un modèle
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
    """Fichier .mprt de Legion+ (props statiques du BSP).

    En-tête : "mprt", u32 version, u32 nombre.
    Entrée  : nom (C-string, sans dossier ni extension), origin xyz,
              rotation (roll, pitch, yaw) en degrés, scale.
    """
    import struct
    with open(path, "rb") as f:
        buf = f.read()
    magic, version, count = struct.unpack_from("<4sII", buf, 0)
    if magic != b"mprt":
        raise ValueError(f"{path} n'est pas un fichier mprt")
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
        # stocké (roll, pitch, yaw) -> Placement attend (pitch, yaw, roll)
        out.append(Placement(name, (v[0], v[1], v[2]), (v[4], v[5], v[3]), scale, "prop_static"))
    return out


def load_placements(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mprt":
        return parse_mprt(path)
    if ext == ".json":
        return parse_json(path)
    if ext == ".csv":
        return parse_csv(path)
    return parse_ent(path)
