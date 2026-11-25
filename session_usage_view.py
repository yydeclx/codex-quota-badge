"""Attach timing only to an identified chat and a matching turn."""
from collections import OrderedDict
from datetime import datetime
import json
import re
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic

from live_metrics import conversation_matches, nonnegative, request_metrics
from sidebar_data import compact_tokens
from sidebar_telemetry import provider_name
from session_usage import SessionUsageAdapter, normalize_chat_title


def rate(numerator, denominator):
    if nonnegative(numerator) and nonnegative(denominator) and denominator > 0 and numerator <= denominator:
        return numerator / denominator
    return None


def percentage(value, precision=1):
    return '—' if value is None else f'{value * 100:.{precision}f}%'


def tokens(value):
    return f'{value:,}' if nonnegative(value) else '—'


def seconds(milliseconds):
    return f'{milliseconds / 1000:.1f}s' if nonnegative(milliseconds) else '—'


def measured_timing(row, speed=None):
    first, last = row.get('firstOutputMs'), row.get('lastOutputMs')
    stream = last - first if nonnegative(first) and nonnegative(last) and last >= first else None
    return {'first_ms': first if nonnegative(first) else None, 'stream_ms': stream,
            'duration_ms': row.get('durationMs') if nonnegative(row.get('durationMs')) else None,
            'speed_value': speed if nonnegative(speed) and speed > 0 else None}


def turn_timing(session, turn, logs, live=None):
    start, end = turn.get('started_at'), turn.get('completed_at')
    if not nonnegative(start):
        return {}
    active = turn.get('status') == 'active' and end is None

    def matches(row, field):
        stamp = row.get(field)
        return (conversation_matches(row.get('conversationId'), session) and nonnegative(stamp)
                and stamp >= start and (active or (nonnegative(end) and stamp < end)))

    entries = [row for row in logs if matches(row, 'timestamp')]
    # Request logs timestamp the start, while completedAt in Codex is the
    # end of the user turn. Match by identity and time, never by provider alone.
    last = max(entries, key=lambda row: row['timestamp'], default={})
    timing = {}
    if last:
        speed = last.get('decodeTokPerSecond')
        timing = {'speed': f'{speed:.0f} tok/s' if nonnegative(speed) and speed else '—',
                  'first': seconds(last.get('firstOutputMs')), 'duration': seconds(last.get('durationMs')),
                  'start': last['timestamp'], **measured_timing(last, speed)}
        first, last_output = last.get('firstOutputMs'), last.get('lastOutputMs')
        if nonnegative(first) and nonnegative(last_output) and last_output >= first:
            timing['stream'] = seconds(last_output - first)
    if active and live:
        details = {**live, **{field: [row for row in live.get(field, ()) if matches(row, 'startedAt')]
                            for field in ('requests', 'finishedRequests')}}
        progress = request_metrics(details)
        if progress and progress['request']['startedAt'] >= timing.get('start', 0):
            row = progress['request']
            speed = progress['output']
            if not row.get('estimated', True) and speed.startswith('约 '):
                speed = speed[2:]
            timing = {'speed': speed, 'first': progress['first'], 'duration': seconds(row.get('durationMs')),
                      'start': row['startedAt'], **measured_timing(row)}
            first = row.get('firstOutputMs')
            last_output = row.get('lastOutputMs')
            if nonnegative(first) and nonnegative(last_output) and last_output >= first:
                timing['stream'] = seconds(last_output - first)
    return timing


def is_speed(value):
    return isinstance(value, str) and re.fullmatch(r'(?:约 )?[0-9]+ tok/s', value) is not None


def last_completed_speed(session, logs):
    # Local request logs may include aborted requests, tool handoffs and other chats.
    latest = max((row for row in logs if row.get('outcome') == 'completed'
                  and conversation_matches(row.get('conversationId'), session)
                  and nonnegative(row.get('timestamp')) and nonnegative(row.get('decodeTokPerSecond'))
                  and row['decodeTokPerSecond'] > 0), key=lambda row: row['timestamp'], default=None)
    return f"{latest['decodeTokPerSecond']:.0f} tok/s" if latest else '—'


def input_line(usage):
    return f"{tokens(usage.get('input_tokens'))} · 缓存 {tokens(usage.get('cached_input_tokens'))}"


def output_line(usage):
    return f"{tokens(usage.get('output_tokens'))} · 推理 {tokens(usage.get('reasoning_output_tokens'))}"


