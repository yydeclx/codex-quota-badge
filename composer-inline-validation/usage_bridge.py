"""Provide local session statistics to the inline composer widget."""
from collections import OrderedDict
import json
from pathlib import Path
import re
import sys
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from session_usage import SessionUsageAdapter
from session_usage_card import PAGE_SIZE
from session_usage_view import format_view
from sidebar_activity import ProviderActivity
from sidebar_data import COMPOSER_STYLES, COMPOSER_WEIGHTS, composer_enabled
from sidebar_telemetry import Telemetry


UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I)


class UsageBridge:
    def __init__(self, home, config=None, *, live=True):
        self.reader = SessionUsageAdapter(home)
        self.config = config
        self.telemetry = Telemetry() if live else None
        self.activity = ProviderActivity() if live else None
        self.next_config = self.next_telemetry = 0
        self.settings = {}
        self.cached = OrderedDict()

    def appearance(self):
        now = monotonic()
        if self.config and now >= self.next_config:
            self.next_config = now + 2
            try:
                settings = json.loads(self.config.read_text(encoding='utf-8-sig'))
                if isinstance(settings, dict):
                    if any(settings.get(key, True) != self.settings.get(key, True)
                           for key in ('activityEnabled', 'realtimeMetricsEnabled')):
                        self.cached.clear()
                    self.settings = settings
            except (OSError, ValueError):
                pass
        result = {}
        for key, catalog in (('composerStyle', COMPOSER_STYLES), ('composerWeight', COMPOSER_WEIGHTS)):
            value = self.settings.get(key)
            result[key] = value if any(value == code for code, _ in catalog) else catalog[0][0]
        result['activityEnabled'] = self.settings.get('activityEnabled', True) is not False
        return result

    def enabled(self):
        return composer_enabled(self.settings)

    def tick(self):
        now = monotonic()
        self.appearance()
        if self.telemetry and now >= self.next_telemetry:
            self.next_telemetry = now + 1
            self.telemetry._records()  # Only local records; skip the quota trend request.
        if self.activity:
            self.activity.set_enabled(self.settings.get('realtimeMetricsEnabled', True))
            self.activity.poll()

    def prime_recent(self, *, budget=.45):
        """Warm one likely restored chat while the desktop window initializes."""
        deadline = monotonic() + budget
        sessions = self.reader.list_recent_sessions(1)
        identity = sessions[0].get('id') if sessions else None
        if not isinstance(identity, str) or not UUID.fullmatch(identity):
            return
        for _ in range(4):
            if monotonic() >= deadline:
                break
            snapshot = self.reader.load_session(identity, recent_turns=0)
            if not snapshot or snapshot.get('status') != 'loading':
                break

    def view(self, identity):
        if not isinstance(identity, str) or not UUID.fullmatch(identity):
            return None
        now = monotonic()
        entry = self.cached.get(identity)
        if entry and now < entry[0]:
            return entry[1]
        snapshot = self.reader.load_session(identity, recent_turns=25)
        if snapshot and snapshot.get('session', {}).get('id') != identity:
            return None
        indicators = self.settings.get('activityEnabled', True)
        activity = self.activity if self.settings.get('realtimeMetricsEnabled', True) else None
        view = format_view([], snapshot, identity, self.telemetry, activity)
        view.update(session=identity, follow_visible=True)
        view['usage_loading'] = bool(snapshot and snapshot.get('status') == 'loading')
        state = view.get('turn_state')
        turns = (snapshot or {}).get('turns') or ()
        if turns and snapshot.get('status') == 'ready':
            active_id = snapshot.get('active_turn_id')
            turn = next((row for row in turns if row['id'] == active_id), turns[0])
            view['turn_id'] = turn['id']
        view['first_turn_active'] = bool(
            snapshot and snapshot.get('status') == 'ready' and snapshot.get('history_complete')
            and state == 'active' and len(turns) == 1 and turns[0]['id'] == snapshot.get('active_turn_id')
        )
        provider = view.get('status_provider')
        health = None
        if indicators and provider and self.telemetry:
            requests = self.activity.details(provider)['requests'] if self.activity else ()
            health = ('amber' if any(row.get('retries', 0) > 0 for row in requests)
                      else self.telemetry.view(provider)['health'][0])
        view['dot_state'] = (state if state == 'active' else health if health in ('red', 'amber') else
                             'completed' if state == 'completed' else None) if indicators else None
        view['turns_count'] = len(view.get('turns', ()))
        view['turn_ids'] = [row['id'] for row in view.get('turns', ())]
        # A prefix is not a fresh result. Let the next renderer poll continue
        # reading immediately; throttle only completed reads.
        self.cached[identity] = monotonic() + (0 if view['usage_loading'] else .65), view
        self.cached.move_to_end(identity)
        while len(self.cached) > 8:
            self.cached.popitem(last=False)
        return view

    @staticmethod
    def summary(view):
        fields = ('session', 'turn_id', 'turn_state', 'summary_speed', 'metrics', 'dot_state', 'message', 'usage_loading',
                  'turns_count', 'turn_ids', 'first_turn_active')
        return {key: view[key] for key in fields if key in view} if view else None

    def card(self, view, request, *, draft=False):
        page = request.get('page')
        if page not in ('summary', 'turns', 'turn'):
            return None
        data = view or {'follow_visible': True, 'title': '新聊天' if draft else '当前会话',
                        'message': '发送后显示本轮统计' if draft else '正在读取当前会话用量'}
        turns = data.get('turns', ())
        turn_id = request.get('turnId')
        if page == 'turn' and not any(row['id'] == turn_id for row in turns):
            return dict(page=page, offset=0, turnId=turn_id, expired=True)
        offset = request.get('offset', 0)
        offset = max(0, min(offset, max(0, len(turns) - PAGE_SIZE))) if type(offset) is int else 0
        card = dict(page=page, offset=offset, turnId=turn_id, title=data.get('title', '当前会话'),
                    message=data.get('message'), turnCount=len(turns))
        if page == 'summary':
            card.update(metrics=data.get('metrics'), running=data.get('turn_state') == 'active')
        elif page == 'turns':
            card['rows'] = [{key: row[key] for key in ('id', 'title', 'detail')}
                            for row in turns[offset:offset + PAGE_SIZE]]
        else:
            card['turn'] = next(row for row in turns if row['id'] == turn_id)
        return card
