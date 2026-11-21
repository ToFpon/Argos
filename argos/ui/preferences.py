"""Préférences (Adw.PreferencesWindow : vraie fenêtre, le thème du système s'y applique). Repris de Sirène."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import features  # noqa: E402
from ..i18n import LANGUAGE_NAMES, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import combo_row  # noqa: E402


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
        size = Adw.SpinRow.new_with_range(96, 320, 16)
        size.set_title(tr("Taille des vignettes"))
        size.set_subtitle(tr("Dans la grille du dossier (appliquée au prochain démarrage)"))
        size.set_value(settings["grid_size"])
        size.connect("notify::value", lambda r, *_: settings.__setitem__("grid_size", int(r.get_value())))
        view.add(size)
        page.add(view)
        return page

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
