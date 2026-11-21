"""Chargement des photos hors du fil principal, avec un petit cache (photo courante + voisines préchargées).

GdkPixbuf lit tous les formats installés (loaders) ; l'orientation EXIF est appliquée (photos de téléphone).
Une image animée (GIF, WebP animé) garde son `GdkPixbuf.PixbufAnimation` : la vue en fait défiler les images.
La texture (`Gdk.Texture`, immuable) est fabriquée dans le fil : le fil principal n'a plus qu'à la dessiner.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

CACHE_ITEMS = 7                     # courante + 3 de chaque côté au plus
MAX_PIXELS = 400_000_000            # au-delà, refus net (une image de 20 000 × 20 000 occupe 1,6 Go en mémoire)


@dataclass
class Picture:
    path: str
    texture: Optional[Gdk.Texture]
    width: int
    height: int
    size: int                           # octets sur le disque
    format: str                         # « JPEG », « PNG »… (nom de GdkPixbuf)
    animation: Optional[GdkPixbuf.PixbufAnimation] = None
    error: str = ""
    mtime: float = 0.0


def format_name(path: str) -> str:
    try:
        info = GdkPixbuf.Pixbuf.get_file_info(path)
        fmt = info[0] if isinstance(info, tuple) else info
        if fmt is not None:
            return fmt.get_name().upper()
    except GLib.Error:
        pass
    return os.path.splitext(path)[1].lstrip(".").upper()


def missing_loader(path: str) -> str:
    """Paquet à installer quand aucun loader ne lit ce format (Ubuntu 24.04)."""
    ext = os.path.splitext(path)[1].lower()
    return {".webp": "webp-pixbuf-loader", ".heic": "heif-gdk-pixbuf", ".heif": "heif-gdk-pixbuf",
            ".avif": "heif-gdk-pixbuf", ".jxl": "libjxl-gdk-pixbuf", ".svg": "librsvg2-common",
            ".svgz": "librsvg2-common"}.get(ext, "")


def load(path: str) -> Picture:
    """Lit la photo (bloquant : à appeler dans un fil)."""
    try:
        st = os.stat(path)
    except OSError as e:
        return Picture(path, None, 0, 0, 0, "", error=e.strerror or str(e))
    fmt = format_name(path)
    try:
        info = GdkPixbuf.Pixbuf.get_file_info(path)
        if isinstance(info, tuple) and info[0] is not None and info[1] * info[2] > MAX_PIXELS:
            from ..i18n import tr
            return Picture(path, None, info[1], info[2], st.st_size, fmt, error=tr("Image trop grande"),
                           mtime=st.st_mtime)
        anim = GdkPixbuf.PixbufAnimation.new_from_file(path)
        if not anim.is_static_image():
            first = anim.get_static_image()
            tex = Gdk.Texture.new_for_pixbuf(first)
            return Picture(path, tex, first.get_width(), first.get_height(), st.st_size, fmt, anim,
                           mtime=st.st_mtime)
        pb = anim.get_static_image()
        pb = pb.apply_embedded_orientation() or pb
        tex = Gdk.Texture.new_for_pixbuf(pb)
        return Picture(path, tex, pb.get_width(), pb.get_height(), st.st_size, fmt, mtime=st.st_mtime)
    except GLib.Error as e:
        from ..i18n import tr
        msg = e.message
        pkg = missing_loader(path)
        if pkg and e.matches(GdkPixbuf.pixbuf_error_quark(), GdkPixbuf.PixbufError.UNKNOWN_TYPE):
            msg = tr("Format non pris en charge : installez le paquet « {0} »").format(pkg)
        return Picture(path, None, 0, 0, st.st_size, fmt, error=msg, mtime=st.st_mtime)


class Loader:
    """Un fil de lecture, demandes servies dans l'ordre de priorité : la photo voulue d'abord, les voisines
    ensuite. Résultat rendu dans la boucle GLib (idle_add)."""

    def __init__(self):
        self._cache: "OrderedDict[str, Picture]" = OrderedDict()
        self._lock = threading.Lock()
        self._wanted: list[str] = []
        self._waiters: dict[str, list[Callable]] = {}
        self._event = threading.Event()
        self._busy: Optional[str] = None
        threading.Thread(target=self._run, daemon=True, name="argos-loader").start()

    def cached(self, path: str) -> Optional[Picture]:
        with self._lock:
            pic = self._cache.get(path)
            if pic is not None:
                self._cache.move_to_end(path)
            return pic

    def forget(self, path: str):
        with self._lock:
            self._cache.pop(path, None)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._wanted.clear()

    def request(self, path: str, callback: Optional[Callable[[Picture], None]] = None,
                preload: tuple[str, ...] = ()):
        """Demande la photo (callback dans la boucle GLib), puis les voisines. Remplace les demandes en attente."""
        pic = self.cached(path)
        if pic is not None and callback is not None:
            GLib.idle_add(lambda cb=callback: (cb(pic), False)[1])     # cb lié tout de suite : callback est remis à None
            callback = None
        with self._lock:
            if callback is not None:
                self._waiters.setdefault(path, []).append(callback)
            keep = set(preload) | {path}
            self._waiters = {p: cbs for p, cbs in self._waiters.items() if p in keep}
            self._wanted = [p for p in (path,) + tuple(preload) if p not in self._cache and p != self._busy]
        self._event.set()

    def _run(self):
        while True:
            self._event.wait()
            with self._lock:
                if not self._wanted:
                    self._event.clear()
                    continue
                path = self._wanted.pop(0)
                self._busy = path
            pic = load(path)
            with self._lock:
                self._busy = None
                self._cache[path] = pic
                self._cache.move_to_end(path)
                while len(self._cache) > CACHE_ITEMS:
                    self._cache.popitem(last=False)
                cbs = self._waiters.pop(path, [])
            for cb in cbs:
                GLib.idle_add(lambda cb=cb, pic=pic: (cb(pic), False)[1])
