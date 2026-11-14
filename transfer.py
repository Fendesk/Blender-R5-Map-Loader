"""Copie d'un export de map vers un autre disque, SANS les textures en double,
avec réduction optionnelle de la résolution des textures.

Les exports Legion+/RSX copient les mêmes textures dans chaque dossier de modèle
(World's Edge : 47,9 Go de PNG pour 8,5 Go de fichiers uniques). Ici :
  - tous les fichiers non-image (.mprt, .cast, .ent, ...) sont copiés à l'identique,
  - chaque texture n'est copiée qu'une fois dans <destination>/_shared_textures/
    (fichiers de même nom : la version la plus lourde = la plus détaillée est gardée),
  - option : les textures plus grandes que N px sont réduites (chargement ~4x plus rapide en
    1024 px, ~4x moins de mémoire vidéo). La réduction tourne dans plusieurs processus Blender
    en arrière-plan, en parallèle.
L'addon retrouve automatiquement les textures dans _shared_textures à l'import.
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

from .castfile import POOL_DIR

IMG_EXT = (".png", ".dds", ".tga", ".jpg", ".jpeg")

_WORKER = r'''
import sys, json, os, bpy
args = sys.argv[sys.argv.index("--") + 1:]
jobs = json.load(open(args[0], encoding="utf-8"))
start, step, max_size = int(args[1]), int(args[2]), int(args[3])
for src, dst in jobs[start::step]:
    tmp = dst + ".tmp.png"
    try:
        img = bpy.data.images.load(src)
        w, h = img.size
        f = max_size / max(w, h)
        if f < 1.0:
            img.scale(max(1, int(w * f)), max(1, int(h * f)))
        img.filepath_raw = tmp
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)
        os.replace(tmp, dst)
    except Exception as exc:
        print("R5_RESIZE_FAIL", src, exc)
'''


def png_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return None


def plan(src_root):
    """Renvoie (fichiers [(src, rel)], textures {nom: src}, taille totale)."""
    files, textures = [], {}
    for d, _dirs, fs in os.walk(src_root):
        if os.path.basename(d) == POOL_DIR:
            for f in fs:
                textures.setdefault(f.lower(), os.path.join(d, f))
            continue
        for f in fs:
            p = os.path.join(d, f)
            if f.lower().endswith(IMG_EXT):
                key = f.lower()
                old = textures.get(key)
                if old is None or os.path.getsize(p) > os.path.getsize(old):
                    textures[key] = p
            else:
                files.append((p, os.path.relpath(p, src_root)))
    total = sum(os.path.getsize(p) for p, _r in files) + sum(os.path.getsize(p) for p in textures.values())
    return files, textures, total


def free_space(path):
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    return shutil.disk_usage(path).free


def _copy(s, d):
    for attempt in range(4):
        try:
            shutil.copyfile(s, d)
            return
        except OSError:
            if attempt == 3:
                raise
            time.sleep(1.0 + attempt)


def copy_map(src_root, dst_root, max_size=0, progress=None, report=print, blender=None):
    files, textures, total = plan(src_root)
    if free_space(dst_root) < total * 1.02:
        raise RuntimeError(f"Pas assez de place : il faut jusqu'à {total * 1.02 / 2**30:.1f} Go, "
                           f"{free_space(dst_root) / 2**30:.1f} Go libres")
    pool = os.path.join(dst_root, POOL_DIR)
    os.makedirs(pool, exist_ok=True)

    plain = [(s, os.path.join(dst_root, r)) for s, r in files]
    resize = []
    for s in textures.values():
        d = os.path.join(pool, os.path.basename(s))
        dims = png_size(s) if max_size else None
        if dims and max(dims) > max_size:
            resize.append((s, d))
        else:
            plain.append((s, d))

    n_total = len(plain) + len(resize)
    done = 0
    t0 = time.time()
    for s, d in plain:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if not (os.path.isfile(d) and os.path.getsize(d) == os.path.getsize(s)):
            _copy(s, d)
        done += 1
        if progress and done % 50 == 0:
            progress(done / n_total)
        if done % 1000 == 0:
            report(f"[R5] copie {done}/{n_total} fichiers ({time.time() - t0:.0f}s)")

    todo = [(s, d) for s, d in resize if not os.path.isfile(d)]
    if todo:
        report(f"[R5] réduction de {len(todo)} textures à {max_size} px…")
        fd, jobs_path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(todo, f)
        fd, worker_path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_WORKER)
        n = max(1, min((os.cpu_count() or 4) - 1, 8))
        exe = blender or sys.executable
        procs = [subprocess.Popen([exe, "-b", "--factory-startup", "--python", worker_path, "--",
                                   jobs_path, str(i), str(n), str(max_size)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for i in range(n)]
        while any(p.poll() is None for p in procs):
            time.sleep(1.0)
            ok = sum(1 for _s, d in todo if os.path.isfile(d))
            if progress:
                progress((done + ok) / n_total)
        # Échecs éventuels : copie de l'original
        for s, d in todo:
            if not os.path.isfile(d):
                _copy(s, d)
        for p in (jobs_path, worker_path):
            try:
                os.remove(p)
            except OSError:
                pass
    report(f"[R5] copie terminée en {time.time() - t0:.0f}s")
    return len(files), len(textures), total