def breakdown(usage, whole_key, part_key):
    whole, part = usage.get(whole_key), usage.get(part_key)
    if nonnegative(whole) and nonnegative(part) and part <= whole:
        return whole - part, part
    return None, None


def summary_metrics(latest, total, turn, timing, window, used, previous_speed='—'):
    context_ratio = min(1, used / window) if nonnegative(used) and nonnegative(window) and window else None
    cache_ratio = rate(total.get('cached_input_tokens'), total.get('input_tokens'))
    last_cache = rate(latest.get('cached_input_tokens'), latest.get('input_tokens'))
    reasoning_ratio = rate(total.get('reasoning_output_tokens'), total.get('output_tokens'))
    round_usage = turn.get('usage') or {}
    request_count = turn.get('calls')
    round_summary = f"输出 {tokens(round_usage.get('output_tokens'))}"
    if request_count is not None:
        round_summary = f'{request_count} 次调用 · {round_summary}'
    speed = timing.get('speed')
    use_previous = not is_speed(speed) and is_speed(previous_speed)
    return {
        'speed': previous_speed if use_previous else speed if is_speed(speed) else '—',
        'speed_label': '上次输出速度' if use_previous else '生成速度', 'timing': '首字 ' + timing.get('first', '—'),
        'context': percentage(context_ratio, 0), 'remaining': f'剩余 {compact_tokens(max(0, window - used))}' if context_ratio is not None else '—',
        'cache': percentage(cache_ratio), 'last_cache': f'最近调用 {percentage(last_cache)}',
        'context_total': f'{tokens(used)} / {compact_tokens(window)}', 'context_ratio': context_ratio or 0,
        'last_input': input_line(latest), 'last_output': output_line(latest), 'round': round_summary,
        'total_input': input_line(total), 'total_output': output_line(total),
        'cache_ratio': cache_ratio or 0, 'uncached_ratio': 1 - cache_ratio if cache_ratio is not None else 0,
        'reasoning_ratio': reasoning_ratio or 0, 'other_output_ratio': 1 - reasoning_ratio if reasoning_ratio is not None else 0,
        'detail': {
            'speed_value': timing.get('speed_value'),
            'duration_ms': timing.get('duration_ms'), 'first_ms': timing.get('first_ms'),
            'stream_ms': timing.get('stream_ms'),
            'used': used, 'window': window,
            'context_input': breakdown(latest, 'input_tokens', 'cached_input_tokens')
            if used is not None and used == latest.get('total_tokens') else (None, None),
            'step_output': latest.get('output_tokens'),
            'step_split': breakdown(latest, 'output_tokens', 'reasoning_output_tokens'),
            'input_total': total.get('input_tokens'),
            'input_split': breakdown(total, 'input_tokens', 'cached_input_tokens'),
            'output_total': total.get('output_tokens'),
            'output_split': breakdown(total, 'output_tokens', 'reasoning_output_tokens'),
        },
    }


def turn_row(turn, timing):
    usage = turn.get('usage') or {}
    status = {'active': '运行中', 'completed': '已完成', 'aborted': '已取消', 'failed': '失败'}.get(turn.get('status'), '本机记录')
    start, end = turn.get('started_at'), turn.get('completed_at')
    clock = datetime.fromtimestamp(start / 1000).strftime('%m/%d %H:%M') if nonnegative(start) else '时间未知'
    duration = seconds(end - start) if nonnegative(start) and nonnegative(end) and end >= start else '—'
    return {'id': turn['id'], 'title': f'{clock} · {status}',
            'detail': f"输出 {compact_tokens(usage.get('output_tokens'))} · {timing.get('speed', '—')}",
            'metrics': [('输入 Token', input_line(usage)), ('未命中输入 / 缓存读取', f"{tokens(usage.get('uncached_input_tokens'))} / {tokens(usage.get('cached_input_tokens'))}"),
                        ('输出 Token', output_line(usage)), ('整轮耗时', duration),
                        ('最近调用首字', timing.get('first', '—')), ('最近调用输出速度', timing.get('speed', '—')),
                        ('最近调用耗时 / 流式', f"{timing.get('duration', '—')} / {timing.get('stream', '—')}")]}


