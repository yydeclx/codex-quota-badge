"""Non-activating statistics control owned by the Codex window."""
import ctypes as C
from ctypes import wintypes as W
from math import cos, tau
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic

from PIL import Image, ImageDraw

from composer_anchor import ABSENT, BLOCKED, NO_MAIN, ComposerSample
from composer_font import scaled_fonts, width as text_width, write as write_text
from session_usage_view import is_speed
from session_usage_card import PAGE_SIZE, WIDTH, render_session_card
from sidebar_data import COMPOSER_STYLES, COMPOSER_WEIGHTS
from tooltip_card import LayeredSurface
from ui_render import raster_cache


HOVER_DELAY, LEAVE_DELAY, FADE_TIME = .085, .180, .120
POPUP_TRAVEL = 4  # logical pixels, matching the reference hover transition
ANCHOR_INTERVAL = .75
LAYOUT_RETRY_INTERVAL = .20
NAV_INTERVAL = .032
OBSTRUCTION_GRACE = .40
ABSENCE_GRACE = .85
IDENTITY_GRACE = .55
ANCHOR_READY = 0x800C
ANCHOR_WAKE_TIMER = 3  # scoped to the sidebar controller
DOT_SIZE, DOT_GAP, PULSE_TIME = 12, 5, 1.4
DOT_COLORS = {
    'active': ((36, 137, 87), (86, 204, 134)),
    'ready': ((43, 127, 255), (98, 156, 255)),
    'completed': ((43, 127, 255), (98, 156, 255)),
    'red': ((194, 76, 69), (251, 123, 119)),
    'amber': ((190, 137, 49), (240, 185, 81)),
}
MOTION_TIMER = 1  # scoped to the composer's own summary window


class TrackMouse(C.Structure):
    _fields_ = [('size', W.DWORD), ('flags', W.DWORD), ('hwnd', W.HWND), ('hover', W.DWORD)]


def animations_enabled(win):
    enabled = W.BOOL()
    query = win.u.SystemParametersInfoW
    query.argtypes = [W.UINT, W.UINT, C.c_void_p, W.UINT]
    query.restype = W.BOOL
    return bool(query(0x1042, 0, C.byref(enabled), 0) and enabled.value)  # SPI_GETCLIENTAREAANIMATION


def ease(progress):
    """CSS ease: cubic-bezier(.25, .1, .25, 1)."""
    lo, hi = 0., 1.
    for _ in range(12):
        t = (lo + hi) / 2
        u = 1 - t
        if .75 * u * t + t**3 < progress:
            lo = t
        else:
            hi = t
    return 0 if progress <= 0 else 1 if progress >= 1 else .3 * u * u * t + 3 * u * t * t + t**3


@raster_cache
def render_dot(scale, dark, state):
    size = round(DOT_SIZE * scale)
    image = Image.new('RGBA', (size * 3, size * 3))
    draw = ImageDraw.Draw(image)
    color = DOT_COLORS[state][bool(dark)]
    center = size * 1.5
    for radius, alpha in ((5 if state == 'active' else 4.5, 53 if state == 'active' else 41), (3, 255)):
        r = round(radius * scale * 3)
        draw.ellipse((center - r, center - r, center + r, center + r), fill=(*color, alpha))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def render_summary(scale, width, height, speed, context, cache, dark=False, hover=False, archive=None):
    return summary_layout(scale, width, height, speed, context, cache, dark, hover, archive)[0]


