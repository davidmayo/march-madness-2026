"""Regression tests for the scoreboard update-check module."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import march_madness.check_need as check_need


def _scoreboard_blob(*events: dict[str, object]) -> dict[str, object]:
    """Build a minimal scoreboard blob for update-check tests."""

    return {"events": list(events)}


def _scoreboard_event(
    *,
    event_id: str,
    date_value: str,
    completed: bool,
    winner_order: int = 1,
) -> dict[str, object]:
    """Build one minimal scoreboard event for update-check tests."""

    competitors = [
        {
            "order": 1,
            "winner": winner_order == 1 and completed,
            "team": {"id": "100"},
        },
        {
            "order": 2,
            "winner": winner_order == 2 and completed,
            "team": {"id": "200"},
        },
    ]
    return {
        "id": event_id,
        "date": date_value,
        "competitions": [
            {
                "status": {"type": {"completed": completed}},
                "competitors": competitors,
            }
        ],
    }


def test_find_new_completed_event_ids_returns_only_newly_finished_games() -> None:
    """Only completed games absent from the saved scoreboard should be returned."""

    current_blob = _scoreboard_blob(
        _scoreboard_event(event_id="1", date_value="2026-03-20T18:00Z", completed=True),
    )
    latest_blob = _scoreboard_blob(
        _scoreboard_event(event_id="1", date_value="2026-03-20T18:00Z", completed=True),
        _scoreboard_event(event_id="2", date_value="2026-03-20T20:00Z", completed=True),
        _scoreboard_event(event_id="3", date_value="2026-03-20T22:00Z", completed=False),
    )

    assert check_need.find_new_completed_event_ids(current_blob, latest_blob) == ["2"]


def test_run_check_returns_1_after_cutoff() -> None:
    """The update checker should stop once the tournament window has passed."""

    assert check_need.run_check(today=date(2026, 5, 1)) == 1


def test_run_check_returns_2_when_fetch_fails(monkeypatch: object) -> None:
    """Network or decoding failures should use exit code 2."""

    def _raise_fetch_error() -> dict[str, object]:
        raise TimeoutError("boom")

    monkeypatch.setattr(check_need, "fetch_scoreboard_blob", _raise_fetch_error)
    assert check_need.run_check(today=date(2026, 3, 25)) == 2


def test_run_check_returns_3_when_no_new_completed_games(monkeypatch: object) -> None:
    """No-update runs should use exit code 3 and avoid rewriting the file."""

    scoreboard_blob = _scoreboard_blob(
        _scoreboard_event(event_id="1", date_value="2026-03-20T18:00Z", completed=True),
    )
    monkeypatch.setattr(check_need, "fetch_scoreboard_blob", lambda: scoreboard_blob)
    monkeypatch.setattr(check_need, "load_scoreboard_blob", lambda: scoreboard_blob)
    monkeypatch.setattr(
        check_need,
        "write_scoreboard_blob",
        lambda scoreboard_blob, path=check_need.SCOREBOARD_PATH: (_ for _ in ()).throw(AssertionError(path)),
    )

    assert check_need.run_check(today=date(2026, 3, 25)) == 3


def test_run_check_returns_0_and_writes_updated_scoreboard(
    monkeypatch: object,
    capsys: object,
) -> None:
    """A newly completed game should update the scoreboard and exit successfully."""

    current_blob = _scoreboard_blob(
        _scoreboard_event(event_id="1", date_value="2026-03-20T18:00Z", completed=True),
    )
    latest_blob = _scoreboard_blob(
        _scoreboard_event(event_id="1", date_value="2026-03-20T18:00Z", completed=True),
        _scoreboard_event(event_id="2", date_value="2026-03-20T20:00Z", completed=True),
    )
    written_blobs: list[dict[str, object]] = []

    monkeypatch.setattr(check_need, "fetch_scoreboard_blob", lambda: latest_blob)
    monkeypatch.setattr(check_need, "load_scoreboard_blob", lambda: current_blob)
    monkeypatch.setattr(
        check_need,
        "write_scoreboard_blob",
        lambda scoreboard_blob, path=check_need.SCOREBOARD_PATH: written_blobs.append(scoreboard_blob),
    )

    assert check_need.run_check(today=date(2026, 3, 25)) == 0
    assert written_blobs == [latest_blob]
    assert capsys.readouterr().out.strip().splitlines() == ["2"]
