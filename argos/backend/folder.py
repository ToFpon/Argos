"""Lecture d'un dossier : photos et sous-dossiers, triés comme Nautilus (ordre naturel : 2 < 10).

Sans GTK. Une photo = un fichier dont l'extension est connue (`IMAGE_EXTS`) ; on ne lit jamais le contenu
pour décider (un dossier de 5000 photos sur un NAS se liste en un `scandir`).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from dataclasses import dataclass, field

# Formats que GdkPixbuf sait lire sur Zorin / Ubuntu 24.04 (loaders de base, librsvg, webp-pixbuf-loader,
# heif-gdk-pixbuf pour HEIC / AVIF, libjxl pour JPEG XL). Un format sans loader installé est quand même listé :
# la visionneuse dit alors ce qu'il faut installer.
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp", ".dib", ".tif", ".tiff", ".svg", ".svgz",
    ".ico", ".heic", ".heif", ".avif", ".jxl", ".tga", ".pnm", ".pbm", ".pgm", ".ppm", ".xpm", ".xbm", ".icns",
}


def is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


def _glib_collate():
    lib = ctypes.CDLL(ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0")
    fn = lib.g_utf8_collate_key_for_filename
    fn.argtypes, fn.restype = [ctypes.c_char_p, ctypes.c_ssize_t], ctypes.c_void_p
    free = lib.g_free
    free.argtypes, free.restype = [ctypes.c_void_p], None
    return fn, free


_collate_fn, _g_free = _glib_collate()


def name_key(name: str) -> bytes:
    """g_utf8_collate_key_for_filename en octets (repris d'Odysseus : par PyGObject, la clé de strxfrm n'est pas
    toujours de l'UTF-8 et faisait lever une erreur sur tout le dossier)."""
    try:
        raw = name.encode("utf-8")
    except UnicodeEncodeError:
        raw = name.encode("utf-8", "surrogateescape").decode("utf-8", "replace").encode("utf-8")
    ptr = _collate_fn(raw.replace(b"\0", b""), -1)
    key = ctypes.string_at(ptr)
    _g_free(ptr)
    return key


@dataclass
class Entry:
    path: str
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    key: bytes = field(default=b"", repr=False)


def scan(folder: str, show_hidden: bool = False, sort_key: str = "name", reverse: bool = False
         ) -> tuple[list[Entry], list[Entry]]:
    """(sous-dossiers, photos) du dossier. Lève OSError si le dossier est illisible."""
    dirs: list[Entry] = []
    images: list[Entry] = []
    with os.scandir(folder) as it:
        for de in it:
            name = de.name
            if not show_hidden and name.startswith("."):
                continue
            try:
                if de.is_dir():
                    dirs.append(Entry(de.path, name, True, key=name_key(name)))
                elif is_image(name) and de.is_file():
                    st = de.stat()
                    images.append(Entry(de.path, name, False, st.st_size, st.st_mtime, name_key(name)))
            except OSError:
                continue
    dirs.sort(key=lambda e: e.key)
    images.sort(key=lambda e: e.key)                     # à égalité de date / taille : ordre des noms
    if sort_key == "date":
        images.sort(key=lambda e: e.mtime)
    elif sort_key == "size":
        images.sort(key=lambda e: e.size)
    if reverse:
        images.reverse()
    return dirs, images


def pictures_dir() -> str:
    """Dossier Images de l'utilisateur (xdg-user-dirs), sinon le dossier personnel."""
    try:
        from gi.repository import GLib
        p = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if p and os.path.isdir(p):
            return p
    except (ImportError, ValueError, AttributeError):
        pass
    return os.path.expanduser("~")


def human_size(n: int) -> str:
    from ..i18n import decimal, tr
    if n < 1000:
        return f"{n} {tr('o')}"
    for unit in (tr("Ko"), tr("Mo"), tr("Go"), tr("To")):
        n /= 1000
        if n < 1000 or unit == tr("To"):
            return f"{decimal(n)} {unit}"
    return ""
