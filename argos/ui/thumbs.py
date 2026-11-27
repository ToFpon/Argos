"""Miniatures de la grille et de la bande : cache freedesktop (~/.cache/thumbnails, partagé avec Nautilus et
Odysseus), fabriquées sinon par deux fils. La DERNIÈRE demande passe en premier (les cases visibles quand on
fait défiler vite), une demande devenue inutile (case recyclée) est abandonnée."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from typing import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

from ..backend import thumbnails  # noqa: E402
from ..backend.folder import is_video  # noqa: E402
from ..backend.loader import image_size  # noqa: E402

WORKERS = 3
FRAME = 3                           # cadre blanc des photos (base.css, .argos-photo)
KEEP = 600                           # textures gardées en mémoire (≈ 600 × 256² × 4 = 150 Mo au pire)


class ThumbLoader:
    def __init__(self, pixels: int = 256):
        self.pixels = pixels
        self._tex: "OrderedDict[str, Gdk.Texture | None]" = OrderedDict()
        self._queue: "OrderedDict[str, tuple[float, list[Callable]]]" = OrderedDict()
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        for i in range(WORKERS):
            threading.Thread(target=self._run, daemon=True, name=f"argos-thumbs-{i}").start()

    def get(self, path: str, mtime: float, callback: Callable[[object], None]):
        """Texture de la miniature (None si impossible) : tout de suite si connue, sinon plus tard par callback."""
        with self._lock:
            if path in self._tex:
                self._tex.move_to_end(path)
                tex = self._tex[path]
                GLib.idle_add(lambda: (callback(tex), False)[1])
                return
            _m, cbs = self._queue.pop(path, (mtime, []))
            cbs.append(callback)
            self._queue[path] = (mtime, cbs)            # en fin : servie en premier (LIFO)
            self._cv.notify()

    def cancel(self, path: str, callback: Callable):
        with self._lock:
            item = self._queue.get(path)
            if item is not None and callback in item[1]:
                item[1].remove(callback)
                if not item[1]:
                    del self._queue[path]

    def forget(self, path: str):
        with self._lock:
            self._tex.pop(path, None)

    def _run(self):
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                path, (mtime, cbs) = self._queue.popitem(last=True)
            tex = self._make(path, mtime)
            with self._lock:
                self._tex[path] = tex
                while len(self._tex) > KEEP:
                    self._tex.popitem(last=False)
            for cb in cbs:          # tex LIÉ tout de suite : sinon la miniature suivante du fil arrivait à sa place
                GLib.idle_add(lambda cb=cb, tex=tex: (cb(tex), False)[1])

    def _make(self, path: str, mtime: float):
        """(texture, photo) à la taille de la case, comme gThumb : jamais plus grande que l'image elle-même (une
        icône de 48 px reste à 48 px), photo = sans transparence (elle aura un cadre). None si impossible."""
        pb = self._cached(path, mtime)
        if pb is None and not thumbnails.failed(path, mtime):
            kind = "video/" if is_video(path) else "image/"
            png = thumbnails.generate(path, kind, mtime, max(128, self.pixels))
            if png is not None:
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file(png)
                except GLib.Error:
                    pb = None
        if pb is None:
            return None
        w, h = pb.get_width(), pb.get_height()
        # taille d'origine SANS décoder (image_size) : get_file_info décodait un AVIF entier à chaque vignette,
        # miniature en cache ou pas
        # (vidéo : l'image tirée remplit la case, pas de taille « d'origine » à respecter)
        nat = (10 ** 6, 10 ** 6) if is_video(path) else image_size(path) or (w, h)
        box = self.pixels - (2 * FRAME if not pb.get_has_alpha() else 0)
        k = min(box / w, box / h)                               # remplit la case…
        k = min(k, max(nat[0], 1) / w, max(nat[1], 1) / h)       # …sans dépasser la taille de l'image
        if abs(k - 1) > 0.01:
            pb = pb.scale_simple(max(1, round(w * k)), max(1, round(h * k)), GdkPixbuf.InterpType.BILINEAR)
        return Gdk.Texture.new_for_pixbuf(pb), not pb.get_has_alpha()

    def _cached(self, path: str, mtime: float):
        """Miniature du cache lue UNE fois (date vérifiée sur le même pixbuf)."""
        name = thumbnails._md5(path) + ".png"
        wanted = thumbnails.size_class(max(128, self.pixels))
        order = list(thumbnails.SIZES)
        for cls in order[order.index(wanted):] + order[:order.index(wanted)][::-1]:
            png = os.path.join(thumbnails.CACHE, cls, name)
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(png)
            except GLib.Error:
                continue
            m = pb.get_option("tEXt::Thumb::MTime")
            if m is not None and m.isdigit() and int(m) != int(mtime):
                continue                                   # périmée : l'image a changé depuis
            return pb
        return None


_shared: dict = {}


def shared(pixels: int = 128) -> ThumbLoader:
    """Un chargeur par taille de case (grille, bande)."""
    if pixels not in _shared:
        _shared[pixels] = ThumbLoader(pixels)
    return _shared[pixels]


def forget_everywhere(path: str):
    for loader in _shared.values():
        loader.forget(path)
