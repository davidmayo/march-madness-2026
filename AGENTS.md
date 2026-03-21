# Purpose
- We are trying to make an application here to run simulations of the NCAA tournament.

# Canonical Bracket Model
- The canonical 2026 men's bracket is defined in `src/march_madness/canonical_bracket.py`.
- That file is the source of truth for:
  - the fixed 64-team field
  - each team's ESPN ID and original seed
  - the exact round-to-round feed graph for all 63 games
  - any ESPN event IDs that can be assigned unambiguously
- Do not add fuzzy team-name matching or "best match" logic for bracket structure. A bracket game should be identified either by its canonical game key, its explicit ESPN event ID, or its exact participant ESPN team IDs once the feeder games are known.

# User Brackets
- Parsed user brackets live in `data/user-brackets/*.json`.
- The parser is in `src/march_madness/user_brackets.py`.
- `PickedTeam` now includes `team_id`, and those IDs are assigned by walking the canonical bracket graph, not by later name matching.
- If the saved NCAA HTML labels differ from ESPN labels, preserve the saved NCAA display names for user-facing data, but keep the ESPN `team_id` as the stable identifier.

# Scoring
- Scoring logic is in `src/march_madness/scoring.py`.
- `get_bracket_from_scoreboard_data(...)` should build from the canonical bracket definition, then apply scoreboard results deterministically.
- Completed scoreboard games should be matched only by:
  - explicit ESPN event ID when available
  - exact pair of participant ESPN team IDs when that game's slots are already known
- Winner propagation through later rounds should happen through the explicit bracket graph.

# Testing
- Regression tests for bracket parsing and scoring are in `tests/test_user_brackets.py`.
- If you change bracket structure, parsing, or scoring behavior, update those tests and run `PYTHONPATH=src UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`.

# uv
- We use uv for Python
- Run a script with `uv run ./path/to/file.py`
- Add a depednecy with `uv add pandas`

# Python
- You can assume Python 3.14
- All functions, methods, and classes should have at least a simple docstring.
- Modules should generally have a good docstring
- Functions/methods should be fully typed.
- You should use more explanatory/clarifying comments than Codex generally would
