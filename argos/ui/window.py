"""Fenêtre d'Argos : grille du dossier (accueil) ↔ visionneuse (disposition de gThumb : en-tête avec retour, zoom,
rotation, infos ; bande de vignettes et barre d'état en bas ; plein écran sans rien autour).

Le dossier est lu dans un fil (`folder.scan`), suivi par un Gio.FileMonitor (photos ajoutées / retirées / changées).
"""

from __future__ import annotations

import os
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from .. import APP_NAME, features  # noqa: E402
from ..backend import folder as fold  # noqa: E402
from ..backend.loader import Loader, Picture  # noqa: E402
from ..i18n import decimal, ntr, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import alert, shown, toast  # noqa: E402
from .gallery import Filmstrip, Grid, store_of  # noqa: E402
from .info import InfoPanel  # noqa: E402
from .thumbs import shared as shared_thumbs  # noqa: E402
from .viewer import ImageView  # noqa: E402


def percent(zoom: float) -> str:
    p = zoom * 100
    return f"{decimal(p, 0 if p >= 10 else 1)} %"


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_size_request(360, 360)
        self.set_default_size(settings["window_width"], settings["window_height"])
        if settings["window_maximized"]:
            self.maximize()
        self.loader = Loader()
        self.folder = ""
        self.dirs: list[fold.Entry] = []
        self.images: list[fold.Entry] = []
        self.index = -1
        self.picture: Optional[Picture] = None
        self._scan_seq = 0
        self._monitor = None
        self._refresh_id = 0
        self._want_path: Optional[str] = None      # photo à montrer une fois le dossier lu

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self.stack.add_named(self._build_browser(), "browser")
        self.stack.add_named(self._build_viewer(), "viewer")
        self.toast_overlay = Adw.ToastOverlay(child=self.stack)
        self.set_content(self.toast_overlay)
        self._actions()
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close)
        self.connect("notify::fullscreened", lambda *_: self._sync_fullscreen())

    # ------------------------------------------------------------------ construction

    def _menu(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append(tr("Ouvrir un dossier…"), "win.open-folder")
        sec.append(tr("Ouvrir des photos…"), "win.open-files")
        menu.append_section(None, sec)
        sort = Gio.Menu()
        sort.append(tr("Nom"), "win.sort::name")
        sort.append(tr("Date de modification"), "win.sort::date")
        sort.append(tr("Taille"), "win.sort::size")
        sort.append(tr("Ordre inverse"), "win.sort-reverse")
        menu.append_submenu(tr("Trier par"), sort)
        sec2 = Gio.Menu()
        sec2.append(tr("Afficher les fichiers cachés"), "win.show-hidden")
        menu.append_section(None, sec2)
        sec3 = Gio.Menu()
        sec3.append(tr("Préférences"), "app.preferences")
        sec3.append(tr("Raccourcis clavier"), "win.show-help-overlay")
        sec3.append(tr("À propos d'Argos"), "app.about")
        menu.append_section(None, sec3)
        return Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=tr("Menu principal"),
                              primary=True)

    def _build_browser(self) -> Gtk.Widget:
        header = Adw.HeaderBar()
        self.up_button = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=tr("Dossier parent (Alt+↑)"),
                                    action_name="win.up")
        header.pack_start(self.up_button)
        header.pack_start(Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=tr("Ouvrir un dossier (Ctrl+O)"),
                                     action_name="win.open-folder"))
        self.browser_title = Adw.WindowTitle()
        header.set_title_widget(self.browser_title)
        header.pack_end(self._menu())
        self.grid = Grid(settings["grid_size"])
        self.grid.connect("open", lambda _g, e: self.open_entry(e))
        self.empty = Adw.StatusPage(icon_name="image-x-generic-symbolic", title=tr("Aucune photo"),
                                    description=tr("Ce dossier ne contient ni photo ni sous-dossier"))
        self.browser_stack = Gtk.Stack()
        self.browser_stack.add_named(self.grid, "grid")
        self.browser_stack.add_named(self.empty, "empty")
        self.browser_stack.add_named(Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True),
                                     "loading")
        view = Adw.ToolbarView(content=self.browser_stack)
        view.add_top_bar(header)
        return view

    def _build_viewer(self) -> Gtk.Widget:
        header = Adw.HeaderBar()
        self.viewer_header = header
        header.pack_start(Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=tr("Retour au dossier (Échap)"),
                                     action_name="win.back"))
        self.viewer_title = Adw.WindowTitle()
        header.set_title_widget(self.viewer_title)
        header.pack_end(self._menu())
        self.info_button = Gtk.ToggleButton(icon_name="document-properties-symbolic",
                                            tooltip_text=tr("Propriétés (I)"))
        header.pack_end(self.info_button)
        header.pack_end(Gtk.Button(icon_name="view-fullscreen-symbolic", tooltip_text=tr("Plein écran (F11)"),
                                   action_name="win.fullscreen"))
        zoom = Gtk.Box(css_classes=["linked"])
        zoom.append(Gtk.Button(icon_name="zoom-out-symbolic", tooltip_text=tr("Zoom arrière (−)"),
                               action_name="win.zoom-out"))
        self.zoom_button = Gtk.Button(label="100 %", tooltip_text=tr("Ajuster à la fenêtre / taille réelle (0 / 1)"),
                                      action_name="win.zoom-toggle")
        self.zoom_button.add_css_class("argos-zoom")
        zoom.append(self.zoom_button)
        zoom.append(Gtk.Button(icon_name="zoom-in-symbolic", tooltip_text=tr("Zoom avant (+)"),
                               action_name="win.zoom-in"))
        header.pack_end(zoom)
        rot = Gtk.Box(css_classes=["linked"])
        rot.append(Gtk.Button(icon_name="object-rotate-left-symbolic", tooltip_text=tr("Pivoter à gauche (Maj+R)"),
                              action_name="win.rotate-left"))
        rot.append(Gtk.Button(icon_name="object-rotate-right-symbolic", tooltip_text=tr("Pivoter à droite (R)"),
                              action_name="win.rotate-right"))
        header.pack_start(rot)

        self.view = ImageView()
        self.view.connect("navigate", lambda _v, step: self.go(step))
        self.view.connect("zoom-changed", lambda *_: self._sync_status())
        self.view.connect("double-click", lambda *_: self.toggle_fullscreen())
        self.error_page = Adw.StatusPage(icon_name="image-missing-symbolic", visible=False)
        overlay = Gtk.Overlay(child=self.view)
        overlay.add_overlay(self.error_page)
        for icon, step, align in (("go-previous-symbolic", -1, Gtk.Align.START),
                                  ("go-next-symbolic", 1, Gtk.Align.END)):
            b = Gtk.Button(icon_name=icon, halign=align, valign=Gtk.Align.CENTER, margin_start=12, margin_end=12,
                           css_classes=["osd", "circular", "argos-arrow"], focusable=False,
                           tooltip_text=tr("Photo précédente") if step < 0 else tr("Photo suivante"))
            b.connect("clicked", lambda _b, s=step: self.go(s))
            overlay.add_overlay(b)

        self.info = InfoPanel()
        self.split = Adw.OverlaySplitView(content=overlay, sidebar=self.info, sidebar_position=Gtk.PackType.END,
                                          show_sidebar=False, min_sidebar_width=240, max_sidebar_width=320)
        self.info_button.bind_property("active", self.split, "show-sidebar",
                                       GObject.BindingFlags.BIDIRECTIONAL)
        self.split.connect("notify::show-sidebar", lambda *_: self._sync_info())

        self.filmstrip = Filmstrip()
        self.filmstrip.connect("picked", lambda _f, i: self.show_index(i))
        self.strip_revealer = Gtk.Revealer(child=self.filmstrip, reveal_child=settings["filmstrip"],
                                           transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        status = Gtk.Box(spacing=6, margin_start=12, margin_end=6, margin_top=2, margin_bottom=2)
        status.add_css_class("argos-status")
        self.status = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class("caption")
        self.status.add_css_class("numeric")
        status.append(self.status)
        self.strip_button = Gtk.ToggleButton(icon_name="view-app-grid-symbolic", active=settings["filmstrip"],
                                             tooltip_text=tr("Bande de vignettes (F9)"), css_classes=["flat"])
        self.strip_button.connect("toggled", lambda b: self.set_filmstrip(b.get_active()))
        status.append(self.strip_button)
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(self.strip_revealer)
        bottom.append(status)

        self.viewer = Adw.ToolbarView(content=self.split, top_bar_style=Adw.ToolbarStyle.FLAT,
                                      bottom_bar_style=Adw.ToolbarStyle.RAISED_BORDER)
        self.viewer.add_top_bar(header)
        self.viewer.add_bottom_bar(bottom)
        return self.viewer

    def _actions(self):
        def add(name, cb, accels=(), param=None):
            a = Gio.SimpleAction.new(name, param)
            a.connect("activate", lambda _a, p: cb(p) if param is not None else cb())
            self.add_action(a)
            if accels:
                self.get_application().set_accels_for_action(f"win.{name}", list(accels))
            return a
        add("open-folder", self.choose_folder, ["<Control>o"])
        add("open-files", self.choose_files, ["<Control><Shift>o"])
        add("up", self.go_up, ["<Alt>Up"])
        add("back", self.back)
        add("fullscreen", self.toggle_fullscreen, ["F11", "f"])
        add("zoom-in", lambda: self.view.zoom_step(1), ["plus", "KP_Add", "equal", "<Control>plus"])
        add("zoom-out", lambda: self.view.zoom_step(-1), ["minus", "KP_Subtract", "<Control>minus"])
        add("zoom-fit", self.view.set_fit, ["0", "KP_0", "<Control>0"])
        add("zoom-100", lambda: self.view.set_zoom(1.0), ["1", "KP_1"])
        add("zoom-toggle", self.zoom_toggle)
        add("rotate-right", lambda: self.view.rotate(1) or self._sync_status(), ["r"])
        add("rotate-left", lambda: self.view.rotate(3) or self._sync_status(), ["<Shift>r"])
        add("filmstrip", lambda: self.strip_button.set_active(not self.strip_button.get_active()), ["F9"])
        add("info", lambda: self.info_button.set_active(not self.info_button.get_active()), ["i"])
        add("trash", self.trash, ["Delete"])
        sort = Gio.SimpleAction.new_stateful("sort", GLib.VariantType.new("s"), GLib.Variant("s", settings["sort_key"]))
        sort.connect("activate", lambda a, v: (a.set_state(v), self._set_sort(v.get_string())))
        self.add_action(sort)
        for key, name in (("sort_reverse", "sort-reverse"), ("show_hidden", "show-hidden")):
            a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", settings[key]))
            a.connect("activate", lambda a, _p, key=key: (a.set_state(GLib.Variant("b", not a.get_state())),
                                                          self._set_option(key, a.get_state().get_boolean())))
            self.add_action(a)
        self._viewer_actions = ("back", "fullscreen", "zoom-in", "zoom-out", "zoom-fit", "zoom-100", "zoom-toggle",
                                "rotate-right", "rotate-left", "filmstrip", "info", "trash")
        self._sync_actions()

    # ------------------------------------------------------------------ dossier

    def open_folder(self, path: str, show: Optional[str] = None):
        """Ouvre un dossier ; `show` = photo à afficher dès qu'il est lu (sinon la grille)."""
        path = os.path.abspath(path)
        if path != self.folder:
            self.loader.clear()
        self.folder = path
        self._want_path = show
        settings["last_folder"] = path
        self.browser_title.set_title(shown(os.path.basename(path) or path))
        self.browser_title.set_subtitle(shown(self._pretty(path)))
        self.up_button.set_sensitive(os.path.dirname(path) != path)
        if show is None:
            self.show_browser()
        self._watch(path)
        self._rescan()

    @staticmethod
    def _pretty(path: str) -> str:
        home = os.path.expanduser("~")
        return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path

    def _rescan(self, quiet: bool = False):
        self._scan_seq += 1
        seq = self._scan_seq
        folder = self.folder
        if not quiet and not self.images:
            self.browser_stack.set_visible_child_name("loading")
        hidden, key, rev = settings["show_hidden"], settings["sort_key"], settings["sort_reverse"]

        def work():
            try:
                result = fold.scan(folder, hidden, key, rev)
                err = ""
            except OSError as e:
                result, err = ([], []), e.strerror or str(e)
            GLib.idle_add(lambda: (self._scanned(seq, result, err), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def _scanned(self, seq: int, result, err: str):
        if seq != self._scan_seq:
            return
        dirs, images = result
        current = self.images[self.index].path if 0 <= self.index < len(self.images) else None
        self.dirs, self.images = dirs, images
        self.grid.set_model(store_of(dirs + images))
        self.filmstrip.set_model(store_of(images))
        if err:
            self.empty.set_title(tr("Accès impossible"))
            self.empty.set_description(shown(err))
            self.browser_stack.set_visible_child_name("empty")
        elif not dirs and not images:
            self.empty.set_title(tr("Aucune photo"))
            self.empty.set_description(tr("Ce dossier ne contient ni photo ni sous-dossier"))
            self.browser_stack.set_visible_child_name("empty")
        else:
            self.browser_stack.set_visible_child_name("grid")
        want = self._want_path
        self._want_path = None
        if want is not None:
            i = self._index_of(want)
            if i >= 0:
                self.show_index(i)
                self.show_viewer()
            elif os.path.isfile(want):           # photo d'un format inconnu, fichier caché… : montrée seule
                self.images.insert(0, fold.Entry(want, os.path.basename(want), False))
                self.filmstrip.set_model(store_of(self.images))
                self.show_index(0)
                self.show_viewer()
        elif self.stack.get_visible_child_name() == "viewer":
            i = self._index_of(current) if current else -1
            if i < 0:                             # photo courante disparue : la voisine, sinon la grille
                if self.images:
                    self.show_index(min(max(self.index, 0), len(self.images) - 1))
                else:
                    self.show_browser()
            else:
                self.index = i
                self.filmstrip.show_index(i)
                self._sync_status()
        elif current:
            self.grid.select_path(current)

    def _index_of(self, path: str) -> int:
        return next((i for i, e in enumerate(self.images) if e.path == path), -1)

    def _watch(self, path: str):
        if self._monitor is not None:
            self._monitor.cancel()
        try:
            self._monitor = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._monitor.connect("changed", self._on_folder_changed)
        except GLib.Error:
            self._monitor = None

    def _on_folder_changed(self, _mon, file, _other, event):
        if event == Gio.FileMonitorEvent.CHANGES_DONE_HINT or event == Gio.FileMonitorEvent.CHANGED:
            p = file.get_path()
            if p:
                self.loader.forget(p)
                shared_thumbs().forget(p)
                if self.picture is not None and self.picture.path == p and event == Gio.FileMonitorEvent.CHANGES_DONE_HINT:
                    GLib.timeout_add(200, lambda: (self.show_index(self.index, keep_view=True), False)[1])
        if self._refresh_id:
            GLib.source_remove(self._refresh_id)
        self._refresh_id = GLib.timeout_add(400, self._refresh_now)

    def _refresh_now(self):
        self._refresh_id = 0
        self._rescan(quiet=True)
        return False

    def go_up(self):
        parent = os.path.dirname(self.folder)
        if parent and parent != self.folder:
            child = self.folder
            self.open_folder(parent)
            GLib.timeout_add(300, lambda: (self.grid.select_path(child), False)[1])

    def open_entry(self, entry: fold.Entry):
        if entry.is_dir:
            self.open_folder(entry.path)
        else:
            i = self._index_of(entry.path)
            if i >= 0:
                self.show_index(i)
                self.show_viewer()

    def open_path(self, path: str):
        """Fichier → son dossier, photo affichée ; dossier → grille."""
        if os.path.isdir(path):
            self.open_folder(path)
        else:
            self.open_folder(os.path.dirname(os.path.abspath(path)) or "/", show=os.path.abspath(path))

    def choose_folder(self):
        d = Gtk.FileDialog(title=tr("Ouvrir un dossier"), modal=True)
        if self.folder:
            d.set_initial_folder(Gio.File.new_for_path(self.folder))

        def done(dlg, res):
            try:
                f = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if f is not None and f.get_path():
                self.open_folder(f.get_path())
        d.select_folder(self, None, done)

    def choose_files(self):
        d = Gtk.FileDialog(title=tr("Ouvrir des photos"), modal=True)
        filt = Gtk.FileFilter(name=tr("Images"))
        filt.add_pixbuf_formats()
        for ext in fold.IMAGE_EXTS:
            filt.add_suffix(ext[1:])
        store = Gio.ListStore(item_type=Gtk.FileFilter)
        store.append(filt)
        d.set_filters(store)
        if self.folder:
            d.set_initial_folder(Gio.File.new_for_path(self.folder))

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            if f is not None and f.get_path():
                self.open_path(f.get_path())
        d.open(self, None, done)

    def _set_sort(self, key: str):
        settings["sort_key"] = key
        self._rescan(quiet=True)

    def _set_option(self, key: str, value: bool):
        settings[key] = value
        self._rescan(quiet=True)

    # ------------------------------------------------------------------ visionneuse

    def show_browser(self):
        if self.is_fullscreen():
            self.unfullscreen()
        self.stack.set_visible_child_name("browser")
        if 0 <= self.index < len(self.images):
            self.grid.select_path(self.images[self.index].path)
        self.grid.view.grab_focus()
        self.set_title(APP_NAME)
        self._sync_actions()

    def show_viewer(self):
        self.stack.set_visible_child_name("viewer")
        self.view.grab_focus()
        self._sync_actions()

    def back(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.show_browser()

    def _sync_actions(self):
        viewing = self.stack.get_visible_child_name() == "viewer"
        for name in self._viewer_actions:
            a = self.lookup_action(name)
            if a is not None:
                a.set_enabled(viewing)

    def go(self, step: int):
        n = len(self.images)
        if not n:
            return
        i = self.index + step
        if features.enabled("loop"):
            i %= n
        elif not 0 <= i < n:
            return
        self.show_index(i)

    def show_index(self, i: int, keep_view: bool = False):
        if not 0 <= i < len(self.images):
            return
        self.index = i
        entry = self.images[i]
        self.filmstrip.show_index(i)
        self.viewer_title.set_title(shown(entry.name))
        self.set_title(f"{shown(entry.name)} — {APP_NAME}")
        self._sync_status()
        path = entry.path
        preload = ()
        if features.enabled("preload"):
            n = len(self.images)
            near = [i + 1, i - 1, i + 2]
            preload = tuple(self.images[j % n].path for j in near if n > 1 and (features.enabled("loop") or 0 <= j < n))
        self.loader.request(path, lambda pic: self._loaded(pic, keep_view), preload)

    def _loaded(self, pic: Picture, keep_view: bool = False):
        if not (0 <= self.index < len(self.images)) or self.images[self.index].path != pic.path:
            return
        self.picture = pic
        if pic.error:
            self.view.set_picture(None)
            self.error_page.set_title(tr("Lecture impossible"))
            self.error_page.set_description(shown(pic.error))
            self.error_page.set_visible(True)
        else:
            self.error_page.set_visible(False)
            self.view.set_picture(pic.texture, pic.animation, keep_view)
        self._sync_status()
        self._sync_info()

    def zoom_toggle(self):
        if self.view.fit:
            self.view.set_zoom(1.0)
        else:
            self.view.set_fit()

    def _sync_status(self):
        n = len(self.images)
        parts = []
        if 0 <= self.index < n:
            parts.append(f"{self.index + 1} / {n}")
            self.viewer_title.set_subtitle(ntr("{0} sur {1} photo", "{0} sur {1} photos", n)
                                           .format(self.index + 1, n))
        pic = self.picture
        if pic is not None and 0 <= self.index < n and pic.path == self.images[self.index].path:
            if pic.width:
                w, h = self.view.image_size() if self.view.texture is not None else (pic.width, pic.height)
                parts.append(f"{w} × {h}")
            parts.append(fold.human_size(pic.size))
            if pic.format:
                parts.append(pic.format)
        if self.view.texture is not None:
            parts.append(percent(self.view.zoom))
        self.zoom_button.set_label(percent(self.view.zoom) if self.view.texture is not None else "—")
        self.status.set_label("  ·  ".join(parts))

    def _sync_info(self):
        if self.split.get_show_sidebar():
            self.info.show_picture(self.picture)

    def set_filmstrip(self, on: bool):
        settings["filmstrip"] = on
        self.strip_revealer.set_reveal_child(on)
        if on and 0 <= self.index < len(self.images):
            GLib.idle_add(lambda: (self.filmstrip.show_index(self.index), False)[1])

    def toggle_fullscreen(self):
        if self.stack.get_visible_child_name() != "viewer":
            return
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _sync_fullscreen(self):
        full = self.is_fullscreen()
        self.viewer.set_reveal_top_bars(not full)
        self.viewer.set_reveal_bottom_bars(not full)
        if full:
            self.split.set_show_sidebar(False)
            self.add_css_class("argos-fullscreen")
        else:
            self.remove_css_class("argos-fullscreen")
        GLib.idle_add(lambda: (self.view.set_fit() if self.view.fit else None, False)[1])

    def trash(self):
        if not 0 <= self.index < len(self.images):
            return
        entry = self.images[self.index]
        try:
            Gio.File.new_for_path(entry.path).trash(None)
        except GLib.Error as e:
            alert(self, tr("Impossible de mettre à la corbeille"), shown(e.message))
            return
        self.loader.forget(entry.path)
        del self.images[self.index]
        self.filmstrip.set_model(store_of(self.images))
        self.grid.set_model(store_of(self.dirs + self.images))
        toast(self, tr("« {0} » mis à la corbeille").format(shown(entry.name)))
        if not self.images:
            self.show_browser()
        else:
            self.show_index(min(self.index, len(self.images) - 1))

    # ------------------------------------------------------------------ clavier, fermeture

    def _on_key(self, ctrl, keyval, keycode, state):
        if self.stack.get_visible_child_name() != "viewer":
            if keyval == Gdk.KEY_BackSpace and not isinstance(self.get_focus(), Gtk.Text):
                self.go_up()
                return True
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if keyval in (Gdk.KEY_Right, Gdk.KEY_space, Gdk.KEY_Page_Down, Gdk.KEY_Down):
            if keyval == Gdk.KEY_Down and not self.view.fit:
                return False
            self.go(1)
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_BackSpace, Gdk.KEY_Page_Up, Gdk.KEY_Up):
            if keyval == Gdk.KEY_Up and not self.view.fit:
                return False
            self.go(-1)
            return True
        if keyval == Gdk.KEY_Home:
            self.show_index(0)
            return True
        if keyval == Gdk.KEY_End:
            self.show_index(len(self.images) - 1)
            return True
        if keyval == Gdk.KEY_Escape:
            self.back()
            return True
        return False

    def _on_close(self, *_):
        settings["window_maximized"] = self.is_maximized()
        if not self.is_maximized() and not self.is_fullscreen():
            w, h = self.get_default_size()
            settings["window_width"], settings["window_height"] = w, h
        if self._monitor is not None:
            self._monitor.cancel()
        return False
