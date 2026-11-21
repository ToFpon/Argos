"""Vue d'une photo : ajustée à la fenêtre par défaut, zoom autour du pointeur (Ctrl + molette, pincement, boutons),
déplacement en glissant, molette = photo suivante / précédente (fonctionnalité `wheel_browse`, comme FastStone),
double-clic = plein écran. Quart de tour d'affichage (`rotation`) : la retouche l'enregistre ensuite.

Gtk.Widget dessiné à la main (`do_snapshot`) : seule la partie visible est envoyée au rendu, avec le filtre
TRILINEAR de GSK en réduction (mipmaps : une photo de 24 Mpx réduite reste nette, sans moiré).
"""

from __future__ import annotations

import math
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from .. import features  # noqa: E402

ZOOM_MIN = 0.02
ZOOM_MAX = 32.0
ZOOM_STEPS = (0.05, 0.1, 0.15, 0.2, 0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0,
              16.0, 24.0, 32.0)


class ImageView(Gtk.Widget):
    __gtype_name__ = "ArgosImageView"
    __gsignals__ = {
        "navigate": (GObject.SignalFlags.RUN_FIRST, None, (int,)),      # +1 suivante, -1 précédente
        "zoom-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "double-click": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, focusable=True, overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("argos-view")
        self.texture: Optional[Gdk.Texture] = None
        self.fit = True                  # ajustée à la fenêtre (suit les redimensionnements)
        self.zoom = 1.0                  # échelle de l'image affichée (1 = 100 %)
        self.rotation = 0                # quarts de tour (affichage)
        self.cx = 0.5                    # point de l'image (fraction 0..1, après rotation) au centre de la vue
        self.cy = 0.5
        self._anim_iter = None
        self._anim_id = 0
        self._scroll_acc = 0.0
        self._drag_start = (0.0, 0.0)
        self._pointer = None

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL
                                           | Gtk.EventControllerScrollFlags.KINETIC)
        scroll.connect("scroll", self._on_scroll)
        scroll.connect("scroll-begin", lambda *_: setattr(self, "_scroll_acc", 0.0))
        self.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "_pointer", (x, y)))
        motion.connect("leave", lambda *_: setattr(self, "_pointer", None))
        self.add_controller(motion)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", lambda *_: self.set_cursor_from_name(self._idle_cursor()))
        self.add_controller(drag)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)
        zoom = Gtk.GestureZoom()
        zoom.connect("begin", lambda *_: setattr(self, "_pinch_from", self.zoom))
        zoom.connect("scale-changed", self._on_pinch)
        self.add_controller(zoom)

    # --- image ---

    def set_picture(self, texture: Optional[Gdk.Texture], animation=None, keep_view: bool = False):
        self._stop_animation()
        self.texture = texture
        if not keep_view:
            self.rotation = 0
            self.fit = True
            self.cx = self.cy = 0.5
        if animation is not None:
            self._anim_iter = animation.get_iter(None)
            self._schedule_frame()
        self._sync_zoom()
        self.set_cursor_from_name(self._idle_cursor())
        self.queue_draw()

    def _stop_animation(self):
        if self._anim_id:
            GLib.source_remove(self._anim_id)
            self._anim_id = 0
        self._anim_iter = None

    def _schedule_frame(self):
        it = self._anim_iter
        if it is None:
            return
        delay = it.get_delay_time()
        if delay < 0:                    # dernière image d'une animation qui ne boucle pas
            return
        self._anim_id = GLib.timeout_add(max(20, delay), self._next_frame)

    def _next_frame(self):
        self._anim_id = 0
        it = self._anim_iter
        if it is None:
            return False
        it.advance(None)
        self.texture = Gdk.Texture.new_for_pixbuf(it.get_pixbuf())
        self.queue_draw()
        self._schedule_frame()
        return False

    # --- géométrie ---

    def image_size(self) -> tuple[int, int]:
        """Taille de l'image affichée (après le quart de tour)."""
        if self.texture is None:
            return 0, 0
        w, h = self.texture.get_width(), self.texture.get_height()
        return (h, w) if self.rotation % 2 else (w, h)

    def fit_zoom(self) -> float:
        iw, ih = self.image_size()
        vw, vh = self.get_width(), self.get_height()
        if not iw or not ih or vw <= 0 or vh <= 0:
            return 1.0
        return min(vw / iw, vh / ih, 1.0)            # une petite image n'est pas agrandie

    def _sync_zoom(self):
        if self.fit:
            self.zoom = self.fit_zoom()
            self.cx = self.cy = 0.5
        self._clamp_center()
        self.emit("zoom-changed")

    def _clamp_center(self):
        iw, ih = self.image_size()
        if not iw:
            return
        vw, vh = self.get_width(), self.get_height()
        dw, dh = iw * self.zoom, ih * self.zoom
        for attr, d, v in (("cx", dw, vw), ("cy", dh, vh)):
            if d <= v:
                setattr(self, attr, 0.5)
            else:
                half = v / 2 / d
                setattr(self, attr, min(1 - half, max(half, getattr(self, attr))))

    def do_size_allocate(self, width, height, baseline):
        self._sync_zoom()

    def image_rect(self) -> Graphene.Rect:
        """Rectangle de l'image affichée, en coordonnées de la vue."""
        iw, ih = self.image_size()
        dw, dh = iw * self.zoom, ih * self.zoom
        x = self.get_width() / 2 - self.cx * dw
        y = self.get_height() / 2 - self.cy * dh
        return Graphene.Rect().init(x, y, dw, dh)

    def do_snapshot(self, snapshot):
        if self.texture is None:
            return
        r = self.image_rect()
        x, y, dw, dh = r.get_x(), r.get_y(), r.get_width(), r.get_height()
        filt = Gsk.ScalingFilter.TRILINEAR if self.zoom < 1 else (
            Gsk.ScalingFilter.NEAREST if self.zoom >= 3 else Gsk.ScalingFilter.LINEAR)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x + dw / 2, y + dh / 2))
        if self.rotation:
            snapshot.rotate(90 * self.rotation)
        tw, th = (dh, dw) if self.rotation % 2 else (dw, dh)
        snapshot.append_scaled_texture(self.texture, filt, Graphene.Rect().init(-tw / 2, -th / 2, tw, th))
        snapshot.restore()

    # --- zoom ---

    def set_zoom(self, zoom: float, at: Optional[tuple[float, float]] = None):
        """Zoom à `zoom`, en gardant immobile le point de l'image sous `at` (coordonnées de la vue)."""
        if self.texture is None:
            return
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        iw, ih = self.image_size()
        if at is None:
            at = (self.get_width() / 2, self.get_height() / 2)
        r = self.image_rect()
        # point de l'image sous le pointeur (fraction), puis centre recalculé pour qu'il y reste
        fx = (at[0] - r.get_x()) / r.get_width() if r.get_width() else 0.5
        fy = (at[1] - r.get_y()) / r.get_height() if r.get_height() else 0.5
        fx, fy = min(1, max(0, fx)), min(1, max(0, fy))
        self.fit = False
        self.zoom = zoom
        self.cx = fx - (at[0] - self.get_width() / 2) / (iw * zoom)
        self.cy = fy - (at[1] - self.get_height() / 2) / (ih * zoom)
        self._clamp_center()
        self.set_cursor_from_name(self._idle_cursor())
        self.emit("zoom-changed")
        self.queue_draw()

    def zoom_step(self, direction: int, at=None):
        cur = self.zoom
        if direction > 0:
            nxt = next((z for z in ZOOM_STEPS if z > cur * 1.01), ZOOM_MAX)
        else:
            nxt = next((z for z in reversed(ZOOM_STEPS) if z < cur / 1.01), ZOOM_MIN)
        fit = self.fit_zoom()
        if (cur < fit < nxt) or (nxt < fit < cur):       # on passe par « ajusté » en chemin
            nxt = fit
        self.set_zoom(nxt, at)
        if abs(nxt - fit) < 1e-6:
            self.set_fit()

    def set_fit(self):
        self.fit = True
        self._sync_zoom()
        self.set_cursor_from_name(self._idle_cursor())
        self.queue_draw()

    def rotate(self, quarter: int):
        self.rotation = (self.rotation + quarter) % 4
        self.fit = True
        self._sync_zoom()
        self.queue_draw()

    # --- événements ---

    def _on_scroll(self, ctrl, dx, dy):
        state = ctrl.get_current_event_state()
        ctrl_held = bool(state & Gdk.ModifierType.CONTROL_MASK)
        unit = ctrl.get_unit() if hasattr(ctrl, "get_unit") else Gdk.ScrollUnit.WHEEL
        if unit == Gdk.ScrollUnit.SURFACE:               # pavé tactile : on cumule jusqu'à un cran
            self._scroll_acc += dy / 40
            if abs(self._scroll_acc) < 1:
                return True
            dy, self._scroll_acc = math.copysign(1, self._scroll_acc), 0.0
        if dy == 0:
            return False
        if ctrl_held or not features.enabled("wheel_browse"):
            if self.texture is not None:
                self.zoom_step(-1 if dy > 0 else 1, self._pointer)
            return True
        self.emit("navigate", 1 if dy > 0 else -1)
        return True

    def _movable(self) -> bool:
        iw, ih = self.image_size()
        return iw * self.zoom > self.get_width() + 0.5 or ih * self.zoom > self.get_height() + 0.5

    def _idle_cursor(self) -> str:
        return "grab" if self.texture is not None and self._movable() else "default"

    def _on_drag_begin(self, gesture, x, y):
        self._drag_start = (self.cx, self.cy)
        if self._movable():
            self.set_cursor_from_name("grabbing")

    def _on_drag_update(self, gesture, ox, oy):
        if self.texture is None or not self._movable():
            return
        iw, ih = self.image_size()
        self.cx = self._drag_start[0] - ox / (iw * self.zoom)
        self.cy = self._drag_start[1] - oy / (ih * self.zoom)
        self._clamp_center()
        self.queue_draw()

    def _on_pressed(self, gesture, n, x, y):
        self.grab_focus()
        if n == 2:
            self.emit("double-click")

    def _on_pinch(self, gesture, scale):
        center = gesture.get_bounding_box_center()
        at = (center[1], center[2]) if isinstance(center, tuple) and len(center) == 3 else None
        self.set_zoom(getattr(self, "_pinch_from", self.zoom) * scale, at)


def pixbuf_rotated(pb: GdkPixbuf.Pixbuf, quarters: int) -> GdkPixbuf.Pixbuf:
    angle = {1: GdkPixbuf.PixbufRotation.CLOCKWISE, 2: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
             3: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}.get(quarters % 4)
    return pb.rotate_simple(angle) if angle is not None else pb
