"""Read selected local Codex usage. Call from one background worker, serially.

No chat text, tool payloads, credentials, or paths are returned.  A loading
snapshot is a prefix of the current rollout; repeat load_session to catch up.
"""

import json
import os
import re
import sqlite3
import unicodedata
from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path


_UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
_ROLLOUT_IDS = re.compile(rf'^rollout-.+-({_UUID})(?:_({_UUID}))?\.jsonl$', re.I)
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_READ_BYTES = 32 * 1024 * 1024
_LINE_BYTES = 1024 * 1024
_INDEX_BYTES = 512 * 1024
_ANCHOR_BYTES = 256
_MAX_SESSIONS = 100
_MAX_TURNS = 100
_MAX_FALLBACK_FILES = 2048
_FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
           'output_tokens', 'reasoning_output_tokens', 'total_tokens')
_START = ('task_started', 'turn_started')
_FINISH = {'task_complete': 'completed', 'turn_complete': 'completed',
           'turn_completed': 'completed', 'turn_aborted': 'aborted'}
_EVENTS = _START + tuple(_FINISH) + ('token_count', 'thread_rolled_back')
_MARKERS = tuple(f'"{kind}"'.encode() for kind in (
    'session_meta', 'turn_context', 'token_usage_record', 'compacted', *_EVENTS))


def normalize_chat_title(title):
    return unicodedata.normalize('NFKC', title).strip().casefold() if isinstance(title, str) else ''


def _object(value):
    return value if isinstance(value, dict) else {}


def _json(raw):
    try:
        return _object(json.loads(raw))
    except (ValueError, RecursionError):
        return {}


