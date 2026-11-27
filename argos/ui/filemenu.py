"""Clic droit sur une photo (visionneuse et grille), comme gThumb (demande de Tof) : plein écran, définir comme
arrière-plan, imprimer, ouvrir avec, copier l'image, copier vers, déplacer vers, renommer, mettre à la corbeille,
supprimer. Mixin de `MainWindow` : la photo visée est `self._ctx` (chemin)."""

from __future__ import annotations

import os
import shutil
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .. import features  # noqa: E402
from ..backend import edit  # noqa: E402
from ..backend import folder as fold  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from .common import AlertDialog, alert, popup_menu, shown, toast  # noqa: E402
from .thumbs import forget_everywhere  # noqa: E402

BACKGROUND_SCHEMA = "org.gnome.desktop.background"


def background_settings():
    src = Gio.SettingsSchemaSource.get_default()
    if src is None or src.lookup(BACKGROUND_SCHEMA, True) is None:
        return None
    return Gio.Settings.new(BACKGROUND_SCHEMA)


def content_type(path: str) -> str:
    ctype, _uncertain = Gio.content_type_guess(path, None)
    return ctype or "image/*"


class FileMenu:
    def _setup_file_menu(self):
        def add(name, cb, param=None, accels=()):
            a = Gio.SimpleAction.new(name, param)
            a.connect("activate", lambda _a, p: cb(p) if param is not None else cb())
            self.add_action(a)
            if accels:
                self.get_application().set_accels_for_action(f"win.{name}", list(accels))
        self._ctx = None
        add("ctx-fullscreen", self._ctx_fullscreen)
        add("ctx-slideshow", self._ctx_slideshow)
        add("wallpaper", self.set_wallpaper)
        add("print", self.print_photo, accels=["<Control>p"])
        add("open-with", self.open_with, GLib.VariantType.new("s"))
        add("copy-image", self.copy_image, accels=["<Control>c"])
        add("copy-to", lambda: self.transfer(False))
        add("move-to", lambda: self.transfer(True))
        add("rename", self.rename, accels=["F2"])
        add("delete", self.delete_for_good, accels=["<Shift>Delete"])
        add("ctx-trash", lambda: self.trash_paths(self._targets()))
        add("image-tools", self.image_tools, accels=["<Control>e"])
        add("annotate", self.annotate, accels=["a"])
        self.lookup_action("annotate").set_enabled(features.enabled("annotate"))
        self._annotating = {}

    # --- cible ---

    def _target(self):
        """Photo visée : celle du clic droit, sinon la photo affichée, sinon celle sélectionnée dans la grille."""
        if self._ctx:
            return self._ctx
        if self.stack.get_visible_child_name() == "viewer" and 0 <= self.index < len(self.images):
            return self.images[self.index].path
        chosen = [e for e in self.grid.selected_entries() if not e.is_dir]
        return chosen[-1].path if chosen else None

    def _targets(self) -> list:
        """Photos visées par une action qui en accepte plusieurs : dans la grille, la sélection (le clic droit
        sélectionne la vignette s'il tombe hors de la sélection) ; dans la visionneuse, la photo affichée."""
        if self.stack.get_visible_child_name() == "viewer":
            t = self._target()
            return [t] if t else []
        chosen = [e.path for e in self.grid.selected_entries() if not e.is_dir]
        if self._ctx and self._ctx not in chosen:
            return [self._ctx]
        return chosen

    def file_menu(self, path: str) -> Gio.Menu:
        if len(self._targets()) > 1:
            return self._multi_menu()
        video = fold.is_video(os.path.basename(path))       # vidéo : rien de ce qui touche à l'image elle-même
        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append(tr("Plein écran"), "win.ctx-fullscreen")
        if not video:
            s1.append(tr("Diaporama"), "win.ctx-slideshow")
        if background_settings() is not None and not video:
            s1.append(tr("Définir comme arrière-plan"), "win.wallpaper")
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        if not video:
            s2.append(tr("Imprimer…"), "win.print")
        apps = Gio.Menu()
        own = self.get_application().get_application_id() + ".desktop"
        for app in Gio.AppInfo.get_all_for_type(content_type(path)):
            if app.get_id() == own or not app.should_show():
                continue
            item = Gio.MenuItem.new(app.get_display_name().replace("_", "__"), None)
            item.set_action_and_target_value("win.open-with", GLib.Variant("s", app.get_id()))
            apps.append_item(item)
        other = Gio.Menu()
        other.append(tr("Autre application…"), "win.open-with::")
        apps.append_section(None, other)
        s2.append_submenu(tr("Ouvrir avec"), apps)
        menu.append_section(None, s2)
        s3 = Gio.Menu()
        if not video:
            s3.append(tr("Copier l'image"), "win.copy-image")
            if not features.blocked("edit"):
                s3.append(tr("Convertir / redimensionner…"), "win.image-tools")
            if features.enabled("annotate") and not features.blocked("annotate"):
                s3.append(tr("Annoter l'image…"), "win.annotate")
            menu.append_section(None, s3)
        s4 = Gio.Menu()
        s4.append(tr("Copier vers…"), "win.copy-to")
        s4.append(tr("Déplacer vers…"), "win.move-to")
        s4.append(tr("Renommer…"), "win.rename")
        menu.append_section(None, s4)
        s5 = Gio.Menu()
        s5.append(tr("Mettre à la corbeille"), "win.ctx-trash")
        s5.append(tr("Supprimer…"), "win.delete")
        menu.append_section(None, s5)
        return menu

    def _multi_menu(self) -> Gio.Menu:
        """Plusieurs photos : seulement ce qui a un sens pour toutes."""
        menu = Gio.Menu()
        s0 = Gio.Menu()
        if not features.blocked("edit") and any(not fold.is_video(os.path.basename(p)) for p in self._targets()):
            s0.append(tr("Convertir / redimensionner…"), "win.image-tools")
            menu.append_section(None, s0)
        s1 = Gio.Menu()
        s1.append(tr("Copier vers…"), "win.copy-to")
        s1.append(tr("Déplacer vers…"), "win.move-to")
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append(tr("Mettre à la corbeille"), "win.ctx-trash")
        s2.append(tr("Supprimer…"), "win.delete")
        menu.append_section(None, s2)
        return menu

    def image_tools(self):
        """Convertir / redimensionner / pivoter (outils image d'Odysseus) : photo affichée ou sélection."""
        paths = [p for p in self._targets() if not fold.is_video(os.path.basename(p))]     # photos seulement
        if not paths:
            return
        if features.blocked("edit"):
            alert(self, tr("ImageMagick n'est pas installé"), "sudo apt install imagemagick")
            return
        from .image_dialog import ImageToolsDialog
        ImageToolsDialog(self, paths, "convert").present(self)

    def annotate(self):
        """Annoter la photo visée (repris d'Odysseus) : une fenêtre par photo ; retouches en attente (rotation,
        recadrage) d'abord enregistrées ou abandonnées, l'annotation part du fichier."""
        if not features.enabled("annotate") or features.blocked("annotate"):
            return
        path = self._target()
        if not path or fold.is_video(os.path.basename(path)):
            return
        win = self._annotating.get(path)
        if win is not None:
            win.present()
            return

        def open_it():
            from .annotate import AnnotateWindow
            w = AnnotateWindow(self, path)
            if getattr(w, "base", None) is None:          # image illisible : déjà signalé
                return
            self._annotating[path] = w
            w.connect("destroy", lambda *_: self._annotating.pop(path, None))
            w.present()
        if self.stack.get_visible_child_name() == "viewer" and (self.pending() or self.view.cropping):
            self.guard(open_it)
        else:
            open_it()

    def show_file_menu(self, widget: Gtk.Widget, path: str, x: float, y: float):
        self._ctx = path
        pop = popup_menu(widget, self.file_menu(path), x, y)
        if not getattr(pop, "_argos_ctx", False):
            pop._argos_ctx = True
            # l'action part APRÈS la fermeture du menu : la cible est oubliée un peu plus tard
            pop.connect("closed", lambda *_: GLib.timeout_add(300, lambda: (setattr(self, "_ctx", None), False)[1]))

    # --- actions ---

    def _ctx_fullscreen(self):
        path = self._target()
        if path is None:
            return
        if self.stack.get_visible_child_name() != "viewer":
            i = self._index_of(path)
            if i < 0:
                return
            self.show_index(i)
            self.show_viewer()
        if not self.is_fullscreen():
            self.fullscreen()

    def _ctx_slideshow(self):
        """Clic droit → Diaporama : à partir de CETTE photo."""
        path = self._target()
        i = self._index_of(path) if path else -1
        if i < 0:
            return
        if self.stack.get_visible_child_name() != "viewer" or i != self.index:
            self.guard(lambda: (self.show_index(i), self.show_viewer(), self._start_slideshow()))
        else:
            self.slideshow()

    def set_wallpaper(self):
        """Copie dans ~/.local/share/backgrounds (la photo peut être déplacée ou supprimée ensuite) sous un nom UNIQUE,
        puis les réglages de GNOME dans l'ordre du script de Tof : options, clair, sombre. Vu chez Tof : image
        visible dans les Paramètres mais pas appliquée — même nom de copie = même adresse, GNOME ne voit aucun
        changement et ne recharge rien (et le thème sombre lit picture-uri-dark). Ancienne copie d'Argos retirée."""
        path, gs = self._target(), background_settings()
        if path is None or gs is None or fold.is_video(os.path.basename(path)):
            return
        folder = os.path.join(GLib.get_user_data_dir(), "backgrounds")
        stem, ext = os.path.splitext(os.path.basename(path))
        try:
            os.makedirs(folder, exist_ok=True)
            dest = os.path.join(folder, f"argos-{stem}-{int(time.time() * 1000)}{ext.lower()}")
            shutil.copyfile(path, dest + ".part")
            os.replace(dest + ".part", dest)
        except OSError as e:
            alert(self, tr("Impossible de définir l'arrière-plan"), shown(e.strerror or str(e)))
            return
        uri = GLib.filename_to_uri(dest, None)
        old = [gs.get_string("picture-uri")]
        gs.set_string("picture-options", "zoom")
        gs.set_string("picture-uri", uri)
        if gs.props.settings_schema.has_key("picture-uri-dark"):
            old.append(gs.get_string("picture-uri-dark"))
            gs.set_string("picture-uri-dark", uri)
        Gio.Settings.sync()                       # écrit tout de suite (dconf), comme gsettings set
        for u in old:                             # nos anciennes copies, plus utilisées
            try:
                p = GLib.filename_from_uri(u)[0] if u.startswith("file://") else ""
            except GLib.Error:
                p = ""
            if p and p != dest and os.path.dirname(p) == folder and os.path.basename(p).startswith("argos-"):
                try:
                    os.remove(p)
                except OSError:
                    pass
        toast(self, tr("« {0} » est maintenant l'arrière-plan").format(shown(os.path.basename(path))))

    def print_photo(self):
        path = self._target()
        if path is None or fold.is_video(os.path.basename(path)):
            return
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
            pb = pb.apply_embedded_orientation() or pb
        except GLib.Error as e:
            alert(self, tr("Impression impossible"), shown(e.message))
            return
        op = Gtk.PrintOperation(n_pages=1, job_name=os.path.basename(path), use_full_page=False)
        setup = Gtk.PageSetup()
        setup.set_orientation(Gtk.PageOrientation.LANDSCAPE if pb.get_width() > pb.get_height()
                              else Gtk.PageOrientation.PORTRAIT)
        op.set_default_page_setup(setup)

        def draw(_op, context, _page):
            cr = context.get_cairo_context()
            w, h = context.get_width(), context.get_height()
            s = min(w / pb.get_width(), h / pb.get_height())
            cr.translate((w - pb.get_width() * s) / 2, (h - pb.get_height() * s) / 2)
            cr.scale(s, s)
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
        op.connect("draw-page", draw)
        try:
            op.run(Gtk.PrintOperationAction.PRINT_DIALOG, self)
        except GLib.Error as e:
            alert(self, tr("Impression impossible"), shown(e.message))

    def open_with(self, param):
        path = self._target()
        if path is None:
            return
        app_id = param.get_string()
        gfile = Gio.File.new_for_path(path)
        if not app_id:
            dlg = Gtk.AppChooserDialog.new(self, Gtk.DialogFlags.MODAL, gfile)

            def resp(d, r):
                info = d.get_app_info() if r == Gtk.ResponseType.OK else None
                d.destroy()
                if info is not None:
                    self._launch(info, gfile)
            dlg.connect("response", resp)
            dlg.present()
            return
        try:
            info = Gio.DesktopAppInfo.new(app_id)
        except TypeError:
            info = None
        if info is not None:
            self._launch(info, gfile)

    def _launch(self, info, gfile):
        try:
            ctx = self.get_display().get_app_launch_context()
            info.launch([gfile], ctx)
        except GLib.Error as e:
            alert(self, tr("Ouverture impossible"), shown(e.message))

    def copy_image(self):
        """L'image (pour la coller dans un document, un courriel…) et le fichier (pour le coller dans Nautilus)."""
        path = self._target()
        if path is None or fold.is_video(os.path.basename(path)):
            return
        try:
            tex = Gdk.Texture.new_from_filename(path)
        except GLib.Error:
            tex = None
        gfile = Gio.File.new_for_path(path)
        providers = [Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([gfile]))]
        if tex is not None:
            providers.insert(0, Gdk.ContentProvider.new_for_value(tex))
        self.get_clipboard().set_content(Gdk.ContentProvider.new_union(providers))
        toast(self, tr("Image copiée"))

    def transfer(self, move: bool):
        paths = self._targets()
        if not paths:
            return
        here = os.path.dirname(os.path.abspath(paths[0]))
        d = Gtk.FileDialog(title=tr("Déplacer vers") if move else tr("Copier vers"), modal=True,
                           initial_folder=Gio.File.new_for_path(here))

        def done(dlg, res):
            try:
                f = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            dest_dir = f.get_path() if f is not None else None
            if not dest_dir or os.path.abspath(dest_dir) == here:
                return

            def work():
                ok, errors = [], []
                for path in paths:
                    dest = edit.free_name(os.path.join(dest_dir, os.path.basename(path)))   # jamais d'écrasement
                    try:
                        src, dst = Gio.File.new_for_path(path), Gio.File.new_for_path(dest)
                        flags = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA
                        if move:
                            src.move(dst, flags, None, None, None)
                        else:
                            src.copy(dst, flags, None, None, None)
                        ok.append((path, dest))
                    except GLib.Error as e:
                        errors.append(f"{os.path.basename(path)} : {e.message}")
                GLib.idle_add(lambda: (self._transferred(ok, errors, move, dest_dir), False)[1])
            threading.Thread(target=work, daemon=True).start()
        d.select_folder(self, None, done)

    def _transferred(self, ok, errors, move, dest_dir):
        if move and ok:
            self._removed_many([p for p, _d in ok])
        if errors:
            alert(self, tr("Déplacement impossible") if move else tr("Copie impossible"), shown("\n".join(errors)))
        if not ok:
            return
        where = shown(dest_dir)
        if len(ok) == 1:
            name = shown(os.path.basename(ok[0][1]))
            toast(self, (tr("« {0} » déplacée dans {1}") if move else tr("« {0} » copiée dans {1}")).format(name, where))
        else:
            toast(self, (ntr("{0} photo déplacée dans {1}", "{0} photos déplacées dans {1}", len(ok)) if move else
                         ntr("{0} photo copiée dans {1}", "{0} photos copiées dans {1}", len(ok))).format(len(ok), where))

    def rename(self):
        path = self._target()
        if path is None:
            return
        folder, old = os.path.split(path)
        row = Adw.EntryRow(title=tr("Nom"), text=shown(old))
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(row)
        msg = Gtk.Label(xalign=0, css_classes=["error", "caption"], margin_top=6, visible=False, wrap=True)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(box)
        col.append(msg)
        d = AlertDialog.new(tr("Renommer"), None)
        d.add_response("cancel", tr("Annuler"))
        d.add_response("ok", tr("Renommer"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.set_close_response("cancel")
        d.set_extra_child(col)
        try:
            w = row.create_pango_layout(shown(old)).get_pixel_size()[0]
        except (TypeError, AttributeError):
            w = 8 * len(old)
        d.set_default_size(max(420, min(760, w + 150)), -1)        # le nom entier lisible (leçon d'Odysseus)

        def taken(text):
            """Nom pris par un AUTRE fichier. Clé FAT / exFAT : « photo.jpg » existe pour « photo.JPG » (même
            fichier, insensible à la casse) — changer la seule casse doit rester possible."""
            target = os.path.join(folder, text)
            if text == old or not os.path.lexists(target):
                return False
            try:
                return not os.path.samefile(target, path)
            except OSError:
                return True

        def problem(text):
            if not text.strip() or "/" in text or text in (".", ".."):
                return tr("Nom impossible")
            if taken(text):
                return tr("« {0} » existe déjà").format(text)
            return ""

        def check(*_):
            err = problem(row.get_text())
            msg.set_text(err)
            msg.set_visible(bool(err) and row.get_text() != old)
            d.set_response_enabled("ok", not err and row.get_text() != old)
        row.connect("changed", check)

        def apply():
            new = row.get_text()
            if problem(new) or new == old:
                return
            target = os.path.join(folder, new)
            try:
                if taken(new):
                    raise FileExistsError(target)
                os.rename(path, target)
            except OSError as e:
                alert(self, tr("Renommage impossible"), shown(e.strerror or str(e)))
                return
            self._renamed(path, target)

        def activated(*_):
            if not problem(row.get_text()) and row.get_text() != old:
                d.close()
                apply()
        row.connect("entry-activated", activated)
        d.connect("response", lambda _d, r: r == "ok" and apply())
        check()
        d.present(self)

        def focus():
            row.grab_focus()
            stem = os.path.splitext(old)[0]
            row.select_region(0, len(stem))
            return False
        GLib.idle_add(focus)

    def _renamed(self, path, target):
        self.loader.forget(path)
        forget_everywhere(path)
        for i, e in enumerate(self.images):
            if e.path == path:
                self.images[i] = fold.Entry(target, os.path.basename(target), False, e.size, e.mtime,
                                            fold.name_key(os.path.basename(target)))
                if i == self.index:
                    self.picture = None
                    self.show_index(i)
        self._folder_cache.pop(self.folder, None)
        self._rescan(quiet=True)

    def delete_for_good(self):
        paths = self._targets()
        if not paths:
            return
        if len(paths) == 1:
            heading = tr("Supprimer définitivement « {0} » ?").format(shown(os.path.basename(paths[0])))
        else:
            heading = ntr("Supprimer définitivement l'élément sélectionné ?",
                          "Supprimer définitivement les {0} éléments sélectionnés ?", len(paths)).format(len(paths))

        def answer(r):
            if r != "delete":
                return
            gone, errors = [], []
            for p in paths:
                try:
                    os.remove(p)
                    gone.append(p)
                except OSError as e:
                    errors.append(f"{os.path.basename(p)} : {e.strerror or e}")
            self._removed_many(gone)
            if errors:
                alert(self, tr("Suppression impossible"), shown("\n".join(errors)))
            if len(gone) == 1:
                toast(self, tr("« {0} » supprimée").format(shown(os.path.basename(gone[0]))))
            elif gone:
                toast(self, ntr("{0} photo supprimée", "{0} photos supprimées", len(gone)).format(len(gone)))
        alert(self, heading, tr("Si vous supprimez un élément, il sera définitivement perdu."),
              (("cancel", tr("Annuler")), ("delete", tr("Supprimer"))), default="cancel", destructive="delete",
              callback=answer)

    def trash_path(self, path):
        if path is not None:
            self.trash_paths([path])

    def trash_paths(self, paths):
        if not paths:
            return
        gone, errors = [], []
        for p in paths:
            try:
                Gio.File.new_for_path(p).trash(None)
                gone.append(p)
            except GLib.Error as e:
                errors.append(f"{os.path.basename(p)} : {e.message}")
        self._removed_many(gone)
        if errors:
            alert(self, tr("Impossible de mettre à la corbeille"), shown("\n".join(errors)))
        if len(gone) == 1:
            toast(self, tr("« {0} » mis à la corbeille").format(shown(os.path.basename(gone[0]))))
        elif gone:
            toast(self, ntr("{0} photo mise à la corbeille", "{0} photos mises à la corbeille", len(gone))
                  .format(len(gone)))

    def _removed(self, path):
        self._removed_many([path])

    def _removed_many(self, paths):
        """Photos parties (corbeille, supprimées, déplacées) : retirées des listes ; dans la visionneuse, la voisine."""
        if not paths:
            return
        gone = set(paths)
        for p in gone:
            self.loader.forget(p)
            forget_everywhere(p)
        cur = self.images[self.index].path if 0 <= self.index < len(self.images) else None
        first = min((i for i, e in enumerate(self.images) if e.path in gone), default=-1)
        if first < 0:
            return
        self.images = [e for e in self.images if e.path not in gone]
        self._folder_cache.pop(self.folder, None)
        self.filmstrip.set_model(store_of_images(self.images))
        self.grid.set_model(store_of_images(self.images))
        if cur is not None and cur not in gone:
            self.index = self._index_of(cur)
            self.filmstrip.show_index(self.index)
        elif self.stack.get_visible_child_name() == "viewer":
            if not self.images:
                self.show_browser()
            else:
                self.show_index(min(first, len(self.images) - 1))
        else:
            self.index = min(first, len(self.images) - 1)
        self._grid_selected()


def store_of_images(entries):
    from .gallery import store_of
    return store_of(entries)
