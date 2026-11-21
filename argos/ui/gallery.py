"""Grille des photos d'un dossier (page d'accueil) et bande de vignettes sous la photo (comme gThumb).

Un seul modèle par dossier (`Gio.ListStore` d'`Item`) ; la grille montre sous-dossiers puis photos, la bande les
photos seulement. Les miniatures arrivent par `thumbs.shared()` (cache partagé avec Nautilus).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GObject, Gtk, Pango  # noqa: E402

from ..backend.folder import Entry  # noqa: E402
from .common import shown  # noqa: E402
from .thumbs import shared  # noqa: E402


class Item(GObject.Object):
    __gtype_name__ = "ArgosItem"

    def __init__(self, entry: Entry):
        super().__init__()
        self.entry = entry


def store_of(entries: list[Entry]) -> Gio.ListStore:
    store = Gio.ListStore(item_type=Item)
    store.splice(0, 0, [Item(e) for e in entries])
    return store


class _Cell(Gtk.Box):
    """Case : miniature carrée (Gtk.Picture CONTAIN dans une boîte de taille fixe) + nom en dessous (grille)."""

    def __init__(self, size: int, with_name: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.size = size
        self.frame = Gtk.Box(width_request=size, height_request=size, halign=Gtk.Align.CENTER)
        self.frame.add_css_class("argos-thumb")
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.icon = Gtk.Image(pixel_size=size // 2, hexpand=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.frame.append(self.picture)
        self.frame.append(self.icon)
        self.append(self.frame)
        self.label = None
        if with_name:
            self.label = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=1, width_request=size,
                                   lines=1)
            self.label.add_css_class("caption")
            self.append(self.label)
        self.path = None
        self._cb = None

    def show_entry(self, entry: Entry):
        self.forget()
        self.path = entry.path
        if self.label is not None:
            self.label.set_label(shown(entry.name))
        self.set_tooltip_text(shown(entry.name))
        self.picture.set_paintable(None)
        if entry.is_dir:
            self.picture.set_visible(False)
            self.icon.set_visible(True)
            self.icon.set_from_icon_name("folder")
            return
        self.picture.set_visible(True)
        self.icon.set_visible(False)
        path = entry.path

        def done(tex):
            if self.path != path:
                return
            if tex is None:
                self.picture.set_visible(False)
                self.icon.set_visible(True)
                self.icon.set_from_icon_name("image-missing")
            else:
                self.picture.set_paintable(tex)
        self._cb = done
        shared().get(path, entry.mtime, done)

    def forget(self):
        if self.path and self._cb is not None:
            shared().cancel(self.path, self._cb)
        self.path = None
        self._cb = None


def _factory(size: int, with_name: bool) -> Gtk.SignalListItemFactory:
    f = Gtk.SignalListItemFactory()
    f.connect("setup", lambda _f, li: li.set_child(_Cell(size, with_name)))
    f.connect("bind", lambda _f, li: li.get_child().show_entry(li.get_item().entry))
    f.connect("unbind", lambda _f, li: li.get_child().forget())
    return f


class Grid(Gtk.ScrolledWindow):
    """Grille du dossier. Signal « activate » (position) : ouvrir le dossier ou la photo."""

    __gsignals__ = {"open": (GObject.SignalFlags.RUN_FIRST, None, (object,))}

    def __init__(self, size: int = 160):
        super().__init__(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.view = Gtk.GridView(model=self.selection, factory=_factory(size, True), max_columns=40,
                                 single_click_activate=False, enable_rubberband=False)
        self.view.add_css_class("argos-grid")
        self.view.connect("activate", self._on_activate)
        self.set_child(self.view)

    def set_model(self, store: Gio.ListStore):
        self.selection.set_model(store)

    def _on_activate(self, _view, pos):
        item = self.selection.get_model().get_item(pos)
        if item is not None:
            self.emit("open", item.entry)

    def select_path(self, path: str):
        model = self.selection.get_model()
        if model is None:
            return
        for i in range(model.get_n_items()):
            if model.get_item(i).entry.path == path:
                self.selection.set_selected(i)
                self.view.scroll_to(i, Gtk.ListScrollFlags.FOCUS | Gtk.ListScrollFlags.SELECT, None)
                return


class Filmstrip(Gtk.ScrolledWindow):
    """Vignettes sous la photo (photos seulement), comme gThumb : une GRILLE qui passe à la ligne, dans un séparateur
    réglable (demande de Tof) — une rangée par défaut, plus en la montant. Signal « picked » (index) au clic."""

    __gsignals__ = {"picked": (GObject.SignalFlags.RUN_FIRST, None, (int,))}
    SIZE = 72

    def __init__(self):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=False)
        self.view = Gtk.GridView(model=self.selection, factory=_factory(self.SIZE, False), max_columns=200,
                                 single_click_activate=True)
        self.view.add_css_class("argos-filmstrip")
        self.view.connect("activate", lambda _v, pos: self.emit("picked", pos))
        self.set_child(self.view)

    def set_model(self, store: Gio.ListStore):
        self.selection.set_model(store)

    def show_index(self, index: int):
        model = self.selection.get_model()
        if model is None or not 0 <= index < model.get_n_items():
            return
        self.selection.set_selected(index)
        self.view.scroll_to(index, Gtk.ListScrollFlags.SELECT, None)
