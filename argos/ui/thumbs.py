"""Miniatures de la grille et de la bande : cache freedesktop (~/.cache/thumbnails, partagé avec Nautilus et
Odysseus), fabriquées sinon par deux fils. La DERNIÈRE demande passe en premier (les cases visibles quand on
fait défiler vite), une demande devenue inutile (case recyclée) est abandonnée."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib  # noqa: E402

from ..backend import thumbnails  # noqa: E402

WORKERS = 2
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
            for cb in cbs:
                GLib.idle_add(lambda cb=cb: (cb(tex), False)[1])

    def _make(self, path: str, mtime: float):
        png = thumbnails.lookup(path, mtime, self.pixels)
        if png is None and not thumbnails.failed(path, mtime):
            png = thumbnails.generate(path, "image/", mtime, self.pixels)
        if png is None:
            return None
        try:
            return Gdk.Texture.new_from_filename(png)
        except GLib.Error:
            return None


_shared = None


def shared() -> ThumbLoader:
    global _shared
    if _shared is None:
        _shared = ThumbLoader()
    return _shared