def _id(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _label(value):
    return value.strip()[:200] if isinstance(value, str) and value.strip() else None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _usage(value):
    data = _object(value)
    if _count(data.get('total_tokens')) is None:
        return None
    return {key: _count(data.get(key)) for key in _FIELDS}


def _plus(left, right):
    if left is None:
        return right
    return {key: left[key] + right[key] if None not in (left[key], right[key]) else None
            for key in _FIELDS}


def _public_usage(usage):
    if usage is None:
        return None
    result = dict(usage)
    for name, whole, part in (
        ('uncached_input_tokens', 'input_tokens', 'cached_input_tokens'),
        ('non_reasoning_output_tokens', 'output_tokens', 'reasoning_output_tokens'),
    ):
        a, b = usage[whole], usage[part]
        result[name] = a - b if a is not None and b is not None and a >= b else None
    return result


def _time(value, fallback):
    # Codex task event times are Unix seconds. The rollout envelope uses ISO timestamps.
    if _count(value) is not None:
        return value * 1000
    if isinstance(fallback, str):
        try:
            parsed = datetime.fromisoformat(fallback.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                return int(parsed.timestamp() * 1000)
        except (ValueError, OverflowError, OSError):
            pass
    return None


def _estimate(usage):
    # Codex can emit a context-full estimate with only total_tokens populated.
    return (usage is not None and usage['total_tokens'] > 0
            and usage['input_tokens'] == usage['output_tokens'] == 0)


@dataclass
class _Turn:
    id: str
    started_at: int = None
    completed_at: int = None
    status: str = 'unknown'
    usage: dict = None
    structured: bool = False
    observed_tokens: int = 0
    observed_calls: int = 0

    def snapshot(self):
        calls = None
        if self.usage and self.observed_tokens == self.usage['total_tokens']:
            calls = self.observed_calls or None
        return {'id': self.id, 'started_at': self.started_at,
                'completed_at': self.completed_at, 'status': self.status,
                'usage': _public_usage(self.usage), 'calls': calls}


@dataclass
class _Rollout:
    thread_id: str
    path: Path
    file_id: tuple
    mtime_ns: int
    size: int
    offset: int = 0
    head: bytes = b''
    tail: bytes = b''
    pending: bytearray = field(default_factory=bytearray)
    discarding: bool = False
    verified: bool = False
    foreign: bool = False
    history_complete: bool = True
    metadata: dict = field(default_factory=dict)
    latest: dict = None
    structured_total: dict = None
    legacy_total: dict = None
    legacy_last: dict = None
    context_window: int = None
    context_used: int = None
    active_turn_id: str = None
    turns: OrderedDict = field(default_factory=OrderedDict)
    response_ids: OrderedDict = field(default_factory=OrderedDict)
    waiting_for_context: bool = False

    def _turn(self, turn_id):
        if turn_id not in self.turns:
            self.turns[turn_id] = _Turn(turn_id)
            if len(self.turns) > _MAX_TURNS:
                self.turns.popitem(last=False)
        return self.turns[turn_id]

    def _record(self, payload, *, seed=False):
        if payload.get('thread_id') != self.thread_id:
            return
        latest = _usage(payload.get('usage'))
        total = _usage(payload.get('thread_token_usage'))
        if latest is None or total is None:
            return
        response_id = _id(payload.get('response_id'))
        if response_id in self.response_ids:
            return
        if self.structured_total is not None and (
            total['total_tokens'] < self.structured_total['total_tokens']
            or seed
        ):
            return
        if response_id:
            self.response_ids[response_id] = None
            if len(self.response_ids) > 256:
                self.response_ids.popitem(last=False)
        self.foreign = False
        self.latest, self.structured_total = latest, total
        self.context_used = latest['total_tokens']
        self.waiting_for_context = True
        turn_id = _id(payload.get('turn_id'))
        per_turn = _usage(payload.get('turn_token_usage'))
        if turn_id and per_turn is not None:
            turn = self._turn(turn_id)
            if not turn.structured:
                turn.observed_tokens = turn.observed_calls = 0
            turn.usage, turn.structured = per_turn, True
            if not seed:
                turn.observed_tokens += latest['total_tokens']
                turn.observed_calls += 1

    def _event(self, payload, timestamp):
        kind = payload.get('type')
        turn_id = _id(payload.get('turn_id'))
        window = _count(payload.get('model_context_window'))
        if kind == 'token_count':
            info = _object(payload.get('info'))
            self.context_window = _count(info.get('model_context_window')) or self.context_window
            latest, total = (_usage(info.get(key)) for key in ('last_token_usage', 'total_token_usage'))
            if latest is None:
                return
            if self.waiting_for_context or self.structured_total is None or _estimate(latest):
                self.context_used = latest['total_tokens']
                self.waiting_for_context = False
            if self.structured_total is not None or _estimate(latest) or _estimate(total):
                return
            # Rate-limit updates can replay the previous usage, including across turns.
            if (total, latest) == (self.legacy_total, self.legacy_last):
                return
            if total is not None and total == self.legacy_total:
                return
            self.latest = latest
            self.legacy_total, self.legacy_last = total, latest
            if self.active_turn_id:
                turn = self._turn(self.active_turn_id)
                turn.usage = _plus(turn.usage, latest)
                turn.observed_tokens += latest['total_tokens']
                turn.observed_calls += 1
        elif kind in _START and turn_id:
            self._start(turn_id, _time(payload.get('started_at'), timestamp))
            self.context_window = window or self.context_window
        elif isinstance(kind, str) and kind in _FINISH:
            turn_id = turn_id or self.active_turn_id
            if turn_id:
                turn = self._turn(turn_id)
                if turn.started_at is None:
                    turn.started_at = _time(payload.get('started_at'), None)
                turn.completed_at = _time(payload.get('completed_at', payload.get('aborted_at')), timestamp)
                turn.status = _FINISH[kind]
                if turn.status == 'completed' and payload.get('error'):
                    turn.status = 'failed'
                if turn_id == self.active_turn_id:
                    self.active_turn_id = None
        elif kind == 'thread_rolled_back':
            count = _count(payload.get('num_turns')) or 0
            for _ in range(min(count, len(self.turns))):
                self.turns.popitem(last=True)
            if count:
                self.active_turn_id = None

    def _start(self, turn_id, started_at):
        if self.active_turn_id and self.active_turn_id != turn_id:
            active = self.turns.get(self.active_turn_id)
            if active is not None:
                active.status = 'unknown'
            self.active_turn_id = None
        turn = self._turn(turn_id)
        turn.started_at = turn.started_at if turn.started_at is not None else started_at
        if turn.status not in ('completed', 'aborted', 'failed'):
            turn.status = 'active'
            self.active_turn_id = turn_id

    def apply(self, raw):
        if not any(marker in raw for marker in _MARKERS):
            return
        item = _json(raw)
        if not item:
            self.history_complete = False
            return
        kind, payload = item.get('type'), _object(item.get('payload'))
        timestamp = item.get('timestamp')
        if kind == 'session_meta':
            self.foreign = payload.get('id') != self.thread_id
            if not self.foreign:
                self.verified = True
                self.history_complete &= not bool(payload.get('history_base'))
                self.metadata.update({key: value for key, value in (
                    ('innerId', _id(payload.get('session_id'))),
                    ('provider', _label(payload.get('model_provider'))),
                    ('model', _label(payload.get('model'))),
                ) if value is not None})
                self.context_window = _count(payload.get('context_window')) or self.context_window
        elif self.verified:
            if kind == 'token_usage_record':
                self._record(payload)
            elif not self.foreign:
                if kind == 'turn_context':
                    model = _label(payload.get('model'))
                    if model:
                        self.metadata['model'] = model
                    turn_id = _id(payload.get('turn_id'))
                    if turn_id and turn_id not in self.turns:
                        self._start(turn_id, _time(None, timestamp))
                elif kind == 'event_msg':
                    owner = payload.get('thread_id')
                    if owner is None or owner == self.thread_id:
                        self._event(payload, timestamp)
                elif kind == 'compacted':
                    self._record(_object(payload.get('latest_token_usage_record')), seed=True)

    def read(self, stream):
        stream.seek(self.offset)
        end = min(self.size, self.offset + _READ_BYTES)
        while self.offset < end:
            raw = stream.readline(min(end - self.offset, _LINE_BYTES + 1))
            if not raw:
                break
            self.offset += len(raw)
            terminated = raw.endswith(b'\n')
            if not self.discarding:
                if len(self.pending) + len(raw) > _LINE_BYTES:
                    if any(marker in self.pending[:2048] or marker in raw[:2048]
                           for marker in _MARKERS):
                        self.history_complete = False
                    self.pending.clear()
                    self.discarding = True
                elif terminated:
                    self.pending.extend(raw)
                    self.apply(self.pending)
                    self.pending.clear()
                else:
                    self.pending.extend(raw)
            if terminated:
                self.discarding = False
        stream.seek(0)
        self.head = stream.read(min(self.offset, _ANCHOR_BYTES))
        stream.seek(self.offset - min(self.offset, _ANCHOR_BYTES))
        self.tail = stream.read(min(self.offset, _ANCHOR_BYTES))


class SessionUsageAdapter:
    """Use CODEX_HOME by default. Methods are blocking, bounded and not thread-safe.

    Call from the same background worker. Catalog IDs are stable Codex thread
    IDs. innerId identifies the current rollout generation, when available.
    Timestamps are Unix milliseconds. Values unavailable are None.
    """

    def __init__(self, codex_home=None):
        self.home = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex').resolve()
        self._roots = (self.home / 'sessions', self.home / 'archived_sessions')
        self._entries = OrderedDict()
        self._rollouts = OrderedDict()
        self._index_state = None

    def _database(self, limit, thread_id=None, visible_title=None):
        db = self.home / 'state_5.sqlite'
        wal, shm = (db.with_name(db.name + suffix) for suffix in ('-wal', '-shm'))
        try:
            if not db.is_file():
                return None
            # With no live sidecars, immutable mode avoids creating them.
            live = wal.is_file() and shm.is_file()
            if not live and wal.exists() and wal.stat().st_size:
                return None
            uri = db.as_uri() + ('?mode=ro' if live else '?mode=ro&immutable=1')
            with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as conn:
                columns = {row[1] for row in conn.execute('PRAGMA table_info(threads)')}
                if not {'id', 'rollout_path'}.issubset(columns):
                    return None
                if visible_title is not None:
                    title_columns = [key for key in ('name', 'title') if key in columns]
                    if not title_columns:
                        return None
                    conn.create_function('normalized_title', 1, normalize_chat_title, deterministic=True)
                    predicates = ' OR '.join(f'normalized_title({key}) = ?' for key in title_columns)
                    # Search all titles, including old records, so a duplicate can
                    # never silently resolve to a more recently used conversation.
                    return conn.execute(f'SELECT id FROM threads WHERE {predicates} LIMIT 2',
                                        (visible_title,) * len(title_columns)).fetchall()
                provider = 'model_provider' if 'model_provider' in columns else 'NULL'
                model = 'model' if 'model' in columns else 'NULL'
                dates = [expr for key, expr in (('recency_at_ms', 'recency_at_ms'),
                                                ('updated_at_ms', 'updated_at_ms'),
                                                ('updated_at', 'updated_at * 1000')) if key in columns]
                update_expr = f'COALESCE({", ".join(dates)})' if len(dates) > 1 else next(iter(dates), 'NULL')
                # Native title is often a verbatim prefix of the first user message.
                title = 'name' if 'name' in columns else 'NULL'
                if {'title', 'first_user_message'}.issubset(columns):
                    title = f'''COALESCE(NULLIF({title}, ''), CASE WHEN
                        first_user_message <> '' AND instr(lower(first_user_message),
                        lower(rtrim(trim(title), '.… '))) = 0 THEN title END)'''
                select = f'SELECT id, rollout_path, {title}, {provider}, {model}, {update_expr} FROM threads'
                if thread_id is not None:
                    return conn.execute(select + ' WHERE id = ?', (thread_id,)).fetchall()
                filters = ['archived = 0'] if 'archived' in columns else []
                if 'attached_thread_parent_id' in columns:
                    filters.append('attached_thread_parent_id IS NULL')
                if 'source' in columns:
                    filters.append('COALESCE(source, \'\') NOT LIKE \'%"subagent"%\'')
                if filters:
                    select += ' WHERE ' + ' AND '.join(filters)
                if dates:
                    select += f' ORDER BY {dates[0]} DESC, id DESC'
                return conn.execute(select + ' LIMIT ?', (limit,)).fetchall()
        except (OSError, sqlite3.Error):
            return None

    def _index(self):
        try:
            with (self.home / 'session_index.jsonl').open('rb') as stream:
                stat = os.fstat(stream.fileno())
                size = stat.st_size
                signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, size)
                state = self._index_state
                if state and signature == state[0]:
                    return state[2]
                start = max(0, size - _INDEX_BYTES)
                result = OrderedDict()
                if state and signature[:2] == state[0][:2] and start <= state[1] < size and size > state[0][3]:
                    start = state[1]
                    stream.seek(start - len(state[3]))
                    if stream.read(len(state[3])) == state[3]:
                        result = state[2]
                    else:
                        start = max(0, size - _INDEX_BYTES)
                stream.seek(start)
                if start and not result:
                    stream.seek(start - 1)
                    if stream.read(1) != b'\n':
                        start += len(stream.readline(_INDEX_BYTES))
                raw = stream.read(_INDEX_BYTES)
                complete = raw.rfind(b'\n') + 1
                end = start + complete
                stream.seek(max(0, end - _ANCHOR_BYTES))
                anchor = stream.read(min(end, _ANCHOR_BYTES))
        except OSError:
            return self._index_state[2] if self._index_state else {}
        for line in raw[:complete].splitlines():
            item = _json(line)
            if _id(item.get('id')):
                result[item['id']] = _label(item.get('thread_name'))
                result.move_to_end(item['id'])
                if len(result) > _MAX_FALLBACK_FILES:
                    result.popitem(last=False)
        self._index_state = signature, end, result, anchor
        return result

    def _fallback(self, limit, thread_id=None):
        names = self._index()
        found = {}
        seen = 0
        today = date.today()
        for days_ago in range(15):
            day = today - timedelta(days=days_ago - 1)
            folder = self._roots[0] / day.strftime('%Y/%m/%d')
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        seen += 1
                        ids = _ROLLOUT_IDS.fullmatch(entry.name)
                        if ids and (thread_id is None or ids[1] == thread_id):
                            try:
                                mtime = entry.stat(follow_symlinks=False).st_mtime_ns
                            except OSError:
                                continue
                            if ids[1] not in found or mtime > found[ids[1]][0]:
                                found[ids[1]] = (mtime, entry.path)
                        if seen >= _MAX_FALLBACK_FILES:
                            break
            except OSError:
                continue
            if seen >= _MAX_FALLBACK_FILES:
                break
        ordered = sorted(found.items(), key=lambda item: item[1], reverse=True)[:limit]
        return [(key, path, names.get(key), None, None, mtime // 1_000_000)
                for key, (mtime, path) in ordered]

    def _entry(self, row):
        thread_id, filename, title, provider, model, updated = row
        if not _id(thread_id) or not isinstance(filename, str) or not filename:
            return None
        try:
            path = (self.home / filename).resolve()
        except (OSError, ValueError, RuntimeError):
            return None
        if path.suffix.lower() != '.jsonl' or not any(path.is_relative_to(root) for root in self._roots):
            return None
        ids = _ROLLOUT_IDS.fullmatch(path.name)
        inner_id = ids[2] if ids and ids[1] == thread_id else None
        session = {'id': thread_id, 'innerId': inner_id or thread_id, 'title': _label(title),
                   'provider': _label(provider), 'model': _label(model), 'updated_at': _count(updated)}
        self._entries[thread_id] = (path, session)
        self._entries.move_to_end(thread_id)
        if len(self._entries) > _MAX_SESSIONS:
            self._entries.popitem(last=False)
        return path, session

    def list_recent_sessions(self, limit=20):
        """Return recent local candidates without scanning rollout bodies."""
        limit = max(0, min(limit, _MAX_SESSIONS))
        if not limit:
            return []
        rows = self._database(limit)
        if rows is None or not rows:
            rows = self._fallback(limit)
        entries = [entry for row in rows if (entry := self._entry(row)) is not None]
        return [dict(session) for _, session in entries]

    def identify_visible_chat(self, title):
        normalized = normalize_chat_title(title)
        rows = self._database(2, visible_title=normalized) if normalized else None
        return rows[0][0] if rows and len(rows) == 1 and _id(rows[0][0]) else None

    def load_session(self, thread_id, *, recent_turns=10):
        """Read one stable ID. Return None if unknown, or a JSON-like snapshot.

        status is ready, loading (more complete bytes to scan) or unavailable
        (missing, unreadable or mismatched rollout). history_complete describes
        the visible timeline, which may start after rotation or compaction.
        """
        if not _id(thread_id):
            return None
        rows = self._database(1, thread_id)
        entry = self._entry(rows[0]) if rows else None
        if not entry:
            fallback = self._fallback(1, thread_id)
            entry = self._entry(fallback[0]) if fallback else None
        if not entry and rows is None:
            entry = self._entries.get(thread_id)
        if not entry:
            return None
        path, session = entry
        state = self._rollouts.get(thread_id)
        if state is not None and state.path != path:
            self._rollouts.pop(thread_id)
            state = None
        status = 'unavailable'
        try:
            with path.open('rb') as stream:
                stat = os.fstat(stream.fileno())
                file_id = (stat.st_dev, stat.st_ino)
                replaced = state is None or state.file_id != file_id or stat.st_size < state.size
                if not replaced and (stat.st_size, stat.st_mtime_ns) != (state.size, state.mtime_ns):
                    replaced = stat.st_size == state.size or stream.read(len(state.head)) != state.head
                    if not replaced:
                        stream.seek(state.offset - len(state.tail))
                        replaced = stream.read(len(state.tail)) != state.tail
                if replaced:
                    state = _Rollout(thread_id, path, file_id, stat.st_mtime_ns, stat.st_size)
                state.size, state.mtime_ns = stat.st_size, stat.st_mtime_ns
                if state.offset < state.size:
                    state.read(stream)
                self._rollouts[thread_id] = state
                self._rollouts.move_to_end(thread_id)
                if len(self._rollouts) > 4:
                    self._rollouts.popitem(last=False)
                if state.offset < state.size or (not state.verified and (not state.size or state.pending)):
                    status = 'loading'
                elif state.verified:
                    status = 'ready'
        except OSError:
            pass
        if state is None or not state.verified:
            return {'session': dict(session), 'latest': None, 'total': None, 'context_window': None,
                    'context_used': None, 'active_turn_id': None, 'turns': [], 'status': status,
                    'history_complete': False}
        session = dict(session)
        session.update({key: value for key, value in state.metadata.items()
                        if key != 'innerId' or session['innerId'] == thread_id})
        limit = max(0, min(recent_turns, _MAX_TURNS))
        turns = list(reversed(state.turns.values()))[:limit]
        return {'session': session, 'latest': _public_usage(state.latest),
                'total': _public_usage(state.structured_total or state.legacy_total),
                'context_window': state.context_window, 'context_used': state.context_used,
                'active_turn_id': state.active_turn_id, 'turns': [turn.snapshot() for turn in turns],
                'status': status, 'history_complete': status == 'ready' and state.history_complete}
