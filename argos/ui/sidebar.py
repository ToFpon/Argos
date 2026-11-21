"""Barre latérale de la grille : arbre des dossiers, comme gThumb (demande de Tof : naviguer sans remonter dossier par
dossier). Racines : Dossier personnel, Images, Bureau, disques montés (Gio.VolumeMonitor), Ordinateur (/).

Sous-dossiers lus DANS UN FIL à l'ouverture d'une branche (un partage réseau lent ne fige pas la fenêtre) : la
branche reçoit un Gio.ListStore vide, rempli au retour. `reveal(chemin)` déplie l'arbre jusqu'au dossier affiché
et le sélectionne (au fil des lectures).
"""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..backend.folder import name_key  # noqa: E402
from ..i18n import tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import shown  # noqa: E402


class Node(GObject.Object):
    __gtype_name__ = "ArgosFolderNode"

    def __init__(self, path: str, label: str = "", icon: str = "folder-symbolic", root: bool = False):
        super().__init__()
        self.path = path
        self.label = label or os.path.basename(path) or path
        self.icon = icon
        self.root = root
        self.children = None            # Gio.ListStore une fois demandé
        self.loading = False


def _special(d) -> str:
    try:
        p = GLib.get_user_special_dir(d)
    except (TypeError, ValueError):
        return ""
    return p if p and os.path.isdir(p) else ""


def roots() -> list[Node]:
    home = os.path.expanduser("~")
    out = [Node(home, tr("Dossier personnel"), "user-home-symbolic", True)]
    seen = {home}
    for d, icon in ((GLib.UserDirectory.DIRECTORY_PICTURES, "folder-pictures-symbolic"),
                    (GLib.UserDirectory.DIRECTORY_DESKTOP, "user-desktop-symbolic"),
                    (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "folder-download-symbolic")):
        p = _special(d)
        if p and p not in seen:
            seen.add(p)
            out.append(Node(p, os.path.basename(p), icon, True))
    for mount in Gio.VolumeMonitor.get().get_mounts():
        root = mount.get_root()
        p = root.get_path() if root is not None else None
        if not p or p in seen or mount.is_shadowed():
            continue
        seen.add(p)
        out.append(Node(p, mount.get_name(), "drive-harddisk-symbolic", True))
    out.append(Node("/", tr("Ordinateur"), "computer-symbolic", True))
    return out


class FolderTree(Gtk.ScrolledWindow):
    """Signal « picked » (chemin) quand on choisit un dossier."""

    __gsignals__ = {"picked": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, width_request=200)
        self.add_css_class("argos-tree")
        self.root_store = Gio.ListStore(item_type=Node)
        self.tree = Gtk.TreeListModel.new(self.root_store, False, False, self._children)
        self.selection = Gtk.SingleSelection(model=self.tree, autoselect=False, can_unselect=True)
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._setup)
        f.connect("bind", self._bind)
        self.view = Gtk.ListView(model=self.selection, factory=f, single_click_activate=True)
        self.view.add_css_class("navigation-sidebar")
        self.view.connect("activate", self._on_activate)
        self.set_child(self.view)
        self._target = ""
        self._reloaded = set()
        self._quiet = False
        self._queue: list = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        threading.Thread(target=self._worker, daemon=True, name="argos-tree").start()
        self.refresh_roots()
        monitor = Gio.VolumeMonitor.get()
        for sig in ("mount-added", "mount-removed"):
            monitor.connect(sig, lambda *_: self.refresh_roots())
        self._monitor = monitor

    def refresh_roots(self):
        self.root_store.splice(0, self.root_store.get_n_items(), roots())
        if self._target:
            GLib.idle_add(lambda: (self.reveal(self._target), False)[1])

    # --- arbre ---

    def _children(self, node: Node):
        if node.children is None:
            node.children = Gio.ListStore(item_type=Node)
            self._load(node)
        return node.children

    def _load(self, node: Node):
        """Lecture des sous-dossiers par UN fil (GTK demande les enfants de chaque ligne affichée, pour la flèche :
        un fil par ligne en lançait des centaines), la dernière demande d'abord (lignes visibles)."""
        node.loading = True
        with self._lock:
            self._queue.append(node)
            self._cv.notify()

    def _worker(self):
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                node = self._queue.pop()
            hidden = settings["show_hidden"]
            try:
                names = [de.name for de in os.scandir(node.path)
                         if (hidden or not de.name.startswith(".")) and de.is_dir(follow_symlinks=True)]
            except OSError:
                names = []
            names.sort(key=name_key)
            GLib.idle_add(lambda node=node, names=names: (self._loaded(node, names), False)[1])

    def _loaded(self, node: Node, names: list[str]):
        node.loading = False
        node.children.splice(0, node.children.get_n_items(),
                             [Node(os.path.join(node.path, n)) for n in names])
        if self._target and (self._target + "/").startswith(node.path.rstrip("/") + "/"):
            self._advance()

    def _setup(self, _f, li):
        exp = Gtk.TreeExpander()
        box = Gtk.Box(spacing=6)
        box._icon = Gtk.Image()
        box._label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        box.append(box._icon)
        box.append(box._label)
        exp.set_child(box)
        li.set_child(exp)

    def _bind(self, _f, li):
        row = li.get_item()
        node = row.get_item()
        exp = li.get_child()
        exp.set_list_row(row)
        box = exp.get_child()
        box._icon.set_from_icon_name(node.icon)
        box._label.set_label(shown(node.label))
        exp.set_tooltip_text(shown(node.path))

    def _on_activate(self, _view, pos):
        row = self.tree.get_item(pos)
        if row is None:
            return
        node = row.get_item()
        if not self._quiet:
            self.emit("picked", node.path)

    # --- dossier affiché ---

    def reveal(self, path: str):
        """Déplie jusqu'à `path` et le sélectionne (la racine la plus précise qui le contient)."""
        self._target = os.path.normpath(path)
        self._reloaded = set()
        self._advance()

    def _advance(self):
        target = self._target
        best, best_len = None, -1
        for i in range(self.tree.get_n_items()):
            row = self.tree.get_item(i)
            p = row.get_item().path
            if p == target:
                self.selection.set_selected(i)
                self.view.scroll_to(i, Gtk.ListScrollFlags.NONE, None)
                self._target = ""
                return
            inside = target.startswith(p.rstrip("/") + "/")
            if inside and len(p) > best_len:
                best, best_len = row, len(p)
        if best is None:
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
            self._target = ""
            return
        node = best.get_item()
        if not best.get_expanded():
            best.set_expanded(True)              # enfants lus dans un fil → _loaded rappelle _advance
            if node.children is not None and node.children.get_n_items() and not node.loading:
                GLib.idle_add(lambda: (self._advance(), False)[1])
            return
        if node.loading:
            return                                # _loaded rappellera _advance
        if node.path not in self._reloaded:       # déplié mais le dossier n'y est pas (créé depuis ?) : relire
            self._reloaded.add(node.path)
            self._load(node)
            return
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self._target = ""
