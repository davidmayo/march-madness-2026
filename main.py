from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator


class Team(BaseModel):
    id: StrictInt
    name: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Team name must be non-empty.")
        return value

    @classmethod
    def from_json_file(cls, path: str | Path) -> list["Team"]:
        raw = Path(path).read_text(encoding="utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("Teams JSON must be a list of team objects.")
        return [cls.model_validate(item) for item in payload]


class BracketStructure(BaseModel):
    final_game: int
    structure: dict[int, tuple[int | None, int | None]]
    games: dict[int, tuple[int | None, int | None]] = Field(default_factory=dict)

    @classmethod
    def from_json_file(cls, path: str | Path) -> "BracketStructure":
        raw = Path(path).read_text(encoding="utf-8")
        return cls.model_validate_json(raw)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "final_game": self.final_game,
            "structure": {
                str(game): list(inputs) for game, inputs in self.structure.items()
            },
            "games": {
                str(game): list(inputs) for game, inputs in self.games.items()
            },
        }

    def to_json_str(self, indent: int = 4) -> str:
        return self.model_dump_json(indent=indent)

    def to_ascii_bracket(
        self,
        teams: list[Team],
        name_width: int = 10,
        *,
        mode: Literal["ascii", "box"] = "ascii",
    ) -> str:
        if name_width <= 0:
            raise ValueError("name_width must be positive.")

        team_names = {team.id: team.name for team in teams}
        parent_by_game: dict[int, tuple[int, int]] = {}
        for parent_game, (left_child, right_child) in self.structure.items():
            if left_child is not None:
                parent_by_game[left_child] = (parent_game, 0)
            if right_child is not None:
                parent_by_game[right_child] = (parent_game, 1)

        round_cache: dict[int, int] = {}

        def game_round(game_id: int) -> int:
            if game_id in round_cache:
                return round_cache[game_id]
            left_child, right_child = self.structure[game_id]
            if left_child is None and right_child is None:
                round_cache[game_id] = 0
            else:
                round_cache[game_id] = 1 + max(
                    game_round(left_child), game_round(right_child)
                )
            return round_cache[game_id]

        max_round = game_round(self.final_game)
        label_width = name_width + 1
        segment_width = 12

        slot_y: dict[tuple[int, int], int] = {}
        center_y: dict[int, int] = {}
        leaf_cursor = 0

        def assign_positions(game_id: int) -> None:
            nonlocal leaf_cursor
            left_child, right_child = self.structure[game_id]
            if left_child is None and right_child is None:
                top = leaf_cursor + 1
                bottom = leaf_cursor + 3
                slot_y[(game_id, 0)] = top
                slot_y[(game_id, 1)] = bottom
                center_y[game_id] = top + 1
                leaf_cursor += 5
                return

            assign_positions(left_child)
            assign_positions(right_child)
            left_center = center_y[left_child]
            right_center = center_y[right_child]
            slot_y[(game_id, 0)] = left_center
            slot_y[(game_id, 1)] = right_center
            center_y[game_id] = (left_center + right_center + 1) // 2

        assign_positions(self.final_game)

        max_y = leaf_cursor
        max_x = label_width + max_round * segment_width + 12
        canvas = [[" " for _ in range(max_x)] for _ in range(max_y)]

        def put(x: int, y: int, ch: str) -> None:
            if 0 <= x < max_x and 0 <= y < max_y:
                canvas[y][x] = ch

        def put_hline(x1: int, x2: int, y: int, ch: str = "-") -> None:
            for x in range(x1, x2 + 1):
                put(x, y, ch)

        def put_text(x: int, y: int, text: str) -> None:
            for i, ch in enumerate(text):
                put(x + i, y, ch)

        if mode == "ascii":
            h_char = "-"
            v_char = "|"
            top_junc = "+"
            bottom_junc = "+"
            tee_char = "|"
        else:
            h_char = "─"
            v_char = "│"
            top_junc = "╮"
            bottom_junc = "╯"
            tee_char = "├"

        def format_team(team_id: int | None) -> str:
            if team_id is None:
                return ""
            name = team_names.get(team_id, f"#{team_id}")
            return name[:name_width]

        game_order = sorted(self.structure.keys(), key=lambda g: (game_round(g), g))
        for game_id in game_order:
            r = game_round(game_id)
            x_junc = label_width + (r * segment_width)
            x_conn_start = 0 if r == 0 else x_junc - 11
            x_label = x_conn_start
            y_top = slot_y[(game_id, 0)]
            y_bottom = slot_y[(game_id, 1)]

            left_team, right_team = self.games.get(game_id, (None, None))
            if y_top - 1 >= 0:
                put_text(x_label, y_top - 1, format_team(left_team))
            if y_bottom - 1 >= 0:
                put_text(x_label, y_bottom - 1, format_team(right_team))

            put_hline(x_conn_start, x_junc - 1, y_top, h_char)
            put_hline(x_conn_start, x_junc - 1, y_bottom, h_char)
            put(x_junc, y_top, top_junc)
            put(x_junc, y_bottom, bottom_junc)
            for y in range(min(y_top, y_bottom) + 1, max(y_top, y_bottom)):
                put(x_junc, y, v_char)

            if game_id in parent_by_game:
                parent_game, side = parent_by_game[game_id]
                parent_round = game_round(parent_game)
                parent_x_junc = label_width + (parent_round * segment_width)
                parent_x_conn_start = parent_x_junc - 11
                target_y = slot_y[(parent_game, side)]
                if target_y == center_y[game_id]:
                    put(x_junc, target_y, tee_char)
                    put_hline(x_junc + 1, parent_x_conn_start - 1, target_y, h_char)
            elif game_id == self.final_game:
                put_hline(x_junc + 1, x_junc + 11, center_y[game_id], h_char)

        output_lines = ["".join(row).rstrip() for row in canvas]
        return "\n".join(output_lines).rstrip()

    def _possible_winners(
        self, game_id: int, cache: dict[int, set[int]] | None = None
    ) -> set[int]:
        if cache is None:
            cache = {}
        if game_id in cache:
            return cache[game_id]

        left_feeder, right_feeder = self.structure[game_id]
        left_team, right_team = self.games.get(game_id, (None, None))
        candidates: set[int] = set()

        if left_feeder is None and right_feeder is None:
            if left_team is not None:
                candidates.add(left_team)
            if right_team is not None:
                candidates.add(right_team)
        else:
            if left_team is not None:
                left_candidates = {left_team}
            else:
                left_candidates = self._possible_winners(left_feeder, cache)

            if right_team is not None:
                right_candidates = {right_team}
            else:
                right_candidates = self._possible_winners(right_feeder, cache)

            candidates.update(left_candidates)
            candidates.update(right_candidates)

        cache[game_id] = candidates
        return candidates

    def validate_with_teams(self, teams: list[Team]) -> "BracketStructure":
        errors: list[str] = []
        valid_team_ids = {team.id for team in teams}

        for game_id, (left_team, right_team) in self.games.items():
            for team_id in (left_team, right_team):
                if team_id is not None and team_id not in valid_team_ids:
                    errors.append(
                        f"Game {game_id} references unknown team id {team_id}."
                    )

        if errors:
            raise ValueError("Invalid games section: " + " ".join(errors))

        return self

    @model_validator(mode="after")
    def _validate_bracket(self) -> "BracketStructure":
        game_ids = set(self.structure.keys())
        errors: list[str] = []

        if self.final_game not in game_ids:
            errors.append(
                f"final_game {self.final_game} must exist as a key in structure."
            )

        incoming_counts = {game_id: 0 for game_id in self.structure}
        leaf_count = 0

        for game_id, inputs in self.structure.items():
            if len(inputs) != 2:
                errors.append(
                    f"Game {game_id} must have exactly two input slots, got {len(inputs)}."
                )
                continue

            left, right = inputs

            if left is None and right is None:
                leaf_count += 1
                continue

            if left is None or right is None:
                errors.append(
                    f"Game {game_id} is non-leaf and must have two non-null inputs, got {inputs}."
                )
                continue

            for child in (left, right):
                if child == game_id:
                    errors.append(
                        f"Game {game_id} cannot reference itself as an input."
                    )
                    continue
                if child not in game_ids:
                    errors.append(
                        f"Game {game_id} references missing input game {child}."
                    )
                    continue
                incoming_counts[child] += 1

        if leaf_count == 0:
            errors.append(
                "Bracket must contain at least one leaf game with [null, null] inputs."
            )

        for game_id, count in incoming_counts.items():
            if game_id == self.final_game:
                if count != 0:
                    errors.append(
                        f"final_game {game_id} must be referenced by exactly 0 games, got {count}."
                    )
            elif count != 1:
                errors.append(
                    f"Game {game_id} must be referenced by exactly 1 game, got {count}."
                )

        for game_id, teams in self.games.items():
            if game_id not in game_ids:
                errors.append(
                    f"games contains unknown game id {game_id}; expected ids in structure."
                )
                continue

            left_team, right_team = teams
            if (
                left_team is not None
                and right_team is not None
                and left_team == right_team
            ):
                errors.append(
                    f"Game {game_id} cannot contain duplicate team id {left_team} in both slots."
                )

        if self.final_game in game_ids:
            visiting: set[int] = set()
            visited: set[int] = set()

            def dfs(node: int) -> None:
                if node in visiting:
                    errors.append(f"Cycle detected at game {node}.")
                    return
                if node in visited:
                    return
                visiting.add(node)
                left, right = self.structure[node]
                for child in (left, right):
                    if child is not None and child in self.structure:
                        dfs(child)
                visiting.remove(node)
                visited.add(node)

            dfs(self.final_game)

            unreachable = game_ids - visited
            if unreachable:
                errors.append(
                    "All games must be reachable from final_game "
                    f"{self.final_game}; unreachable games: {sorted(unreachable)}."
                )

        if not errors:
            cache: dict[int, set[int]] = {}
            for game_id, (left_feeder, right_feeder) in self.structure.items():
                if left_feeder is None and right_feeder is None:
                    continue

                left_team, right_team = self.games.get(game_id, (None, None))

                if left_team is not None:
                    left_possible = self._possible_winners(left_feeder, cache)
                    if left_team not in left_possible:
                        errors.append(
                            f"Game {game_id} left slot team {left_team} is not derivable "
                            f"from feeder game {left_feeder}; possible: {sorted(left_possible)}."
                        )

                if right_team is not None:
                    right_possible = self._possible_winners(right_feeder, cache)
                    if right_team not in right_possible:
                        errors.append(
                            f"Game {game_id} right slot team {right_team} is not derivable "
                            f"from feeder game {right_feeder}; possible: {sorted(right_possible)}."
                        )

        if errors:
            raise ValueError("Invalid bracket structure: " + " ".join(errors))

        return self


def load_tournament(
    bracket_path: str | Path, teams_path: str | Path
) -> tuple[BracketStructure, list[Team]]:
    teams = Team.from_json_file(teams_path)
    bracket = BracketStructure.from_json_file(bracket_path)
    bracket.validate_with_teams(teams)
    return bracket, teams
