"""Regression tests for parsing the saved NCAA user brackets."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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