@raster_cache
def summary_layout(scale, width, height, speed, context, cache, dark=False, hover=False, archive=None,
                   with_dot=False, status=None, style='classic', weight='normal'):
    """Draw a single line, shortening by complete groups as space decreases."""
    p = lambda value: round(value * scale * 3)
    image = Image.new('RGBA', (p(width), p(height)))
    draw = ImageDraw.Draw(image)
    bold = weight == 'bold'
    regular = scaled_fonts(archive, p(11), bold=bold)
    color = '#d8d7d4' if dark else '#555450'
    fill = (255, 255, 255, 24) if dark else (0, 0, 0, 13)
    pill = style == 'pill'
    base = (255, 255, 255, 14) if dark else (0, 0, 0, 9)
    draw.rounded_rectangle((0, 0, image.width - 1, image.height - 1), p(height/2 if pill else 7),
                           fill=fill if hover else base if pill else (255, 255, 255, 1))
    # On a narrow toolbar keep the speed before dropping secondary metrics.
    dot_width = p(DOT_SIZE + DOT_GAP) if with_dot else 0
    available = p(width - (4 if status else 12)) - dot_width
    width_of = lambda value: text_width(draw, value, regular, tabular=True)
    lead = status or (speed if is_speed(speed) else '— tok/s')
    if style == 'english' and lead.startswith('约 '):
        lead = '~' + lead[2:]
    if status == '思考中' and style == 'english':
        lead = 'Thinking'
    if width_of(lead) > available:
        lead = lead.removeprefix('约 ')
    if width_of(lead) > available:
        regular = scaled_fonts(archive, p(9), bold=bold)
    if width_of(lead) > available:
        # At minimum width the number still has priority; full units remain in the card.
        lead = '启动' if status == '正在启动' else lead.split(' ')[0]
    if width_of(lead) > available:
        lead = '—'
    labels, separator = {'english':(('Ctx ', 'Cache '),' · '), 'compact':(('上下文', '缓存'),' / ')}.get(
        style,(('上下文 ', '缓存 '),' · '))
    values = [label + value for value, label in zip((context, cache), labels)
              if status == '思考中' or (not status and value != '—')]
    if status == '思考中' and width_of(separator.join((lead, *values))) > available:
        regular = scaled_fonts(archive, p(9), bold=bold)
    # Center the visible line, not a reserved speed slot: short speeds
    # should leave the same inset at both ends of the hover background.
    dot_offset = None
    for count in range(len(values), -1, -1):
        line = separator.join((lead, *values[:count]))
        line_width = width_of(line)
        if line_width <= available:
            start = (image.width - dot_width - line_width) / 2
            if with_dot:
                dot_offset = round(start / 3)
            write_text(draw, (start + dot_width, image.height // 2), line, regular, color, tabular=True)
            break
    return image.resize((round(width * scale), round(height * scale)), Image.Resampling.LANCZOS), dot_offset


def render_composer_preview(scale, style, weight, active=False):
    image, offset = summary_layout(scale,256,26,'56 tok/s','11%','45.4%',with_dot=True,
                                   status='思考中' if active else None,style=style,weight=weight)
    image = image.copy()
    if offset is not None:
        dot = render_dot(scale,False,'active' if active else 'completed')
        image.alpha_composite(dot,(offset,(image.height-dot.height)//2))
    return image


def inside(rect, x, y):
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def popup_position(host, summary, size, gap):
    width, height = size
    if width + gap * 2 > host[2] - host[0] or height + gap * 2 > host[3] - host[1]:
        return None
    centered = (summary[0] + summary[2] - width) // 2
    x = max(host[0] + gap, min(centered, host[2] - width - gap))
    y = summary[1] - height - gap
    if y < host[1] + gap:
        y = summary[3] + gap
        if y + height + gap > host[3]:
            return None
    return x, y


class ComposerUsage:
    def __init__(self, owner, monitor, anchor_reader=None):
        self.owner = owner
        self.monitor = monitor
        self.anchor_reader = anchor_reader
        self.host = None
        self.window = self.popup = None
        self.anchor = self.rect = None
        self.new_chat = False
        self.composer_running = False
        self.summary_session = None
        self.summary_usage = {}
        self.archive = None
        self.result = SimpleQueue()
        self.busy = False
        self.next_anchor = 0
        self.nav_until = 0
        self.absent_since = self.blocked_since = self.identity_missing_since = None
        self.suspended = False
        self.generation = 0
        self.anchor_layout = None
        self.last_anchor_start = float('-inf')
        self.scale = 1.0
        self.summary_key = self.card_key = None
        self.summary_surface = None
        self.summary_position = None
        self.dot_patch = self.dot_frame = self.dot_state = self.dot_offset = None
        self.pulse_start = 0.
        self.card_surface = None
        self.timer_ms = None
        self.motion = False
        self.dark = False
        self.pointer_windows = set()
        self.suppress_hover = False
        self.fold()

    @property
    def win(self):
        return self.owner.win

    def fold(self):
        if self.popup:
            self.win.u.ShowWindow(self.popup, 0)
        if self.card_surface:
            self.card_surface.close()
        self.card_surface = None
        self.open = self.pinned = False
        self.transition = None
        self.opacity = 0.
        self.hover_since = self.left_at = None
        self.page = 'summary'
        self.offset = 0
        self.turn = self.focus = None
        self.actions = []
        self.card_key = self.popup_rect = self.popup_target = self.popup_frame = None
        self._schedule()

    def hide(self):
        self.rect = self.summary_position = None
        self.dot_state = None
        self.dot_frame = None
        self.suppress_hover = False
        self.pointer_windows.clear()
        self.fold()
        if self.window:
            self.win.u.ShowWindow(self.window, 0)

    def suspend(self):
        """Let the OS stack this owned summary with Codex during focus changes."""
        u = self.win.u
        if not self.host or not u.IsWindowVisible(self.host) or u.IsIconic(self.host):
            if self.anchor or self.rect:
                self.invalidate()
            return
        if not self.suspended:
            self.suspended = True
            self.fold()
            self.generation += 1
            self.next_anchor = 0

    def invalidate(self):
        self.hide()
        self.generation += 1
        self.anchor = self.anchor_layout = None
        self.new_chat = False
        self.composer_running = False
        self.absent_since = self.blocked_since = self.identity_missing_since = None
        self.next_anchor = self.nav_until = 0

    def destroy(self):
        self.invalidate()
        self.summary_session = None
        self.summary_usage.clear()
        for handle in (self.popup, self.window):
            if handle and self.win.u.IsWindow(handle):
                self.win.u.DestroyWindow(handle)
        self.popup = self.window = self.host = None
        self.dot_patch = None
        if self.summary_surface:
            self.summary_surface.close()
        self.summary_surface = self.summary_key = self.summary_position = None

    def _bounds(self, hwnd):
        rect = W.RECT()
        if hwnd and self.win.u.GetWindowRect(hwnd, C.byref(rect)):
            return rect.left, rect.top, rect.right, rect.bottom
        return None

    def status(self):
        visible = bool(self.rect and self.window and self.win.u.IsWindowVisible(self.window))
        return {'visible': visible, 'anchor': self.anchor,
                'rect': self._bounds(self.window) if visible else None}

    def _make_windows(self):
        u = self.win.u
        exstyle = 0x80 | 0x08000000 | 0x80000
        context = u.SetThreadDpiAwarenessContext(u.GetWindowDpiAwarenessContext(self.host))
        try:
            self.window = u.CreateWindowExW(exstyle, 'CodexQuotaNativeV2', 'Codex 会话用量', 0x80000000,
                                            0, 0, 1, 1, self.host, None, self.win.instance, None)
            self.popup = u.CreateWindowExW(exstyle, 'CodexQuotaNativeV2', 'Codex 会话用量详情', 0x80000000,
                                           0, 0, 1, 1, self.window, None, self.win.instance, None)
        finally:
            u.SetThreadDpiAwarenessContext(context)
        if not self.window or not self.popup:
            self.destroy()
            return False
        return True

    def navigation_hint(self):
        # Coalesce Chromium's event burst without waiting for the heartbeat.
        now = monotonic()
        self.next_anchor = min(self.next_anchor, max(now, self.last_anchor_start + NAV_INTERVAL))
        self.nav_until = now + .24

    def anchor_due(self, now):
        return not self.busy and now >= self.next_anchor

    def anchor_wake_delay(self, now):
        if self.busy or now >= self.nav_until:
            return None
        return max(1, round((self.next_anchor - now) * 1000))

    def _follow_chat(self, title):
        follow = getattr(self.monitor, 'follow', None)
        changed = follow(title) if follow else False
        if changed:
            self.summary_session = None
            self.summary_usage.clear()
        if changed and self.open:
            self.page, self.turn, self.offset, self.focus = 'summary', None, 0, None

    def _accept_observation(self, sample, layout, observed):
        found = sample.anchor if isinstance(sample, ComposerSample) else sample
        if isinstance(sample, ComposerSample) and found is NO_MAIN:
            self.new_chat = False
            self.composer_running = False
        elif isinstance(sample, ComposerSample) and sample.running is not None:
            self.composer_running = sample.running
        if isinstance(sample, ComposerSample) and sample.new_chat is True:
            self.new_chat = True
            self.identity_missing_since = None
            self._follow_chat(None)
        elif isinstance(sample, ComposerSample) and sample.chat_title:
            self.new_chat = False
            self.identity_missing_since = None
            self._follow_chat(sample.chat_title)
        elif isinstance(sample, ComposerSample) or found is None:
            if isinstance(sample, ComposerSample) and sample.new_chat is False:
                self.new_chat = False
            # A virtualized sidebar can briefly report no selected row even
            # though the same input is still mounted. Require a sustained miss.
            if self.identity_missing_since is None:
                self.identity_missing_since = observed
            elif observed - self.identity_missing_since >= IDENTITY_GRACE:
                self._follow_chat(None)
        if found is BLOCKED:
            self.absent_since = None
            if self.blocked_since is None:
                self.blocked_since = observed
            elif observed - self.blocked_since >= OBSTRUCTION_GRACE:
                self.anchor = self.anchor_layout = None
        elif found is NO_MAIN:
            # A complete observation found an auxiliary composer only (for
            # example a file/full-screen pane). Remove the old main-chat badge
            # immediately instead of carrying it through the page transition.
            self.absent_since = self.blocked_since = None
            self.anchor = self.anchor_layout = None
        elif found is ABSENT:
            self.blocked_since = None
            if self.absent_since is None:
                self.absent_since = observed
            elif observed - self.absent_since >= ABSENCE_GRACE:
                self.anchor = self.anchor_layout = None
        else:
            self.absent_since = self.blocked_since = None
            if found:
                self.anchor, self.anchor_layout = found, layout

    def _read_anchor(self, now, layout):
        try:
            generation, measured_layout, observed, found = self.result.get_nowait()
        except Empty:
            pass
        else:
            self.busy = False
            if generation == self.generation and measured_layout == layout:
                self._accept_observation(found, layout, observed)
        if self.busy or now < self.next_anchor:
            return
        settling = self.absent_since is not None or self.blocked_since is not None or self.identity_missing_since is not None
        interval = ANCHOR_INTERVAL if self.anchor_layout == layout and not settling else LAYOUT_RETRY_INTERVAL
        self.last_anchor_start = now
        # A brief follow-up catches layout frames that Chromium does not emit.
        self.next_anchor = now + (min(interval, NAV_INTERVAL) if now < self.nav_until else interval)
        self.busy = True
        generation, host = self.generation, self.host

        def scan():
            found = None
            try:
                if self.anchor_reader is None:
                    from composer_anchor import find_composer_anchor
                    found = find_composer_anchor(host, report_blocked=True, read_chat=True)
                else:
                    found = self.anchor_reader(host)
            except Exception:
                pass
            finally:
                # Geometry must never wait for SQLite or rollout parsing.
                self.result.put((generation, layout, monotonic(), found))
                controller = getattr(self.owner, 'controller', None)
                if controller:
                    self.win.u.PostMessageW(controller, ANCHOR_READY, 0, 0)

        try:
            Thread(target=scan, name='composer-anchor', daemon=True).start()
        except RuntimeError:
            self.busy = False

    def view(self):
        config = self.owner.config
        live = config.get('realtimeMetricsEnabled', True) and config.get('activityEnabled', True)
        return self.monitor.view(self.owner.telemetry, self.owner.activity if live else None)

    def _dark(self):
        dark = W.BOOL()
        try:
            get_attribute = C.WinDLL('dwmapi').DwmGetWindowAttribute
            return get_attribute(W.HWND(self.host), 20, C.byref(dark), C.sizeof(dark)) == 0 and bool(dark.value)
        except OSError:
            return False

    def refresh(self, target):
        now = monotonic()
        if not target:
            self.invalidate()
            return
        if target != self.host:
            self.destroy()
            self._follow_chat(None)
            self.host = target
            executable = self.win.process_path(target)
            self.archive = str(Path(executable).with_name('resources') / 'app.asar') if executable else None
        self.suspended = False
        bounds = self._bounds(target)
        if not bounds or not self.win.u.IsWindowVisible(target) or self.win.u.IsIconic(target):
            if self.anchor or self.rect or self.busy:
                self.invalidate()
            return
        dpi = self.win.u.GetDpiForWindow(target) or 96
        scale = dpi / 96
        layout = (bounds, dpi)
        if self.anchor_layout is not None and self.anchor_layout != layout:
            old, old_dpi = self.anchor_layout
            ox, oy = bounds[0] - old[0], bounds[1] - old[1]
            # A same-size move translates the measured anchor immediately.
            # Resizing or changing DPI needs a new toolbar measurement.
            if old_dpi == dpi and tuple(v + (oy if i % 2 else ox) for i, v in enumerate(old)) == bounds:
                self.anchor = tuple(v + (oy if i % 2 else ox) for i, v in enumerate(self.anchor))
                self.anchor_layout = layout
                self.next_anchor = 0
            else:
                self.invalidate()
        self._read_anchor(now, layout)
        self.monitor.poll()
        anchor = self.anchor
        if (not anchor
                or not (bounds[0] <= anchor[0] < anchor[2] <= bounds[2] and bounds[1] <= anchor[1] < anchor[3] <= bounds[3])):
            if self.rect:
                self.hide()
            return
        view = self.view()
        status, state, values = self._summary_content(view)
        width, height = min(68 if status == '就绪' else 256, int((anchor[2] - anchor[0]) / scale)), min(26, int((anchor[3] - anchor[1]) / scale))
        if width < 44 or height < 18:
            self.hide()
            return
        w, h = round(width * scale), round(height * scale)
        x = (anchor[0] + anchor[2] - w) // 2
        y = (anchor[1] + anchor[3] - h) // 2
        rect = x, y, x + w, y + h
        if not self.window and not self._make_windows():
            return
        self.scale = scale
        self.motion = animations_enabled(self.win)
        self.dark = self._dark()
        args = (scale, width, height, *values, self.dark, self.hover_since is not None or self.open,
                self.archive, state in DOT_COLORS)
        style, weight = self.owner.config.get('composerStyle','classic'), self.owner.config.get('composerWeight','normal')
        key = (*args, style, weight, status)
        changed = self.summary_key != key
        if changed:
            surface, self.dot_offset = summary_layout(*args,status,style,weight)
            replacement = LayeredSurface(self.win, surface)
            if self.summary_surface:
                self.summary_surface.close()
            self.summary_surface, self.summary_key = replacement, key
            self.dot_patch = None
            if self.dot_offset is not None:
                size = round(DOT_SIZE * scale)
                top = (h - size) // 2
                self.dot_patch = (surface.crop((self.dot_offset, top, self.dot_offset + size, top + size)),
                                  self.dot_offset, top)
            self.dot_frame = None
        self.rect = rect
        if state != self.dot_state:
            self.dot_state = state
            self.pulse_start = now
        dot_changed = self._update_dot(now)
        if changed or dot_changed or (x, y) != self.summary_position:
            self.summary_surface.present(self.window, position=(x, y))
            self.summary_position = (x, y)
        if not self.win.u.IsWindowVisible(self.window):
            self.win.u.ShowWindow(self.window, 4)  # SW_SHOWNOACTIVATE
        if self.open:
            self._draw_popup(view)
        self._schedule()

    def _summary_content(self, view):
        metrics = view.get('metrics') or {}
        speed = metrics.get('speed', view.get('summary_speed', '—'))
        session = view.get('session')
        if session and session != self.summary_session:
            self.summary_session = session
            self.summary_usage.clear()
        if session:
            for key in ('context', 'cache'):
                if metrics.get(key) not in (None, '', '—'):
                    self.summary_usage[key] = metrics[key]
        context, cache = (metrics.get(key) or '—' for key in ('context', 'cache'))
        if self.summary_session and not self.new_chat:
            context = self.summary_usage.get('context', context)
            cache = self.summary_usage.get('cache', cache)
        active = not self.new_chat and view.get('turn_state') == 'active'
        current_speed = active and is_speed(metrics.get('speed')) and metrics.get('speed_label') != '上次输出速度'
        if (self.composer_running or active) and not current_speed:
            if self.new_chat:
                return '正在启动', 'active', ('—', '—', '—')
            return '思考中', 'active', ('—', context, cache)
        if self.new_chat:
            return '就绪', 'ready', ('—', '—', '—')
        state = 'active' if self.composer_running else self._indicator_state(view)
        return None, state, (speed, context, cache)

    def _indicator_state(self, view):
        if not self.owner.config.get('activityEnabled', True):
            return None
        state = view.get('turn_state')
        if state == 'active':
            return state
        provider = view.get('status_provider')
        if provider:
            # Reuse the provider tile's own health result, not a second set
            # of retry/authorization rules or provider-wide completion flags.
            cached = getattr(self.owner, 'cached_indicator_map', {}).get(provider)
            health_reader = getattr(self.owner, 'chain_health', None)
            health = cached[1][0] if cached else health_reader(provider)[0] if health_reader else None
            if health in ('red', 'amber'):
                return health
        return 'completed' if state == 'completed' else None

    def _update_dot(self, now):
        if not self.dot_patch or not self.dot_state:
            return False
        # Quantize only the opacity, not the geometry or the rest of the text.
        opacity = (.35 + .65 * (.5 + .5 * cos(tau * (now - self.pulse_start) / PULSE_TIME))
                   if self.dot_state == 'active' and self.motion and not self.suspended else .92)
        alpha = min(255, round(opacity * 16) * 16)
        frame = self.dot_state, alpha
        if frame == self.dot_frame:
            return False
        background, x, y = self.dot_patch
        patch = background.copy()
        dot = render_dot(self.scale, self.dark, self.dot_state).copy()
        dot.putalpha(dot.getchannel('A').point(lambda value: value * alpha // 255))
        patch.alpha_composite(dot)
        self.summary_surface.update_region(patch, x, y)
        self.dot_frame = frame
        return True

    def _present_dot(self, now):
        if self.rect and self.summary_surface and self._update_dot(now):
            self.summary_surface.present(self.window)

    def _draw_popup(self, view=None):
        host = self._bounds(self.host)
        if not (self.rect and host and self.popup):
            self.fold()
            return
        if view is None:
            view = self.view()
        if self.page == 'turn' and not any(turn['id'] == self.turn for turn in view.get('turns', ())):
            self.page = 'turns'
        self.offset = min(self.offset, max(0, len(view.get(self.page, ())) - PAGE_SIZE))
        self.card_scale = min(self.scale, (host[2] - host[0]) / (WIDTH + 16))
        key = (self.card_scale, view, self.focus, self.page, self.offset, self.turn, self.archive, self.dark)
        if key != self.card_key:
            surface, self.actions = render_session_card(*key)
            if self.card_surface:
                self.card_surface.close()
            self.card_surface = LayeredSurface(self.win, surface)
            self.card_key = key
            self.popup_size = surface.size
            self.popup_frame = None
        position = popup_position(host, self.rect, self.popup_size, round(4 * self.scale))
        if position is None:
            self.fold()
            return
        self.popup_target = position
        self._present_popup(monotonic())

    def _present_popup(self, now):
        if not self.open or not self.popup_target or not self.card_surface:
            return
        if self.transition:
            start, initial, target = self.transition
            duration = FADE_TIME * abs(target - initial)
            t = min(1., (now - start) / duration) if self.motion and duration else 1.
            self.opacity = initial + (target - initial) * ease(t)
            if t >= 1.:
                self.transition = None
                if not target:
                    self.fold()
                    return
        x, y = self.popup_target
        if self.motion:
            y += round((1 - self.opacity) * POPUP_TRAVEL * self.scale)
        frame = x, y, round(255 * self.opacity)
        if frame != self.popup_frame:
            self.card_surface.present(self.popup, frame[2], (x, y))
            if self.popup_rect is None:
                self.win.u.ShowWindow(self.popup, 4)
            self.popup_rect = x, y, x + self.popup_size[0], y + self.popup_size[1]
            self.popup_frame = frame

    def _open_popup(self, pinned=False):
        self.open = True
        self.pinned = pinned
        self.left_at = None
        self.transition = (monotonic(), self.opacity, 1.) if self.opacity < 1. else None
        self._draw_popup()
        self._schedule()

    def _close_popup(self):
        self.pinned = False
        self.hover_since = self.left_at = None
        self.transition = monotonic(), self.opacity, 0.
        self._present_popup(monotonic())
        self._schedule()

    def _schedule(self):
        pending = bool(self.rect and (self.transition or self.left_at is not None
                      or (not self.open and not self.suppress_hover and self.hover_since is not None)))
        pulsing = bool(self.rect and self.dot_state == 'active' and self.motion and not self.suspended)
        ms = 15 if pending else 50 if pulsing else None
        if ms == self.timer_ms or not self.window:
            return
        if ms is None:
            self.win.u.KillTimer(self.window, MOTION_TIMER)
            self.timer_ms = None
        elif self.win.u.SetTimer(self.window, MOTION_TIMER, ms, None):
            self.timer_ms = ms

    def _hit(self, x, y):
        return next((i for i, (rect, _) in enumerate(self.actions) if inside(rect, x / self.card_scale, y / self.card_scale)), None)

    def poll_pointer(self):
        if not self.rect:
            return
        point = W.POINT()
        if not self.win.u.GetCursorPos(C.byref(point)):
            return
        hit = self.win.u.WindowFromPoint(point)
        self._pointer(hit, point.x, point.y, monotonic())

    def _pointer(self, hit, x, y, now):
        if hit and hit == self.window:
            self.left_at = None
            if not self.suppress_hover:
                if self.hover_since is None:
                    self.hover_since = now
                if ((not self.open and now - self.hover_since >= HOVER_DELAY)
                        or (self.transition and self.transition[2] == 0.)):
                    self._open_popup()
        else:
            self.suppress_hover = False
            self.hover_since = None
            if self.open and hit == self.popup and self.popup_rect:
                self.left_at = None
                if self.transition and self.transition[2] == 0.:
                    self._open_popup()
                if self.popup_rect:
                    index = self._hit(x - self.popup_rect[0], y - self.popup_rect[1])
                    if index != self.focus:
                        self.focus = index
                        self._draw_popup()
            elif self.open and not self.pinned and not (self.transition and self.transition[2] == 0.):
                if self.left_at is None:
                    self.left_at = now
                elif now - self.left_at >= LEAVE_DELAY:
                    self._close_popup()
        self._schedule()

    def handle(self, hwnd, msg, wp, lp):
        if not hwnd or hwnd not in (self.window, self.popup):
            return None
        if msg == 0x21:  # WM_MOUSEACTIVATE
            return 3
        if msg == 0x113 and hwnd == self.window and wp == MOTION_TIMER:
            if self.rect and self.timer_ms is not None:
                self.poll_pointer()
                now = monotonic()
                if self.transition:
                    self._present_popup(now)
                if self.dot_state == 'active':
                    self._present_dot(now)
                self._schedule()
            return 0
        if msg == 0x200 and hwnd in (self.window, self.popup):  # WM_MOUSEMOVE
            if hwnd not in self.pointer_windows:
                track = self.win.u.TrackMouseEvent
                track.argtypes = [C.POINTER(TrackMouse)]
                track.restype = W.BOOL
                request = TrackMouse(C.sizeof(TrackMouse), 2, hwnd, 0)  # TME_LEAVE
                if track(C.byref(request)):
                    self.pointer_windows.add(hwnd)
            self.poll_pointer()
            return 0
        if msg == 0x2A3:  # WM_MOUSELEAVE
            self.pointer_windows.discard(hwnd)
            self.poll_pointer()
            return 0
        if msg == 0x202 and hwnd == self.window:
            if self.pinned:
                self.suppress_hover = True
                self._close_popup()
            else:
                self.suppress_hover = False
                self._open_popup(pinned=True)
            return 0
        if msg == 0x202 and hwnd == self.popup:
            index = self._hit(C.c_short(lp & 65535).value, C.c_short((lp >> 16) & 65535).value)
            if index is not None:
                action, value = self.actions[index][1]
                if action == 'usage_select':
                    self.monitor.select(value)
                    self.page = 'summary'
                elif action == 'usage_turn':
                    self.turn, self.page = value, 'turn'
                else:
                    self.page = value
                self.offset, self.focus = 0, None
                self._draw_popup()
            return 0
        if msg == 0x20A and hwnd == self.popup:
            self.offset = max(0, self.offset + (-1 if C.c_short((wp >> 16) & 65535).value > 0 else 1))
            self.focus = None
            self._draw_popup()
            return 0
        if msg == 0x10:
            self.fold()
            return 0
        return self.win.u.DefWindowProcW(hwnd, msg, wp, lp)
