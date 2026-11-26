"""Fenêtre « Raccourcis clavier » (Gtk.ShortcutsWindow, décrite en XML : add_section n'existe qu'à
partir de GTK 4.14, le XML marche partout)."""

from __future__ import annotations

from xml.sax.saxutils import escape

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from ..i18n import tr  # noqa: E402


def _groups():
    return [
        (tr("Photos"), [
            ("Right Left", tr("Photo suivante / précédente")),
            ("space BackSpace", tr("Photo suivante / précédente")),
            ("Home End", tr("Première / dernière photo")),
            ("Delete", tr("Mettre à la corbeille")),
            ("<Shift>Delete", tr("Supprimer…")),
            ("F2", tr("Renommer…")),
            ("<Control>c", tr("Copier l'image")),
            ("<Control>p", tr("Imprimer…")),
            ("<Control>e", tr("Convertir / redimensionner…")),
            ("a", tr("Annoter l'image…")),
            ("r <Shift>r", tr("Pivoter à droite / à gauche")),
            ("i", tr("Propriétés")),
        ]),
        (tr("Zoom"), [
            ("plus minus", tr("Zoom avant / arrière ; dans la grille : taille des vignettes")),
            ("0", tr("Ajuster à la fenêtre")),
            ("1", tr("Taille réelle (100 %)")),
        ]),
        (tr("Fenêtre"), [
            ("f F11", tr("Plein écran")),
            ("F5", tr("Diaporama (Espace : pause)")),
            ("Escape", tr("Quitter le plein écran, puis revenir au dossier")),
            ("F9", tr("Barre latérale (grille) / bande de vignettes (photo)")),
            ("<Alt>Up", tr("Dossier parent")),
            ("<Control>o", tr("Ouvrir un dossier")),
            ("<Control>comma", tr("Préférences")),
            ("F1 <Control>question", tr("Raccourcis clavier")),
            ("<Control>w", tr("Fermer la fenêtre")),
        ]),
        (tr("Souris"), [
            (None, tr("Molette : photo suivante / précédente")),
            (None, tr("Ctrl + molette : zoom autour du pointeur ; dans la grille : taille des vignettes")),
            (None, tr("Glisser : déplacer la photo agrandie")),
            (None, tr("Double-clic : plein écran")),
        ]),
    ]


def shortcuts_window() -> Gtk.ShortcutsWindow:
    # max-height : tout sur UNE page. Sur deux pages, GTK 4.14 ajoute des boutons « 1 / 2 » dont les
    # étiquettes se mesurent en négatif (« GtkLabel reported min width -4 », vu chez Tof) ; la ligne
    # « Raccourcis clavier » ajoutée en 1.0.3 avait fait passer la fenêtre à deux pages.
    parts =['<interface><object class="GtkShortcutsWindow" id="win"><property name="modal">1</property>'
             '<child><object class="GtkShortcutsSection"><property name="section-name">main</property>'
             '<property name="max-height">16</property>']
    for title, rows in _groups():
        parts.append(f'<child><object class="GtkShortcutsGroup"><property name="title">{escape(title)}</property>')
        for accel, text in rows:
            parts.append('<child><object class="GtkShortcutsShortcut">')
            if accel:
                parts.append(f'<property name="accelerator">{escape(accel)}</property>')
            else:
                parts.append('<property name="shortcut-type">gesture</property><property name="icon">'
                             '<object class="GThemedIcon"><property name="name">input-mouse-symbolic</property>'
                             '</object></property>')
            parts.append(f'<property name="title">{escape(text)}</property></object></child>')
        parts.append('</object></child>')
    parts.append('</object></child></object></interface>')
    Gio.ThemedIcon.new("x")             # type GThemedIcon enregistré : le XML le crée par son nom
    builder = Gtk.Builder.new_from_string("".join(parts), -1)
    return builder.get_object("win")


def attach_help(window: Gtk.ApplicationWindow):
    window.set_help_overlay(shortcuts_window())
    window.get_application().set_accels_for_action("win.show-help-overlay", ["F1", "<Control>question"])
