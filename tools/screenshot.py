#!/usr/bin/env python3
"""Capture de la fenêtre d'Argos (à lancer sous xvfb-run), réglages jetables.

    xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tools/screenshot.py SORTIE.png CHEMIN [options]

CHEMIN : une photo (visionneuse) ou un dossier (grille).
Options : --dark --size=LxH --delay=ms --zoom=FACTEUR --info --fullscreen --no-strip --prefs=PAGE --crop --rotate
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
out = sys.argv[1]
target = sys.argv[2]
opts = {}
for a in sys.argv[3:]:
    k, _, v = a[2:].partition("=")
    opts[k] = v or True
cfg = tempfile.mkdtemp(prefix="argos-shot-")
os.environ["XDG_CONFIG_HOME"] = cfg
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(cfg, "cache"))
w, h = (opts.get("size") or "1100x760").split("x")
import json  # noqa: E402
os.makedirs(os.path.join(cfg, "argos"))
with open(os.path.join(cfg, "argos", "settings.json"), "w") as fh:
    json.dump({"window_width": int(w), "window_height": int(h), "theme": "dark" if opts.get("dark") else "light",
               "filmstrip": not opts.get("no-strip"), "strip_height": int(opts.get("strip", 110))}, fh)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from argos.app import ArgosApp  # noqa: E402

app = ArgosApp()


def snap(widget, path):
    p = Gtk.WidgetPaintable.new(widget)
    sn = Gtk.Snapshot()
    p.snapshot(sn, widget.get_width(), widget.get_height())
    node = sn.to_node()
    tex = widget.get_native().get_renderer().render_texture(node, None)
    tex.save_to_png(path)
    print("capture", path, widget.get_width(), widget.get_height())


def stage():
    win = app.get_active_window()
    if os.path.isfile(target) and (win.picture is None or win.picture.path != os.path.abspath(target)):
        GLib.timeout_add(100, stage)            # photo pas encore lue
        return False
    if opts.get("zoom"):
        win.view.set_zoom(float(opts["zoom"]))
    if opts.get("info"):
        win.info_button.set_active(True)
        win.browser_info_button.set_active(True)
        if win.images:
            win.grid.select_path(win.images[0].path)
    if opts.get("crop"):
        win.start_crop()
        fw, fh = win.view.full_size()
        win.view.sel = [fw * 0.2, fh * 0.15, fw * 0.55, fh * 0.6]
        win.view.queue_draw()
    if opts.get("rotate"):
        win.rotate(1)
    if opts.get("fullscreen"):
        win.fullscreen()
    if opts.get("prefs"):
        from argos.ui.preferences import PreferencesWindow
        pw = PreferencesWindow(win)
        pw.set_visible_page_name(opts["prefs"])
        pw.present()
        GLib.timeout_add(int(opts.get("delay", 1500)), lambda: (snap(pw, out), app.quit(), False)[2])
        return False
    GLib.timeout_add(int(opts.get("delay", 1500)), lambda: (snap(win, out), app.quit(), False)[2])
    return False


app.connect("open", lambda *_: GLib.timeout_add(1200, stage))
sys.exit(app.run([sys.argv[0], target]))
