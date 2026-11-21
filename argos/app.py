"""Point d'entrée d'Argos.

Instance unique (GApplication). `argos PHOTO` ouvre la photo dans son dossier, `argos DOSSIER` la grille du dossier,
`argos` seul le dernier dossier ouvert (sinon Images). Chaque ouverture depuis l'extérieur (Nautilus, Odysseus,
« Ouvrir avec ») réutilise la fenêtre active, comme gThumb.
"""

from __future__ import annotations

import os
import signal
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, VERSION  # noqa: E402
from .backend import folder as fold  # noqa: E402
from .i18n import tr  # noqa: E402
from .settings import settings  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class ArgosApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        GLib.set_application_name(APP_NAME)

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        if display is not None:
            css = Gtk.CssProvider()
            css.load_from_path(os.path.join(DATA_DIR, "base.css"))
            Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            Gtk.IconTheme.get_for_display(display).add_search_path(os.path.join(DATA_DIR, "icons"))
            Gtk.Window.set_default_icon_name(APP_ID)
        from .ui.preferences import apply_theme
        apply_theme()
        from .backend import edit
        threading.Thread(target=edit.sweep_backups, daemon=True).start()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (self.quit(), False)[1])
        for name, cb, accels in (("preferences", self._prefs, ["<Control>comma"]),
                                 ("about", self._about, []),
                                 ("quit", self.quit, ["<Control>q"]),
                                 ("close", self._close_window, ["<Control>w"])):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
            self.set_accels_for_action(f"app.{name}", accels)

    def _window(self):
        win = self.get_active_window()
        if win is None:
            from .ui.window import MainWindow
            win = MainWindow(self)
            from .ui.shortcuts import attach_help
            attach_help(win)
        return win

    def do_activate(self):
        new = self.get_active_window() is None
        win = self._window()
        if new:
            last = settings["last_folder"]
            win.open_folder(last if last and os.path.isdir(last) else fold.pictures_dir())
        win.present()

    def do_open(self, files, n_files, hint):
        paths = [f.get_path() for f in files if f.get_path()]
        win = self._window()
        if paths:
            win.open_path(paths[0])
        else:
            win.open_folder(fold.pictures_dir())
        win.present()

    def _close_window(self):
        win = self.get_active_window()
        if win is not None:
            win.close()

    def _prefs(self):
        from .ui.preferences import PreferencesWindow
        PreferencesWindow(self.get_active_window()).present()

    def _about(self):
        about = Adw.AboutWindow(transient_for=self.get_active_window(), application_name=APP_NAME,
                                application_icon=APP_ID, version=VERSION, developer_name="Tof",
                                license_type=Gtk.License.GPL_3_0,
                                comments=tr("Visionneuse de photos simple et rapide"),
                                website="https://github.com/ToFpon/Argos",
                                issue_url="https://github.com/ToFpon/Argos/issues")
        about.add_credit_section(tr("Remerciements"), ["Ponce De Leon https://forum.zorin.com/u/Ponce-De-Leon"])
        about.present()


def _process_name(name: str):
    """« argos » dans le Moniteur système, ps, killall, au lieu de « python3 » (lanceur `python3 -c …`) : le
    Moniteur système trouve aussi l'icône en comparant ce nom à l'Exec du .desktop (vu avec Calypso)."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(15, ctypes.c_char_p(name.encode()[:15]), 0, 0, 0)     # 15 = PR_SET_NAME
    except (OSError, AttributeError):
        pass


def main(argv=None) -> int:
    if "--version" in (argv or sys.argv[1:]):
        print(f"{APP_NAME} {VERSION}")
        return 0
    _process_name("argos")
    GLib.set_prgname(APP_ID)
    return ArgosApp().run(argv if argv is not None else sys.argv)
