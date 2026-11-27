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
from ..i18n import LANGUAGE, decimal, ntr, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import alert, shown, toast  # noqa: E402
from .gallery import Filmstrip, Grid, nearest_size, step_size, store_of  # noqa: E402
from .filemenu import FileMenu  # noqa: E402
from .info import InfoPanel  # noqa: E402
from .thumbs import forget_everywhere  # noqa: E402
from .viewer import ImageView  # noqa: E402


def percent(zoom: float) -> str:
    p = zoom * 100
    return decimal(p, 0 if p >= 10 else 1) + ("%" if LANGUAGE == "en" else " %")


def count_text(entries) -> str:
    """« 12 photos », ou « 12 photos, 1 vidéo » quand le dossier a des vidéos."""
    videos = sum(1 for e in entries if fold.is_video(e.name))
    photos = len(entries) - videos
    text = ntr("{0} photo", "{0} photos", photos).format(photos)
    if videos:
        text += ", " + ntr("{0} vidéo", "{0} vidéos", videos).format(videos)
    return text


def duration_text(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class MainWindow(FileMenu, Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_size_request(360, 360)
        self.set_default_size(settings["window_width"], settings["window_height"])
        if settings["window_maximized"]:
            self.maximize()
        self.loader = Loader()
        self.folder = ""
        self._videos_shown = None        # vidéos montrées par la dernière lecture du dossier (fonctionnalité « videos »)
        self.dirs: list[fold.Entry] = []
        self.images: list[fold.Entry] = []
        self.index = -1
        self.picture: Optional[Picture] = None
        self._scan_seq = 0
        self._monitor = None
        self._refresh_id = 0
        self._want_path: Optional[str] = None      # photo à montrer une fois le dossier lu
        from collections import OrderedDict
        # enregistrement en cours (fil) : un seul à la fois, les suites (« Enregistrer ? » en changeant de photo)
        # attendent sa fin — sinon la rotation était appliquée DEUX fois à la photo
        self._saving = False
        self._after_save: list = []
        self._folder_cache: "OrderedDict[str, tuple]" = OrderedDict()   # dossier → (options, (dossiers, photos))

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
        sec.append(tr("Convertir / redimensionner…"), "win.image-tools")
        sec.append(tr("Annoter l'image…"), "win.annotate")
        sec.append(tr("Diaporama"), "win.slideshow")
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
        self.sidebar_button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=settings["sidebar"],
                                               tooltip_text=tr("Barre latérale (F9)"))
        header.pack_start(self.sidebar_button)
        header.pack_start(self.up_button)
        header.pack_start(Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=tr("Ouvrir un dossier (Ctrl+O)"),
                                     action_name="win.open-folder"))
        self.browser_title = Adw.WindowTitle()
        header.set_title_widget(self.browser_title)
        header.pack_end(self._menu())
        self.grid = Grid(nearest_size(settings["grid_size"]))
        # Ctrl + molette sur la grille : taille des vignettes (comme Nautilus / gThumb), en CAPTURE avant le défilement
        grid_wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL,
                                               propagation_phase=Gtk.PropagationPhase.CAPTURE)
        grid_wheel.connect("scroll", self._on_grid_wheel)
        self.grid.add_controller(grid_wheel)
        self._grid_wheel_acc = 0.0
        settings.connect(self._on_setting)
        self.grid.connect("open", lambda _g, e: self.open_entry(e))
        self.grid.connect("context-menu", self._grid_menu)
        self.empty = Adw.StatusPage(icon_name="image-x-generic-symbolic", title=tr("Aucune image"))
        self.browser_stack = Gtk.Stack()
        self.grid_overlay = Gtk.Overlay(child=self.grid)      # porte le menu du clic droit (voir show_file_menu)
        self.browser_stack.add_named(self.grid_overlay, "grid")
        self.browser_stack.add_named(self.empty, "empty")
        # roue de taille normale, centrée (vu chez Tof : étirée, elle remplissait toute la grille)
        self.browser_stack.add_named(Gtk.Spinner(spinning=True, width_request=32, height_request=32,
                                                 halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER),
                                     "loading")
        from .sidebar import FolderTree
        self.tree = FolderTree()
        self.tree.connect("picked", lambda _t, path: path != self.folder and self.open_folder(path))
        # propriétés sous l'arbre, comme gThumb (demande de Tof), affichées par le bouton (i) de la barre d'état
        self.browser_info = InfoPanel()
        self.browser_info.set_visible(settings["browser_info"])
        side = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=self.tree, end_child=self.browser_info,
                         resize_end_child=False, shrink_end_child=False, shrink_start_child=False, wide_handle=True)
        self.side_paned = side
        # vu chez Tof : posé au « map », le séparateur n'avait pas encore de taille (position 0, arbre écrasé) →
        # posé quand il en a une, proportion retenue (`info_ratio`, au glisser seulement)
        self.tree.set_size_request(-1, 120)
        self._side_pending = True
        self._side_placing = False
        side.connect("notify::max-position", lambda *_: self._side_pending and self._place_side())
        side.connect("notify::position", self._side_moved)
        bar = Gtk.Box(spacing=6, margin_start=12, margin_end=6, margin_top=2, margin_bottom=2)
        bar.add_css_class("argos-status")
        self.browser_status = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.browser_status.add_css_class("caption")
        self.browser_status.add_css_class("numeric")
        bar.append(self.browser_status)
        self.browser_info_button = Gtk.ToggleButton(icon_name="document-properties-symbolic", css_classes=["flat"],
                                                    active=settings["browser_info"],
                                                    tooltip_text=tr("Propriétés (I)"))
        self.browser_info_button.connect("toggled", lambda b: self._set_browser_info(b.get_active()))
        bar.append(self.browser_info_button)
        self.grid.selection.connect("selection-changed", lambda *_: self._grid_selected())
        self.browser_split = Adw.OverlaySplitView(sidebar=side, content=self.browser_stack,
                                                  show_sidebar=settings["sidebar"], min_sidebar_width=230,
                                                  max_sidebar_width=320, sidebar_width_fraction=0.25)
        self.sidebar_button.bind_property("active", self.browser_split, "show-sidebar",
                                          GObject.BindingFlags.BIDIRECTIONAL)
        self.browser_split.connect("notify::show-sidebar", self._sidebar_toggled)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        narrow.add_setter(self.browser_split, "collapsed", True)
        self.add_breakpoint(narrow)
        view = Adw.ToolbarView(content=self.browser_split, bottom_bar_style=Adw.ToolbarStyle.RAISED_BORDER)
        view.add_bottom_bar(bar)
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
        self.crop_button = Gtk.Button(icon_name="argos-crop-symbolic", tooltip_text=tr("Recadrer (C)"),
                                      action_name="win.crop")
        header.pack_start(self.crop_button)
        # Annoter (repris d'Odysseus, idée d'un utilisateur) : fenêtre à part, enregistrement sûr
        self.annotate_button = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text=tr("Annoter l'image (A)"),
                                          action_name="win.annotate", visible=features.enabled("annotate"))
        header.pack_start(self.annotate_button)
        save_menu = Gio.Menu()
        save_menu.append(tr("Enregistrer sous…"), "win.save-as")
        save_menu.append(tr("Annuler les modifications"), "win.revert")
        self.save_button = Adw.SplitButton(label=tr("Enregistrer"), menu_model=save_menu, visible=False,
                                           tooltip_text=tr("Remplacer la photo (Ctrl+S)"), action_name="win.save")
        self.save_button.add_css_class("suggested-action")
        header.pack_end(self.save_button)

        self.view = ImageView()
        self.view.connect("navigate", lambda _v, step: self.go(step))
        self.view.connect("zoom-changed", lambda *_: self._sync_status())
        self.view.connect("double-click", lambda *_: self.toggle_fullscreen())
        # menu accroché à l'OVERLAY (piège de GTK 4.14 vu dans Nausicaa : accroché ailleurs, un menu recevait une
        # hauteur trop petite et se tronquait avec une barre de défilement ; même coordonnées : la vue le remplit)
        self.view.connect("context-menu", lambda v, x, y: 0 <= self.index < len(self.images) and
                          self.show_file_menu(v.get_parent(), self.images[self.index].path, x, y))
        self.error_page = Adw.StatusPage(icon_name="image-missing-symbolic", visible=False)
        overlay = Gtk.Overlay(child=self.view)
        # vidéos (demande de Tof, comme gThumb) : lecteur de GTK (GStreamer), lecture dès qu'on arrive dessus
        self.video = Gtk.Video(autoplay=True, visible=False, hexpand=True, vexpand=True, focusable=False)
        self.video.add_css_class("argos-video")
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL
                                          | Gtk.EventControllerScrollFlags.DISCRETE)
        wheel.connect("scroll", self._on_video_wheel)
        self.video.add_controller(wheel)
        clicks = Gtk.GestureClick()
        clicks.connect("pressed", lambda _g, n, _x, _y: n == 2 and self.toggle_fullscreen())
        self.video.add_controller(clicks)
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", lambda _g, _n, x, y: 0 <= self.index < len(self.images) and
                      self.show_file_menu(self.video.get_parent(), self.images[self.index].path, x, y))
        self.video.add_controller(right)
        self._video_error_id = 0
        overlay.add_overlay(self.video)
        overlay.add_overlay(self.error_page)
        self._arrows = []
        for icon, step, align in (("go-previous-symbolic", -1, Gtk.Align.START),
                                  ("go-next-symbolic", 1, Gtk.Align.END)):
            b = Gtk.Button(icon_name=icon, halign=align, valign=Gtk.Align.CENTER, margin_start=12, margin_end=12,
                           css_classes=["osd", "circular", "argos-arrow"], focusable=False,
                           tooltip_text=tr("Photo précédente") if step < 0 else tr("Photo suivante"))
            b.connect("clicked", lambda _b, s=step: self.go(s))
            overlay.add_overlay(b)
            self._arrows.append(b)
        # plein écran : flèches et pointeur cachés au repos, montrés au bord de l'écran ou à la navigation (demande
        # de Tof) — avant, la souris étant toujours « sur l'image », les flèches restaient affichées
        self.view_overlay = overlay
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_view_motion)
        overlay.add_controller(motion)
        self._chrome_timer = 0
        self._last_motion = None
        self._slideshow_id = 0
        self._slideshow_paused = False
        self._slide_step = 0             # photo suivante / précédente demandée : transition dans ce sens
        self._shown_path = None          # photo demandée en dernier à la visionneuse

        self.info = InfoPanel()
        self.split = Adw.OverlaySplitView(content=overlay, sidebar=self.info, sidebar_position=Gtk.PackType.END,
                                          show_sidebar=False, min_sidebar_width=240, max_sidebar_width=320)
        self.info_button.bind_property("active", self.split, "show-sidebar",
                                       GObject.BindingFlags.BIDIRECTIONAL)
        self.split.connect("notify::show-sidebar", lambda *_: self._sync_info())

        self.filmstrip = Filmstrip()
        self.filmstrip.connect("picked", lambda _f, i: i == self.index or self.guard(lambda: self.show_index(i)))
        self.filmstrip.set_visible(settings["filmstrip"])
        # séparateur réglable (demande de Tof) : la bande garde sa hauteur quand la fenêtre change de taille
        self.strip_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=self.split,
                                     end_child=self.filmstrip, resize_start_child=True, resize_end_child=False,
                                     shrink_start_child=False, shrink_end_child=False, wide_handle=True)
        self.strip_paned.connect("notify::position", self._strip_moved)
        # hauteur posée une fois le séparateur dimensionné (au « map », il n'a pas encore de taille)
        self.strip_paned.connect("notify::max-position", lambda *_: self._strip_pending and self._place_strip())
        self._strip_pending = True
        self._placing = False
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
        bottom.append(self._build_crop_bar())
        bottom.append(status)

        self.viewer = Adw.ToolbarView(content=self.strip_paned, top_bar_style=Adw.ToolbarStyle.FLAT,
                                      bottom_bar_style=Adw.ToolbarStyle.RAISED_BORDER)
        self.viewer.add_top_bar(header)
        self.viewer.add_bottom_bar(bottom)
        return self.viewer

    RATIOS = (("free", None), ("original", "original"), ("1:1", 1.0), ("4:3", 4 / 3), ("3:2", 3 / 2),
              ("16:9", 16 / 9), ("3:4", 3 / 4), ("2:3", 2 / 3), ("9:16", 9 / 16))

    def _build_crop_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        bar.append(Gtk.Label(label=tr("Proportions")))
        labels = [tr("Libre"), tr("Comme l'original")] + [r for r, _v in self.RATIOS[2:]]
        self.ratio_drop = Gtk.DropDown.new_from_strings(labels)
        self.ratio_drop.connect("notify::selected", lambda *_: self._ratio_changed())
        bar.append(self.ratio_drop)
        hint = Gtk.Label(label=tr("Glissez pour tracer le cadre, ses bords et ses coins pour l'ajuster"), xalign=0,
                         hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        hint.add_css_class("dim-label")
        bar.append(hint)
        cancel = Gtk.Button(label=tr("Annuler"))
        cancel.connect("clicked", lambda *_: self.end_crop(False))
        bar.append(cancel)
        ok = Gtk.Button(label=tr("Recadrer"), css_classes=["suggested-action"])
        ok.connect("clicked", lambda *_: self.end_crop(True))
        bar.append(ok)
        self.crop_revealer = Gtk.Revealer(child=bar, reveal_child=False,
                                          transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        return self.crop_revealer

    def _ratio_changed(self):
        value = self.RATIOS[self.ratio_drop.get_selected()][1]
        if value == "original":
            w, h = self.view.full_size()
            value = w / h if h else None
        self.view.set_ratio(value)

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
        add("slideshow", self.slideshow, ["F5"])
        add("zoom-in", lambda: self._zoom(1), ["plus", "KP_Add", "equal", "<Control>plus"])
        add("zoom-out", lambda: self._zoom(-1), ["minus", "KP_Subtract", "<Control>minus"])
        add("zoom-fit", self.view.set_fit, ["0", "KP_0", "<Control>0"])
        add("zoom-100", lambda: self.view.set_zoom(1.0), ["1", "KP_1"])
        add("zoom-toggle", self.zoom_toggle)
        add("rotate-right", lambda: self.rotate(1), ["r"])
        add("rotate-left", lambda: self.rotate(3), ["<Shift>r"])
        add("filmstrip", self.toggle_side, ["F9"])
        add("info", lambda: self.info_button.set_active(not self.info_button.get_active()), ["i"])
        add("trash", self.trash, ["Delete"])
        add("crop", self.start_crop, ["c"])
        add("save", self.save, ["<Control>s"])
        add("save-as", self.save_as, ["<Control><Shift>s"])
        add("revert", self.revert)
        sort = Gio.SimpleAction.new_stateful("sort", GLib.VariantType.new("s"), GLib.Variant("s", settings["sort_key"]))
        sort.connect("activate", lambda a, v: (a.set_state(v), self._set_sort(v.get_string())))
        self.add_action(sort)
        for key, name in (("sort_reverse", "sort-reverse"), ("show_hidden", "show-hidden")):
            a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", settings[key]))
            a.connect("activate", lambda a, _p, key=key: (a.set_state(GLib.Variant("b", not a.get_state())),
                                                          self._set_option(key, a.get_state().get_boolean())))
            self.add_action(a)
        # (zoom-in / zoom-out servent aussi dans la grille : taille des vignettes)
        self._viewer_actions = ("back", "fullscreen", "zoom-fit", "zoom-100", "zoom-toggle",
                                "rotate-right", "rotate-left", "info", "crop", "save", "save-as",
                                "revert")
        self._setup_file_menu()
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
        self.tree.reveal(path)
        if show is None:
            self.show_browser()
        else:
            # photo demandée (Odysseus, Nautilus, « Ouvrir avec ») : visionneuse TOUT DE SUITE, la photo lue en même
            # temps que le dossier (vu chez Tof : la grille passait une fraction de seconde avant)
            self.images = [fold.Entry(show, os.path.basename(show), False)]
            self.index = -1
            self.filmstrip.set_model(store_of([]))
            self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
            self.show_index(0)
            self.show_viewer()
            self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._watch(path)
        cached = self._folder_cache.get(path)
        if cached is not None and cached[0] == self._scan_options():
            self._folder_cache.move_to_end(path)
            self._scan_seq += 1
            self._scanned(self._scan_seq, cached[1], "")       # tout de suite, puis relu en arrière-plan
            self._rescan(quiet=True)
        else:
            self._rescan()

    @staticmethod
    def _pretty(path: str) -> str:
        home = os.path.expanduser("~")
        return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path

    FOLDER_CACHE = 30

    @staticmethod
    def _scan_options():
        return settings["show_hidden"], settings["sort_key"], settings["sort_reverse"], \
            features.enabled("videos") and not features.blocked("videos")

    @staticmethod
    def _signature(result):
        dirs, images = result
        return [d.path for d in dirs], [(e.path, e.size, e.mtime) for e in images]

    def _rescan(self, quiet: bool = False):
        self._scan_seq += 1
        seq = self._scan_seq
        folder = self.folder
        if not quiet:
            self.browser_stack.set_visible_child_name("loading")
        opts = self._scan_options()
        hidden, key, rev, videos = opts
        self._videos_shown = videos

        def work():
            try:
                result = fold.scan(folder, hidden, key, rev, videos)
                err = ""
            except OSError as e:
                result, err = ([], []), e.strerror or str(e)
            GLib.idle_add(lambda: (self._scanned(seq, result, err, folder, opts), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def _scanned(self, seq: int, result, err: str, folder: str = "", opts=None):
        if seq != self._scan_seq:
            return
        if folder and not err:
            # dossiers déjà vus gardés (demande de Tof : revenir sur un dossier ne recharge plus)
            old = self._folder_cache.get(folder)
            self._folder_cache[folder] = (opts, result)
            self._folder_cache.move_to_end(folder)
            while len(self._folder_cache) > self.FOLDER_CACHE:
                self._folder_cache.popitem(last=False)
            # relecture en arrière-plan identique à ce qui est affiché : rien ne bouge (défilement, vignettes)
            if old is not None and self._want_path is None and self.dirs is old[1][0] and \
                    self._signature(old[1]) == self._signature(result):
                self._folder_cache[folder] = (opts, old[1])
                if self.browser_stack.get_visible_child_name() == "loading":
                    self.browser_stack.set_visible_child_name("grid")
                return
        dirs, images = result
        current = self.images[self.index].path if 0 <= self.index < len(self.images) else None
        self.dirs, self.images = dirs, images
        self.grid.set_model(store_of(images))          # photos seulement : les dossiers sont dans l'arbre (gThumb)
        self.filmstrip.set_model(store_of(images))
        if err:
            self.empty.set_title(tr("Accès impossible"))
            self.empty.set_description(shown(err))
            self.browser_stack.set_visible_child_name("empty")
        elif not images:
            self.empty.set_title(tr("Aucune image"))
            self.empty.set_description("")
            self.browser_stack.set_visible_child_name("empty")
        else:
            self.browser_stack.set_visible_child_name("grid")
        self._grid_selected()
        want = self._want_path
        self._want_path = None
        if want is not None:
            i = self._index_of(want)
            if i >= 0 and want == self._shown_path and self.stack.get_visible_child_name() == "viewer":
                # photo ouverte de l'extérieur, déjà affichée (liste provisoire) : seulement sa place dans le dossier
                # — la relire remettait à zéro un zoom, une rotation ou un recadrage commencé pendant la lecture
                self.index = i
                self.filmstrip.show_index(i)
                self._sync_status()
            elif i >= 0:
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
                forget_everywhere(p)
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

    def _grid_menu(self, grid, entry, x, y):
        # translate_coordinates : (x, y) dans le PyGObject de Zorin, (ok, x, y) ailleurs (vu dans Nausicaa)
        r = grid.view.translate_coordinates(self.grid_overlay, x, y)
        if isinstance(r, tuple) and len(r) == 3:
            x, y = (r[1], r[2]) if r[0] else (x, y)
        elif isinstance(r, tuple) and len(r) == 2:
            x, y = r
        self.show_file_menu(self.grid_overlay, entry.path, x, y)

    def _set_browser_info(self, on: bool):
        settings["browser_info"] = on
        self.browser_info.set_visible(on)
        if on:
            self._side_pending = True
            GLib.idle_add(lambda: (self._place_side(), False)[1])
            self._grid_selected()

    def _place_side(self):
        h = self.side_paned.get_height()
        if h < 200 or not self.browser_info.get_visible():
            return
        self._side_pending = False
        ratio = min(0.85, max(0.25, settings["info_ratio"]))
        self._side_placing = True
        self.side_paned.set_position(int(h * ratio))
        self._side_placing = False

    def _side_moved(self, *_):
        if self._side_placing or self._side_pending or not self.browser_info.get_visible():
            return
        h = self.side_paned.get_height()
        if h >= 200 and self.side_paned.get_mapped():
            settings["info_ratio"] = round(self.side_paned.get_position() / h, 3)

    def _grid_selected(self):
        chosen = self.grid.selected_entries()
        entry = chosen[0] if len(chosen) == 1 else None
        total = sum(e.size for e in self.images)
        parts = [count_text(self.images) + f" ({fold.human_size(total)})"]
        if len(chosen) > 1:
            parts.append(ntr("{0} sélectionnée ({1})", "{0} sélectionnées ({1})", len(chosen)).format(
                len(chosen), fold.human_size(sum(e.size for e in chosen))))
            entry = chosen[-1]
        elif entry is not None:
            parts.append(tr("« {0} » sélectionné").format(shown(entry.name)) if entry.is_dir else
                         tr("« {0} » sélectionnée ({1})").format(shown(entry.name), fold.human_size(entry.size)))
        self.browser_status.set_label("  ·  ".join(parts))
        if self.browser_info.get_visible():
            self.browser_info.show_path(entry.path if entry is not None else (self.folder or None))

    def _sidebar_toggled(self, *_):
        if not self.browser_split.get_collapsed():       # repliée par la largeur : pas un choix
            settings["sidebar"] = self.browser_split.get_show_sidebar()

    def toggle_side(self):
        """F9 : barre latérale dans la grille, bande de vignettes dans la visionneuse."""
        if self.stack.get_visible_child_name() == "viewer":
            self.strip_button.set_active(not self.strip_button.get_active())
        else:
            self.sidebar_button.set_active(not self.sidebar_button.get_active())

    def _set_sort(self, key: str):
        settings["sort_key"] = key
        self._rescan(quiet=True)

    def _set_option(self, key: str, value: bool):
        settings[key] = value
        self._rescan(quiet=True)

    # ------------------------------------------------------------------ visionneuse

    def show_browser(self):
        self._stop_video()
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
        if self.view.cropping:
            self.end_crop(False)
        elif self.is_fullscreen():
            self.unfullscreen()
        else:
            self.guard(self.show_browser)

    def _sync_actions(self):
        viewing = self.stack.get_visible_child_name() == "viewer"
        for name in self._viewer_actions:
            a = self.lookup_action(name)
            if a is not None:
                a.set_enabled(viewing)
        self._sync_edit()

    # -- plein écran : flèches et pointeur à minuterie ---------------------------------------------

    CHROME_MS = 2000                 # flèches et pointeur cachés après 2 s sans mouvement
    EDGE = 0.15                      # « bord » de l'écran : 15 % de chaque côté (120 px au moins)

    def _show_chrome(self, arrows: bool):
        """Plein écran : pointeur (et flèches si `arrows`) montrés, cachés de nouveau après CHROME_MS."""
        if not self.is_fullscreen():
            return
        self.view.set_pointer_hidden(False)
        if arrows:
            for b in self._arrows:
                b.add_css_class("shown")
        if self._chrome_timer:
            GLib.source_remove(self._chrome_timer)
        self._chrome_timer = GLib.timeout_add(self.CHROME_MS, self._hide_chrome)

    def _hide_chrome(self):
        self._chrome_timer = 0
        if any(b.get_state_flags() & Gtk.StateFlags.PRELIGHT for b in self._arrows):
            self._chrome_timer = GLib.timeout_add(self.CHROME_MS, self._hide_chrome)   # pointeur sur une flèche
            return False
        for b in self._arrows:
            b.remove_css_class("shown")
        if self.is_fullscreen() and not self.view.cropping:
            self.view.set_pointer_hidden(True)
        return False

    def _reset_chrome(self):
        """Hors du plein écran : tout redevient comme avant (flèches au survol, pointeur visible)."""
        if self._chrome_timer:
            GLib.source_remove(self._chrome_timer)
            self._chrome_timer = 0
        for b in self._arrows:
            b.remove_css_class("shown")
        self.view.set_pointer_hidden(False)

    def _on_view_motion(self, _ctl, x, y):
        # GTK envoie aussi un « mouvement » sans mouvement quand le contenu change sous le pointeur : ignoré
        if self._last_motion is not None and abs(x - self._last_motion[0]) < 2 and abs(y - self._last_motion[1]) < 2:
            return
        self._last_motion = (x, y)
        width = self.view_overlay.get_width()
        edge = max(120, width * self.EDGE)
        self._show_chrome(arrows=x < edge or x > width - edge)

    # -- diaporama ---------------------------------------------------------------------------------

    def slideshow(self):
        """F5, menus : plein écran, photo suivante toutes les N s (Préférences), Espace = pause / reprise, ← → pour
        naviguer, Échap pour arrêter. Dans la grille : à partir de la photo sélectionnée (sinon la première)."""
        if not self.images:
            return
        if self.stack.get_visible_child_name() != "viewer":
            chosen = [e for e in self.grid.selected_entries() if not e.is_dir]
            start = self._index_of(chosen[0].path) if chosen else 0
            self.show_index(max(0, start))
            self.show_viewer()
            self._start_slideshow()
            return
        self.guard(self._start_slideshow)

    def _start_slideshow(self):
        if self.stack.get_visible_child_name() != "viewer" or not self.images:
            return
        self._slideshow_paused = False
        if 0 <= self.index < len(self.images) and fold.is_video(self.images[self.index].name):
            i = self._step_index(1, photos_only=True)          # diaporama de PHOTOS : on part de la suivante
            if i is None:
                toast(self, tr("Aucune photo pour le diaporama"))
                return
            self.show_index(i)
        if not self.is_fullscreen():
            self.fullscreen()
        self._arm_slideshow()
        toast(self, tr("Diaporama — Espace : pause, Échap : arrêter"))

    def _arm_slideshow(self):
        if self._slideshow_id:
            GLib.source_remove(self._slideshow_id)
        self._slideshow_id = GLib.timeout_add(int(settings["slideshow_delay"]) * 1000, self._slideshow_tick)

    def _slideshow_tick(self):
        self._slideshow_id = 0
        if self.stack.get_visible_child_name() != "viewer" or self._slideshow_paused:
            return False
        if self._step_index(1, photos_only=True) is None:      # plus de photo (les vidéos sont sautées)
            self.stop_slideshow()
            toast(self, tr("Fin du diaporama"))
            return False
        self._go(1, photos_only=True)                # le minuteur vient de se terminer : slideshow_running() est faux
        self._arm_slideshow()
        return False

    def slideshow_running(self) -> bool:
        return bool(self._slideshow_id) or self._slideshow_paused

    def toggle_slideshow_pause(self):
        self._slideshow_paused = not self._slideshow_paused
        if self._slideshow_paused:
            if self._slideshow_id:
                GLib.source_remove(self._slideshow_id)
                self._slideshow_id = 0
            toast(self, tr("Diaporama en pause"))
        else:
            self._arm_slideshow()
            toast(self, tr("Diaporama repris"))

    def stop_slideshow(self):
        if self._slideshow_id:
            GLib.source_remove(self._slideshow_id)
            self._slideshow_id = 0
        self._slideshow_paused = False

    def go(self, step: int):
        if self.slideshow_running() and not self._slideshow_paused:
            self._arm_slideshow()                   # navigation à la main : le minuteur repart de zéro
        self._show_chrome(arrows=True)
        if self.pending() or self.view.cropping:
            self.guard(lambda: self._go(step))
            return
        self._go(step)

    def _step_index(self, step: int, photos_only: bool = False) -> Optional[int]:
        """Position après `step` (bouclée si « Revenir au début ») ; `photos_only` (diaporama) : vidéos sautées."""
        n = len(self.images)
        i = self.index
        for _ in range(n):
            i += step
            if features.enabled("loop"):
                i %= n
            elif not 0 <= i < n:
                return None
            if not (photos_only and fold.is_video(self.images[i].name)):
                return i if i != self.index else None
        return None

    def _go(self, step: int, photos_only: Optional[bool] = None):
        if not self.images:
            return
        if photos_only is None:
            photos_only = self.slideshow_running()
        i = self._step_index(step, photos_only=photos_only)
        if i is None:
            self._slide_step = 0
            return
        self._slide_step = step                     # transition dans le sens du pas (diaporama, clavier, molette)
        self.show_index(i)

    def show_index(self, i: int, keep_view: bool = False):
        if not 0 <= i < len(self.images):
            return
        self.index = i
        entry = self.images[i]
        self._shown_path = entry.path
        if self.showing_video() and (self.video.get_file() is None or self.video.get_file().get_path() != entry.path):
            self._stop_video()                      # on quitte la vidéo : le son s'arrête tout de suite
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
        step, self._slide_step = self._slide_step, 0
        transition = (settings["slideshow_transition"], step) if step else None
        if pic.video:
            self.error_page.set_visible(False)
            self.view.set_picture(None)
            self._play(pic.path)
        elif pic.error:
            self._stop_video()
            self.error_page.set_icon_name("image-missing-symbolic")
            self.error_page.set_tooltip_text(None)
            self.view.set_picture(None)
            self.error_page.set_title(tr("Lecture impossible"))
            self.error_page.set_description(shown(pic.error))
            self.error_page.set_visible(True)
        else:
            self._stop_video()
            self.error_page.set_visible(False)
            self.view.set_picture(pic.texture, pic.animation, keep_view,
                                  vector=pic.path if pic.format in ("SVG", "SVGZ") else None,
                                  transition=transition)
        self._sync_status()
        self._sync_info()
        self._sync_edit()

    # ------------------------------------------------------------------ retouche

    # ------------------------------------------------------------------ vidéos

    def showing_video(self) -> bool:
        return self.video.get_visible()

    def _play(self, path: str):
        """Vidéo affichée : lue tout de suite par Gtk.Video (GStreamer). Format illisible : page d'erreur."""
        if self.video.get_visible() and self.video.get_file() is not None and \
                self.video.get_file().get_path() == path:
            return
        self._stop_video()
        self.video.set_visible(True)
        self.video.set_filename(path)
        stream = self.video.get_media_stream()
        if stream is not None:
            self._video_error_id = stream.connect("notify::error", self._on_video_error)

    def _on_video_error(self, stream, _pspec):
        err = stream.get_error()
        if err is None:
            return
        self._stop_video()
        # le message de GStreamer (« Error from element /GstPlayBin:playbin/… ») ne dit rien à personne : une phrase
        # claire, le détail dans l'infobulle
        self.error_page.set_icon_name("video-x-generic-symbolic")
        self.error_page.set_title(tr("Lecture impossible"))
        self.error_page.set_description(tr("Format non pris en charge, ou fichier abîmé.") + "\n\n" + tr(
            "Il manque peut-être un décodeur : sudo apt install gstreamer1.0-libav — ou ouvrez la vidéo avec un "
            "autre lecteur (clic droit, Ouvrir avec)."))
        self.error_page.set_tooltip_text(shown(err.message))
        self.error_page.set_visible(True)

    def _stop_video(self):
        stream = self.video.get_media_stream()
        if stream is not None:
            if self._video_error_id:
                stream.disconnect(self._video_error_id)
            stream.pause()
        self._video_error_id = 0
        self.video.set_file(None)
        self.video.set_visible(False)

    def toggle_video(self):
        stream = self.video.get_media_stream()
        if stream is not None:
            if stream.get_ended():
                stream.seek(0)
                stream.play()
            else:
                stream.set_playing(not stream.get_playing())

    def _on_video_wheel(self, _ctrl, _dx, dy):
        if not features.enabled("wheel_browse") or dy == 0:
            return False
        self.go(1 if dy > 0 else -1)
        return True

    def editable(self) -> bool:
        """La photo affichée peut-elle être retouchée et enregistrée ? (pas une animation ni un SVG)"""
        pic = self.picture
        return (features.enabled("edit") and not features.blocked("edit") and pic is not None and not pic.error
                and pic.animation is None and pic.format not in ("SVG",) and not pic.video
                and 0 <= self.index < len(self.images) and pic.path == self.images[self.index].path)

    def pending(self) -> bool:
        return self.editable() and (self.view.rotation % 4 != 0 or self.view.crop is not None)

    def _sync_edit(self):
        # vidéo affichée : ni pivoter, ni zoomer, ni annoter (le lecteur de GTK a ses propres commandes)
        video = self.stack.get_visible_child_name() == "viewer" and self.showing_video()
        for name in ("rotate-left", "rotate-right", "zoom-in", "zoom-out", "zoom-toggle", "zoom-fit", "zoom-100",
                     "annotate"):
            a = self.lookup_action(name)
            if a is not None:
                on = not video and (name != "annotate" or features.enabled("annotate"))
                if name not in ("zoom-in", "zoom-out") or self.stack.get_visible_child_name() == "viewer":
                    on = on and (self.stack.get_visible_child_name() == "viewer" or name == "annotate")
                a.set_enabled(on)
        self.save_button.set_visible(self.pending())
        ok = self.editable()
        for name in ("crop", "save-as"):
            a = self.lookup_action(name)
            if a is not None:
                a.set_enabled(ok and self.stack.get_visible_child_name() == "viewer")

    def rotate(self, quarter: int):
        if self.view.cropping or self._saving or self.showing_video():
            return
        self.view.rotate(quarter)
        self._sync_status()
        self._sync_edit()

    def start_crop(self):
        if not self.editable() or self.view.cropping or self._saving:
            return
        self.view.start_crop()
        self._ratio_changed()
        self.crop_revealer.set_reveal_child(True)
        self.view.grab_focus()

    def end_crop(self, apply: bool):
        if not self.view.cropping:
            return
        self.view.finish_crop(apply)
        self.crop_revealer.set_reveal_child(False)
        self._sync_status()
        self._sync_edit()

    def revert(self):
        if self.view.cropping:
            self.end_crop(False)
        self.view.crop = None
        self.view.rotation = 0
        self.view.set_fit()
        self._sync_status()
        self._sync_edit()

    def guard(self, then):
        """Modifications en attente : « Enregistrer ? » avant de quitter la photo (comme Papers / Calypso)."""
        if self._saving:                    # la photo est en train d'être enregistrée : on y va après
            self._after_save.append(then)
            return
        if self.view.cropping:
            self.end_crop(False)
        if not self.pending():
            then()
            return
        name = os.path.basename(self.picture.path)

        def answer(r):
            if r == "save":
                self.save(after=then)
            elif r == "discard":
                self.revert()
                then()
        alert(self, tr("Enregistrer les modifications ?"),
              tr("« {0} » a été pivotée ou recadrée.").format(shown(name)),
              (("cancel", tr("Annuler")), ("discard", tr("Abandonner")), ("save", tr("Enregistrer"))),
              default="save", destructive="discard", suggested="save", callback=answer)

    def _edit_params(self):
        return self.view.rotation % 4, self.view.crop

    def save(self, after=None):
        """Remplace la photo par sa version pivotée / recadrée ; l'original est gardé pour « Annuler »."""
        if self._saving:
            if after is not None:
                self._after_save.append(after)
            return
        if not self.pending():
            return
        path = self.picture.path
        rotation, crop = self._edit_params()
        self._saving = True
        if after is not None:
            self._after_save.append(after)

        def work():
            from ..backend import edit
            try:
                copy = edit.backup(path)
                edit.apply(path, path, rotation, crop, replace=True)
                GLib.idle_add(lambda: (self._saved(path, path, copy), False)[1])
            except (edit.EditError, OSError) as e:
                msg = str(e)
                GLib.idle_add(lambda: (self._save_failed(msg), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def _save_done(self, run_after: bool):
        self._saving = False
        waiting, self._after_save = self._after_save, []
        if run_after:
            for cb in waiting:
                cb()

    def _save_failed(self, msg: str):
        self._save_done(False)              # photo pas enregistrée : on ne la quitte pas, les retouches restent
        alert(self, tr("Enregistrement impossible"), shown(msg))

    def _saved(self, src: str, target: str, copy: Optional[str], after=None):
        self.loader.forget(target)
        forget_everywhere(target)
        if copy:
            def undo():
                from ..backend import edit
                try:
                    edit.restore(copy, target)
                except OSError as e:
                    alert(self, tr("Impossible d'annuler"), shown(e.strerror or str(e)))
                    return
                self.loader.forget(target)
                forget_everywhere(target)
                self._reload_current()
            toast(self, tr("« {0} » enregistrée").format(shown(os.path.basename(target))), tr("Annuler"), undo, 8)
        else:
            toast(self, tr("Enregistrée sous « {0} »").format(shown(os.path.basename(target))))
        if src == target:
            self.view.crop = None
            self.view.rotation = 0
            self._reload_current()
        self._sync_edit()
        if src == target:
            self._save_done(True)
        if after is not None:
            after()

    def annotation_saved(self, target: str, copy: Optional[str]):
        """Image annotée enregistrée (fenêtre d'annotation) : caches oubliés, photo affichée relue ; photo remplacée
        → « Annuler » remet l'original. Un nouveau fichier arrive dans la grille par la surveillance du dossier."""
        self.loader.forget(target)
        forget_everywhere(target)
        if copy:
            def undo():
                from ..backend import edit
                try:
                    edit.restore(copy, target)
                except OSError as e:
                    alert(self, tr("Impossible d'annuler"), shown(e.strerror or str(e)))
                    return
                self.loader.forget(target)
                forget_everywhere(target)
                self._reload_current()
            toast(self, tr("« {0} » annotée").format(shown(os.path.basename(target))), tr("Annuler"), undo, 8)
        if 0 <= self.index < len(self.images) and \
                os.path.realpath(self.images[self.index].path) == os.path.realpath(target):
            self._reload_current()

    def _reload_current(self):
        if 0 <= self.index < len(self.images):
            path = self.images[self.index].path
            self.loader.forget(path)
            self.show_index(self.index)

    def save_as(self):
        if not self.editable():
            return
        from ..backend import edit
        path = self.picture.path
        stem, ext = os.path.splitext(os.path.basename(path))
        d = Gtk.FileDialog(title=tr("Enregistrer sous"), modal=True,
                           initial_folder=Gio.File.new_for_path(os.path.dirname(path)),
                           initial_name=f"{stem} ({tr('modifiée')}){ext}" if self.pending() else f"{stem}.png"
                           if ext.lower() != ".png" else f"{stem}.jpg")
        store = Gio.ListStore(item_type=Gtk.FileFilter)
        for e, label in edit.SAVE_FORMATS.items():
            f = Gtk.FileFilter(name=label)
            f.add_suffix(e)
            if e == "jpg":
                f.add_suffix("jpeg")
            if e == "tif":
                f.add_suffix("tiff")
            store.append(f)
        d.set_filters(store)
        rotation, crop = self._edit_params()

        def done(dlg, res):
            try:
                f = dlg.save_finish(res)
            except GLib.Error:
                return
            target = f.get_path() if f is not None else None
            if not target:
                return
            out_ext = os.path.splitext(target)[1].lower().lstrip(".")
            if out_ext not in edit.SAVE_FORMATS and out_ext not in ("jpeg", "tiff"):
                alert(self, tr("Format inconnu"),
                      tr("Terminez le nom par l'extension du format voulu : {0}").format(
                          ", ".join("." + e for e in edit.SAVE_FORMATS)))
                return

            def work():
                try:
                    # le sélecteur a déjà demandé « Remplacer ? » si le nom existait
                    written = edit.apply(path, target, rotation, crop, replace=True)
                    GLib.idle_add(lambda: (self._saved(path, written, None), False)[1])
                except (edit.EditError, OSError) as e:
                    msg = str(e)
                    GLib.idle_add(lambda: (alert(self, tr("Enregistrement impossible"), shown(msg)), False)[1])
            threading.Thread(target=work, daemon=True).start()
        d.save(self, None, done)

    def _zoom(self, step: int):
        """+ / − : zoom de la photo dans la visionneuse, taille des vignettes dans la grille."""
        if self.stack.get_visible_child_name() == "viewer":
            self.view.zoom_step(step)
        else:
            settings["grid_size"] = step_size(settings["grid_size"], step)

    def _on_grid_wheel(self, ctl, _dx, dy):
        if not ctl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False
        self._grid_wheel_acc += dy                 # pavé tactile : petits pas cumulés jusqu'à un cran
        if abs(self._grid_wheel_acc) >= 1:
            step = -1 if self._grid_wheel_acc > 0 else 1         # vers le haut : plus grandes
            self._grid_wheel_acc = 0.0
            settings["grid_size"] = step_size(settings["grid_size"], step)
        return True

    def _on_setting(self, key, value):
        if key == "grid_size":
            self.grid.set_size(nearest_size(value))
        elif key == "features":
            if self.folder and self._scan_options()[3] != self._videos_shown:
                if not self._scan_options()[3] and self.showing_video():
                    self.show_browser()
                self._rescan(quiet=True)                # vidéos montrées / retirées en direct
            self.annotate_button.set_visible(features.enabled("annotate"))
            a = self.lookup_action("annotate")
            if a is not None:
                a.set_enabled(features.enabled("annotate"))

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
            if any(fold.is_video(e.name) for e in self.images):
                self.viewer_title.set_subtitle(tr("{0} sur {1}").format(self.index + 1, n))
            else:
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
            if pic.video and pic.duration:
                parts.append(duration_text(pic.duration))
        if self.view.texture is not None:
            parts.append(percent(self.view.zoom))
        self.zoom_button.set_label(percent(self.view.zoom) if self.view.texture is not None else "—")
        self.status.set_label("  ·  ".join(parts))

    def _sync_info(self):
        if self.split.get_show_sidebar():
            self.info.show_picture(self.picture)

    STRIP_MIN = 96

    def _place_strip(self):
        """Hauteur retenue de la bande (`strip_height`), posée par la position du séparateur."""
        h = self.strip_paned.get_height()
        if h <= 0 or not self.filmstrip.get_visible():
            return
        self._strip_pending = False
        want = max(self.STRIP_MIN, min(settings["strip_height"], h - 150))
        self._placing = True
        self.strip_paned.set_position(h - want)
        self._placing = False

    def _strip_moved(self, *_):
        if self._placing or not self.filmstrip.get_visible() or self.is_fullscreen():
            return
        h = self.strip_paned.get_height()
        if h > 0 and self.strip_paned.get_mapped():
            settings["strip_height"] = max(self.STRIP_MIN, h - self.strip_paned.get_position())

    def set_filmstrip(self, on: bool):
        settings["filmstrip"] = on
        self.filmstrip.set_visible(on and not self.is_fullscreen())
        if on:
            self._strip_pending = True
            GLib.idle_add(lambda: (self._place_strip(), False)[1])
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
        self.filmstrip.set_visible(settings["filmstrip"] and not full)
        if not full:
            self._strip_pending = True
            GLib.idle_add(lambda: (self._place_strip(), False)[1])
        if full:
            self.split.set_show_sidebar(False)
            self.add_css_class("argos-fullscreen")
            self._last_motion = None
            self._show_chrome(arrows=True)            # montrées un instant, puis cachées
        else:
            self.remove_css_class("argos-fullscreen")
            self._reset_chrome()
            self.stop_slideshow()                     # quitter le plein écran arrête le diaporama
        GLib.idle_add(lambda: (self.view.set_fit() if self.view.fit else None, False)[1])

    def trash(self):
        """Suppr : la photo affichée, ou les photos sélectionnées dans la grille."""
        self.trash_paths(self._targets())

    # ------------------------------------------------------------------ clavier, fermeture

    def _on_key(self, ctrl, keyval, keycode, state):
        if self.stack.get_visible_child_name() != "viewer":
            if keyval in (Gdk.KEY_i, Gdk.KEY_I) and not state & Gdk.ModifierType.CONTROL_MASK \
                    and not isinstance(self.get_focus(), Gtk.Text):
                self.browser_info_button.set_active(not self.browser_info_button.get_active())
                return True
            if keyval == Gdk.KEY_BackSpace and not isinstance(self.get_focus(), Gtk.Text):
                self.go_up()
                return True
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if self.slideshow_running() and keyval in (Gdk.KEY_space, Gdk.KEY_p, Gdk.KEY_P):
            self.toggle_slideshow_pause()
            return True
        if self.showing_video() and keyval in (Gdk.KEY_space, Gdk.KEY_k, Gdk.KEY_K):
            self.toggle_video()                       # vidéo : Espace = lecture / pause (pas la photo suivante)
            return True
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
            if self.slideshow_running():
                self.stop_slideshow()                 # Échap : arrête le diaporama ET quitte le plein écran
            self.back()
            return True
        if self.view.cropping and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.end_crop(True)
            return True
        return False

    def _on_close(self, *_):
        if self.pending() and not getattr(self, "_closing", False):
            def go():
                self._closing = True
                self.close()
            self.guard(go)
            return True
        settings["window_maximized"] = self.is_maximized()
        if not self.is_maximized() and not self.is_fullscreen():
            w, h = self.get_default_size()
            settings["window_width"], settings["window_height"] = w, h
        if self._monitor is not None:
            self._monitor.cancel()
        self.stop_slideshow()
        self._stop_video()
        self._reset_chrome()
        settings.disconnect(self._on_setting)
        return False
