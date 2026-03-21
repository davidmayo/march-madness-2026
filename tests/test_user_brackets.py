"""Regression tests for parsing the saved NCAA user brackets."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from march_madness.scoring import get_bracket_from_scoreboard_data
from march_madness.scoring import load_user_bracket
from march_madness.scoring import score
from march_madness.scoring import score_saved_user_brackets
from march_madness.user_brackets import export_saved_user_brackets
from march_madness.user_brackets import parse_homepage_real_names
from march_madness.user_brackets import parse_saved_user_bracket


def test_parse_austin_jude_bracket() -> None:
    """Parse a representative saved bracket and verify the core fields."""

    homepage_real_names = parse_homepage_real_names()
    bracket = parse_saved_user_bracket(
        ROOT / "data" / "brackets" / "austin-jude.html",
        homepage_real_names=homepage_real_names,
    )

    assert bracket.bracket_metadata.user_name == "Austin Jude"
    assert bracket.bracket_metadata.entry_name == "Austin Jude2343623"
    assert bracket.bracket_metadata.ncaa_com_id is None
    assert bracket.bracket_metadata.ncaa_com_url is None

    assert len(bracket.bracket_picks.games) == 63
    assert bracket.bracket_picks.champion.team_name == "Florida"
    assert bracket.bracket_picks.champion.seed == "1"
    assert bracket.bracket_picks.tiebreaker.winner_score == 68
    assert bracket.bracket_picks.tiebreaker.loser_score == 66

    first_game = bracket.bracket_picks.games[0]
    assert first_game.game_key == "east-round-1-game-1"
    assert first_game.picked_team_1.team_name == "Duke"
    assert first_game.picked_team_2.team_name == "Siena"
    assert first_game.picked_winner.team_name == "Duke"


def test_parse_david_mayo_bracket() -> None:
    """Parse the special-case saved bracket that exposes the print/PDF link."""

    homepage_real_names = parse_homepage_real_names()
    bracket = parse_saved_user_bracket(
        ROOT / "data" / "brackets" / "david-mayo.html",
        homepage_real_names=homepage_real_names,
    )

    assert bracket.bracket_metadata.user_name == "David Mayo"
    assert bracket.bracket_metadata.entry_name == "Basketball!"
    assert bracket.bracket_metadata.ncaa_com_id == 1181849
    assert (
        bracket.bracket_metadata.ncaa_com_url
        == "https://play-prod.ncaa.com/mens-bracket-challenge/api/v2/"
        "ncaabracketchallenge/pdf/entry/1181849?name=Basketball!"
    )

    assert len(bracket.bracket_picks.games) == 63
    assert bracket.bracket_picks.champion.team_name == "Arkansas"
    assert bracket.bracket_picks.games[-1].picked_winner.team_name == "Arkansas"


def test_export_saved_user_brackets(tmp_path: Path) -> None:
    """Export every saved bracket to JSON and verify the basic output shape."""

    written_paths = export_saved_user_brackets(tmp_path)

    assert len(written_paths) == 14
    output_path = tmp_path / "austin-jude.json"
    assert output_path.exists()

    exported = json.loads(output_path.read_text())
    assert set(exported) == {"bracket_metadata", "bracket_picks"}
    assert exported["bracket_metadata"]["user_name"] == "Austin Jude"
    assert len(exported["bracket_picks"]["games"]) == 63


def test_scoreboard_reference_bracket_uses_completed_games_only() -> None:
    """Build the current reference bracket from the scoreboard snapshot."""

    scoreboard_blob = json.loads((ROOT / "data" / "espn" / "api" / "scoreboard.json").read_text())
    reference_bracket = get_bracket_from_scoreboard_data(scoreboard_blob)

    completed_games = [game for game in reference_bracket.games if game.winner_team_id is not None]
    completed_game_ids = {game.id for game in completed_games}

    assert len(completed_games) == 8
    assert "west-round-1-game-1" in completed_game_ids
    assert "midwest-round-1-game-8" in completed_game_ids


def test_traditional_score_for_austin_jude() -> None:
    """Score Austin's exported bracket with traditional round scoring."""

    scoreboard_blob = json.loads((ROOT / "data" / "espn" / "api" / "scoreboard.json").read_text())
    reference_bracket = get_bracket_from_scoreboard_data(scoreboard_blob)
    austin_bracket = load_user_bracket(ROOT / "data" / "user-brackets" / "austin-jude.json")

    result = score(reference_bracket, austin_bracket, calculate_details=True)

    assert result.current_score == 6.0
    assert result.max_possible_score == 190.0
    assert result.correctly_picked_games == 6
    assert result.incorrectly_picked_games == 2
    assert float(result) == 6.0


def test_scoring_all_saved_user_brackets() -> None:
    """Score all saved brackets and verify the best traditional score so far."""

    scoreboard_blob = json.loads((ROOT / "data" / "espn" / "api" / "scoreboard.json").read_text())
    reference_bracket = get_bracket_from_scoreboard_data(scoreboard_blob)

    scored = score_saved_user_brackets(reference_bracket, calculate_details=True)

    assert len(scored) == 14
    assert scored[0][2].current_score == 8.0
    assert {scored[0][1].bracket_metadata.user_name, scored[1][1].bracket_metadata.user_name} == {
        "Chloe Hart",
        "Mitchell Mcculley",
    }
