from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import BracketStructure, Team, load_tournament


def _base_payload() -> dict:
    return {
        "final_game": 6,
        "structure": {
            "6": [4, 5],
            "5": [2, 3],
            "4": [0, 1],
            "3": [None, None],
            "2": [None, None],
            "1": [None, None],
            "0": [None, None],
        },
        "games": {
            "0": [111, 222],
            "1": [333, 444],
            "2": [555, 666],
            "3": [777, 888],
        },
    }


def test_team_from_json_file_loads_8_teams() -> None:
    teams = Team.from_json_file("definitions/teams.json")
    assert len(teams) == 8
    assert teams[0].id == 111
    assert teams[0].name == "Kentucky"


def test_team_id_must_be_int() -> None:
    with pytest.raises(ValidationError):
        Team.model_validate({"id": "111", "name": "Kentucky"})


def test_team_name_must_be_non_empty() -> None:
    with pytest.raises(ValidationError, match=r"non-empty"):
        Team.model_validate({"id": 111, "name": "   "})


def test_loads_sample_bracket_json_with_games() -> None:
    bracket = BracketStructure.from_json_file("definitions/bracket.json")
    assert bracket.final_game == 6
    assert bracket.structure[6] == (4, 5)
    assert bracket.games[0] == (111, 222)


def test_non_final_game_referenced_twice_fails() -> None:
    payload = _base_payload()
    payload["structure"]["6"] = [4, 2]
    payload["structure"]["5"] = [2, 3]

    with pytest.raises(
        ValidationError, match=r"Game 2 must be referenced by exactly 1 game"
    ):
        BracketStructure.model_validate(payload)


def test_non_final_game_referenced_zero_times_fails() -> None:
    payload = _base_payload()
    payload["structure"]["4"] = [0, 0]

    with pytest.raises(
        ValidationError, match=r"Game 1 must be referenced by exactly 1 game"
    ):
        BracketStructure.model_validate(payload)


def test_final_game_referenced_by_other_game_fails() -> None:
    payload = _base_payload()
    payload["structure"]["5"] = [6, 3]

    with pytest.raises(
        ValidationError, match=r"final_game 6 must be referenced by exactly 0 games"
    ):
        BracketStructure.model_validate(payload)


def test_reference_to_missing_game_fails() -> None:
    payload = _base_payload()
    payload["structure"]["6"] = [4, 99]

    with pytest.raises(ValidationError, match=r"references missing input game 99"):
        BracketStructure.model_validate(payload)


def test_mixed_null_non_null_structure_inputs_fail() -> None:
    payload = _base_payload()
    payload["structure"]["0"] = [None, 1]

    with pytest.raises(
        ValidationError, match=r"Game 0 is non-leaf and must have two non-null inputs"
    ):
        BracketStructure.model_validate(payload)


def test_cycle_detection_fails() -> None:
    payload = _base_payload()
    payload["structure"]["4"] = [0, 6]

    with pytest.raises(ValidationError, match=r"Cycle detected"):
        BracketStructure.model_validate(payload)


def test_disconnected_game_fails() -> None:
    payload = _base_payload()
    payload["structure"]["7"] = [None, None]

    with pytest.raises(ValidationError, match=r"unreachable games: \[7\]"):
        BracketStructure.model_validate(payload)


def test_games_partial_coverage_allowed() -> None:
    payload = _base_payload()
    payload["games"] = {
        "0": [111, 222],
        "1": [333, 444],
        "2": [555, 666],
        "3": [777, 888],
    }
    bracket = BracketStructure.model_validate(payload)
    assert set(bracket.games) == {0, 1, 2, 3}


def test_games_unknown_game_key_fails() -> None:
    payload = _base_payload()
    payload["games"]["99"] = [111, 222]
    with pytest.raises(ValidationError, match=r"games contains unknown game id 99"):
        BracketStructure.model_validate(payload)


def test_games_wrong_number_of_slots_fails() -> None:
    payload = _base_payload()
    payload["games"]["0"] = [111]
    with pytest.raises(ValidationError):
        BracketStructure.model_validate(payload)


def test_games_duplicate_team_in_same_game_fails() -> None:
    payload = _base_payload()
    payload["games"]["0"] = [111, 111]
    with pytest.raises(ValidationError, match=r"cannot contain duplicate team id 111"):
        BracketStructure.model_validate(payload)


def test_games_unknown_team_id_fails_when_validating_with_teams() -> None:
    payload = _base_payload()
    payload["games"]["0"] = [999, 222]
    bracket = BracketStructure.model_validate(payload)
    teams = Team.from_json_file("definitions/teams.json")
    with pytest.raises(ValueError, match=r"unknown team id 999"):
        bracket.validate_with_teams(teams)


def test_feeder_consistency_valid_partial_downstream_assignment() -> None:
    payload = _base_payload()
    payload["games"]["4"] = [222, None]
    bracket = BracketStructure.model_validate(payload)
    assert bracket.games[4] == (222, None)


def test_feeder_consistency_invalid_downstream_assignment_fails() -> None:
    payload = _base_payload()
    payload["games"]["4"] = [555, None]
    with pytest.raises(ValidationError, match=r"left slot team 555 is not derivable"):
        BracketStructure.model_validate(payload)


def test_json_round_trip_with_games() -> None:
    source = Path("definitions/bracket.json").read_text(encoding="utf-8")
    bracket = BracketStructure.model_validate_json(source)
    rendered = bracket.to_json_str(indent=4)
    reparsed = BracketStructure.model_validate_json(rendered)

    assert reparsed == bracket
    assert reparsed.to_json_dict() == bracket.to_json_dict()


def test_load_tournament_validates_and_returns_models() -> None:
    bracket, teams = load_tournament("definitions/bracket.json", "definitions/teams.json")
    assert bracket.final_game == 6
    assert len(teams) == 8


def test_ascii_bracket_render_contains_connectors_and_truncated_names() -> None:
    bracket, teams = load_tournament("definitions/bracket.json", "definitions/teams.json")
    ascii_bracket = bracket.to_ascii_bracket(teams, name_width=10)

    assert "Kentucky" in ascii_bracket
    assert "Louisville" in ascii_bracket
    assert "Arizona St" in ascii_bracket
    assert "Arizona St." not in ascii_bracket
    assert "+" in ascii_bracket
    assert "|" in ascii_bracket
    assert "-" in ascii_bracket
    assert "Kentucky  -----+" not in ascii_bracket


def test_ascii_bracket_matches_reference_file_exactly() -> None:
    bracket, teams = load_tournament("definitions/bracket.json", "definitions/teams.json")
    expected = Path(".mayo/ascii.txt").read_text(encoding="utf-8").rstrip("\n")
    assert bracket.to_ascii_bracket(teams, name_width=10) == expected


def test_box_mode_uses_box_drawing_characters() -> None:
    bracket, teams = load_tournament("definitions/bracket.json", "definitions/teams.json")
    rendered = bracket.to_ascii_bracket(teams, name_width=10, mode="box")

    assert "╮" in rendered
    assert "╯" in rendered
    assert "─" in rendered
    assert "├" in rendered
