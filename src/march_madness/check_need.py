"""Check whether the saved scoreboard needs to be refreshed from ESPN."""

from __future__ import annotations

import json
import sys
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen

from march_madness.scoring import get_completed_scoreboard_event_ids


ROOT = Path(__file__).resolve().parents[2]
SCOREBOARD_PATH = ROOT / "data" / "espn" / "api" / "scoreboard.json"
SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard?groups=100&seasontype=3&dates=20260319-20260407"
)
TOURNAMENT_UPDATE_CUTOFF = date(2026, 4, 30)


def should_skip_update_check(today: date) -> bool:
    """Return whether tournament update checks should stop for the season."""

    return today > TOURNAMENT_UPDATE_CUTOFF


def fetch_scoreboard_blob(url: str = SCOREBOARD_URL) -> dict[str, Any]:
    """Fetch the current ESPN scoreboard payload and decode it as JSON."""

    request = Request(
        url,
        headers={
            # ESPN occasionally rejects requests that look too bare. A simple
            # descriptive user-agent is enough for this low-volume automation.
            "User-Agent": "march-madness-2026-updater/1.0",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def load_scoreboard_blob(path: Path = SCOREBOARD_PATH) -> dict[str, Any]:
    """Load one saved scoreboard payload from disk."""

    return json.loads(path.read_text())


def find_new_completed_event_ids(
    current_scoreboard_blob: dict[str, Any],
    latest_scoreboard_blob: dict[str, Any],
) -> list[str]:
    """Return completed event IDs present in the latest blob but not the current one."""

    current_event_ids = set(get_completed_scoreboard_event_ids(current_scoreboard_blob))
    return [
        event_id
        for event_id in get_completed_scoreboard_event_ids(latest_scoreboard_blob)
        if event_id not in current_event_ids
    ]


def write_scoreboard_blob(scoreboard_blob: dict[str, Any], path: Path = SCOREBOARD_PATH) -> None:
    """Write one scoreboard payload to disk in a stable JSON format."""

    path.write_text(f"{json.dumps(scoreboard_blob, indent=2)}\n", encoding="utf-8")


def run_check(today: date | None = None) -> int:
    """Run the update check and return the requested process exit code."""

    effective_today = today or datetime.now(tz=UTC).date()
    if should_skip_update_check(effective_today):
        return 1

    try:
        latest_scoreboard_blob = fetch_scoreboard_blob()
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return 2

    current_scoreboard_blob = load_scoreboard_blob()
    new_completed_event_ids = find_new_completed_event_ids(
        current_scoreboard_blob=current_scoreboard_blob,
        latest_scoreboard_blob=latest_scoreboard_blob,
    )
    if not new_completed_event_ids:
        return 3

    write_scoreboard_blob(latest_scoreboard_blob)
    for event_id in new_completed_event_ids:
        print(event_id)
    return 0


def main() -> None:
    """Run the update check and exit with the documented status code."""

    raise SystemExit(run_check())


if __name__ == "__main__":
    main()
