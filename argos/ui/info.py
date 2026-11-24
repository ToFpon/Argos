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
from gi.repository import GLib, Gtk, Pango  # noqa: E402

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
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10,
                           margin_start=12, margin_end=12)
        self.set_child(self.box)
        self._path = None

    def _group(self, title: str, rows: list[tuple[str, str]]):
        """Titre puis lignes « libellé   valeur » serrées, comme gThumb (demande de Tof) ; valeur trop longue
        coupée au milieu, entière dans l'infobulle."""
        if not rows:
            return
        head = Gtk.Label(label=title, xalign=0, margin_top=4)
        head.add_css_class("heading")
        self.box.append(head)
        grid = Gtk.Grid(column_spacing=12, row_spacing=2)
        for i, (k, v) in enumerate(rows):
            key = Gtk.Label(label=k, xalign=0, yalign=0)
            key.add_css_class("caption")
            key.add_css_class("dim-label")
            val = Gtk.Label(label=shown(v), xalign=0, hexpand=True, selectable=True, max_width_chars=1,
                            ellipsize=Pango.EllipsizeMode.MIDDLE, tooltip_text=shown(v))
            val.add_css_class("caption")
            grid.attach(key, 0, i, 1, 1)
            grid.attach(val, 1, i, 1, 1)
        self.box.append(grid)

    def show_picture(self, pic):
        clear_box(self.box)
        if pic is None:
            self._path = None
            return
        self._path = pic.path
        rows = [(tr("Nom"), os.path.basename(pic.path)), (tr("Dossier"), os.path.dirname(pic.path))]
        if pic.width:
            rows.append((tr("Dimensions"), f"{pic.width} × {pic.height} ({decimal(pic.width * pic.height / 1e6)} Mpx)"))
        if not getattr(pic, "is_dir", False):
            rows.append((tr("Taille"), human_size(pic.size)))
        if pic.format:
            rows.append((tr("Format"), pic.format))
        if pic.mtime:
            rows.append((tr("Modifié le"), date_text(datetime.datetime.fromtimestamp(pic.mtime))))
        self._group(tr("Fichier"), rows)
        path = pic.path
        if getattr(pic, "is_dir", False):
            return

        def work():
            fields = exif_fields(path)
            GLib.idle_add(lambda: (self._exif(path, fields), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def show_path(self, path):
        """Pour la grille : photo ou dossier sélectionné (dimensions et format lus dans un fil)."""
        if path is None:
            self.show_picture(None)
            return
        self._path = path

        def work():
            from types import SimpleNamespace
            from ..backend.loader import format_name, image_size
            try:
                st = os.stat(path)
            except OSError:
                return
            w = h = 0
            fmt = ""
            if not os.path.isdir(path):
                w, h = image_size(path) or (0, 0)          # sans décoder (AVIF : get_file_info décodait tout)
                fmt = format_name(path)
            pic = SimpleNamespace(path=path, width=w, height=h, size=0 if os.path.isdir(path) else st.st_size,
                                  format=fmt, mtime=st.st_mtime, is_dir=os.path.isdir(path))
            GLib.idle_add(lambda: (self._path == path and self.show_picture(pic), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def _exif(self, path, fields):
        if path == self._path:
            self._group(tr("Prise de vue"), fields)

