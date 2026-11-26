"""Vue d'une photo : ajustée à la fenêtre par défaut, zoom autour du pointeur (Ctrl + molette, pincement, boutons),
déplacement en glissant, molette = photo suivante / précédente (fonctionnalité `wheel_browse`, comme FastStone),
double-clic = plein écran. Quart de tour d'affichage (`rotation`) : la retouche l'enregistre ensuite.

Gtk.Widget dessiné à la main (`do_snapshot`) : seule la partie visible est envoyée au rendu, avec le filtre
TRILINEAR de GSK en réduction (mipmaps : une photo de 24 Mpx réduite reste nette, sans moiré).
"""

from __future__ import annotations

import math
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

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
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
    }

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, focusable=True, overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("argos-view")
        self.texture: Optional[Gdk.Texture] = None
        self.vector: Optional[str] = None     # SVG : redessiné à la taille affichée (_hires)
        self._hires = None
        self._hires_seq = 0
        self._hires_timer = 0
        self.fit = True                  # ajustée à la fenêtre (suit les redimensionnements)
        self.zoom = 1.0                  # échelle de l'image affichée (1 = 100 %)
        self.rotation = 0                # quarts de tour (affichage)
        self.crop = None                 # recadrage en attente (x, y, l, h) en pixels de l'image tournée
        self.cropping = False            # mode recadrage : image entière + cadre à glisser
        self.sel = None                  # cadre du mode recadrage (x, y, l, h), pixels de l'image tournée
        self.crop_ratio = None           # proportion imposée (l / h) ou None (libre)
        self._sel_mode = None
        self._sel_start = None
        self.cx = 0.5                    # point de l'image (fraction 0..1, après rotation) au centre de la vue
        self.cy = 0.5
        self._anim_iter = None
        self._anim_id = 0
        self._scroll_acc = 0.0
        self._drag_start = (0.0, 0.0)
        self._pointer = None
        self._trans = None               # transition du diaporama : [genre, sens, ancienne image (nœud), avancement]
        self._trans_anim = None

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL
                                           | Gtk.EventControllerScrollFlags.KINETIC)
        scroll.connect("scroll", self._on_scroll)
        scroll.connect("scroll-begin", lambda *_: setattr(self, "_scroll_acc", 0.0))
        self.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda *_: setattr(self, "_pointer", None))
        self.add_controller(motion)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", lambda _g, _n, x, y: not self.cropping and self.emit("context-menu", x, y))
        self.add_controller(right)
        zoom = Gtk.GestureZoom()
        zoom.connect("begin", lambda *_: setattr(self, "_pinch_from", self.zoom))
        zoom.connect("scale-changed", self._on_pinch)
        self.add_controller(zoom)

    # --- image ---

    def set_picture(self, texture: Optional[Gdk.Texture], animation=None, keep_view: bool = False,
                    vector: Optional[str] = None, transition: Optional[tuple[str, int]] = None):
        """vector : chemin d'un SVG — agrandi à la fenêtre comme gThumb, et redessiné net à la taille affichée
        (vu chez Tof : une icône 48 × 48 restait minuscule, puis pixelisait au zoom).
        transition : (« slide » | « fade », sens +1 / -1) — diaporama (demande de Tof) : l'image d'avant, figée en
        nœud de rendu, glisse hors de l'écran pendant que la nouvelle arrive (ou fondu enchaîné)."""
        old = None
        if transition and transition[0] in ("slide", "fade") and self.texture is not None and texture is not None \
                and self.get_width() > 0:
            snap = Gtk.Snapshot()
            self._draw(snap)
            old = snap.to_node()
        self._stop_transition()
        self._stop_animation()
        self.texture = texture
        self.vector = vector
        self._hires = None
        self._hires_seq = getattr(self, "_hires_seq", 0) + 1
        if not keep_view:
            self.rotation = 0
            self.crop = None
            self.cropping = False
            self.fit = True
            self.cx = self.cy = 0.5
        if animation is not None:
            self._anim_iter = animation.get_iter(None)
            self._schedule_frame()
        self._sync_zoom()
        self.set_cursor_from_name(self._idle_cursor())
        if old is not None:
            self._start_transition(transition[0], 1 if transition[1] >= 0 else -1, old)
        self.queue_draw()

    TRANSITION_MS = {"slide": 450, "fade": 600}

    def _start_transition(self, kind: str, direction: int, old):
        self._trans = [kind, direction, old, 0.0]

        def step(value):
            if self._trans is not None:
                self._trans[3] = value
                self.queue_draw()
        anim = Adw.TimedAnimation(widget=self, value_from=0.0, value_to=1.0, duration=self.TRANSITION_MS[kind],
                                  target=Adw.CallbackAnimationTarget.new(step))
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC if kind == "slide" else Adw.Easing.EASE_IN_OUT_CUBIC)
        anim.connect("done", lambda a: self._trans_anim is a and self._stop_transition())
        self._trans_anim = anim
        anim.play()                      # animations coupées dans le système : libadwaita va droit à la fin

    def _stop_transition(self):
        anim, self._trans_anim = self._trans_anim, None
        if anim is not None:
            anim.skip()
        if self._trans is not None:
            self._trans = None
            self.queue_draw()

    def in_transition(self) -> bool:
        return self._trans is not None

    HIRES_MAX = 6000                 # côté le plus long d'un rendu vectoriel (au-delà, mémoire)

    def _want_hires(self):
        """SVG : rendu à la taille affichée, 150 ms après le dernier changement de zoom, dans un fil."""
        if not getattr(self, "vector", None) or self.texture is None:
            return
        if getattr(self, "_hires_timer", 0):
            GLib.source_remove(self._hires_timer)
        self._hires_timer = GLib.timeout_add(150, self._render_hires)

    def _render_hires(self):
        self._hires_timer = 0
        path = self.vector
        if not path or self.texture is None:
            return False
        w0, h0 = self.texture.get_width(), self.texture.get_height()
        scale = self.zoom * (self.get_scale_factor() or 1)
        w, h = round(w0 * scale), round(h0 * scale)
        k = min(1.0, self.HIRES_MAX / max(w, h, 1))
        w, h = max(1, round(w * k)), max(1, round(h * k))
        cur = self._hires
        if cur is not None and abs(cur.get_width() - w) <= 2:
            return False
        if w <= w0 and h <= h0 and cur is None:
            return False                              # plus petit que l'original : l'original suffit
        self._hires_seq += 1
        seq = self._hires_seq

        def work():
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, w, h, False)
                tex = Gdk.Texture.new_for_pixbuf(pb)
            except GLib.Error:
                return
            GLib.idle_add(lambda: (self._got_hires(seq, path, tex), False)[1])
        threading.Thread(target=work, daemon=True).start()
        return False

    def _got_hires(self, seq, path, tex):
        if seq == self._hires_seq and path == self.vector:
            self._hires = tex
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

    def full_size(self) -> tuple[int, int]:
        """Taille de l'image entière après le quart de tour."""
        if self.texture is None:
            return 0, 0
        w, h = self.texture.get_width(), self.texture.get_height()
        return (h, w) if self.rotation % 2 else (w, h)

    def image_size(self) -> tuple[int, int]:
        """Taille de l'image affichée (après le quart de tour et le recadrage en attente)."""
        if self.crop is not None and not self.cropping:
            return self.crop[2], self.crop[3]
        return self.full_size()

    def fit_zoom(self) -> float:
        iw, ih = self.image_size()
        vw, vh = self.get_width(), self.get_height()
        if not iw or not ih or vw <= 0 or vh <= 0:
            return 1.0
        if getattr(self, "vector", None):
            return min(vw / iw, vh / ih)              # dessin vectoriel : à la taille de la fenêtre
        return min(vw / iw, vh / ih, 1.0)            # une petite image n'est pas agrandie

    def _sync_zoom(self):
        if self.fit:
            self.zoom = self.fit_zoom()
            self.cx = self.cy = 0.5
        self._clamp_center()
        self.emit("zoom-changed")
        self._want_hires()

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
        t = self._trans
        if t is None:
            self._draw(snapshot)
            return
        kind, direction, old, p = t
        if kind == "fade":
            snapshot.push_cross_fade(p)
            snapshot.append_node(old)
            snapshot.pop()
            self._draw(snapshot)
            snapshot.pop()
            return
        w = self.get_width()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(-direction * p * w, 0))
        snapshot.append_node(old)
        snapshot.restore()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(direction * (1 - p) * w, 0))
        self._draw(snapshot)
        snapshot.restore()

    def _draw(self, snapshot):
        if self.texture is None:
            return
        r = self.image_rect()
        x, y = r.get_x(), r.get_y()
        fw, fh = self.full_size()
        dw, dh = fw * self.zoom, fh * self.zoom
        clipped = self.crop is not None and not self.cropping
        if clipped:                                  # image entière placée pour que le cadre tombe sur r
            snapshot.push_clip(r)
            x -= self.crop[0] * self.zoom
            y -= self.crop[1] * self.zoom
        filt = Gsk.ScalingFilter.TRILINEAR if self.zoom < 1 else (
            Gsk.ScalingFilter.NEAREST if self.zoom >= 3 else Gsk.ScalingFilter.LINEAR)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x + dw / 2, y + dh / 2))
        if self.rotation:
            snapshot.rotate(90 * self.rotation)
        tw, th = (dh, dw) if self.rotation % 2 else (dw, dh)
        tex = self.texture
        hires = getattr(self, "_hires", None)
        if hires is not None:
            tex = hires
            filt = Gsk.ScalingFilter.LINEAR if hires.get_width() < tw * 1.5 else Gsk.ScalingFilter.TRILINEAR
        elif getattr(self, "vector", None):
            filt = Gsk.ScalingFilter.LINEAR              # en attendant le rendu net : flou plutôt que pixels
        snapshot.append_scaled_texture(tex, filt, Graphene.Rect().init(-tw / 2, -th / 2, tw, th))
        snapshot.restore()
        if clipped:
            snapshot.pop()
        if self.cropping and self.sel is not None:
            self._snapshot_selection(snapshot, r)

    # --- recadrage ---

    HANDLE = 14

    def sel_rect(self) -> Graphene.Rect:
        r = self.image_rect()
        x, y, w, h = self.sel
        z = self.zoom
        return Graphene.Rect().init(r.get_x() + x * z, r.get_y() + y * z, w * z, h * z)

    def _snapshot_selection(self, snapshot, r):
        s = self.sel_rect()
        shade = Gdk.RGBA()
        shade.parse("rgba(0,0,0,0.55)")
        sx, sy, sw, sh = s.get_x(), s.get_y(), s.get_width(), s.get_height()
        rx, ry, rw, rh = r.get_x(), r.get_y(), r.get_width(), r.get_height()
        for x, y, w, h in ((rx, ry, rw, sy - ry), (rx, sy + sh, rw, ry + rh - sy - sh),
                           (rx, sy, sx - rx, sh), (sx + sw, sy, rx + rw - sx - sw, sh)):
            if w > 0 and h > 0:
                snapshot.append_color(shade, Graphene.Rect().init(x, y, w, h))
        white = Gdk.RGBA()
        white.parse("rgba(255,255,255,0.9)")
        third = Gdk.RGBA()
        third.parse("rgba(255,255,255,0.35)")
        for i in (1, 2):                             # règle des tiers
            snapshot.append_color(third, Graphene.Rect().init(sx + sw * i / 3, sy, 1, sh))
            snapshot.append_color(third, Graphene.Rect().init(sx, sy + sh * i / 3, sw, 1))
        for x, y, w, h in ((sx, sy, sw, 2), (sx, sy + sh - 2, sw, 2), (sx, sy, 2, sh), (sx + sw - 2, sy, 2, sh)):
            snapshot.append_color(white, Graphene.Rect().init(x, y, w, h))
        k = 4
        for cx, cy in ((sx, sy), (sx + sw, sy), (sx, sy + sh), (sx + sw, sy + sh)):
            snapshot.append_color(white, Graphene.Rect().init(cx - k, cy - k, 2 * k, 2 * k))

    def start_crop(self):
        """Mode recadrage : image entière, cadre de départ = recadrage en attente, sinon toute l'image."""
        if self.texture is None:
            return
        fw, fh = self.full_size()
        self.sel = list(self.crop) if self.crop else [0, 0, fw, fh]
        self.cropping = True
        self.fit = True
        self._sync_zoom()
        if self.crop_ratio:
            self.set_ratio(self.crop_ratio)
        self.queue_draw()

    def set_ratio(self, ratio):
        """Proportion imposée : le plus grand cadre de cette proportion, centré sur le cadre actuel."""
        self.crop_ratio = ratio
        if not self.cropping or not ratio or self.sel is None:
            self.queue_draw()
            return
        fw, fh = self.full_size()
        x, y, w, h = self.sel
        cx, cy = x + w / 2, y + h / 2
        if w / h > ratio:
            w = h * ratio
        else:
            h = w / ratio
        x = min(max(0, cx - w / 2), fw - w)
        y = min(max(0, cy - h / 2), fh - h)
        self.sel = [x, y, w, h]
        self.queue_draw()

    def finish_crop(self, apply: bool):
        if apply and self.sel is not None:
            fw, fh = self.full_size()
            x, y, w, h = (int(round(v)) for v in self.sel)
            x, y = max(0, x), max(0, y)
            w, h = max(1, min(w, fw - x)), max(1, min(h, fh - y))
            self.crop = None if (x, y, w, h) == (0, 0, fw, fh) else (x, y, w, h)
        self.cropping = False
        self.sel = None
        self.fit = True
        self._sync_zoom()
        self.queue_draw()

    def _hit(self, x, y) -> str:
        s = self.sel_rect()
        sx, sy, sw, sh = s.get_x(), s.get_y(), s.get_width(), s.get_height()
        k = self.HANDLE
        left, right = abs(x - sx) < k, abs(x - sx - sw) < k
        top, bottom = abs(y - sy) < k, abs(y - sy - sh) < k
        inside_x, inside_y = sx - k < x < sx + sw + k, sy - k < y < sy + sh + k
        edge = ("n" if top and inside_x else "s" if bottom and inside_x else "") + \
               ("w" if left and inside_y else "e" if right and inside_y else "")
        if edge:
            return edge
        if sx < x < sx + sw and sy < y < sy + sh:
            return "move"
        return "new"

    CURSORS = {"n": "n-resize", "s": "s-resize", "e": "e-resize", "w": "w-resize", "ne": "ne-resize",
               "nw": "nw-resize", "se": "se-resize", "sw": "sw-resize", "move": "move", "new": "crosshair"}

    def _crop_drag_begin(self, x, y):
        self._sel_mode = self._hit(x, y)
        r = self.image_rect()
        px = (x - r.get_x()) / self.zoom
        py = (y - r.get_y()) / self.zoom
        if self._sel_mode == "new":
            self.sel = [px, py, 0, 0]
            self._sel_mode = "se"
        self._sel_start = (list(self.sel), px, py)

    def _crop_drag_update(self, ox, oy):
        if self._sel_start is None:
            return
        (x, y, w, h), _px, _py = self._sel_start
        fw, fh = self.full_size()
        dx, dy = ox / self.zoom, oy / self.zoom
        mode = self._sel_mode
        if mode == "move":
            x = min(max(0, x + dx), fw - w)
            y = min(max(0, y + dy), fh - h)
            self.sel = [x, y, w, h]
            self.queue_draw()
            return
        x0, y0, x1, y1 = x, y, x + w, y + h
        if "w" in mode:
            x0 = min(max(0, x0 + dx), x1 - 1)
        if "e" in mode:
            x1 = max(min(fw, x1 + dx), x0 + 1)
        if "n" in mode:
            y0 = min(max(0, y0 + dy), y1 - 1)
        if "s" in mode:
            y1 = max(min(fh, y1 + dy), y0 + 1)
        ratio = self.crop_ratio
        if ratio:
            nw, nh = x1 - x0, y1 - y0
            if mode in ("n", "s"):
                nw = nh * ratio
            elif mode in ("e", "w"):
                nh = nw / ratio
            elif nw / nh > ratio:
                nh = nw / ratio
            else:
                nw = nh * ratio
            # le coin opposé reste fixe ; on rabote si ça sort de l'image
            ax = x1 if "w" in mode else x0
            ay = y1 if "n" in mode else y0
            maxw = ax if "w" in mode else fw - ax
            maxh = ay if "n" in mode else fh - ay
            if mode in ("n", "s"):
                maxw = min(fw, 2 * min(x0 + (x1 - x0) / 2, fw - x0 - (x1 - x0) / 2))
            if mode in ("e", "w"):
                maxh = min(fh, 2 * min(y0 + (y1 - y0) / 2, fh - y0 - (y1 - y0) / 2))
            scale = min(1.0, maxw / nw if nw else 1, maxh / nh if nh else 1)
            nw, nh = nw * scale, nh * scale
            if mode in ("n", "s"):
                cx = (x0 + x1) / 2
                x0, x1 = cx - nw / 2, cx + nw / 2
            else:
                x0, x1 = (ax - nw, ax) if "w" in mode else (ax, ax + nw)
            if mode in ("e", "w"):
                cy = (y0 + y1) / 2
                y0, y1 = cy - nh / 2, cy + nh / 2
            else:
                y0, y1 = (ay - nh, ay) if "n" in mode else (ay, ay + nh)
        self.sel = [x0, y0, x1 - x0, y1 - y0]
        self.queue_draw()

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
        self._want_hires()
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
        if self.crop is not None:                   # le cadre tourne avec l'image
            fw, fh = self.full_size()
            x, y, w, h = self.crop
            for _ in range(quarter % 4):
                x, y, w, h, fw, fh = fh - y - h, x, h, w, fh, fw
            self.crop = (x, y, w, h)
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
        if ctrl_held or self.cropping or not features.enabled("wheel_browse"):
            if self.texture is not None:
                self.zoom_step(-1 if dy > 0 else 1, self._pointer)
            return True
        self.emit("navigate", 1 if dy > 0 else -1)
        return True

    def _on_motion(self, _c, x, y):
        self._pointer = (x, y)
        if self.cropping and self.sel is not None:
            self.set_cursor_from_name(self.CURSORS[self._hit(x, y)])

    def _movable(self) -> bool:
        iw, ih = self.image_size()
        return iw * self.zoom > self.get_width() + 0.5 or ih * self.zoom > self.get_height() + 0.5

    pointer_hidden = False                   # plein écran, souris immobile : pointeur caché (fenêtre)

    def set_pointer_hidden(self, hidden: bool):
        if hidden != self.pointer_hidden:
            self.pointer_hidden = hidden
            self.set_cursor_from_name(self._idle_cursor())

    def _idle_cursor(self) -> str:
        if self.cropping:
            return "crosshair"
        if self.pointer_hidden:
            return "none"
        return "grab" if self.texture is not None and self._movable() else "default"

    def _on_drag_begin(self, gesture, x, y):
        if self.cropping:
            self._crop_drag_begin(x, y)
            return
        self._drag_start = (self.cx, self.cy)
        if self._movable():
            self.set_cursor_from_name("grabbing")

    def _on_drag_update(self, gesture, ox, oy):
        if self.cropping:
            self._crop_drag_update(ox, oy)
            return
        if self.texture is None or not self._movable():
            return
        iw, ih = self.image_size()
        self.cx = self._drag_start[0] - ox / (iw * self.zoom)
        self.cy = self._drag_start[1] - oy / (ih * self.zoom)
        self._clamp_center()
        self.queue_draw()

    def _on_drag_end(self, *_):
        if self.cropping and self.sel is not None and (self.sel[2] < 4 or self.sel[3] < 4):
            fw, fh = self.full_size()           # simple clic hors du cadre : on garde l'image entière
            self.sel = [0, 0, fw, fh]
            if self.crop_ratio:
                self.set_ratio(self.crop_ratio)
            self.queue_draw()
        self._sel_start = None
        if not self.cropping:
            self.set_cursor_from_name(self._idle_cursor())

    def _on_pressed(self, gesture, n, x, y):
        self.grab_focus()
        if n == 2 and not self.cropping:
            self.emit("double-click")

    def _on_pinch(self, gesture, scale):
        center = gesture.get_bounding_box_center()
        at = (center[1], center[2]) if isinstance(center, tuple) and len(center) == 3 else None
        self.set_zoom(getattr(self, "_pinch_from", self.zoom) * scale, at)


def pixbuf_rotated(pb: GdkPixbuf.Pixbuf, quarters: int) -> GdkPixbuf.Pixbuf:
    angle = {1: GdkPixbuf.PixbufRotation.CLOCKWISE, 2: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
             3: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}.get(quarters % 4)
    return pb.rotate_simple(angle) if angle is not None else pb
