"""Export only one Codex session's usage view for the local prototype."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from session_usage import SessionUsageAdapter
from session_usage_view import format_view
from sidebar_telemetry import Telemetry


def read_view(session_id, *, adapter=None, include_telemetry=True):
    adapter = adapter or SessionUsageAdapter()
    for _ in range(4):
        snapshot = adapter.load_session(session_id, recent_turns=1)
        if not snapshot or snapshot['status'] != 'loading':
            break
    if not snapshot or snapshot.get('session', {}).get('id') != session_id:
        return None
    telemetry = Telemetry() if include_telemetry else None
    if telemetry:
        telemetry._records()  # Synchronous bounded read without daily trends.
    view = format_view([], snapshot, session_id, telemetry, None)
    fields = ('session', 'turn_state', 'summary_speed', 'metrics')
    return {key: view[key] for key in fields if key in view}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session_id')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = read_view(args.session_id)
    if result is None:
        parser.exit(1, 'This session ID could not be verified in the local records.\n')
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False)
