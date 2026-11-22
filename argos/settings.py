"""Préférences persistantes (JSON dans ~/.config/argos). Seules les valeurs choisies par l'utilisateur sont
écrites : une valeur par défaut corrigée plus tard s'applique à tous ceux qui n'y ont pas touché.
(Repris de Sirène.)"""

from __future__ import annotations

import json
import os
from typing import Any

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "argos")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS: dict[str, Any] = {
    "theme": "system",              # system, light, dark
    "language": "system",           # system, fr, en, de (appliqué au prochain démarrage)
    "window_width": 1100,
    "window_height": 760,
    "window_maximized": False,
    "last_folder": "",              # dossier ouvert la dernière fois (vide : Images)
    "filmstrip": True,
    "strip_height": 110,            # hauteur de la bande de vignettes (séparateur réglable)
    "browser_info": False,
    "info_ratio": 0.55,             # part de la barre latérale laissée à l'arbre quand les propriétés sont affichées          # propriétés sous l'arbre des dossiers (grille)
    "sidebar": True,                # arbre des dossiers à gauche de la grille              # bande de vignettes sous la photo
    "grid_size": 128,               # taille des vignettes de la grille (px), 128 comme gThumb
    "sort_key": "name",             # name, date, size
    "sort_reverse": False,
    "show_hidden": False,
    "previous_defaults": {},        # application par défaut d'avant, par type (defaultapp.py)
    "image_resize": "scale",       # outils image (repris d'Odysseus) : derniers choix
    "image_scale": 50,
    "image_width": 1920,
    "image_height": 1080,
    "image_no_enlarge": True,
    "image_rotate": 90,
    "image_format": "jpg",
    "image_quality": 90,
    "image_replace": False,
    "features": {},                 # fonctionnalités activées / désactivées (voir features.py)
}


def _fits(value, default) -> bool:
    """Valeur du bon type (un settings.json abîmé ou écrit à la main ne doit rien casser)."""
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


class Settings:
    def __init__(self, path: str = CONFIG_FILE):
        self.path = path
        self.data: dict[str, Any] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data = {k: v for k, v in loaded.items() if k in DEFAULTS and _fits(v, DEFAULTS[k])}
        except (OSError, ValueError):
            pass
        self._listeners = []

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        if self[key] == value:
            return
        self.data[key] = value
        self.save()
        for cb in list(self._listeners):
            cb(key, value)

    def connect(self, cb):
        self._listeners.append(cb)

    def disconnect(self, cb):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass


settings = Settings()
