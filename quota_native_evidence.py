"""Bounded, read-only native quota observations; never collect prompts or tokens.

Creator identity plus a matching quota window is supporting evidence, not a
per-request billing assertion. Results are intentionally incomplete when bounded.
"""
from collections import OrderedDict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time


MAX_FILES = 16
HEAD_BYTES = 64 * 1024
TAIL_BYTES = 512 * 1024
TOTAL_BYTES = 4 * 1024 * 1024
TIME_BUDGET = .5
WEEK_SECONDS = 10080 * 60
RESET_JITTER = 120
_FILE_CACHE = OrderedDict()
_PATH_CACHE = OrderedDict()


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _signature(path):
    try:
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns)
    except OSError:
        return None


def _remember(cache, key, value):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > 32:
        cache.popitem(last=False)


def _candidate_paths(home, deadline):
    """Use the existing native index, including its WAL; never create/rebuild it."""
    database = home / 'state_5.sqlite'
    signature = (_signature(database), _signature(home / 'state_5.sqlite-wal'))
    if signature[0] is None:
        return ()
    key = str(database)
    cached = _PATH_CACHE.get(key)
    if cached and cached[0] == signature:
        return cached[1]
    if time.monotonic() >= deadline:
        return ()
    connection = None
    try:
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True,
                                     timeout=min(.01, max(0, deadline-time.monotonic())))
        connection.execute('PRAGMA query_only=ON')
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        columns = {row[1] for row in connection.execute('PRAGMA table_info(threads)')}
        if not {'rollout_path', 'updated_at'} <= columns:
            return ()
        rows = connection.execute(
            'SELECT rollout_path FROM threads ORDER BY updated_at DESC LIMIT 16').fetchall()
        paths = tuple(row[0] for row in rows if isinstance(row[0], str) and row[0])
        if time.monotonic() < deadline and signature == (
                _signature(database), _signature(home / 'state_5.sqlite-wal')):
            _remember(_PATH_CACHE, key, (signature, paths))
        return paths
    except (OSError, sqlite3.Error, ValueError):
        return ()
    finally:
        if connection is not None:
            connection.close()


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        stamp = parsed.timestamp()
        return stamp if _finite(stamp) and stamp > 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def _sample(line):
    try:
        row = json.loads(line)
        if not isinstance(row, dict) or row.get('type') != 'event_msg':
            return None
        payload = row.get('payload')
        if not isinstance(payload, dict) or payload.get('type') != 'token_count':
            return None
        limits = payload.get('rate_limits')
        if not isinstance(limits, dict):
            return None
        # Missing IDs are allowed for older native records. Known IDs must be codex.
        for key in ('limit_id', 'limitId'):
            if limits.get(key) is not None and limits[key] != 'codex':
                return None
        # Native accounts with only a weekly limit expose it as primary. Slot
        # names describe priority, not duration; never infer weekly from a slot.
        windows = [window for name in ('primary', 'secondary')
                   if isinstance((window := limits.get(name)), dict)
                   and _finite(window.get('window_minutes'))
                   and window['window_minutes'] == 10080]
        if not windows:
            return None
        at = _timestamp(row.get('timestamp'))
        samples = set()
        for window in windows:
            used, reset = window.get('used_percent'), window.get('resets_at')
            if (at is None or not _finite(used) or not 0 <= used <= 100
                    or not _finite(reset) or reset <= 0
                    or not reset-WEEK_SECONDS <= at < reset):
                return None
            samples.add((at, 100-used, reset))
        # Duplicate identical windows yield one observation; conflicting weekly
        # windows are ambiguous and must not choose an account/window by priority.
        return next(iter(samples)) if len(samples) == 1 else None
    except (ValueError, TypeError, RecursionError):
        return None


