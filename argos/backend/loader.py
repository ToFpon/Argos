"""Chargement des photos hors du fil principal, avec un petit cache (photo courante + voisines préchargées).

GdkPixbuf lit tous les formats installés (loaders) ; l'orientation EXIF est appliquée (photos de téléphone).
Une image animée (GIF, WebP animé) garde son `GdkPixbuf.PixbufAnimation` : la vue en fait défiler les images.
La texture (`Gdk.Texture`, immuable) est fabriquée dans le fil : le fil principal n'a plus qu'à la dessiner.
"""

from __future__ import annotations

import os
import struct
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

CACHE_ITEMS = 7                     # courante + 3 de chaque côté au plus
CACHE_BYTES = 640 * 1024 * 1024     # …et 640 Mo de pixels au plus : 7 AVIF de 48 Mpx décodés pesaient 1,3 Go
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
    video: bool = False                 # vidéo : lue par Gtk.Video, pas de texture
    duration: float = 0.0               # secondes (vidéo)
    codecs: tuple = ()                  # (« H.264 », « AAC »…) d'après ffprobe (vidéo)


VIDEO_CODECS = {"h264": "H.264", "hevc": "HEVC (H.265)", "vp8": "VP8", "vp9": "VP9", "av1": "AV1",
                "mpeg4": "MPEG-4", "mpeg2video": "MPEG-2", "aac": "AAC", "mp3": "MP3", "opus": "Opus",
                "vorbis": "Vorbis", "ac3": "AC-3", "eac3": "E-AC-3", "flac": "FLAC", "dts": "DTS"}


def probe_video(path: str) -> tuple[int, int, float, tuple]:
    """(largeur, hauteur, durée, codecs) par ffprobe ; zéros si ffprobe manque ou échoue."""
    import json
    import shutil
    import subprocess
    if not shutil.which("ffprobe"):
        return 0, 0, 0.0, ()
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                              os.path.abspath(path)], capture_output=True, text=True, errors="replace", timeout=15).stdout
        data = json.loads(out or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0, 0, 0.0, ()
    w = h = 0
    codecs = []
    for s in data.get("streams", []):
        kind, name = s.get("codec_type"), s.get("codec_name") or ""
        if kind == "video" and not (s.get("disposition") or {}).get("attached_pic"):
            if not w:
                w, h = int(s.get("width") or 0), int(s.get("height") or 0)
                rot = (s.get("tags") or {}).get("rotate") or next(
                    (d.get("rotation") for d in s.get("side_data_list", []) if "rotation" in d), 0)
                try:
                    if int(float(rot)) % 180:
                        w, h = h, w
                except (TypeError, ValueError):
                    pass
            codecs.append(VIDEO_CODECS.get(name, name.upper()))
        elif kind == "audio" and name:
            codecs.append(VIDEO_CODECS.get(name, name.upper()))
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    return w, h, duration, tuple(dict.fromkeys(c for c in codecs if c))


HEIF_EXTS = {".avif": "AVIF", ".avifs": "AVIF", ".heic": "HEIC", ".heif": "HEIF", ".hif": "HEIF"}
HEADER_BYTES = 512 * 1024


def _heif_size(path: str) -> Optional[tuple[int, int]]:
    """Taille d'un AVIF / HEIC lue dans son EN-TÊTE (boîte ISOBMFF « ispe »), sans décoder. Le loader heif de
    GdkPixbuf DÉCODE l'image entière pour get_file_info (0,7 s pour un AVIF de 24 Mpx, vu chez Tof : gros
    dossiers d'AVIF qui « galèrent »). Plusieurs « ispe » (tuiles d'un HEIC de téléphone, vignette intégrée,
    couche alpha) : la plus grande est l'image entière. Quart de tour « irot » impair : largeur et hauteur
    échangées (libheif l'applique au décodage)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(HEADER_BYTES)
    except OSError:
        return None
    best = None
    i = data.find(b"ispe")
    while i >= 4:
        if i + 16 <= len(data) and struct.unpack(">I", data[i - 4:i])[0] >= 20:
            w, h = struct.unpack(">II", data[i + 8:i + 16])
            if w and h and (best is None or w * h > best[0] * best[1]):
                best = (w, h)
        i = data.find(b"ispe", i + 4)
    if best is None:
        return None
    r = data.find(b"irot")
    if r >= 4 and r + 4 < len(data) and struct.unpack(">I", data[r - 4:r])[0] == 9 and data[r + 4] & 1:
        best = (best[1], best[0])
    return best


def image_size(path: str) -> Optional[tuple[int, int]]:
    """(largeur, hauteur) sans décoder l'image quand c'est possible ; None si illisible."""
    if os.path.splitext(path)[1].lower() in HEIF_EXTS:
        found = _heif_size(path)
        if found is not None:
            return found
    try:
        info = GdkPixbuf.Pixbuf.get_file_info(path)
        if isinstance(info, tuple) and info[0] is not None:
            return info[1], info[2]
    except GLib.Error:
        pass
    return None


def format_name(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in HEIF_EXTS:
        return HEIF_EXTS[ext]                 # sans get_file_info (décodage complet pour ce loader)
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
    from .folder import is_video
    if is_video(path):
        w, h, duration, codecs = probe_video(path)
        return Picture(path, None, w, h, st.st_size, os.path.splitext(path)[1].lstrip(".").upper(),
                       mtime=st.st_mtime, video=True, duration=duration, codecs=codecs)
    fmt = format_name(path)
    try:
        dims = image_size(path)
        if dims is not None and dims[0] * dims[1] > MAX_PIXELS:
            from ..i18n import tr
            return Picture(path, None, dims[0], dims[1], st.st_size, fmt, error=tr("Image trop grande"),
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


def _weight(cache) -> int:
    """Octets de pixels décodés gardés (4 par pixel)."""
    return sum(p.width * p.height * 4 for p in cache.values() if p.texture is not None)


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
                while len(self._cache) > CACHE_ITEMS or (len(self._cache) > 1 and _weight(self._cache) > CACHE_BYTES):
                    self._cache.popitem(last=False)          # la plus ancienne ; la dernière lue reste
                cbs = self._waiters.pop(path, [])
            for cb in cbs:
                GLib.idle_add(lambda cb=cb, pic=pic: (cb(pic), False)[1])
