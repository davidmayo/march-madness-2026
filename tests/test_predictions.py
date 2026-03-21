"""Regression tests for tournament Monte Carlo predictions."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from march_madness.predictions import build_prediction_report
from march_madness.predictions import build_prediction_history
from march_madness.predictions import load_latest_kenpom_ratings
from march_madness.predictions import simulate_remaining_tournament
from march_madness.predictions import write_prediction_history_files
from march_madness.scoring import get_completed_scoreboard_event_ids
from march_madness.scoring import get_bracket_from_scoreboard_data


def test_simulate_remaining_tournament_completes_the_bracket() -> None:
    """One simulation should finish every remaining game without changing completed results."""

    scoreboard_blob = json.loads((ROOT / "data" / "espn" / "api" / "scoreboard.json").read_text())
    reference_bracket = get_bracket_from_scoreboard_data(scoreboard_blob)
    _snapshot_path, kenpom_ratings = load_latest_kenpom_ratings()

    completed_winners_by_game_id = {
        game.id: game.winner_team_id
        for game in reference_bracket.games
        if game.winner_team_id is not None
    }
    simulated_bracket = simulate_remaining_tournament(
        reference_bracket,
        kenpom_ratings,
        random_generator=random.Random(123),
    )

    assert len([game for game in simulated_bracket.games if game.winner_team_id is not None]) == 63
    for game in simulated_bracket.games:
        assert game.winner_team_id is not None
        if game.id in completed_winners_by_game_id:
            assert game.winner_team_id == completed_winners_by_game_id[game.id]


def test_build_prediction_report_summarizes_all_users() -> None:
    """The prediction report should summarize every saved bracket with category metrics."""

    scoreboard_blob = json.loads((ROOT / "data" / "espn" / "api" / "scoreboard.json").read_text())
    reference_bracket = get_bracket_from_scoreboard_data(scoreboard_blob)

    report = build_prediction_report(
        reference_bracket,
        simulation_count=25,
        random_seed=123,
    )

    assert report.simulation_count == 25
    assert report.completed_game_count == 28
    assert report.remaining_game_count == 35
    assert len(report.users) == 14
    assert {tuple(user.user_categories) for user in report.users} == {("student",), ("staff",)}
    assert report.users[0].average_finishing_position <= report.users[-1].average_finishing_position

    category_sizes = {"student": 8, "staff": 6}
    for user in report.users:
        assert 0.0 <= user.average_score <= 192.0
        assert 0.0 <= user.winning_percentage <= 100.0
        assert user.score_interval.lower <= user.score_interval.upper
        assert 1.0 <= user.average_finishing_position <= 14.0
        assert user.finishing_position_interval.lower <= user.finishing_position_interval.upper

        user_category = user.user_categories[0]
        assert user.average_category_finishing_position is not None
        assert user.category_winning_percentage is not None
        assert user.category_finishing_position_interval is not None
        assert 1.0 <= user.average_category_finishing_position <= category_sizes[user_category]
        assert 0.0 <= user.category_winning_percentage <= 100.0
        assert (
            user.category_finishing_position_interval.lower
            <= user.category_finishing_position_interval.upper
        )


def test_build_prediction_history_and_write_files(tmp_path: Path) -> None:
    """Prediction history should cover every completed-game checkpoint and write JSON files."""

    scoreboard_blob = json.loads((ROOT / "data" / "espn" / "api" / "scoreboard.json").read_text())
    completed_event_ids = get_completed_scoreboard_event_ids(scoreboard_blob)

    history, reports = build_prediction_history(
        scoreboard_blob,
        simulation_count=5,
        random_seed=123,
    )

    assert len(reports) == len(completed_event_ids) + 1
    assert history.completed_game_count_max == len(completed_event_ids)
    assert len(history.checkpoints) == len(reports)
    assert len(history.users) == 14
    for series in history.users:
        assert len(series.points) == len(reports)
        assert series.points[0].games_completed == 0
        assert series.points[0].average_score >= 0.0
        assert series.points[0].score_interval_lower <= series.points[0].score_interval_upper
        assert (
            series.points[0].finishing_position_interval_lower
            <= series.points[0].finishing_position_interval_upper
        )
        assert series.points[0].average_category_finishing_position is not None
        assert (
            series.points[0].category_finishing_position_interval_lower
            <= series.points[0].category_finishing_position_interval_upper
        )
        assert series.points[0].category_winning_percentage is not None
        assert (
            series.points[0].winning_percentage_interval_lower
            <= series.points[0].winning_percentage_interval_upper
        )
        assert series.points[-1].games_completed == len(completed_event_ids)
    assert history.checkpoints[0].label == "Initial"
    assert " beats " in history.checkpoints[1].label

    output_dir = tmp_path / "checkpoints"
    history_output_path = tmp_path / "prediction-history.json"
    written_history = write_prediction_history_files(
        output_dir=output_dir,
        history_output_path=history_output_path,
        simulation_count=5,
        random_seed=123,
    )

    assert written_history.completed_game_count_max == len(completed_event_ids)
    assert history_output_path.exists()
    assert len(list(output_dir.glob("*.json"))) == len(completed_event_ids) + 1
