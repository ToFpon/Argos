"""Grille des photos d'un dossier (page d'accueil) et bande de vignettes sous la photo (comme gThumb).

Un seul modèle par dossier (`Gio.ListStore` d'`Item`) ; la grille montre sous-dossiers puis photos, la bande les
photos seulement. Les miniatures arrivent par `thumbs.shared()` (cache partagé avec Nautilus).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..backend.folder import Entry  # noqa: E402
from .common import shown  # noqa: E402
from .thumbs import shared  # noqa: E402


class Item(GObject.Object):
    __gtype_name__ = "ArgosItem"

    def __init__(self, entry: Entry):
        super().__init__()
        self.entry = entry


class EntryList(GObject.Object, Gio.ListModel):
    """Modèle VIRTUEL (repris d'Odysseus) : les `Item` ne sont créés que pour les cases affichées — avec un
    Gio.ListStore, 7 685 icônes = 7 685 objets GObject faits d'un coup, deux fois (grille + bande)."""
    __gtype_name__ = "ArgosEntryList"

    def __init__(self, entries: list[Entry]):
        super().__init__()
        self.entries = entries
        self._items: dict[int, Item] = {}

    def do_get_item_type(self):
        return Item.__gtype__

    def do_get_n_items(self):
        return len(self.entries)

    def do_get_item(self, i):
        if not 0 <= i < len(self.entries):
            return None
        it = self._items.get(i)
        if it is None:
            it = self._items[i] = Item(self.entries[i])
        return it


def store_of(entries: list[Entry]) -> EntryList:
    return EntryList(list(entries))


def columns_for(width: int, cell: int) -> int:
    """Vrai nombre de colonnes : sans lui, Gtk.GridView remplit d'abord « max_columns » colonnes sans connaître
    la largeur des cases (leçon d'Odysseus : ~2 000 cases créées d'un coup, secondes figées)."""
    return max(1, min(64, width // max(1, cell)))


class _SizedGrid:
    """Grille branchée sur son modèle seulement une fois dimensionnée, colonnes d'après la largeur réelle."""

    def _init_sizing(self, cell: int):
        self._cell = cell
        self._pending = None
        self._pending_id = 0
        self._cols_id = 0

    def _sized(self) -> bool:
        return self.get_mapped() and self.get_width() > 0 and self.get_height() > 0

    def set_model(self, model):
        if model is None or self._sized():
            self._pending = None
            self._apply_cols(columns_for(self.get_width(), self._cell)) if self._sized() else None
            self.selection.set_model(model)
            return
        self._pending = model
        self.selection.set_model(None)
        if not self._pending_id:
            self._pending_id = GLib.timeout_add(30, self._attach_pending)

    def _attach_pending(self):
        if self._pending is None or self.get_root() is None:
            self._pending_id = 0
            return False
        if not self._sized():
            return True
        self._pending_id = 0
        model, self._pending = self._pending, None
        self._apply_cols(columns_for(self.get_width(), self._cell))
        self.selection.set_model(model)
        self._attached()
        return False

    def _attached(self):
        pass

    def _size_allocated(self, width, height, baseline):
        Gtk.ScrolledWindow.do_size_allocate(self, width, height, baseline)
        cols = columns_for(width, self._cell)
        if cols != self.view.get_max_columns() and not self._cols_id:
            self._cols_id = GLib.idle_add(self._apply_cols_idle, cols)

    def _apply_cols_idle(self, cols):
        self._cols_id = 0
        if self.get_root() is not None:
            self.view.set_max_columns(cols)
        return False

    def _apply_cols(self, cols):
        if cols != self.view.get_max_columns():
            self.view.set_max_columns(cols)


class _Cell(Gtk.Box):
    """Case comme gThumb : miniature à sa taille réelle (préparée à la taille de la case par `thumbs`, jamais
    agrandie : une icône de 48 px reste à 48 px), centrée ; photo (sans transparence) dans un cadre blanc avec ombre
    (`.argos-photo`) ; dossier : icône + nom (la grille n'en montre plus, demande de Tof — reste pour d'autres usages)."""

    def __init__(self, size: int, with_name: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.size = size
        self.frame = Gtk.Box(width_request=size, height_request=size, halign=Gtk.Align.CENTER)
        self.frame.add_css_class("argos-thumb")
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=False, hexpand=True, vexpand=True,
                                   halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.icon = Gtk.Image(pixel_size=size // 2, hexpand=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.frame.append(self.picture)
        self.frame.append(self.icon)
        self.append(self.frame)
        self.label = None
        if with_name:
            self.label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, width_request=size,
                                   lines=2, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                   justify=Gtk.Justification.CENTER)
            self.label.add_css_class("argos-folder-name")
            self.append(self.label)
        self.path = None
        self._cb = None

    def show_entry(self, entry: Entry):
        self.forget()
        self.path = entry.path
        if self.label is not None:
            self.label.set_label(shown(entry.name))
            self.label.set_visible(entry.is_dir)
        self.set_tooltip_text(shown(entry.name))
        self.picture.set_paintable(None)
        self.picture.remove_css_class("argos-photo")
        if entry.is_dir:
            self.picture.set_visible(False)
            self.icon.set_visible(True)
            self.icon.set_from_icon_name("folder")
            return
        self.picture.set_visible(True)
        self.icon.set_visible(False)
        path = entry.path

        def done(result):
            if self.path != path:
                return
            if result is None:
                self.picture.set_visible(False)
                self.icon.set_visible(True)
                self.icon.set_from_icon_name("image-missing")
                return
            tex, photo = result
            self.picture.set_paintable(tex)
            if photo:
                self.picture.add_css_class("argos-photo")
        self._cb = done
        shared(self.size).get(path, entry.mtime, done)

    def forget(self):
        if self.path and self._cb is not None:
            shared(self.size).cancel(self.path, self._cb)
        self.path = None
        self._cb = None


def _factory(size: int, with_name: bool) -> Gtk.SignalListItemFactory:
    f = Gtk.SignalListItemFactory()
    f.connect("setup", lambda _f, li: li.set_child(_Cell(size, with_name)))
    f.connect("bind", lambda _f, li: li.get_child().show_entry(li.get_item().entry))
    f.connect("unbind", lambda _f, li: li.get_child().forget())
    return f


class Grid(_SizedGrid, Gtk.ScrolledWindow):
    """Grille du dossier. Signal « activate » (position) : ouvrir le dossier ou la photo."""

    __gsignals__ = {"open": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
                    "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float))}

    def __init__(self, size: int = 128):
        super().__init__(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._init_sizing(size + 16 + 6)                # case + padding (base.css) + espacement
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.view = Gtk.GridView(model=self.selection, factory=_factory(size, True), max_columns=4,
                                 single_click_activate=False, enable_rubberband=False)
        self.view.add_css_class("argos-grid")
        self.view.connect("activate", self._on_activate)
        self.set_child(self.view)
        right = Gtk.GestureClick(button=3)
        right.connect("pressed", self._on_right)
        self.view.add_controller(right)

    def do_size_allocate(self, width, height, baseline):
        self._size_allocated(width, height, baseline)

    def _on_right(self, _g, _n, x, y):
        """Clic droit sur une vignette : elle est sélectionnée, puis le menu (photos seulement)."""
        w = self.view.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and not isinstance(w, _Cell):
            w = w.get_parent()
        if w is None or not w.path:
            return
        model = self.selection.get_model()
        for i in range(model.get_n_items()):
            entry = model.get_item(i).entry
            if entry.path == w.path:
                self.selection.set_selected(i)
                if not entry.is_dir:
                    self.emit("context-menu", entry, x, y)
                return

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


class Filmstrip(_SizedGrid, Gtk.ScrolledWindow):
    """Vignettes sous la photo (photos seulement), comme gThumb : une GRILLE qui passe à la ligne, dans un séparateur
    réglable (demande de Tof) — une rangée par défaut, plus en la montant. Signal « picked » (index) au clic."""

    __gsignals__ = {"picked": (GObject.SignalFlags.RUN_FIRST, None, (int,))}
    SIZE = 96

    def __init__(self):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self._init_sizing(self.SIZE + 8 + 6)
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=False)
        self.view = Gtk.GridView(model=self.selection, factory=_factory(self.SIZE, False), max_columns=8,
                                 single_click_activate=True)
        self.view.add_css_class("argos-filmstrip")
        self.view.connect("activate", lambda _v, pos: self.emit("picked", pos))
        self.set_child(self.view)

    def do_size_allocate(self, width, height, baseline):
        self._size_allocated(width, height, baseline)

    def _attached(self):
        if getattr(self, "_want", None) is not None:
            self.show_index(self._want)

    def show_index(self, index: int):
        self._want = index
        model = self.selection.get_model()
        if model is None or not 0 <= index < model.get_n_items():
            return
        self.selection.set_selected(index)
        self.view.scroll_to(index, Gtk.ListScrollFlags.SELECT, None)
