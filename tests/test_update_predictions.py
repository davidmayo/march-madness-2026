"""Regression tests for incremental prediction checkpoint updates."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from march_madness.update_predictions import determine_new_completed_games


def _scoreboard_blob(*events: dict[str, object]) -> dict[str, object]:
    """Build a minimal scoreboard blob for incremental-update tests."""

    return {"events": list(events)}


def _scoreboard_event(
    *,
    event_id: str,
    date_value: str,
    completed: bool,
) -> dict[str, object]:
    """Build one minimal scoreboard event for incremental-update tests."""

    return {
        "id": event_id,
        "date": date_value,
        "competitions": [
            {
                "status": {"type": {"completed": completed}},
                "competitors": [
                    {
                        "order": 1,
                        "winner": completed,
                        "team": {"id": "100"},
                    },
                    {
                        "order": 2,
                        "winner": False,
                        "team": {"id": "200"},
                    },
                ],
            }
        ],
    }


def test_determine_new_completed_games_uses_scoreboard_time_order() -> None:
    """New games should be reported in completed-time order with checkpoint counts."""

    previous_blob = _scoreboard_blob(
        _scoreboard_event(event_id="1", date_value="2026-03-20T18:00Z", completed=True),
    )
    current_blob = _scoreboard_blob(
        _scoreboard_event(event_id="3", date_value="2026-03-20T22:00Z", completed=True),
        _scoreboard_event(event_id="1", date_value="2026-03-20T18:00Z", completed=True),
        _scoreboard_event(event_id="2", date_value="2026-03-20T20:00Z", completed=True),
    )

    new_games = determine_new_completed_games(previous_blob, current_blob)

    assert [new_game.event_id for new_game in new_games] == ["2", "3"]
    assert [new_game.completed_count for new_game in new_games] == [2, 3]
    assert [new_game.date for new_game in new_games] == [
        "2026-03-20T20:00Z",
        "2026-03-20T22:00Z",
    ]
