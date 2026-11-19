"""Bounded, read-only OpenCodex request evidence; no quota attribution policy.

``collect_requests(Path('.../.opencodex'), since, until, account_ids)`` uses
epoch seconds and an inclusive [since, until] window. Output rows are grouped
by UTC five-minute bucket and validated account, with ``at`` equal to the
first consumption completion time and ``start``/``end`` the completion extrema.
Consumption time is timestamp + durationMs/1000 (timestamp when absent).
Unknown accounts remain None. Consumers MUST check ``complete``: an empty
partial result is not evidence of zero local usage.

Only usage.jsonl is opened. Cache state is process-local (two homes), independent
of the requested window/account whitelist. No credentials, bodies or raw request
IDs are retained. A restart performs one bounded tail scan, never a full replay.
Call from the existing polling context; this module starts no background work.
"""

from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time


_TAIL_BYTES = 16 * 1024 * 1024
_LINE_BYTES = 256 * 1024
_MAX_LINES = 15000
_SECONDS = 0.9
_MAX_AGE = 8 * 86400
_MAX_BUCKETS = 5000
_MAX_RECORDS = 30000  # Additional bound on per-request dedup/window metadata.
_BUCKET = 300
_ACCOUNT_FIELDS = ('actualAccountId', 'codexAccountId', 'billingAccountId')
_BASE_PROVIDERS = frozenset(('openai', 'chatgpt', 'openai-multi'))
# OpenCodex 2.71 src/providers/label.ts explicitly documents these three as
# MAIN_CODEX_ACCOUNT_ID; src/codex/account-id.ts defines that ID as '__main__'.
# Do not generalize this to arbitrary providers ending in '-main'.
_MAIN_PROVIDERS = frozenset(p + '-main' for p in _BASE_PROVIDERS)
_POOL_PROVIDER = re.compile(r'^(?:openai|chatgpt|openai-multi)-p[0-9a-f]{6}$')
_THIRD_PARTY = re.compile(r'^(?:skg|ocx|combo)(?:[0-9]|[/_:\-]|$)', re.I)
_CACHES = OrderedDict()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            value = parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    result = _number(value)
    if result is not None and result >= 100_000_000_000:
        result /= 1000  # PersistedUsageEntry.timestamp is Date.now() (milliseconds).
    return result if result is not None and result <= 253402300799 else None


def _weight(row):
    usage = row.get('usage')
    if usage is None:
        usage = {}
    if not isinstance(usage, dict):
        raise ValueError
    # Invalid supplied details must not silently turn into a valid total.
    for name in ('inputTokens', 'outputTokens', 'cachedInputTokens', 'cacheReadInputTokens', 'totalTokens'):
        if usage.get(name) is not None and _number(usage[name]) is None:
            raise ValueError
    if row.get('totalTokens') is not None and _number(row['totalTokens']) is None:
        raise ValueError
    incoming, outgoing = (_number(usage.get(k)) for k in ('inputTokens', 'outputTokens'))
    cached = _number(usage.get('cachedInputTokens', usage.get('cacheReadInputTokens')))
    if incoming is not None and outgoing is not None and cached is not None:
        if cached > incoming:
            raise ValueError
        weight = incoming - cached + cached * 0.1 + outgoing
    else:
        total = usage.get('totalTokens')
        weight = _number(row.get('totalTokens') if total is None else total)
    if weight is None:
        status = _number(row.get('status'))
        # Null means absent telemetry, not malformed telemetry. A known failed
        # request with no positive work is unbilled; successful missing usage is
        # a localized coverage gap. Attempts/spend never manufacture token work.
        details = [n for n in (incoming, outgoing, cached) if n is not None]
        if status is not None and (status >= 400 or status == 0) and not any(details):
            return 0
        return None
    if not math.isfinite(weight):
        raise ValueError
    return weight


def _official(row):
    provider = row.get('provider')
    if not isinstance(provider, str):
        return False
    if provider not in _BASE_PROVIDERS and provider not in _MAIN_PROVIDERS and not _POOL_PROVIDER.fullmatch(provider):
        return False
    # Combo children may carry an official final provider. Explicit third-party
    # model/route markers still disqualify them; selected/currentAccount are unused.
    for name in ('model', 'requestedModel', 'resolvedModel', 'servedModel'):
        value = row.get(name)
        if isinstance(value, str) and _THIRD_PARTY.match(value):
            return False
    route = row.get('routeDecision')
    kind = route.get('routeKind') if isinstance(route, dict) else None
    return not (isinstance(kind, str) and 'combo' in kind.lower())


