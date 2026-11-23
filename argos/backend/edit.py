"""Retouche simple par ImageMagick (repris des outils image d'Odysseus) : pivoter, recadrer, convertir.

Écriture sûre : résultat sous un nom caché dans le dossier de destination, mis sur disque (fsync), puis
`os.replace` (remplacer l'original) ou posé sous un nom libre (« photo (2).png ») — jamais d'écrasement non voulu.
JPEG : qualité gardée (`-quality` de l'original si ImageMagick la retrouve, sinon 92), profil ICC et EXIF gardés,
orientation EXIF remise à « normale » (`-auto-orient` avant toute chose : ce qu'on voit est ce qu'on enregistre).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from typing import Optional

# Formats d'enregistrement proposés : extension → nom affiché
SAVE_FORMATS = {
    "jpg": "JPEG", "png": "PNG", "webp": "WebP", "tif": "TIFF", "bmp": "BMP", "gif": "GIF",
}


class EditError(Exception):
    pass


def magick_command() -> Optional[list[str]]:
    if shutil.which("magick"):
        return ["magick"]
    if shutil.which("convert"):
        return ["convert"]
    return None


@contextlib.contextmanager
def magick_paths(src: str, out: str):
    """(entrée, sortie) à donner à ImageMagick. Il lit « %d », « %o », « %x »… d'un chemin comme un GABARIT, à
    l'entrée comme à la sortie, et « %% » n'y échappe pas (« 50%off.jpg », dossier « Taux 5%o » : « no images
    defined », audit de novembre 2026, repris d'Odysseus) → avec un « % », il ne voit que des liens aux noms sûrs
    dans un dossier privé. `out` doit exister (fichier vide) : le lien écrit à travers."""
    if "%" not in src and "%" not in out:
        yield src, out
        return
    safe = tempfile.mkdtemp(prefix="argos-img-")
    try:
        im_src = os.path.join(safe, "in" + os.path.splitext(src)[1].lower())
        os.symlink(os.path.abspath(src), im_src)
        if not os.path.lexists(out):
            with open(out, "x"):
                pass
        im_out = os.path.join(safe, "out")
        os.symlink(os.path.abspath(out), im_out)
        yield im_src, im_out
        if os.path.isfile(im_out) and not os.path.islink(im_out):
            shutil.move(im_out, out)               # un codeur a recréé la sortie au lieu d'écrire à travers
    finally:
        shutil.rmtree(safe, ignore_errors=True)


def free_name(path: str) -> str:
    """« photo.png » s'il est libre, sinon « photo (2).png », « photo (3).png »…"""
    if not os.path.lexists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.lexists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def _quality(src: str) -> str:
    try:
        with magick_paths(src, os.devnull) as (im_src, _out):
            out = subprocess.run(["identify", "-format", "%Q", im_src + "[0]"], capture_output=True, text=True,
                                 errors="replace", timeout=20)
        q = int(out.stdout.strip() or 0)
        if 1 <= q <= 100:
            return str(q)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return "92"


def build_args(src: str, rotation: int = 0, crop: Optional[tuple[int, int, int, int]] = None,
               out_ext: str = "", quality: str = "") -> list[str]:
    """Arguments d'ImageMagick (sans la commande ni la sortie). crop = (x, y, l, h) en pixels de l'image AFFICHÉE
    (après l'orientation EXIF et le quart de tour)."""
    args = [os.path.abspath(src) + "[0]", "-auto-orient"]
    if rotation % 4:
        args += ["-rotate", str(90 * (rotation % 4))]
    if crop is not None:
        x, y, w, h = crop
        args += ["-crop", f"{w}x{h}+{x}+{y}", "+repage"]
    ext = (out_ext or os.path.splitext(src)[1].lstrip(".")).lower()
    if ext in ("jpg", "jpeg", "webp"):
        args += ["-quality", quality or "92"]
    if ext in ("jpg", "jpeg", "bmp"):
        args += ["-background", "white", "-alpha", "remove", "-alpha", "off"]
    return args


def apply(src: str, dest: str, rotation: int = 0, crop=None, replace: bool = False) -> str:
    """Écrit le résultat dans `dest` (remplacé seulement si `replace`, sinon nom libre). Renvoie le chemin écrit."""
    cmd = magick_command()
    if cmd is None:
        raise EditError("ImageMagick")
    if replace and os.path.islink(dest):
        dest = os.path.realpath(dest)             # lien symbolique : c'est la photo visée qu'on remplace, le lien reste
    folder = os.path.dirname(os.path.abspath(dest)) or "."
    ext = os.path.splitext(dest)[1].lstrip(".").lower()
    quality = _quality(src) if ext in ("jpg", "jpeg", "webp") and os.path.splitext(src)[1].lower() in (
        ".jpg", ".jpeg", ".webp") else ""
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".argos-", suffix="." + (ext or "png"))
    os.close(fd)
    try:
        with magick_paths(src, tmp) as (im_src, im_out):
            r = subprocess.run(cmd + build_args(im_src, rotation, crop, ext, quality)
                               + [f"{ext}:{im_out}" if ext else im_out],
                               capture_output=True, text=True, errors="replace", timeout=300)
        if r.returncode != 0 or os.path.getsize(tmp) == 0:
            raise EditError((r.stderr or "").strip().splitlines()[-1] if r.stderr.strip() else "ImageMagick")
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        if replace and os.path.exists(dest):
            st = os.stat(dest)
            os.chmod(tmp, st.st_mode & 0o7777)
            target = dest
            os.replace(tmp, target)
        else:
            target = _place_new(tmp, dest)
            os.chmod(target, 0o666 & ~_umask())
        dfd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        return target
    except OSError as e:
        raise EditError(e.strerror or str(e)) from e
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _place_new(tmp: str, dest: str) -> str:
    """Pose `tmp` sous un nom libre : lien dur (échoue si le nom vient d'être pris : on prend le suivant), repli par
    renommage là où les liens durs n'existent pas (FAT, exFAT)."""
    for _ in range(50):
        target = free_name(dest)
        try:
            os.link(tmp, target)
            os.remove(tmp)
            return target
        except FileExistsError:
            continue
        except OSError:
            if os.path.lexists(target):
                continue
            os.rename(tmp, target)
            return target
    raise EditError(dest)


def _umask() -> int:
    m = os.umask(0)
    os.umask(m)
    return m


UNDO_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "argos", "undo")


def backup(path: str) -> str:
    """Copie de l'original avant de le remplacer (« Annuler » du toast) ; balayée au démarrage suivant."""
    os.makedirs(UNDO_DIR, mode=0o700, exist_ok=True)
    fd, copy = tempfile.mkstemp(dir=UNDO_DIR, suffix=os.path.splitext(path)[1])
    os.close(fd)
    shutil.copy2(path, copy)
    return copy


def restore(copy: str, path: str):
    """Remet l'original (copie cachée à côté, fsync, échange) puis efface la sauvegarde. Lien symbolique : la photo
    visée est remise, le lien reste."""
    path = os.path.realpath(path)
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".argos-", suffix=os.path.splitext(path)[1])
    os.close(fd)
    try:
        shutil.copy2(copy, tmp)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    os.remove(copy)


def sweep_backups(max_age_s: float = 86400):
    import time
    try:
        for name in os.listdir(UNDO_DIR):
            p = os.path.join(UNDO_DIR, name)
            if time.time() - os.path.getmtime(p) > max_age_s:
                os.remove(p)
    except OSError:
        pass