def format_view(catalog, snapshot, requested, telemetry, activity):
    sessions = []
    for row in catalog:
        updated = row.get('updated_at')
        date = None
        if nonnegative(updated):
            # Older state databases index in seconds, newer ones in milliseconds.
            try:
                date = datetime.fromtimestamp(updated / 1000 if updated >= 100_000_000_000 else updated).strftime('%m/%d %H:%M')
            except (ValueError, OSError, OverflowError):
                pass
        detail = ' · '.join(filter(None, ('当前展示' if row['id'] == requested else None,
                                           date, row.get('model') or row.get('provider'))))
        sessions.append({'id': row['id'], 'title': row.get('title') or '未命名会话', 'detail': detail})
    view = {'sessions': sessions}
    if not snapshot or snapshot.get('status') != 'ready':
        # A delayed or unavailable rollout should not hide known request timing.
        identity = snapshot['session'] if snapshot else {'id': requested}
        if telemetry and (identity.get('id') or identity.get('innerId')):
            view['summary_speed'] = last_completed_speed(identity, telemetry.session_samples(identity))
    if not snapshot:
        if requested:
            view['message'] = '正在读取，或该会话的本机记录不可用'
        return view
    session = snapshot['session']
    view['session'] = session['id']
    model = session.get('model') or ''
    route = model.split('/', 1)[0] if '/' in model else session.get('provider')
    if route:
        view['status_provider'] = provider_name(route)

    view['title'] = session.get('title') or '未命名会话'
    subtitle = [session.get('model') or session.get('provider'), '本机会话']
    if snapshot['status'] != 'ready':
        view['message'] = '正在读取本机用量' if snapshot['status'] == 'loading' else '本机用量记录暂不可用'
        view['subtitle'] = ' · '.join(filter(None, subtitle))
        return view
    if not snapshot.get('history_complete', True):
        subtitle.append('历史记录可能不全')
    view['subtitle'] = ' · '.join(filter(None, subtitle))
    logs = telemetry.session_samples(session) if telemetry else ()
    live = activity.details(None) if activity else None
    turns = snapshot.get('turns') or ()
    timing = {turn['id']: turn_timing(session, turn, logs, live) for turn in turns}
    view['turns'] = [turn_row(turn, timing[turn['id']]) for turn in turns]
    turn = next((row for row in turns if row['id'] == snapshot.get('active_turn_id')), turns[0] if turns else {})
    # Use this chat's latest turn only, regardless of the provider's other work.
    turn_state = turn.get('status')
    if turn_state in ('active', 'completed'):
        view['turn_state'] = turn_state
    current_timing = timing.get(turn.get('id'), {})
    previous_speed = last_completed_speed(session, logs)
    if snapshot.get('latest') or snapshot.get('total') or current_timing or is_speed(previous_speed):
        view['metrics'] = summary_metrics(snapshot.get('latest') or {}, snapshot.get('total') or {}, turn, current_timing,
                                          snapshot.get('context_window'), snapshot.get('context_used'), previous_speed)
    else:
        view['message'] = '等待该会话的第一条用量记录'
    return view