@dataclass(frozen=True)
class _Request:
    at: float
    weight: float
    accounts: tuple
    main: bool
    logical: bytes | None = None


@dataclass
class _Cache:
    signature: tuple = ()
    offset: int = 0
    skipping: bool = False
    pending: bool = True
    eof: bool = False
    first: float | None = None
    latest: float | None = None
    floor: float = 0
    gaps: dict = field(default_factory=dict)
    unlocated: tuple | None = None
    last_at: float | None = None
    points: set = field(default_factory=set)
    transient: str | None = None
    anchors: tuple = ()
    anchor_start: int = 0
    records: dict = field(default_factory=dict)


def _signature(stat):
    # Windows Python 3.12 path.stat()/fstat() can disagree on ctime after
    # append (creation vs change time). Identity/size/mtime are consistent.
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _digest(data):
    return hashlib.sha256(data).digest()


def _anchors(stream, size, start):
    result = []
    for offset, length in ((start, min(size - start, 128)),
                           (max(start, size - 128), min(size - start, 128))):
        stream.seek(offset)
        result.append((offset, length, _digest(stream.read(length))))
    return tuple(result)


def _same_prefix(stream, anchors):
    for offset, length, digest in anchors:
        stream.seek(offset)
        if _digest(stream.read(length)) != digest:
            return False
    return True


def _merge(previous, request):
    return _Request(max(request.at, previous.at), max(request.weight, previous.weight),
                    tuple(sorted(set(request.accounts) | set(previous.accounts))),
                    request.main or previous.main, previous.logical or request.logical)


