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
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

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


def videos_step(win):
    from argos import features as feats
    folder = os.path.join(TMP, "Avec vidéo")
    os.makedirs(folder)
    for i in (1, 3):
        subprocess.run(["convert", "-size", "120x80", f"xc:rgb({i * 60},90,40)", os.path.join(folder, f"{i} photo.jpg")],
                       check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=15",
                    "-t", "4", "-c:v", "libvpx-vp9", "-b:v", "100k", "-an", os.path.join(folder, "2 clip.webm")],
                   check=True)
    back = win.folder
    win.open_folder(folder)
    yield lambda: win.browser_stack.get_visible_child_name() == "grid" and len(win.images) == 3
    check([e.name for e in win.images] == ["1 photo.jpg", "2 clip.webm", "3 photo.jpg"], "vidéo listée avec les photos")
    from argos.i18n import ntr
    check(ntr("{0} vidéo", "{0} vidéos", 1).format(1) in win.browser_status.get_label(),
          "barre d'état : « 2 photos, 1 vidéo »")
    win.show_index(1)
    win.show_viewer()
    yield lambda: win.showing_video() and win.video.get_media_stream() is not None \
        and win.video.get_media_stream().get_playing()
    check(win.showing_video() and win.view.texture is None, "vidéo : lue dans la visionneuse, tout de suite")
    check(not win.editable() and not win.lookup_action("rotate-right").get_enabled(),
          "vidéo : pas de retouche (pivoter grisé)")
    labels = []
    menu = win.file_menu(win.images[1].path)
    for i in range(menu.get_n_items()):
        sec = menu.get_item_link(i, "section")
        labels += [sec.get_item_attribute_value(j, "label").get_string() for j in range(sec.get_n_items())]
    check(tr("Imprimer…") not in labels and tr("Annoter l'image…") not in labels and tr("Renommer…") in labels,
          "vidéo : clic droit sans ce qui touche à l'image")
    win._on_key(None, Gdk.KEY_space, 0, 0)
    yield 200
    check(not win.video.get_media_stream().get_playing() and win.index == 1, "vidéo : Espace = pause")
    yield lambda: win.picture is not None and win.picture.video and win.picture.duration > 3
    check("0:04" in win.status.get_label(), "barre d'état : durée de la vidéo")
    win.go(1)
    yield lambda: win.picture is not None and win.picture.path.endswith("3 photo.jpg")
    check(not win.showing_video() and win.view.texture is not None, "photo suivante : la vidéo s'arrête")
    # diaporama : la vidéo est sautée
    win.show_index(0)
    yield lambda: win.picture is not None and win.picture.path.endswith("1 photo.jpg")
    settings["slideshow_delay"] = 1
    win.activate_action("win.slideshow", None)
    yield lambda: win.index != 0
    check(win.index == 2 and not win.showing_video(), "diaporama : vidéo sautée")
    win._on_key(None, Gdk.KEY_Escape, 0, 0)
    yield lambda: not win.is_fullscreen()
    settings["slideshow_delay"] = 4
    # interrupteur : coupé, la vidéo disparaît du dossier
    win.show_browser()
    feats.set_enabled("videos", False)
    yield lambda: len(win.images) == 2
    check([e.name for e in win.images] == ["1 photo.jpg", "3 photo.jpg"], "« Vidéos » coupée : photos seulement")
    feats.set_enabled("videos", True)
    yield lambda: len(win.images) == 3
    win.open_folder(back)
    yield lambda: win.browser_stack.get_visible_child_name() == "grid" and win.folder == back
    win.show_index(0)
    win.show_viewer()                       # rendu comme trouvé : la visionneuse
    yield lambda: win.picture is not None and win.picture.path == win.images[0].path


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
    # choisir un autre dossier dans l'arbre l'ouvre (simple clic = sélection), et il reste sélectionné
    sub = os.path.join(PHOTOS, "Sous-dossier")
    pos = next(i for i in range(win.tree.tree.get_n_items()) if win.tree.tree.get_item(i).get_item().path == sub) \
        if any(win.tree.tree.get_item(i).get_item().path == sub for i in range(win.tree.tree.get_n_items())) else None
    if pos is None:
        win.tree.tree.get_item(next(i for i in range(win.tree.tree.get_n_items())
                                    if win.tree.tree.get_item(i).get_item().path == PHOTOS)).set_expanded(True)
        yield lambda: any(win.tree.tree.get_item(i).get_item().path == sub for i in range(win.tree.tree.get_n_items()))
        pos = next(i for i in range(win.tree.tree.get_n_items()) if win.tree.tree.get_item(i).get_item().path == sub)
    win.tree.selection.set_selected(pos)
    yield lambda: win.folder == sub
    check(win.tree.selection.get_selected_item().get_item().path == sub, "arbre : dossier choisi ouvert et sélectionné")
    check(win.tree.selection.get_selected_item().get_expanded(), "arbre : dossier choisi déplié")
    win.open_folder(PHOTOS)
    yield lambda: win.browser_stack.get_visible_child_name() == "grid"
    yield tree_selected

    # miniatures : chaque case reçoit SA miniature (bug vu chez Tof : les miniatures se mélangeaient)
    from argos.ui.thumbs import shared
    got = {}
    for e in win.images[:6]:
        shared(128).get(e.path, e.mtime, lambda res, p=e.path: got.__setitem__(p, res))
    yield lambda: len(got) == 6
    texs = [r[0] for r in got.values() if r]
    check(len({id(t) for t in texs}) == 6, "une miniature distincte par photo")
    w, h = texs[0].get_width(), texs[0].get_height()
    check(max(w, h) <= 128 - 6 and got[win.images[0].path][1], f"miniature à la taille de la case, photo encadrée ({w} × {h})")

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
    from argos.backend import edit as _edit
    before = set(os.listdir(_edit.UNDO_DIR)) if os.path.isdir(_edit.UNDO_DIR) else set()
    win.save()
    win.save()                 # Ctrl+S deux fois (ou « Enregistrer ? » en changeant de photo) pendant l'écriture
    yield lambda: dims(path) == (200, 300) and not win.pending()
    yield 600
    check(dims(path) == (200, 300), "enregistrée pivotée, une seule fois (pas de seconde rotation)")
    check(len(set(os.listdir(_edit.UNDO_DIR)) - before) == 1, "un seul enregistrement lancé")
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
    # flèches et pointeur à minuterie (demande de Tof)
    arrows = lambda: all(b.has_css_class("shown") for b in win._arrows)  # noqa: E731
    check(arrows(), "plein écran : flèches montrées un instant")
    win.CHROME_MS = 300
    win._show_chrome(arrows=True)
    yield lambda: not any(b.has_css_class("shown") for b in win._arrows)
    check(not any(b.has_css_class("shown") for b in win._arrows) and win.view.pointer_hidden,
          "au repos : flèches et pointeur cachés")
    w = win.view_overlay.get_width()
    win._on_view_motion(None, w / 2, 200)
    check(not arrows() and not win.view.pointer_hidden, "souris au milieu : pointeur, pas de flèches")
    win._on_view_motion(None, w - 20, 200)
    check(arrows(), "souris au bord : flèches")
    yield lambda: not arrows()
    win.go(1)
    yield 50
    check(arrows(), "photo suivante (clavier, molette) : flèches montrées")
    check(win.view.in_transition() and win.view._trans[1] == 1, "photo suivante au clavier : transition aussi")
    win.go(-1)
    yield lambda: not arrows()
    win.back()
    yield lambda: not win.is_fullscreen()
    check(win.stack.get_visible_child_name() == "viewer", "Échap : sort du plein écran, reste sur la photo")
    check(not win.view.pointer_hidden and not any(b.has_css_class("shown") for b in win._arrows),
          "hors du plein écran : pointeur normal")
    win.CHROME_MS = type(win).CHROME_MS

    # diaporama (F5)
    settings["slideshow_delay"] = 1
    start = win.index
    win.activate_action("win.slideshow", None)
    yield lambda: win.is_fullscreen()
    check(win.slideshow_running(), "F5 : diaporama en plein écran")
    yield lambda: win.index != start
    check(win.index == (start + 1) % len(win.images), "diaporama : photo suivante toute seule")
    yield lambda: win.view.in_transition()
    check(win.view._trans[0] == "slide" and win.view._trans[1] == 1, "diaporama : la photo arrive en glissant")
    yield lambda: not win.view.in_transition()
    check(not win.view.in_transition(), "glissement terminé")
    settings["slideshow_transition"] = "fade"
    was = win.index
    yield lambda: win.index != was and win.view.in_transition()
    check(win.view._trans[0] == "fade", "diaporama : fondu enchaîné (Préférences)")
    settings["slideshow_transition"] = "slide"
    win._on_key(None, Gdk.KEY_space, 0, 0)
    check(win._slideshow_paused, "Espace : pause")
    paused_at = win.index
    yield 1500
    check(win.index == paused_at, "en pause : la photo reste")
    win._on_key(None, Gdk.KEY_space, 0, 0)
    yield lambda: win.index != paused_at
    check(win.index != paused_at, "Espace : reprise")
    win._on_key(None, Gdk.KEY_Escape, 0, 0)
    yield lambda: not win.is_fullscreen()
    check(not win.slideshow_running() and win.stack.get_visible_child_name() == "viewer",
          "Échap : diaporama arrêté, plein écran quitté")
    settings["slideshow_delay"] = 4

    # vidéos (demande de Tof, comme gThumb) : dans la grille, lues dans la visionneuse, sautées par le diaporama
    yield from videos_step(win)

    # annoter (repris d'Odysseus) : fenêtre, un rectangle, Ctrl+S remplace la photo, « Annuler » la remet
    from argos import features as feats
    from argos.ui.annotate import AnnotateWindow, Shape
    win.show_index(0)
    yield lambda: win.picture is not None and win.picture.path == win.images[0].path
    target = win.images[0].path
    before = open(target, "rb").read()
    check(win.annotate_button.get_visible(), "Annoter : bouton dans l'en-tête")
    win.activate_action("win.annotate", None)
    yield lambda: any(isinstance(w, AnnotateWindow) and w.get_visible() for w in Gtk.Window.list_toplevels())
    aw = next(w for w in Gtk.Window.list_toplevels() if isinstance(w, AnnotateWindow))
    check((aw.img_w, aw.img_h) == (win.picture.width, win.picture.height), "Annoter : fenêtre ouverte sur la photo")
    shp = Shape("rect", (1.0, 1.0, 0.0), 1.0, 20, fill=True)
    shp.points = [(1, 1), (aw.img_w - 1, aw.img_h - 1)]
    aw.shapes.append(shp)
    aw.dirty = True
    aw.save()
    yield lambda: not aw.dirty and open(target, "rb").read() != before
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(target)
    px = pb.get_pixels()[pb.get_height() // 2 * pb.get_rowstride() + pb.get_width() // 2 * pb.get_n_channels():][:3]
    check(px[0] > 220 and px[1] > 220 and px[2] < 60, "Annoter : photo enregistrée avec le rectangle jaune")
    check(not [f for f in os.listdir(PHOTOS) if f.startswith(".argos-")], "Annoter : aucun fichier caché laissé")
    aw.close()
    yield lambda: aw.get_root() is None or not aw.get_visible()
    from argos.backend import edit as _edit
    copies = sorted(os.listdir(_edit.UNDO_DIR), key=lambda f: os.path.getmtime(os.path.join(_edit.UNDO_DIR, f)))
    _edit.restore(os.path.join(_edit.UNDO_DIR, copies[-1]), target)
    check(open(target, "rb").read() == before, "Annoter : l'original gardé est remis tel quel")
    feats.set_enabled("annotate", False)
    yield 50
    check(not win.annotate_button.get_visible() and not win.lookup_action("annotate").get_enabled(),
          "Annoter coupée : bouton et action retirés")
    feats.set_enabled("annotate", True)

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
    yield lambda: win.grid.selection.get_model() is not None
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
    # le dossier arrive APRÈS un geste sur la photo déjà affichée (gros dossier) : zoom gardé, photo pas relue
    win.view.set_zoom(2.0)
    cached = win._folder_cache.get(win.folder)
    win._want_path = target
    win._scan_seq += 1
    win._scanned(win._scan_seq, cached[1], "")
    yield 400
    check(not win.view.fit and abs(win.view.zoom - 2.0) < 1e-6 and win.images[win.index].path == target,
          "dossier lu après un zoom : la photo ouverte garde son zoom")

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

    # grille : vrai nombre de colonnes (leçon d'Odysseus : sinon GTK crée des milliers de cases d'un coup)
    from argos.ui.gallery import columns_for
    win.show_browser()
    yield lambda: win.grid.get_width() > 0 and win.grid.selection.get_model() is not None
    yield 100
    check(win.grid.view.get_max_columns() == columns_for(win.grid.get_width(), win.grid._cell),
          f"grille : {win.grid.view.get_max_columns()} colonnes d'après la largeur")

    # sélection multiple dans la grille (demande de Tof) : barre d'état, menu réduit, corbeille pour toutes
    win.show_browser()
    yield lambda: win.grid.selection.get_model() is not None
    two = [win.images[0].path, win.images[1].path]
    win.grid.selection.select_range(0, 2, True)
    yield 50
    check(len(win.grid.selected_entries()) == 2 and "2 " in win.browser_status.get_label(),
          "sélection multiple : 2 photos, barre d'état")
    labels = []
    menu = win.file_menu(two[0])
    for i in range(menu.get_n_items()):
        sec = menu.get_item_link(i, "section")
        labels += [sec.get_item_attribute_value(j, "label").get_string() for j in range(sec.get_n_items())]
    check(tr("Renommer…") not in labels and tr("Mettre à la corbeille") in labels, "menu de plusieurs photos")
    # convertir les photos sélectionnées (outils image d'Odysseus)
    from argos.ui import image_dialog
    win.image_tools()
    yield lambda: image_dialog._last is not None and image_dialog._last.dialog.get_visible()
    dlg = image_dialog._last
    dlg.convert.set_enable_expansion(True)
    dlg.fmt.set_selected(1)                                  # PNG
    dlg.replace.set_active(False)
    dlg.suffix.set_text("")
    dlg._go()
    pngs = [os.path.splitext(p)[0] + ".png" for p in two]
    yield lambda: all(os.path.exists(p) for p in pngs) and not dlg.dialog.get_visible()
    check(all(os.path.exists(p) for p in pngs) and all(os.path.exists(p) for p in two),
          "convertir : PNG posés à côté, originaux gardés")
    yield lambda: all(win._index_of(p) >= 0 for p in pngs)
    win.grid.selection.unselect_all()
    for i, e in enumerate(win.images):
        if e.path in two:
            win.grid.selection.select_item(i, False)
    yield 50
    n = len(win.images)
    win.trash()
    yield 100
    check(len(win.images) == n - 2 and not any(os.path.exists(p) for p in two), "Suppr : les deux à la corbeille")

    # SVG : agrandi à la fenêtre et redessiné net au zoom (vu chez Tof : icône minuscule, puis pixelisée)
    icons = os.path.join(TMP, "Icônes")
    os.makedirs(icons)
    with open(os.path.join(icons, "icone.svg"), "w") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><circle cx="24" cy="24" r="20" '
                 'fill="#e01b24"/></svg>')
    win.open_path(os.path.join(icons, "icone.svg"))
    yield lambda: win.picture is not None and win.picture.path.endswith("icone.svg") and win.view._hires is not None
    check(win.view.zoom > 3 and win.view.fit, "SVG : agrandi à la fenêtre")
    res = []
    from argos.ui.thumbs import shared as _sh
    _sh(128).get(os.path.join(icons, "icone.svg"), os.path.getmtime(os.path.join(icons, "icone.svg")), res.append)
    yield lambda: res
    check(res[0] and res[0][0].get_width() == 48 and not res[0][1], "icône : vignette à sa vraie taille, sans cadre")
    check(abs(win.view._hires.get_width() - 48 * win.view.zoom) < 4, "SVG : rendu net à la taille affichée")
    win.view.set_zoom(win.view.zoom * 2)
    yield lambda: abs(win.view._hires.get_width() - 48 * win.view.zoom) < 4
    check(abs(win.view._hires.get_width() - 48 * win.view.zoom) < 4, "SVG : redessiné après un zoom")

    # taille des vignettes : choix simple, en direct (demande d'un utilisateur)
    win.show_browser()
    yield lambda: win.stack.get_visible_child_name() == "browser"
    settings["grid_size"] = 128
    yield 100
    win.activate_action("win.zoom-in", None)                 # Ctrl + + dans la grille
    yield 200
    check(settings["grid_size"] == 192 and win.grid.size == 192, "Ctrl + + dans la grille : vignettes plus grandes, tout de suite")
    win.activate_action("win.zoom-out", None)
    win.activate_action("win.zoom-out", None)
    yield 200
    check(win.grid.size == 96, "Ctrl + − : plus petites")
    win.activate_action("win.zoom-out", None)
    yield 100
    check(win.grid.size == 96, "bornée à « Petites »")
    yield lambda: win.grid.view.get_max_columns() > 1
    cols_small = win.grid.view.get_max_columns()
    settings["grid_size"] = 256
    yield lambda: win.grid.view.get_max_columns() < cols_small
    check(win.grid.view.get_max_columns() < cols_small, "« Très grandes » : moins de colonnes")
    yield lambda: any(c.picture.get_paintable() is not None for c in _cells(win.grid.view))
    check(any(c.size == 256 and c.picture.get_paintable() is not None for c in _cells(win.grid.view)),
          "nouvelles cases avec leurs miniatures")
    settings["grid_size"] = 128

    # préférences
    from argos.ui.preferences import PreferencesWindow
    pw = PreferencesWindow(win)
    pw.present()
    yield 200
    rows = _rows(pw)
    size_row = next(r for r in rows if isinstance(r, Adw.ComboRow) and r.get_title() == tr("Taille des vignettes"))
    size_row.set_selected(2)
    yield 200
    check(win.grid.size == 192, "Préférences → Taille des vignettes « Grandes » : grille changée tout de suite")
    win.activate_action("win.zoom-out", None)
    yield 200
    check(size_row.get_selected() == 1, "Ctrl + − : le choix des Préférences suit")
    start_row = next(r for r in rows if isinstance(r, Adw.ComboRow) and r.get_title() == tr("Ouvrir Argos sur"))
    start_row.set_selected(1)
    yield 100
    from argos.app import start_folder
    from argos.backend import folder as fold
    check(settings["startup"] == "pictures" and start_folder() == fold.pictures_dir(), "« Ouvrir Argos sur » : Images")
    settings["startup_folder"] = os.path.join(PHOTOS, "Sous-dossier")
    start_row.set_selected(2)
    yield 100
    check(start_folder() == os.path.join(PHOTOS, "Sous-dossier"), "« Ouvrir Argos sur » : dossier choisi")
    settings["startup_folder"] = os.path.join(PHOTOS, "disparu")
    check(start_folder() == fold.pictures_dir(), "dossier choisi disparu : Images")
    start_row.set_selected(0)
    yield 100
    check(start_folder() == PHOTOS or settings["last_folder"] == start_folder(), "« Ouvrir Argos sur » : dernier dossier")
    for d in dialogs(app):
        d.close()
    pw.close()


def _cells(view):
    from argos.ui.gallery import _Cell
    out, todo = [], [view]
    while todo:
        w = todo.pop()
        if isinstance(w, _Cell):
            out.append(w)
        c = w.get_first_child()
        while c is not None:
            todo.append(c)
            c = c.get_next_sibling()
    return out


def _rows(window):
    out, todo = [], [window]
    while todo:
        w = todo.pop()
        if isinstance(w, Adw.PreferencesRow):
            out.append(w)
        c = w.get_first_child()
        while c is not None:
            todo.append(c)
            c = c.get_next_sibling()
    return out


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
