from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator


class BracketStructure(BaseModel):
    final_game: int
    structure: dict[int, tuple[int | None, int | None]]

    @classmethod
    def from_json_file(cls, path: str | Path) -> "BracketStructure":
        raw = Path(path).read_text(encoding="utf-8")
        return cls.model_validate_json(raw)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "final_game": self.final_game,
            "structure": {str(game): list(inputs) for game, inputs in self.structure.items()},
        }

    def to_json_str(self, indent: int = 4) -> str:
        return self.model_dump_json(indent=indent)

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
                    errors.append(f"Game {game_id} cannot reference itself as an input.")
                    continue
                if child not in game_ids:
                    errors.append(
                        f"Game {game_id} references missing input game {child}."
                    )
                    continue
                incoming_counts[child] += 1

        if leaf_count == 0:
            errors.append("Bracket must contain at least one leaf game with [null, null] inputs.")

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

        if errors:
            raise ValueError("Invalid bracket structure: " + " ".join(errors))

        return self
