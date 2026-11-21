"""Panneau des propriétés (I) : fichier (nom, dossier, taille, format, dimensions, date) et prise de vue (EXIF :
date, appareil, objectif, exposition, ouverture, sensibilité, focale) lue par GExiv2 si présent
(gir1.2-gexiv2-0.10, la bibliothèque de Nautilus et Shotwell ; en Recommends). Lecture dans un fil."""

from __future__ import annotations

import datetime
import os
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..backend.folder import human_size  # noqa: E402
from ..i18n import LANGUAGE, decimal, tr  # noqa: E402
from .common import clear_box, shown  # noqa: E402

try:
    gi.require_version("GExiv2", "0.10")
    from gi.repository import GExiv2  # noqa: E402
except (ValueError, ImportError):
    GExiv2 = None


def date_text(d: datetime.datetime, seconds: bool = False) -> str:
    fmt = {"fr": "%d/%m/%Y %H:%M", "de": "%d.%m.%Y %H:%M"}.get(LANGUAGE, "%Y-%m-%d %H:%M")
    return d.strftime(fmt + (":%S" if seconds else ""))


def _fraction(text: str) -> Optional[float]:
    try:
        if "/" in text:
            a, b = text.split("/", 1)
            return float(a) / float(b) if float(b) else None
        return float(text)
    except (ValueError, ZeroDivisionError):
        return None


def exif_fields(path: str) -> list[tuple[str, str]]:
    """Champs de prise de vue, dans l'ordre d'affichage (bloquant : à appeler dans un fil)."""
    if GExiv2 is None:
        return []
    try:
        md = GExiv2.Metadata()
        md.open_path(path)
    except GLib.Error:
        return []

    def tag(*names):
        for n in names:
            try:
                if md.try_has_tag(n):
                    v = md.try_get_tag_interpreted_string(n) or md.try_get_tag_string(n)
                    if v and v.strip():
                        return v.strip()
            except (GLib.Error, TypeError):
                continue
        return ""

    out: list[tuple[str, str]] = []
    raw_date = tag("Exif.Photo.DateTimeOriginal", "Exif.Image.DateTime", "Xmp.exif.DateTimeOriginal")
    if raw_date:
        try:
            d = datetime.datetime.strptime(raw_date[:19], "%Y:%m:%d %H:%M:%S")
            raw_date = date_text(d, True)
        except ValueError:
            pass
        out.append((tr("Prise le"), raw_date))
    make, model = tag("Exif.Image.Make"), tag("Exif.Image.Model")
    camera = model if make and model.lower().startswith(make.split()[0].lower()) else " ".join(x for x in (make, model) if x)
    if camera:
        out.append((tr("Appareil"), camera))
    lens = tag("Exif.Photo.LensModel", "Exif.CanonCs.LensType", "Exif.NikonLd3.LensIDNumber")
    if lens:
        out.append((tr("Objectif"), lens))
    exp = _fraction(tag("Exif.Photo.ExposureTime") and md.try_get_tag_string("Exif.Photo.ExposureTime") or "")
    if exp:
        out.append((tr("Exposition"), f"1/{round(1 / exp)} s" if exp < 0.5 else f"{decimal(exp)} s"))
    fnum = _fraction(md.try_get_tag_string("Exif.Photo.FNumber") if tag("Exif.Photo.FNumber") else "")
    if fnum:
        out.append((tr("Ouverture"), f"f/{decimal(fnum)}"))
    iso = tag("Exif.Photo.ISOSpeedRatings", "Exif.Photo.PhotographicSensitivity")
    if iso:
        out.append((tr("Sensibilité"), f"ISO {iso.split()[0]}"))
    focal = _fraction(md.try_get_tag_string("Exif.Photo.FocalLength") if tag("Exif.Photo.FocalLength") else "")
    if focal:
        text = f"{decimal(focal, 0 if focal >= 10 else 1)} mm"
        f35 = tag("Exif.Photo.FocalLengthIn35mmFilm")
        if f35 and f35.split()[0].isdigit() and int(f35.split()[0]) > 0:
            text += " " + tr("(équivalent {0} mm)").format(f35.split()[0])
        out.append((tr("Focale"), text))
    flash = tag("Exif.Photo.Flash")
    if flash:
        out.append((tr("Flash"), flash))
    try:
        ok, lat, lon, _alt = md.try_get_gps_info()
    except (GLib.Error, TypeError, ValueError):
        lat = lon = 0
    if tag("Exif.GPSInfo.GPSLatitude") and (lat or lon):
        out.append((tr("Position"), f"{lat:.5f}, {lon:.5f}"))
    return out


class InfoPanel(Gtk.ScrolledWindow):
    def __init__(self):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=240)
        self.add_css_class("argos-info")
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=18,
                           margin_start=18, margin_end=18)
        self.set_child(self.box)
        self._path = None

    def _group(self, title: str, rows: list[tuple[str, str]]):
        if not rows:
            return
        head = Gtk.Label(label=title, xalign=0)
        head.add_css_class("heading")
        self.box.append(head)
        for k, v in rows:
            key = Gtk.Label(label=k, xalign=0)
            key.add_css_class("caption")
            key.add_css_class("dim-label")
            val = Gtk.Label(label=shown(v), xalign=0, wrap=True, wrap_mode=2, selectable=True)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.append(key)
            col.append(val)
            self.box.append(col)

    def show_picture(self, pic):
        clear_box(self.box)
        if pic is None:
            self._path = None
            return
        self._path = pic.path
        rows = [(tr("Nom"), os.path.basename(pic.path)), (tr("Dossier"), os.path.dirname(pic.path))]
        if pic.width:
            rows.append((tr("Dimensions"), f"{pic.width} × {pic.height} ({decimal(pic.width * pic.height / 1e6)} Mpx)"))
        rows.append((tr("Taille"), human_size(pic.size)))
        if pic.format:
            rows.append((tr("Format"), pic.format))
        if pic.mtime:
            rows.append((tr("Modifié le"), date_text(datetime.datetime.fromtimestamp(pic.mtime))))
        self._group(tr("Fichier"), rows)
        path = pic.path

        def work():
            fields = exif_fields(path)
            GLib.idle_add(lambda: (self._exif(path, fields), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def _exif(self, path, fields):
        if path == self._path:
            self._group(tr("Prise de vue"), fields)

