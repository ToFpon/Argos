"""Fonctionnalités facultatives d'Argos, toutes désactivables sans redémarrage.

Chaque fonctionnalité se consulte au moment de l'usage (enabled(id)), jamais à l'import :
Préférences → Fonctionnalités l'active ou la coupe tout de suite. (Repris de Sirène.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .i18n import tr
from .settings import settings


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    description: str
    icon: str
    default: bool = True
    available: Optional[Callable[[], bool]] = None    # composant indispensable présent ? (sinon grisée)
    missing: str = ""                                 # ce qu'il faut installer


def _magick() -> bool:
    from .backend.edit import magick_command
    return magick_command() is not None


FEATURES = [
    Feature("wheel_browse", tr("Molette = photo suivante"),
            tr("La molette passe à la photo suivante ou précédente, Ctrl + molette zoome (comme FastStone) ; "
               "coupée, la molette zoome"),
            "input-mouse-symbolic"),
    Feature("loop", tr("Revenir au début"),
            tr("Après la dernière photo du dossier, on repart de la première"),
            "media-playlist-repeat-symbolic"),
    Feature("preload", tr("Préchargement"),
            tr("Les photos voisines sont lues d'avance : la suivante s'affiche tout de suite"),
            "emblem-synchronizing-symbolic"),
    Feature("edit", tr("Retouche simple"),
            tr("Pivoter, recadrer et enregistrer sous un autre format"),
            "document-edit-symbolic", available=_magick,
            missing=tr("Demande ImageMagick (paquet imagemagick)")),
]

BY_ID = {f.id: f for f in FEATURES}


def enabled(fid: str) -> bool:
    f = BY_ID.get(fid)
    if f is None:
        return False
    value = settings["features"].get(fid, f.default)
    return value if isinstance(value, bool) else f.default     # « "no" » écrit à la main ne vaut pas vrai


def blocked(fid: str) -> bool:
    f = BY_ID.get(fid)
    return f is not None and f.available is not None and not f.available()


def set_enabled(fid: str, value: bool):
    feats = dict(settings["features"])
    feats[fid] = bool(value)
    settings["features"] = feats