class SessionUsageMonitor:
    """One local worker, with a generation check when the selection changes."""
    def __init__(self, reader=None, selection_path=None, clock=monotonic, follow_visible=False, on_result=None):
        self.reader = reader or SessionUsageAdapter()
        self.follow_visible = follow_visible
        self.on_result = on_result
        self.visible_title = None
        self.visible_key = None
        self.selection_path = selection_path
        self.clock = clock
        self.snapshot = None
        self.ready_snapshots = OrderedDict()
        self.handoff_reader = None
        self.visible_identity_checked = False
        self.catalog = []
        self.requested = None
        if selection_path is not None and not follow_visible:
            try:
                value = json.loads(selection_path.read_text(encoding='utf-8'))
                if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
                    self.requested = value
            except (ValueError, OSError):
                pass
        self.generation = 0
        self.next_poll = 0
        self.next_catalog = 0
        self.busy = False
        self.results = SimpleQueue()

    def prepare_follow(self, title, previous_key):
        """Verify and load the new selection before publishing a new toolbar frame.

        Called only by the single anchor worker; use its own incremental reader
        rather than sharing the normal usage worker's file offsets.
        """
        key = normalize_chat_title(title)
        if not key or key == previous_key:
            return None
        if self.handoff_reader is None:
            self.handoff_reader = SessionUsageAdapter(self.reader.home)
        identity = self.handoff_reader.identify_visible_chat(title)
        if not identity:
            return None, None
        snapshot = self.handoff_reader.load_session(identity, recent_turns=25)
        deadline = monotonic() + .12
        for _ in range(3):
            if not snapshot or snapshot.get('status') != 'loading' or monotonic() >= deadline:
                break
            snapshot = self.handoff_reader.load_session(identity, recent_turns=25)
        return identity, snapshot

    def follow(self, title, prepared=None):
        if not self.follow_visible:
            return False
        key = normalize_chat_title(title) or None
        if key == self.visible_key:
            return False
        self.visible_key, self.visible_title = key, title if key else None
        self.requested = self.snapshot = None
        self.generation += 1
        self.next_poll = 0
        self.visible_identity_checked = prepared is not None
        if prepared is not None:
            self._accept_visible_snapshot(*prepared)
        return True

    def select(self, identity):
        if identity == self.requested:
            return
        self.requested = identity
        self.snapshot = None
        self.generation += 1
        self.next_poll = 0
        if self.selection_path is not None and not self.follow_visible:
            from sidebar_modules import atomic_json
            try:
                atomic_json(self.selection_path, identity)
            except OSError:
                pass

    def _accept_visible_snapshot(self, identity, snapshot):
        # Only consult cache after the visible title has freshly resolved to
        # one exact chat ID. Never fill a new chat with the old chat's data.
        self.requested = identity
        if not identity or (snapshot and snapshot.get('session', {}).get('id') != identity):
            self.snapshot = None
            return
        if snapshot and snapshot.get('status') == 'ready':
            self.ready_snapshots[identity] = (self.clock(), snapshot)
            self.ready_snapshots.move_to_end(identity)
            while len(self.ready_snapshots) > 8:
                self.ready_snapshots.popitem(last=False)
            self.snapshot = snapshot
            return
        cached = self.ready_snapshots.get(identity)
        self.snapshot = cached[1] if cached and self.clock() - cached[0] <= 8 else snapshot
        if snapshot and snapshot.get('status') == 'loading':
            if self.next_poll > 0:
                self.next_poll = min(self.next_poll, self.clock() + .20)

    def poll(self):
        try:
            result = self.results.get_nowait()
            generation, catalog, identity, snapshot = result[:4]
        except Empty:
            pass
        else:
            self.busy = False
            if catalog is not None:
                self.catalog = catalog
            if generation == self.generation:
                if self.follow_visible and len(result) == 5:
                    self._accept_visible_snapshot(identity, snapshot)
                elif identity == self.requested:
                    self.snapshot = snapshot
        now = self.clock()
        if self.busy or now < self.next_poll:
            return
        refresh_catalog = now >= self.next_catalog
        if self.follow_visible and not self.visible_title:
            return
        if not refresh_catalog and not self.requested and not self.visible_title:
            return
        if refresh_catalog:
            self.next_catalog = now + 6
        self.next_poll = now + 1
        generation, identity = self.generation, self.requested
        title = self.visible_title
        self.busy = True
        handoff = self.visible_identity_checked
        self.visible_identity_checked = False

        def refresh():
            found, snapshot = None, None
            resolved = identity
            try:
                if self.follow_visible and not handoff:
                    resolved = self.reader.identify_visible_chat(title) if title else None
                if resolved:
                    snapshot = self.reader.load_session(resolved, recent_turns=25)
                    # Large rollout files are scanned in bounded chunks. Give
                    # the worker a short burst rather than one chunk per second.
                    if self.follow_visible:
                        deadline = monotonic() + .16
                        for _ in range(3):
                            if (not snapshot or snapshot.get('status') != 'loading'
                                    or monotonic() >= deadline or generation != self.generation):
                                break
                            snapshot = self.reader.load_session(resolved, recent_turns=25)
                if refresh_catalog and not self.follow_visible:
                    found = self.reader.list_recent_sessions()
            except (OSError, ValueError, RuntimeError):
                pass
            finally:
                result = (generation, found, resolved, snapshot)
                self.results.put(result + (True,) if self.follow_visible else result)
                if self.on_result:
                    self.on_result()

        try:
            Thread(target=refresh, name='session-usage', daemon=True).start()
        except RuntimeError:
            self.busy = False

    def tile(self):
        snapshot = self.snapshot or {}
        if snapshot.get('status') != 'ready':
            return '—'
        used, window = snapshot.get('context_used'), snapshot.get('context_window')
        ratio = min(1, used / window) if nonnegative(used) and nonnegative(window) and window else None
        return percentage(ratio, 0)

    def view(self, telemetry, activity=None):
        view = format_view(self.catalog, self.snapshot, self.requested, telemetry, activity)
        if self.follow_visible:
            view['follow_visible'] = True
            if not self.snapshot:
                view['message'] = ('正在读取当前会话用量' if self.requested or self.busy and self.visible_title
                                   else '暂无法确认当前会话')
        return view
