"""Essai de l'interface dans la vraie boucle GLib (sous xvfb-run) : doit finir par « Aucune erreur. »

    xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tests/ui_smoke.py

Étapes écrites comme des générateurs : `yield n` attend n ms (la boucle tourne), `yield cond` attend que
cond() soit vraie (5 s au plus).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="argos-ui-")
for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
    os.environ[var] = os.path.join(TMP, var.lower())
os.environ["GTK_A11Y"] = "none"
os.environ["GSETTINGS_BACKEND"] = "memory"      # « Définir comme arrière-plan » : jamais le vrai bureau

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from argos.app import ArgosApp  # noqa: E402
from argos.i18n import tr  # noqa: E402
from argos.settings import settings  # noqa: E402

PHOTOS = os.path.join(TMP, "Photos")
errors: list[str] = []


def check(cond, what):
    if not cond:
        errors.append(what)
        print("ÉCHEC :", what)
    else:
        print("ok :", what)


def dims(path):
    out = subprocess.run(["identify", "-format", "%w %h", path], capture_output=True, text=True)
    return tuple(int(v) for v in out.stdout.split())


def setup_photos():
    os.makedirs(os.path.join(PHOTOS, "Sous-dossier"))
    for i in range(1, 7):
        subprocess.run(["convert", "-size", "300x200", f"xc:rgb({i * 40},80,120)",
                        os.path.join(PHOTOS, f"photo {i}.jpg")], check=True)
    subprocess.run(["convert", "-size", "8x8", "xc:red", "xc:blue", "-set", "delay", "20", "-loop", "0",
                    os.path.join(PHOTOS, "photo 7.gif")], check=True)
    with open(os.path.join(PHOTOS, "photo 8.png"), "w") as fh:
        fh.write("abîmée")


def on_log(domain, level, message, *_):
    if level & (GLib.LogLevelFlags.LEVEL_CRITICAL | GLib.LogLevelFlags.LEVEL_WARNING):
        if "accessibility bus" not in message:
            errors.append(f"{domain}: {message}")
            print(f"!! {domain}: {message}")


def dialogs(app):
    return [w for w in Gtk.Window.list_toplevels() if isinstance(w, Adw.MessageDialog) and w.get_visible()]


def steps(app):
    win = app.get_active_window()
    yield lambda: win.browser_stack.get_visible_child_name() == "grid"
    check(len(win.images) == 8 and len(win.dirs) == 1, "dossier lu : 8 photos, 1 sous-dossier")
    check([e.name for e in win.images][:2] == ["photo 1.jpg", "photo 2.jpg"], "ordre naturel")

    # arbre des dossiers : déplié jusqu'au dossier affiché, sélectionné
    def tree_selected():
        item = win.tree.selection.get_selected_item()
        return item is not None and item.get_item().path == PHOTOS
    yield tree_selected
    check(tree_selected(), "barre latérale : dossier affiché sélectionné")

    # miniatures : chaque case reçoit SA miniature (bug vu chez Tof : les miniatures se mélangeaient)
    from argos.ui.thumbs import shared
    got = {}
    for e in win.images[:6]:
        shared().get(e.path, e.mtime, lambda tex, p=e.path: got.__setitem__(p, tex))
    yield lambda: len(got) == 6
    sizes = {p: (t.get_width(), t.get_height()) if t else None for p, t in got.items()}
    check(len({id(t) for t in got.values()}) == 6 and all(sizes.values()), "une miniature distincte par photo")

    # propriétés dans la grille (bouton (i) de la barre d'état)
    win.browser_info_button.set_active(True)
    win.grid.select_path(win.images[0].path)
    yield lambda: win.browser_info.box.get_first_child() is not None
    check("photo 1.jpg" in win.browser_status.get_label(), "barre d'état de la grille : photo sélectionnée")
    texts = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            texts.append(w.get_label())
        c = w.get_first_child()
        while c is not None:
            walk(c)
            c = c.get_next_sibling()
    walk(win.browser_info.box)
    check("photo 1.jpg" in texts and len(texts) > 6, "propriétés de la photo sélectionnée sous l'arbre")
    yield lambda: not win._side_pending
    tree_h = win.side_paned.get_position()
    check(tree_h > win.side_paned.get_height() * 0.4, f"l'arbre garde sa place ({tree_h} px)")
    win.browser_info_button.set_active(False)

    # ouvrir une photo depuis la grille
    win.open_entry(win.images[1])
    yield lambda: win.picture is not None and win.picture.path.endswith("photo 2.jpg")
    check(win.stack.get_visible_child_name() == "viewer", "visionneuse affichée")
    yield lambda: win.strip_paned.get_height() > 0 and not win._strip_pending
    h = win.strip_paned.get_height() - win.strip_paned.get_position()
    check(abs(h - settings["strip_height"]) < 12, f"bande à la hauteur retenue ({h} px)")
    win.strip_paned.set_position(win.strip_paned.get_height() - 200)
    yield 50
    check(abs(settings["strip_height"] - 200) < 12, "hauteur de la bande retenue au glisser")
    check(win.viewer_title.get_title() == "photo 2.jpg", "titre = nom de la photo")
    check("2 / 8" in win.status.get_label() and "300 × 200" in win.status.get_label(), "barre d'état")

    # molette = suivante, Ctrl+molette = zoom (signal de la vue)
    win.view.emit("navigate", 1)
    yield lambda: win.picture.path.endswith("photo 3.jpg")
    check(win.index == 2, "molette : photo suivante")
    win.go(-1)
    yield lambda: win.picture.path.endswith("photo 2.jpg")
    check(win.loader.cached(win.images[2].path) is not None, "voisine préchargée")

    win.view.zoom_step(1)
    yield 50
    check(not win.view.fit and win.view.zoom > 1.0, "zoom avant")
    win.view.set_fit()
    check(win.view.fit, "ajusté à la fenêtre")
    win.view.set_zoom(4.0, (10, 10))
    yield 50
    check(abs(win.view.zoom - 4.0) < 1e-6, "zoom autour d'un point")
    win.zoom_toggle()
    check(win.view.fit, "zoom ↔ ajusté")

    # bouclage : de la dernière à la première
    win.show_index(7)
    yield lambda: win.picture.path.endswith("photo 8.png")
    check(win.error_page.get_visible(), "photo abîmée : message de lecture impossible")
    win.go(1)
    yield lambda: win.picture.path.endswith("photo 1.jpg")
    check(win.index == 0 and not win.error_page.get_visible(), "après la dernière : la première")

    # GIF animé : pas de retouche
    win.show_index(6)
    yield lambda: win.picture.path.endswith("photo 7.gif")
    check(win.picture.animation is not None and not win.editable(), "GIF animé : animé, pas de retouche")

    # pivoter puis enregistrer
    win.show_index(0)
    yield lambda: win.picture.path.endswith("photo 1.jpg")
    path = win.picture.path
    win.rotate(1)
    check(win.pending() and win.save_button.get_visible(), "pivotée : bouton Enregistrer")
    check("200 × 300" in win.status.get_label(), "dimensions après rotation")
    win.save()
    yield lambda: dims(path) == (200, 300) and not win.pending()
    check(dims(path) == (200, 300), "enregistrée pivotée")
    yield lambda: win.picture is not None and win.picture.width == 200
    check(win.view.rotation == 0 and win.picture.width == 200, "photo relue après l'enregistrement")

    # recadrer
    win.start_crop()
    check(win.view.cropping and win.crop_revealer.get_reveal_child(), "mode recadrage")
    win.ratio_drop.set_selected(2)                       # 1:1
    yield 30
    x, y, w, h = win.view.sel
    check(abs(w - h) < 1, "proportion carrée imposée")
    win.view.sel = [10, 20, 100, 100]
    win.end_crop(True)
    check(win.view.crop == (10, 20, 100, 100) and win.pending(), "recadrage en attente")
    check(win.view.image_size() == (100, 100), "vue = le cadre")

    # quitter la photo avec des modifications : question
    win.go(1)
    yield lambda: len(dialogs(app)) == 1
    d = dialogs(app)[0]
    check(d.get_heading() == tr("Enregistrer les modifications ?"), "question avant de quitter la photo")
    d.response("save")
    d.close()
    yield lambda: dims(path) == (100, 100)
    check(dims(path) == (100, 100), "recadrage enregistré depuis la question")
    yield lambda: win.index == 1
    check(win.index == 1, "puis photo suivante")

    # abandonner
    win.rotate(1)
    win.back()
    yield lambda: len(dialogs(app)) == 1
    d = dialogs(app)[0]
    d.response("discard")
    d.close()
    yield lambda: win.stack.get_visible_child_name() == "browser"
    check(dims(win.images[1].path) == (300, 200), "abandon : photo intacte, retour à la grille")

    # plein écran
    win.open_entry(win.images[2])
    yield lambda: win.stack.get_visible_child_name() == "viewer"
    win.toggle_fullscreen()
    yield lambda: win.is_fullscreen()
    yield 200
    check(not win.viewer.get_reveal_top_bars(), "plein écran : barres masquées")
    win.back()
    yield lambda: not win.is_fullscreen()
    check(win.stack.get_visible_child_name() == "viewer", "Échap : sort du plein écran, reste sur la photo")

    # propriétés
    win.info_button.set_active(True)
    yield 300
    check(win.split.get_show_sidebar(), "panneau des propriétés")

    # corbeille
    victim = win.images[win.index].path
    n = len(win.images)
    win.trash()
    yield 100
    check(not os.path.exists(victim) and len(win.images) == n - 1, "mise à la corbeille")

    # photo ajoutée de l'extérieur : suivie
    subprocess.run(["convert", "-size", "50x50", "xc:green", os.path.join(PHOTOS, "photo 9.jpg")], check=True)
    yield lambda: any(e.name == "photo 9.jpg" for e in win.images)
    check(any(e.name == "photo 9.jpg" for e in win.images), "nouvelle photo apparue (suivi du dossier)")

    # sous-dossier et parent
    win.show_browser()
    win.open_entry(win.dirs[0])
    yield lambda: win.folder.endswith("Sous-dossier") and win.browser_stack.get_visible_child_name() == "empty"
    check(True, "sous-dossier vide : page « Aucune photo »")
    win.go_up()
    yield lambda: win.folder == PHOTOS and win.browser_stack.get_visible_child_name() == "grid"
    check(settings["last_folder"] == PHOTOS, "dernier dossier retenu")

    # dossier déjà vu : affiché tout de suite, sans roue (cache), même modèle gardé si rien n'a changé
    win.open_folder(os.path.join(PHOTOS, "Sous-dossier"))
    yield lambda: win.browser_stack.get_visible_child_name() == "empty"
    win.open_folder(PHOTOS)
    check(win.browser_stack.get_visible_child_name() == "grid" and len(win.images) > 3,
          "dossier déjà vu : grille tout de suite")
    model = win.grid.selection.get_model()
    yield 600
    check(win.grid.selection.get_model() is model, "relecture identique : grille inchangée")

    # photo ouverte de l'extérieur : visionneuse immédiatement, jamais la grille avant
    win.show_browser()
    target = win.images[2].path
    win.open_path(target)
    check(win.stack.get_visible_child_name() == "viewer", "photo ouverte : visionneuse tout de suite")
    yield lambda: win.picture is not None and win.picture.path == target and len(win.images) > 3
    check(win.images[win.index].path == target, "puis le dossier autour de la photo")

    # menu du clic droit (photo) : contenu, renommer, copier vers (sans écrasement), supprimer définitivement
    win.show_index(0)
    yield lambda: win.picture is not None and win.picture.path == win.images[0].path
    path = win.images[0].path
    menu = win.file_menu(path)
    labels = []
    for i in range(menu.get_n_items()):
        sec = menu.get_item_link(i, "section")
        for j in range(sec.get_n_items()):
            labels.append(sec.get_item_attribute_value(j, "label").get_string())
    check(tr("Ouvrir avec") in labels and tr("Renommer…") in labels and tr("Imprimer…") in labels,
          "menu du clic droit")
    win._renamed(path, path)            # sans effet de bord
    new = os.path.join(PHOTOS, "renommée.jpg")
    os.rename(path, new)
    win._renamed(path, new)
    check(win.images[win.index].path == new, "renommer : la photo affichée suit")
    yield lambda: win.picture is not None and win.picture.path == new
    other = os.path.join(TMP, "ailleurs")
    os.makedirs(other)
    win._ctx = new
    from argos.backend import edit as _edit
    dest = _edit.free_name(os.path.join(other, "renommée.jpg"))
    shutil.copyfile(new, dest)
    check(_edit.free_name(os.path.join(other, "renommée.jpg")).endswith("renommée (2).jpg"), "copier vers : jamais d'écrasement")
    n = len(win.images)
    os.remove(new)
    win._removed(new)
    check(len(win.images) == n - 1 and win.images[win.index].path != new, "supprimée : la voisine s'affiche")
    # définir comme arrière-plan (bug vu chez Tof : gs.settings_schema n'existe pas en Python)
    from argos.ui.filemenu import background_settings
    gs = background_settings()
    if gs is not None:
        win._ctx = win.images[0].path
        win.set_wallpaper()
        uri = gs.get_string("picture-uri")
        check("/backgrounds/argos-" in uri and gs.get_string("picture-uri-dark") == uri and
              gs.get_string("picture-options") == "zoom", "arrière-plan : copie posée, clair et sombre")
        yield 20
        win._ctx = win.images[1].path
        win.set_wallpaper()
        uri2 = gs.get_string("picture-uri")
        bg = os.path.dirname(GLib.filename_from_uri(uri2)[0])
        check(uri2 != uri and len([n for n in os.listdir(bg) if n.startswith("argos-")]) == 1,
              "arrière-plan suivant : nouvelle adresse, ancienne copie retirée")
    else:
        print("(schéma org.gnome.desktop.background absent : arrière-plan non testé)")
    win._ctx = None
    win.copy_image()
    yield 100

    # préférences
    from argos.ui.preferences import PreferencesWindow
    pw = PreferencesWindow(win)
    pw.present()
    yield 200
    pw.close()


def run_steps(app):
    gen = steps(app)

    def advance(value=None):
        try:
            item = next(gen) if value is None else gen.send(value)
        except StopIteration:
            finish(app)
            return False
        except Exception:      # noqa: BLE001
            errors.append(traceback.format_exc())
            print(traceback.format_exc())
            finish(app)
            return False
        if callable(item):
            waited = [0]

            def poll():
                ok = False
                try:
                    ok = item()
                except Exception:  # noqa: BLE001
                    ok = False
                if ok or waited[0] > 5000:
                    if not ok:
                        errors.append("délai dépassé : " + getattr(item, "__name__", "condition") +
                                      " " + str(item.__code__.co_firstlineno))
                        print("ÉCHEC : délai dépassé, ligne", item.__code__.co_firstlineno)
                    GLib.idle_add(lambda: advance(True) and False)
                    return False
                waited[0] += 30
                return True
            GLib.timeout_add(30, poll)
        else:
            GLib.timeout_add(int(item or 0), lambda: advance(True) and False)
        return False
    advance()


def finish(app):
    for w in list(Gtk.Window.list_toplevels()):
        if isinstance(w, Adw.MessageDialog):
            w.close()
    win = app.get_active_window()
    if win is not None:
        win._closing = True
    app.quit()


def main():
    setup_photos()
    wm = None
    if shutil.which("openbox"):          # plein écran : il faut un gestionnaire de fenêtres
        wm = subprocess.Popen(["openbox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for dom in ("Gtk", "Gdk", "GLib", "GLib-GObject", "Adwaita", "GdkPixbuf", None):
        GLib.log_set_handler(dom, GLib.LogLevelFlags.LEVEL_CRITICAL | GLib.LogLevelFlags.LEVEL_WARNING, on_log, None)
    app = ArgosApp()
    app.connect("open", lambda *_: GLib.timeout_add(300, lambda: run_steps(app) or False))
    app.run([sys.argv[0], PHOTOS])
    if wm is not None:
        wm.terminate()
    shutil.rmtree(TMP, ignore_errors=True)
    if errors:
        print(f"\n{len(errors)} erreur(s).")
        sys.exit(1)
    print("\nAucune erreur.")


if __name__ == "__main__":
    main()