def _read_file(path, signature, identity_key, budget, deadline):
    """Cache only a digest and anonymous numeric observations, never raw JSON."""
    key = str(path)
    cached = _FILE_CACHE.get(key)
    if cached and cached[0] == signature:
        return cached[2] if cached[1] == identity_key else ()
    samples = []
    try:
        with path.open('rb') as stream:
            limit = min(HEAD_BYTES, budget[0])
            if limit <= 0:
                return ()
            first = stream.readline(limit)
            budget[0] -= len(first)
            if not first.endswith(b'\n') or time.monotonic() >= deadline:
                return ()
            meta = json.loads(first)
            payload = meta.get('payload') if isinstance(meta, dict) else None
            if meta.get('type') != 'session_meta' or not isinstance(payload, dict):
                return ()
            creator = payload.get('creator_account_id')
            if not isinstance(creator, str) or not creator:
                return ()
            digest = hashlib.sha256(('opencodex-main-quota-v1\0' + creator).encode()).hexdigest()
            del meta, payload, creator, first
            if digest != identity_key:
                return ()
            size = signature[2]
            count = min(TAIL_BYTES, budget[0], size)
            if count <= 0 or time.monotonic() >= deadline:
                return ()
            start = size-count
            stream.seek(start)
            raw = stream.read(count)
            budget[0] -= len(raw)
        # A tail may begin inside a JSON line. Conservatively drop that first line.
        if start:
            boundary = raw.find(b'\n')
            raw = raw[boundary+1:] if boundary >= 0 else b''
        complete = True
        for line in raw.splitlines(keepends=True):
            if time.monotonic() >= deadline:
                complete = False
                break
            if not line.endswith(b'\n'):
                continue
            sample = _sample(line)
            if sample is not None:
                samples.append(sample)
        # A total-budget-shortened tail is not a reusable complete per-file cache.
        if complete and count == min(TAIL_BYTES, size) and _signature(path) == signature:
            _remember(_FILE_CACHE, key, (signature, digest, tuple(samples)))
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        pass
    return samples


def collect_native_samples(codex_home, identity_key, reset, since, until, *, paths=None):
    """Return anonymous weekly endpoints, sorted and deduplicated.

    All public times are epoch seconds; bounds are inclusive, reset jitter <=120s.
    Samples must lie inside their own weekly window and not in the future.
    Historical windows are allowed within [since, until]. Weekly is identified
    by duration in either primary or secondary. `paths` may be an iterable
    or a callable accepting codex_home (Path), for fixtures/injected discovery.
    Time checks are cooperative: an individual OS read cannot be preempted.
    """
    started = time.monotonic()
    deadline = started + TIME_BUDGET
    now = time.time()
    if (not isinstance(identity_key, str) or len(identity_key) != 64
            or any(char not in '0123456789abcdef' for char in identity_key)
            or not all(_finite(value) for value in (reset, since, until))
            or reset <= 0 or since < 0 or since > until or since > now):
        return []
    found = set()
    budget = [TOTAL_BYTES]
    try:
        home = Path(codex_home).absolute()
        candidates = (_candidate_paths(home, deadline) if paths is None else
                      paths(home) if callable(paths) else paths)
        seen = set()
        for index, candidate in enumerate(candidates):
            if index >= MAX_FILES or budget[0] <= 0 or time.monotonic() >= deadline:
                break
            if not isinstance(candidate, (str, Path)):
                continue
            path = Path(candidate)
            if not path.is_absolute():
                path = home / path
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            signature = _signature(path)
            if signature is None:
                continue
            for at, remaining, observed_reset in _read_file(
                    path, signature, identity_key, budget, deadline):
                if since <= at <= min(until, now) and abs(observed_reset-reset) <= RESET_JITTER:
                    found.add((at, remaining, observed_reset))
    except (OSError, ValueError, TypeError, sqlite3.Error):
        pass
    return [{'at': at, 'remaining': remaining, 'reset': observed_reset,
             'source': 'native-response'} for at, remaining, observed_reset in sorted(found)]
