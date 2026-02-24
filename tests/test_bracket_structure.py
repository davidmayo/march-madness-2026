from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import BracketStructure


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
    }


def test_loads_sample_bracket_json() -> None:
    bracket = BracketStructure.from_json_file("definitions/bracket.json")
    assert bracket.final_game == 6
    assert bracket.structure[6] == (4, 5)
    assert bracket.structure[0] == (None, None)


def test_non_final_game_referenced_twice_fails() -> None:
    payload = _base_payload()
    payload["structure"]["6"] = [4, 2]
    payload["structure"]["5"] = [2, 3]

    with pytest.raises(ValidationError, match=r"Game 2 must be referenced by exactly 1 game"):
        BracketStructure.model_validate(payload)


def test_non_final_game_referenced_zero_times_fails() -> None:
    payload = _base_payload()
    payload["structure"]["6"] = [4, 5]
    payload["structure"]["4"] = [0, 0]

    with pytest.raises(ValidationError, match=r"Game 1 must be referenced by exactly 1 game"):
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


def test_mixed_null_non_null_inputs_fail() -> None:
    payload = _base_payload()
    payload["structure"]["0"] = [None, 1]

    with pytest.raises(
        ValidationError, match=r"Game 0 is non-leaf and must have two non-null inputs"
    ):
        BracketStructure.model_validate(payload)


def test_wrong_number_of_inputs_fail() -> None:
    payload = _base_payload()
    payload["structure"]["0"] = [None]

    with pytest.raises(ValidationError):
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


def test_json_round_trip() -> None:
    source = Path("definitions/bracket.json").read_text(encoding="utf-8")
    bracket = BracketStructure.model_validate_json(source)
    rendered = bracket.to_json_str(indent=4)
    reparsed = BracketStructure.model_validate_json(rendered)

    assert reparsed == bracket
    assert reparsed.to_json_dict() == bracket.to_json_dict()