def _gap(cache, lower, upper, category):
    bucket = int(upper // _BUCKET)
    previous = cache.gaps.get(bucket)
    if previous is not None:
        lower, upper = min(lower, previous[0]), max(upper, previous[1])
    cache.gaps[bucket] = (lower, upper, category)


def _unlocated_gap(cache, category):
    if cache.unlocated is None:
        cache.unlocated = (cache.last_at if cache.last_at is not None else 0, category)


def _ingest(cache, raw):
    at = None
    try:
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError
        at = _timestamp(row.get('timestamp'))
        if at is None:
            raise ValueError
        duration = row.get('durationMs', 0)
        duration = 0 if duration is None else _number(duration)
        if duration is None or not math.isfinite(at + duration / 1000):
            raise ValueError
        at += duration / 1000
        if at > 253402300799:
            raise ValueError
        # A late, out-of-order row cannot prove that the omitted prefix of a
        # tail scan covered its earlier timestamp.
        if cache.first is None:
            cache.first = at
        cache.latest = at if cache.latest is None else max(cache.latest, at)
        cache.points.add(at)
        if cache.unlocated is not None:
            lower, category = cache.unlocated
            _gap(cache, min(lower, at), max(lower, at), category)
            cache.unlocated = None
        cache.last_at = at
        if not _official(row):
            return
        weight = _weight(row)
        if weight is None:
            _gap(cache, at, at, 'missing-usage')
            return
        if weight <= 0:
            return  # Status alone is not billing evidence; attempts are not additive.
        identity = row.get('requestId')
        if not isinstance(identity, str) or not identity:
            raise ValueError  # Without a stable ID we cannot safely deduplicate.
        accounts = tuple(sorted({row[k] for k in _ACCOUNT_FIELDS
                                 if isinstance(row.get(k), str) and 0 < len(row[k]) <= 512}))
        logical = row.get('logicalRequestId')
        logical = _digest(b'logical\0' + logical.encode('utf-8')) if isinstance(logical, str) and logical else None
        request = _Request(at, weight, accounts, row.get('provider') in _MAIN_PROVIDERS, logical)
        key = _digest(b'request\0' + identity.encode('utf-8'))
        previous = cache.records.get(key)
        if previous is not None:
            # Repeated snapshots/attempt rows describe ONE request, not new spend.
            # Retain the strongest observed work measurement, never sum attempts.
            request = _merge(previous, request)
        if request.at >= cache.floor:
            cache.records[key] = request
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        if at is not None and math.isfinite(at) and 0 <= at <= 253402300799:
            cache.latest = at if cache.latest is None else max(cache.latest, at)
            _gap(cache, at, at, 'invalid-record')
        else:
            _unlocated_gap(cache, 'invalid-record')


def _prune(cache):
    if cache.latest is None:
        return
    cache.floor = max(cache.floor, cache.latest - _MAX_AGE)
    points = sorted(at for at in cache.points if at >= cache.floor)
    if len(points) > _MAX_RECORDS:
        cache.floor = max(cache.floor, points[-_MAX_RECORDS])
    records = sorted(((k, r) for k, r in cache.records.items() if r.at >= cache.floor),
                     key=lambda pair: pair[1].at)
    if len(records) > _MAX_RECORDS:
        cache.floor = max(cache.floor, math.nextafter(records[-_MAX_RECORDS - 1][1].at, math.inf))
        records = [(k, r) for k, r in records if r.at >= cache.floor]
    buckets = sorted({(int(r.at // _BUCKET), r.accounts, r.main) for _, r in records})
    if len(buckets) > _MAX_BUCKETS:
        # Drop entire boundary time buckets, including ties across accounts, so
        # the coverage start never claims a partially retained bucket is complete.
        cache.floor = max(cache.floor, (buckets[-_MAX_BUCKETS - 1][0] + 1) * _BUCKET)
        records = [(k, r) for k, r in records if r.at >= cache.floor]
    cache.records = dict(records)
    gaps = sorted((gap for gap in cache.gaps.values() if gap[1] >= cache.floor), key=lambda gap: gap[1])
    if len(gaps) > _MAX_BUCKETS:
        cache.floor = max(cache.floor, math.nextafter(gaps[-_MAX_BUCKETS - 1][1], math.inf))
    cache.gaps = {int(gap[1] // _BUCKET): gap for gap in gaps if gap[1] >= cache.floor}
    cache.points = {at for at in points if at >= cache.floor}
    cache.records = {k: r for k, r in cache.records.items() if r.at >= cache.floor}


def _scan(path, cache, signature, deadline):
    with path.open('rb') as stream:
        actual = _signature(os.fstat(stream.fileno()))
        if actual != signature:
            signature = actual
        size = signature[2]
        reset = (not cache.signature or signature[:2] != cache.signature[:2]
                 or size < cache.signature[2]
                 or (size == cache.signature[2] and signature != cache.signature))
        if not reset and signature != cache.signature:
            reset = not _same_prefix(stream, cache.anchors)
        # Reserve the small anchor reads within the byte budget. Initial anchors
        # are inside the retained tail, never a separate read of an old prefix.
        byte_budget = max(1, _TAIL_BYTES - 512)
        if reset:
            cache = _Cache(offset=max(0, size - byte_budget))
            cache.anchor_start = cache.offset
            if cache.offset > 0:
                stream.seek(cache.offset - 1)
                cache.skipping = stream.read(1) != b'\n'
        cache.signature = signature
        cache.transient = None
        cache.eof = False
        cache.pending = False
        stream.seek(cache.offset)
        read_bytes = 0
        lines = 0
        while cache.offset < size:
            if time.monotonic() >= deadline or lines >= _MAX_LINES or read_bytes >= byte_budget:
                cache.pending = True
                cache.transient = 'resource-limit'
                break
            before = stream.tell()
            limit = min(_LINE_BYTES, size - before, byte_budget - read_bytes)
            raw = stream.readline(limit)
            read_bytes += len(raw)
            lines += 1
            if not raw:
                cache.transient = 'file-changed'
                break
            after = stream.tell()
            newline = raw.endswith(b'\n')
            if cache.skipping:
                cache.offset = after
                if newline:
                    cache.skipping = False
                continue
            if not newline:
                if len(raw) >= _LINE_BYTES:
                    _unlocated_gap(cache, 'line-too-large')
                    cache.skipping = True
                    cache.offset = after
                    continue
                # Keep the start of a partial line for a later append. No prefix
                # JSON is parsed, and an unchanged signature never rereads it.
                cache.offset = before
                cache.transient = 'partial-line' if after == size else 'resource-limit'
                cache.pending = after != size
                break
            cache.offset = after
            if len(raw) > _LINE_BYTES:
                _unlocated_gap(cache, 'line-too-large')
            elif raw.strip():
                _ingest(cache, raw)
        else:
            cache.eof = not cache.skipping
            if cache.skipping:
                cache.transient = 'partial-line'
        cache.anchors = _anchors(stream, size, cache.anchor_start)
        if _signature(os.fstat(stream.fileno())) != signature:
            cache.eof = False
            cache.transient = 'file-changed'
        _prune(cache)
        return cache


def _account(request, allowed):
    # Evidence conflicts are unresolved even if only one candidate is currently
    # in the whitelist. A changing UI selection cannot disambiguate billing.
    candidates = set(request.accounts)
    if request.main:
        candidates.add('__main__')
    if len(candidates) == 1:
        value = next(iter(candidates))
        if value in allowed:
            return value
    return None


def collect_requests(home, since, until, account_ids):
    """Return safe usage metadata; ``home`` is the .opencodex directory itself.

    ``start`` is the earliest retained continuous coverage timestamp, advanced
    to a real log timestamp after damage when a usable suffix exists.
    ``coverageComplete`` covers start..until; ``complete`` covers since..until.
    ``end`` echoes until (seconds). ``error`` is a
    fixed safe category, never an exception, filename, request ID or log text.
    Bare official provider names and unmapped pool labels have account=None.
    Partial reads can resume on subsequent calls; window/whitelist changes use
    the retained data without destructive filtering. No fixed percentage is used.
    """
    deadline = time.monotonic() + _SECONDS
    start, end = _number(since), _number(until)
    result = {'requests': [], 'complete': False, 'coverageComplete': False,
              'start': None, 'end': end, 'method': 'usage-log'}
    if start is None or end is None or start > end:
        result['error'] = 'invalid-window'
        return result
    if not isinstance(account_ids, (list, tuple, set, frozenset, dict)) or len(account_ids) > _MAX_BUCKETS:
        result['error'] = 'invalid-accounts'
        return result
    allowed = {item for item in account_ids if isinstance(item, str) and 0 < len(item) <= 512}
    try:
        path = Path(home) / 'usage.jsonl'
        key = os.path.normcase(os.path.abspath(path.parent))
        signature = _signature(path.stat())
        cache = _CACHES.get(key, _Cache())
        if signature != cache.signature or cache.pending:
            cache = _scan(path, cache, signature, deadline)
        _CACHES[key] = cache
        _CACHES.move_to_end(key)
        while len(_CACHES) > 2:
            _CACHES.popitem(last=False)
    except (OSError, ValueError, TypeError):
        result['error'] = 'unavailable'
        return result
    coverage = max(cache.first, cache.floor) if cache.first is not None else None
    damage = None
    if coverage is not None:
        relevant = [gap for gap in cache.gaps.values() if gap[0] <= end and gap[1] >= coverage]
        if relevant:
            last_gap = max(relevant, key=lambda gap: gap[1])
            points = sorted(cache.points)
            recovery = bisect_right(points, last_gap[1])
            if recovery < len(points) and points[recovery] <= end:
                coverage = max(coverage, points[recovery])
            else:
                damage = last_gap[2]
    if cache.unlocated is not None and cache.unlocated[0] <= end:
        damage = cache.unlocated[1]
    if coverage is None and cache.gaps:
        damage = next(iter(cache.gaps.values()))[2]
    # Keep historical complete spans, not only the suffix after the last gap.
    # A later missing-usage row must not erase earlier calibration evidence.
    ranges=[]
    if cache.eof and cache.first is not None:
        cursor=max(cache.first,cache.floor,start)
        safe_end=min(end,math.nextafter(cache.unlocated[0],-math.inf)) if cache.unlocated else end
        for lower,upper,_ in sorted(cache.gaps.values()):
            if upper<cursor or lower>safe_end:continue
            if lower>cursor:ranges.append([cursor,math.nextafter(lower,-math.inf)])
            cursor=max(cursor,math.nextafter(upper,math.inf))
        if cursor<=safe_end:ranges.append([cursor,safe_end])
    result['coverageRanges']=ranges
    result['start'] = coverage
    result['coverageComplete'] = bool(cache.eof and not damage and coverage is not None and coverage <= end)
    result['complete'] = bool(result['coverageComplete'] and coverage <= start)
    error = damage or cache.transient
    if error is None and not result['complete']:
        error = 'coverage-gap'
    # Physical request IDs always deduplicate, including a later snapshot that
    # first adds logicalRequestId. Distinct attempt rows with a shared logical ID
    # then collapse once, before time filtering, without an unbounded alias map.
    logical_requests = {}
    for key, request in cache.records.items():
        identity = request.logical or key
        previous = logical_requests.get(identity)
        logical_requests[identity] = _merge(previous, request) if previous is not None else request
    grouped = {}
    for request in logical_requests.values():
        if not start <= request.at <= end:
            continue
        account = _account(request, allowed)
        key = (int(request.at // _BUCKET), account)
        row = grouped.get(key)
        if row is None:
            grouped[key] = {'at': request.at, 'weight': request.weight, 'account': account,
                            'start': request.at, 'end': request.at}
        else:
            weight = row['weight'] + request.weight
            if not math.isfinite(weight):
                result['complete'] = False
                result['coverageComplete'] = False
                error = 'invalid-record'
                continue
            row.update(at=min(row['at'], request.at), start=min(row['start'], request.at),
                       end=max(row['end'], request.at), weight=weight)
    result['requests'] = sorted(grouped.values(), key=lambda row: (row['at'], row['account'] or ''))
    if error:
        result['error'] = error
    return result
