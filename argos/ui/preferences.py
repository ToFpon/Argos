"""Préférences (Adw.PreferencesWindow : vraie fenêtre, le thème du système s'y applique). Repris de Sirène."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import features  # noqa: E402
from ..i18n import LANGUAGE_NAMES, ntr, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import choose_folder, combo_row, shown  # noqa: E402
from .gallery import GRID_SIZES, nearest_size  # noqa: E402


def apply_theme():
    Adw.StyleManager.get_default().set_color_scheme({
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(settings["theme"], Adw.ColorScheme.DEFAULT))


def _choice(title: str, key: str, labels: list[str], values: list, subtitle: str = "") -> Adw.ComboRow:
    cur = settings[key]
    row = combo_row(title, labels, values.index(cur) if cur in values else 0, subtitle)
    row.connect("notify::selected", lambda r, *_: settings.__setitem__(key, values[r.get_selected()]))
    return row


def default_app_row(title: str, what: str, app_name: str) -> Adw.SwitchRow:
    """Interrupteur « application par défaut » (defaultapp.py), comme celui d'Odysseus : l'état affiché est
    celui du système ; grisé quand l'application tourne depuis les sources (pas de lanceur installé)."""
    from .. import defaultapp
    row = Adw.SwitchRow(title=title, active=defaultapp.is_default())
    row.set_subtitle(what + "\n" + defaultapp.COMMAND)
    row.set_subtitle_lines(0)
    if not defaultapp.installed():
        row.set_subtitle(row.get_subtitle() + "\n" + tr("{0} n'est pas installé (lancé depuis les sources)").format(app_name))
        row.set_sensitive(False)
        return row

    def toggled(r, *_):
        if r.get_active() != defaultapp.is_default() and not defaultapp.set_default(r.get_active()):
            r.set_active(defaultapp.is_default())
    row.connect("notify::active", toggled)
    return row


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, parent=None):
        super().__init__(title=tr("Préférences"), search_enabled=False, default_width=600, default_height=560,
                         transient_for=parent)
        self.add(self._general())
        self.add(self._features())

    def _general(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Général"), icon_name="preferences-system-symbolic", name="general")
        system = Adw.PreferencesGroup()
        system.add(default_app_row(tr("Visionneuse de photos par défaut"),
                                   tr("Les photos s'ouvrent avec Argos ; coupé, l'application d'avant reprend la main."),
                                   "Argos"))
        page.add(system)
        page.add(self._startup_group())
        look = Adw.PreferencesGroup(title=tr("Apparence"))
        theme = _choice(tr("Style"), "theme", [tr("Comme le système"), tr("Clair"), tr("Sombre")],
                        ["system", "light", "dark"])
        theme.connect("notify::selected", lambda *_: apply_theme())
        look.add(theme)
        codes = list(LANGUAGE_NAMES)
        look.add(_choice(tr("Langue"), "language", [tr("Comme le système")] + [LANGUAGE_NAMES[c] for c in codes[1:]],
                         codes, tr("Appliquée au prochain démarrage")))
        page.add(look)
        view = Adw.PreferencesGroup(title=tr("Affichage"))
        # choix simple (demande d'un utilisateur, utile à ceux qui voient mal), appliqué tout de suite ; aussi
        # Ctrl + molette / Ctrl + + / − sur la grille
        size = combo_row(tr("Taille des vignettes"),
                         [tr("Petites"), tr("Moyennes"), tr("Grandes"), tr("Très grandes")],
                         GRID_SIZES.index(nearest_size(settings["grid_size"])),
                         tr("Aussi Ctrl + molette ou Ctrl + + / − dans la grille"))
        size.connect("notify::selected", lambda r, *_: settings.__setitem__("grid_size", GRID_SIZES[r.get_selected()]))

        def follow(key, value):                     # changée par Ctrl + molette pendant que la fenêtre est ouverte
            if key == "grid_size" and GRID_SIZES[size.get_selected()] != nearest_size(value):
                size.set_selected(GRID_SIZES.index(nearest_size(value)))
        settings.connect(follow)
        self.connect("close-request", lambda *_: settings.disconnect(follow) or False)
        view.add(size)
        delays = [2, 3, 4, 5, 8, 10, 15]
        view.add(_choice(tr("Diaporama"), "slideshow_delay",
                         [ntr("{0} seconde par photo", "{0} secondes par photo", d).format(d) for d in delays], delays,
                         tr("F5, ou Diaporama dans le menu ; Espace pour la pause, Échap pour arrêter")))
        view.add(_choice(tr("Transition entre les photos"), "slideshow_transition",
                         [tr("Glissement"), tr("Fondu enchaîné"), tr("Sans transition")], ["slide", "fade", "none"],
                         tr("Diaporama, flèches du clavier et molette")))
        page.add(view)
        return page

    def _startup_group(self) -> Adw.PreferencesGroup:
        """« Ouvrir Argos sur » (demande d'un utilisateur, comme dans Odysseus) : dernier dossier, Images ou un dossier
        choisi — avec l'arbre masqué (F9), Argos s'ouvre toujours au même endroit."""
        group = Adw.PreferencesGroup(title=tr("Comportement"))
        choices = ["last", "pictures", "folder"]
        where = combo_row(tr("Ouvrir Argos sur"),
                          [tr("Le dernier dossier ouvert"), tr("Le dossier Images"), tr("Un dossier choisi")],
                          choices.index(settings["startup"]) if settings["startup"] in choices else 0)
        folder = Adw.ActionRow(title=tr("Dossier"), use_markup=False)
        folder.set_subtitle(shown(settings["startup_folder"]) or tr("Aucun dossier choisi"))
        pick = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=tr("Choisir un dossier"),
                          valign=Gtk.Align.CENTER, css_classes=["flat"])
        folder.add_suffix(pick)
        folder.set_activatable_widget(pick)

        def sync():
            folder.set_sensitive(settings["startup"] == "folder")

        def chose(r, *_):
            settings["startup"] = choices[r.get_selected()]
            sync()
            if settings["startup"] == "folder" and not settings["startup_folder"]:
                browse()

        def picked(path):
            settings["startup_folder"] = path
            folder.set_subtitle(shown(path))

        def browse(*_):
            from ..backend import folder as fold
            choose_folder(self, tr("Ouvrir Argos sur"), settings["startup_folder"] or fold.pictures_dir(), picked)
        where.connect("notify::selected", chose)
        pick.connect("clicked", browse)
        sync()
        group.add(where)
        group.add(folder)
        return group

    def _features(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Fonctionnalités"), icon_name="application-x-addon-symbolic",
                                   name="features")
        group = Adw.PreferencesGroup(description=tr("Chaque fonctionnalité s'active ou se coupe tout de "
                                                    "suite, sans redémarrer Argos"))
        self.feature_rows = {}
        for f in features.FEATURES:
            row = Adw.SwitchRow(title=f.title, subtitle=f.description, active=features.enabled(f.id))
            row.add_prefix(Gtk.Image(icon_name=f.icon))
            if features.blocked(f.id):                  # composant absent : grisée, la raison reste lisible
                row.set_active(False)
                row.set_subtitle(f.description + "\n⚠ " + f.missing)
                row.set_sensitive(False)
            row.connect("notify::active", lambda r, *_, fid=f.id: features.set_enabled(fid, r.get_active()))
            self.feature_rows[f.id] = row
            group.add(row)
        page.add(group)
        return page
