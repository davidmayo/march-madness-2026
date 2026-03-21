"""Render the mostly static frontend pages from the saved tournament data.

This module keeps the web layer intentionally simple:
- page content is built from the existing JSON exports and scoring logic
- HTML is rendered with small Python helpers instead of a template dependency
- the result stays easy to serve from FastAPI today and easy to pre-render later
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from pathlib import Path
import math

import plotly.graph_objects as go
import plotly.io as pio

from march_madness.canonical_bracket import REGION_ORDER
from march_madness.canonical_bracket import ROUND_NAMES
from march_madness.canonical_bracket import canonical_team_name_by_id
from march_madness.predictions import PredictionHistoryCheckpoint
from march_madness.predictions import TournamentPredictionHistory
from march_madness.predictions import UserPredictionHistorySeries
from march_madness.predictions import load_prediction_history
from march_madness.scoring import get_bracket_from_scoreboard_data
from march_madness.scoring import load_saved_user_brackets
from march_madness.scoring import score_saved_user_brackets
from march_madness.scrape import Bracket
from march_madness.user_brackets import BracketGamePick
from march_madness.user_brackets import UserBracket


ROOT = Path(__file__).resolve().parents[3]
SCOREBOARD_PATH = ROOT / "data" / "espn" / "api" / "scoreboard.json"
USER_CATEGORY_FILTER_VALUES: tuple[str, ...] = ("all", "student", "staff")


@dataclass(frozen=True)
class BracketLink:
    """Represent one selectable saved user bracket."""

    slug: str
    user_name: str
    entry_name: str
    user_categories: tuple[str, ...]


@dataclass(frozen=True)
class StandingsRow:
    """Represent one row in the rendered standings table."""

    slug: str
    user_name: str
    current_score: float
    max_possible_score: float | None
    correctly_picked_games: int | None
    incorrectly_picked_games: int | None
    champion_pick: str
    user_categories: tuple[str, ...]


@dataclass(frozen=True)
class SiteData:
    """Collect the loaded data needed by the frontend pages."""

    bracket_links: tuple[BracketLink, ...]
    bracket_lookup: dict[str, UserBracket]
    standings: tuple[StandingsRow, ...]
    standings_by_slug: dict[str, StandingsRow]
    reference_bracket: Bracket
    completed_game_count: int


@lru_cache(maxsize=1)
def load_site_data() -> SiteData:
    """Load the current standings and bracket data for the frontend."""

    scoreboard_blob = json.loads(SCOREBOARD_PATH.read_text())
    reference_bracket = get_bracket_from_scoreboard_data(scoreboard_blob)
    loaded_user_brackets = load_saved_user_brackets()

    bracket_links = tuple(
        sorted(
            (
                BracketLink(
                    slug=path.stem,
                    user_name=user_bracket.bracket_metadata.user_name,
                    entry_name=user_bracket.bracket_metadata.entry_name,
                    user_categories=tuple(user_bracket.bracket_metadata.user_categories),
                )
                for path, user_bracket in loaded_user_brackets
            ),
            key=lambda link: (link.user_name, link.slug),
        )
    )
    bracket_lookup = {path.stem: user_bracket for path, user_bracket in loaded_user_brackets}

    standings_rows = tuple(
        StandingsRow(
            slug=path.stem,
            user_name=user_bracket.bracket_metadata.user_name,
            current_score=result.current_score,
            max_possible_score=result.max_possible_score,
            correctly_picked_games=result.correctly_picked_games,
            incorrectly_picked_games=result.incorrectly_picked_games,
            champion_pick=user_bracket.bracket_picks.champion.team_name,
            user_categories=tuple(user_bracket.bracket_metadata.user_categories),
        )
        for path, user_bracket, result in score_saved_user_brackets(
            reference_bracket,
            calculate_details=True,
        )
    )
    standings_by_slug = {row.slug: row for row in standings_rows}
    completed_game_count = sum(1 for game in reference_bracket.games if game.winner_team_id is not None)

    return SiteData(
        bracket_links=bracket_links,
        bracket_lookup=bracket_lookup,
        standings=standings_rows,
        standings_by_slug=standings_by_slug,
        reference_bracket=reference_bracket,
        completed_game_count=completed_game_count,
    )


@lru_cache(maxsize=1)
def load_prediction_history_data() -> TournamentPredictionHistory:
    """Load the cached prediction-history dataset for the frontend."""

    return load_prediction_history()


def default_bracket_slug() -> str:
    """Return the preferred default bracket slug for the viewer."""

    site_data = load_site_data()
    if "david-mayo" in site_data.bracket_lookup:
        return "david-mayo"
    return site_data.bracket_links[0].slug


def render_standings_page(user_category_filter: str = "all") -> str:
    """Render the current standings page."""

    site_data = load_site_data()
    normalized_filter = _normalize_user_category_filter(user_category_filter)
    filtered_rows = _filter_standings_rows(site_data.standings, normalized_filter)
    leader = filtered_rows[0] if filtered_rows else None
    highest_ceiling = max((row.max_possible_score or 0.0) for row in filtered_rows) if filtered_rows else 0.0

    summary_cards = "".join(
        (
            _render_stat_card("Viewing", _category_filter_label(normalized_filter)),
            _render_stat_card("Visible Brackets", str(len(filtered_rows))),
            _render_stat_card("Highest Ceiling", _format_score(highest_ceiling)),
            _render_stat_card("Completed Games", f"{site_data.completed_game_count} of 63"),
        )
    )
    if leader is not None:
        summary_cards = "".join(
            (
                summary_cards,
                _render_stat_card("Current Leader", leader.user_name),
                _render_stat_card("Top Score", _format_score(leader.current_score)),
            )
        )

    rows_html = "".join(_render_standings_row(rank, row) for rank, row in enumerate(filtered_rows, start=1))
    empty_state_html = ""
    if not filtered_rows:
        empty_state_html = """
        <div class="empty-state">
            <h3>No brackets in this category</h3>
            <p>Try switching to a different standings filter.</p>
        </div>
        """
    body = f"""
    <section class="hero">
        <p class="eyebrow">Traditional scoring</p>
        <h1>Current Standings</h1>
        <p class="lede">
            Live standings computed from the local ESPN scoreboard snapshot and the saved NCAA bracket picks.
        </p>
        <div class="filter-tabs">
            {_render_standings_filter_tabs(normalized_filter)}
        </div>
        <div class="stat-grid">{summary_cards}</div>
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Group table</p>
                <h2>Standings Board</h2>
            </div>
            <p class="panel-note">Current score, remaining ceiling, pick accuracy, and category.</p>
        </div>
        {empty_state_html}
        <div class="table-wrap">
            <table class="standings-table">
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>Name</th>
                        <th>Category</th>
                        <th>Score</th>
                        <th>Max Possible</th>
                        <th>Correct</th>
                        <th>Incorrect</th>
                        <th>Champion Pick</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </section>
    """
    return _render_page_shell(
        title="Standings",
        active_path="/standings",
        page_body=body,
    )


def render_bracket_page(bracket_slug: str) -> tuple[int, str]:
    """Render one user's bracket page and return ``(status_code, html)``."""

    site_data = load_site_data()
    user_bracket = site_data.bracket_lookup.get(bracket_slug)
    if user_bracket is None:
        html = render_not_found_page(
            title="Bracket Not Found",
            message=f"No saved bracket exists for {bracket_slug!r}.",
        )
        return 404, html

    standings_row = site_data.standings_by_slug.get(bracket_slug)
    reference_lookup = {game.id: game for game in site_data.reference_bracket.games}
    regional_sections = "".join(
        _render_region_section(
            region=region,
            user_bracket=user_bracket,
            reference_lookup=reference_lookup,
        )
        for region in REGION_ORDER
    )
    final_rounds_section = _render_final_rounds_section(
        user_bracket=user_bracket,
        reference_lookup=reference_lookup,
    )

    score_card = "Not yet scored"
    max_card = "Pending"
    if standings_row is not None:
        score_card = _format_score(standings_row.current_score)
        max_card = _format_score(standings_row.max_possible_score)

    champion = user_bracket.bracket_picks.champion
    tiebreaker = user_bracket.bracket_picks.tiebreaker
    bracket_selector_html = _render_bracket_selector(site_data, bracket_slug)
    category_badges = _render_category_badges(tuple(user_bracket.bracket_metadata.user_categories))
    body = f"""
    <section class="hero">
        <p class="eyebrow">Bracket viewer</p>
        <h1>{escape(user_bracket.bracket_metadata.user_name)}</h1>
        <div class="hero-topline">
            <p class="lede">Entry name: {escape(user_bracket.bracket_metadata.entry_name)}</p>
            {category_badges}
        </div>
        <div class="hero-controls">
            {bracket_selector_html}
        </div>
        <div class="stat-grid">
            {_render_stat_card("Current Score", score_card)}
            {_render_stat_card("Max Possible", max_card)}
            {_render_stat_card("Champion Pick", _format_team_label(champion.seed, champion.team_name))}
            {_render_stat_card("Tiebreaker", f"{tiebreaker.winner_score} to {tiebreaker.loser_score}")}
        </div>
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Regional picks</p>
                <h2>Road To Indianapolis</h2>
            </div>
            <p class="panel-note">Picked matchups and winners, with completed-game status when available.</p>
        </div>
        {regional_sections}
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Final rounds</p>
                <h2>Final Four And Title Game</h2>
            </div>
        </div>
        {final_rounds_section}
    </section>
    """
    html = _render_page_shell(
        title=f"{user_bracket.bracket_metadata.user_name} Bracket",
        active_path=f"/brackets/{bracket_slug}",
        page_body=body,
    )
    return 200, html


def render_prediction_page() -> str:
    """Render the Monte Carlo prediction-history page."""

    prediction_history = load_prediction_history_data()
    checkpoints = prediction_history.checkpoints
    chart_series = [series for series in prediction_history.users if series.points]
    chart_series.sort(
        key=lambda series: (
            series.points[-1].average_finishing_position,
            -series.points[-1].winning_percentage,
            series.user_name,
        )
    )

    current_leader = chart_series[0] if chart_series else None
    win_probability_leader = max(
        chart_series,
        key=lambda series: series.points[-1].winning_percentage,
        default=None,
    )
    summary_cards = "".join(
        (
            _render_stat_card("Simulations Per Checkpoint", _format_score(float(prediction_history.simulation_count))),
            _render_stat_card("Checkpoints", _format_score(float(len(checkpoints)))),
            _render_stat_card("Latest Completed Games", _format_score(float(prediction_history.completed_game_count_max))),
        )
    )
    if current_leader is not None:
        summary_cards = "".join(
            (
                summary_cards,
                _render_stat_card("Current Best Avg Finish", current_leader.user_name),
                _render_stat_card(
                    "Current Avg Finish",
                    _format_score(current_leader.points[-1].average_finishing_position),
                ),
            )
        )
    if win_probability_leader is not None:
        summary_cards = "".join(
            (
                summary_cards,
                _render_stat_card("Highest Win Rate", win_probability_leader.user_name),
                _render_stat_card(
                    "Current Win Rate",
                    _format_percent(win_probability_leader.points[-1].winning_percentage),
                ),
            )
        )

    average_finish_chart = _render_prediction_history_chart(
        chart_id="prediction-average-finish-chart",
        checkpoints=checkpoints,
        series_list=chart_series,
        metric_name="average_finishing_position",
        y_axis_label="Average Finish",
        hover_label="Average Finish",
        y_min=1.0,
        y_max=float(max(len(chart_series), 1)),
        y_tick_values=_average_finish_tick_values(len(chart_series)),
        invert_y_axis=True,
        include_plotlyjs=True,
    )
    average_score_y_min, average_score_y_max, average_score_ticks = _average_score_axis(
        chart_series,
    )
    average_score_chart = _render_prediction_history_chart(
        chart_id="prediction-average-points-chart",
        checkpoints=checkpoints,
        series_list=chart_series,
        metric_name="average_score",
        y_axis_label="Average Points",
        hover_label="Average Points",
        y_min=average_score_y_min,
        y_max=average_score_y_max,
        y_tick_values=average_score_ticks,
    )
    winning_percentage_y_min, winning_percentage_y_max, winning_percentage_ticks = _winning_percentage_axis(
        chart_series,
    )
    winning_percentage_chart = _render_prediction_history_chart(
        chart_id="prediction-winning-percentage-chart",
        checkpoints=checkpoints,
        series_list=chart_series,
        metric_name="winning_percentage",
        y_axis_label="Winning Percentage",
        hover_label="Winning Percentage",
        y_min=winning_percentage_y_min,
        y_max=winning_percentage_y_max,
        y_tick_values=winning_percentage_ticks,
        show_interval_band=False,
    )
    default_table_series = sorted(
        chart_series,
        key=lambda series: (
            -series.points[-1].winning_percentage,
            series.points[-1].average_finishing_position,
            series.user_name,
        ),
    )
    rows_html = "".join(_render_prediction_snapshot_row(series) for series in default_table_series)
    body = f"""
    <section class="hero">
        <p class="eyebrow">Monte Carlo history</p>
        <h1>Prediction Engine</h1>
        <p class="lede">
            {prediction_history.simulation_count:,} KenPom-driven simulations were rerun after every completed game,
            starting from the untouched bracket and continuing through the latest scoreboard snapshot.
        </p>
        <fieldset class="prediction-scope-controls" aria-label="Prediction scope">
            <legend>View Scope</legend>
            <label class="prediction-scope-option">
                <input type="radio" name="prediction_scope" value="all" checked>
                <span>Everyone</span>
            </label>
            <label class="prediction-scope-option">
                <input type="radio" name="prediction_scope" value="student">
                <span>Students</span>
            </label>
            <label class="prediction-scope-option">
                <input type="radio" name="prediction_scope" value="staff">
                <span>Staff</span>
            </label>
        </fieldset>
        <div class="stat-grid">{summary_cards}</div>
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Trend graph</p>
                <h2>Average Finish Over Time</h2>
            </div>
            <p class="panel-note">Each checkpoint is labeled by the newly completed winner, with first place shown at the top.</p>
        </div>
        {average_finish_chart}
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Scoring outlook</p>
                <h2>Average Points Over Time</h2>
            </div>
            <p class="panel-note">Higher is better. These lines track expected traditional points as the tournament advances.</p>
        </div>
        {average_score_chart}
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Win equity</p>
                <h2>Winning Percentage Over Time</h2>
            </div>
            <p class="panel-note">A win counts any simulation where a user shared first place.</p>
        </div>
        {winning_percentage_chart}
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Latest checkpoint</p>
                <h2>Current Forecast Snapshot</h2>
            </div>
            <p class="panel-note">Sorted by the most recent average finishing position.</p>
        </div>
        <div class="table-wrap">
            <table class="standings-table prediction-table">
                <thead>
                    <tr>
                        <th><button class="table-sort-button" type="button" data-sort-key="user_name" data-default-direction="asc">Name</button></th>
                        <th><button class="table-sort-button" type="button" data-sort-key="category" data-default-direction="asc">Category</button></th>
                        <th><button class="table-sort-button" type="button" data-sort-key="average_score" data-default-direction="desc">Avg Points</button></th>
                        <th><button class="table-sort-button" type="button" data-sort-key="average_finish" data-default-direction="asc">Avg Finish</button></th>
                        <th><button class="table-sort-button is-active is-desc" type="button" data-sort-key="winning_percentage" data-default-direction="desc">Winning %</button></th>
                    </tr>
                </thead>
                <tbody id="prediction-snapshot-body">{rows_html}</tbody>
            </table>
        </div>
    </section>
    """
    return _render_page_shell(
        title="Prediction",
        active_path="/prediction",
        page_body=body,
        extra_body_html=_render_prediction_page_script(prediction_history),
    )


def render_historical_page() -> str:
    """Render the placeholder historical page."""

    body = """
    <section class="hero">
        <p class="eyebrow">Archive</p>
        <h1>Historical Results</h1>
        <p class="lede">
            This page will eventually collect prior scoreboard snapshots, leaderboard movement,
            and bracket-elimination history over time.
        </p>
    </section>
    <section class="panel placeholder-panel">
        <h2>Planned Content</h2>
        <p>
            The intended direction is a static-friendly archive of daily standings, completed games,
            and notable swings in each user bracket's maximum possible score.
        </p>
    </section>
    """
    return _render_page_shell(
        title="Historical",
        active_path="/historical",
        page_body=body,
    )


def render_not_found_page(title: str, message: str) -> str:
    """Render a simple site-styled not-found page."""

    body = f"""
    <section class="hero">
        <p class="eyebrow">Not found</p>
        <h1>{escape(title)}</h1>
        <p class="lede">{escape(message)}</p>
    </section>
    """
    return _render_page_shell(
        title=title,
        active_path="",
        page_body=body,
    )


def _render_page_shell(
    *,
    title: str,
    active_path: str,
    page_body: str,
    extra_body_html: str = "",
) -> str:
    """Wrap page-specific HTML with the shared site shell."""

    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)} | March Madness 2026</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div class="page-noise"></div>
    <header class="site-header">
        <a class="brand" href="/standings">
            <span class="brand-mark">MM</span>
            <span class="brand-copy">
                <span class="brand-title">March Madness 2026</span>
                <span class="brand-subtitle">Saved brackets, live standings, static-first frontend</span>
            </span>
        </a>
        <nav class="desktop-nav">
            {_render_main_nav_links(active_path)}
        </nav>
    </header>
    <main class="site-main">
        {page_body}
    </main>
    {extra_body_html}
</body>
</html>
"""


def _render_main_nav_links(active_path: str) -> str:
    """Render the compact desktop nav."""

    links = [
        ("Brackets", f"/brackets/{default_bracket_slug()}", active_path.startswith("/brackets")),
        ("Standings", "/standings", active_path == "/standings"),
        ("Prediction", "/prediction", active_path == "/prediction"),
        ("Historical", "/historical", active_path == "/historical"),
    ]
    return "".join(
        f'<a class="nav-link{" is-active" if is_active else ""}" href="{escape(href)}">{escape(label)}</a>'
        for label, href, is_active in links
    )

def _render_bracket_selector(site_data: SiteData, current_bracket_slug: str) -> str:
    """Render the bracket-switcher form shown only on bracket pages."""

    options_html = "".join(
        (
            f'<option value="{escape(link.slug)}"{" selected" if link.slug == current_bracket_slug else ""}>'
            f'{escape(link.user_name)}'
            f"</option>"
        )
        for link in site_data.bracket_links
    )
    return f"""
    <form class="bracket-selector" action="/brackets" method="get">
        <label for="bracket_slug">Switch bracket</label>
        <div class="selector-row">
            <select id="bracket_slug" name="bracket_slug">
                {options_html}
            </select>
            <button type="submit">View</button>
        </div>
    </form>
    """


def _render_region_section(region: str, user_bracket: UserBracket, reference_lookup: dict[str, object]) -> str:
    """Render one region's rounds in a bracket-like four-column grid."""

    columns = "".join(
        _render_round_column(
            region=region,
            round_id=round_id,
            user_bracket=user_bracket,
            reference_lookup=reference_lookup,
        )
        for round_id in range(1, 5)
    )
    return f"""
    <section class="region-card">
        <div class="region-header">
            <p class="section-kicker">Region</p>
            <h3>{escape(region.title())}</h3>
        </div>
        <div class="region-scroll">
            <div class="round-grid bracket-round-grid">{columns}</div>
        </div>
    </section>
    """


def _render_round_column(
    *,
    region: str,
    round_id: int,
    user_bracket: UserBracket,
    reference_lookup: dict[str, object],
) -> str:
    """Render all games for one region/round combination."""

    games = [
        game
        for game in user_bracket.bracket_picks.games
        if game.region == region and game.round_id == round_id
    ]
    games.sort(key=lambda game: game.matchup_index)
    cards_html = "".join(
        _render_game_card(
            game,
            reference_lookup,
            slot_center=_region_round_slot_center(round_id, game.matchup_index),
            show_left_connector=round_id > 1,
            show_right_connector=round_id < 4,
        )
        for game in games
    )
    connectors_html = _render_round_connectors(round_id, len(games))
    return f"""
    <section class="round-column round-{round_id}">
        <h4>{escape(ROUND_NAMES[round_id])}</h4>
        <div class="round-ladder">
            {cards_html}
            {connectors_html}
        </div>
    </section>
    """


def _render_final_rounds_section(user_bracket: UserBracket, reference_lookup: dict[str, object]) -> str:
    """Render the Final Four and championship picks."""

    final_four_games = [
        game for game in user_bracket.bracket_picks.games if game.round_id == 5
    ]
    championship_games = [
        game for game in user_bracket.bracket_picks.games if game.round_id == 6
    ]
    final_four_games.sort(key=lambda game: game.matchup_index)
    championship_games.sort(key=lambda game: game.matchup_index)

    final_four_cards = "".join(
        _render_game_card(
            game,
            reference_lookup,
            slot_center=slot_center,
            show_left_connector=False,
            show_right_connector=True,
        )
        for game, slot_center in zip(final_four_games, (2, 6), strict=False)
    )
    final_four_connectors = _render_final_round_connectors(len(final_four_games))
    championship_cards = "".join(
        _render_game_card(
            game,
            reference_lookup,
            slot_center=4,
            show_left_connector=True,
            show_right_connector=False,
        )
        for game in championship_games
    )
    return f"""
    <div class="final-round-grid">
        <section class="round-column final-round-column final-four-column">
            <h4>Final Four</h4>
            <div class="final-round-ladder">
                {final_four_cards}
                {final_four_connectors}
            </div>
        </section>
        <section class="round-column final-round-column championship-column">
            <h4>Championship</h4>
            <div class="final-round-ladder">{championship_cards}</div>
        </section>
    </div>
    """


def _render_game_card(
    game: BracketGamePick,
    reference_lookup: dict[str, object],
    *,
    slot_center: int | None = None,
    show_left_connector: bool = False,
    show_right_connector: bool = False,
) -> str:
    """Render a single picked game node with live-result status when available."""

    reference_game = reference_lookup.get(game.game_key)
    status_class = "pending"
    status_label = "Pending"

    if reference_game is not None and getattr(reference_game, "winner_team_id", None) is not None:
        actual_winner_id = getattr(reference_game, "winner_team_id")
        if game.picked_winner.team_id == actual_winner_id:
            status_class = "correct"
            status_label = "Correct"
        else:
            status_class = "wrong"
            status_label = "Miss"
    actual_winner_name = None
    if reference_game is not None and getattr(reference_game, "winner_team_id", None) is not None:
        actual_winner_id = getattr(reference_game, "winner_team_id")
        actual_winner_name = canonical_team_name_by_id().get(actual_winner_id, actual_winner_id)

    team_1_classes = "node-team"
    team_2_classes = "node-team"
    if game.picked_team_1.team_id == game.picked_winner.team_id:
        team_1_classes += " is-picked-winner"
    if game.picked_team_2.team_id == game.picked_winner.team_id:
        team_2_classes += " is-picked-winner"

    footer_label = f"Picked {game.picked_winner.team_name}"
    if actual_winner_name is not None:
        footer_label += f" • Actual {actual_winner_name}"

    class_names = ["game-card", "bracket-node", status_class]
    if show_left_connector:
        class_names.append("has-left-connector")
    if show_right_connector:
        class_names.append("has-right-connector")
    style_attribute = ""
    if slot_center is not None:
        style_attribute = f' style="--slot-center: {slot_center};"'

    return f"""
    <article class="{' '.join(class_names)}"{style_attribute}>
        <div class="game-card-head node-head">
            <span class="game-index">Game {game.matchup_index}</span>
            <span class="status-pill {status_class}">{escape(status_label)}</span>
        </div>
        <div class="{team_1_classes}">
            <span class="seed-chip">{escape(game.picked_team_1.seed)}</span>
            <span class="team-name">{escape(game.picked_team_1.team_name)}</span>
        </div>
        <div class="{team_2_classes}">
            <span class="seed-chip">{escape(game.picked_team_2.seed)}</span>
            <span class="team-name">{escape(game.picked_team_2.team_name)}</span>
        </div>
        <p class="node-foot">{escape(footer_label)}</p>
    </article>
    """


def _render_round_connectors(round_id: int, game_count: int) -> str:
    """Render the vertical connector spines for one region-round column."""

    if round_id >= 4 or game_count < 2:
        return ""

    connector_segments = []
    for pair_index in range(game_count // 2):
        top_matchup_index = (pair_index * 2) + 1
        bottom_matchup_index = top_matchup_index + 1
        connector_segments.append(
            _render_connector_segment(
                top_slot=_region_round_slot_center(round_id, top_matchup_index),
                bottom_slot=_region_round_slot_center(round_id, bottom_matchup_index),
            )
        )
    return f'<div class="round-connector-layer">{"".join(connector_segments)}</div>'


def _region_round_slot_center(round_id: int, matchup_index: int) -> int:
    """Return the slot-center used to place one regional game in the bracket UI."""

    return (2**round_id * matchup_index) - (2 ** (round_id - 1))


def _render_final_round_connectors(game_count: int) -> str:
    """Render the connector spine between the two semifinal games."""

    if game_count < 2:
        return ""
    return f'<div class="round-connector-layer">{_render_connector_segment(top_slot=2, bottom_slot=6)}</div>'


def _render_connector_segment(*, top_slot: int, bottom_slot: int) -> str:
    """Render one vertical connector segment between two game centers."""

    return f"""
    <span
        class="round-connector-segment"
        style="--connector-top-slot: {top_slot};
               --connector-bottom-slot: {bottom_slot};"
    ></span>
    """


def _render_standings_row(rank: int, row: StandingsRow) -> str:
    """Render one row in the standings table."""

    return f"""
    <tr>
        <td>{rank}</td>
        <td>
            <a class="standings-link" href="/brackets/{escape(row.slug)}">{escape(row.user_name)}</a>
        </td>
        <td>{_render_table_category_badges(row.user_categories)}</td>
        <td>{_format_score(row.current_score)}</td>
        <td>{_format_score(row.max_possible_score)}</td>
        <td>{row.correctly_picked_games if row.correctly_picked_games is not None else "—"}</td>
        <td>{row.incorrectly_picked_games if row.incorrectly_picked_games is not None else "—"}</td>
        <td>{escape(row.champion_pick)}</td>
    </tr>
    """


def _render_prediction_snapshot_row(series: UserPredictionHistorySeries) -> str:
    """Render one row in the latest-checkpoint prediction snapshot table."""

    latest_point = series.points[-1]
    return f"""
    <tr>
        <td>
            <a class="standings-link" href="/brackets/{escape(series.slug)}">{escape(series.user_name)}</a>
        </td>
        <td>{_render_table_category_badges(tuple(series.user_categories))}</td>
        <td>{_format_score(latest_point.average_score)}</td>
        <td>{_format_score(latest_point.average_finishing_position)}</td>
        <td>{_format_percent(latest_point.winning_percentage)}</td>
    </tr>
    """


def _render_prediction_history_chart(
    *,
    chart_id: str,
    checkpoints: list[PredictionHistoryCheckpoint],
    series_list: list[UserPredictionHistorySeries],
    metric_name: str,
    y_axis_label: str,
    hover_label: str,
    y_min: float,
    y_max: float,
    y_tick_values: list[float],
    invert_y_axis: bool = False,
    include_plotlyjs: bool = False,
    show_interval_band: bool = True,
) -> str:
    """Render one Plotly line chart for the prediction-history page."""

    checkpoint_labels = [checkpoint.label for checkpoint in checkpoints]
    label_by_games_completed = {
        checkpoint.games_completed: checkpoint.label
        for checkpoint in checkpoints
    }
    figure = go.Figure()
    trace_map: list[dict[str, int]] = []
    interval_lower_field, interval_upper_field = _prediction_interval_fields(metric_name)

    for index, series in enumerate(series_list):
        color = _prediction_series_color(index)
        line_dash = _prediction_series_dash(series)
        y_values = [
            getattr(point, metric_name)
            for point in series.points
        ]
        x_values = [
            label_by_games_completed[point.games_completed]
            for point in series.points
        ]
        interval_lower_values = []
        interval_upper_values = []
        if show_interval_band:
            interval_lower_values = [
                getattr(point, interval_lower_field)
                for point in series.points
            ]
            interval_upper_values = [
                getattr(point, interval_upper_field)
                for point in series.points
            ]
        hover_template = (
            f"<b>{escape(series.user_name)}</b><br>"
            "%{x}<br>"
            f"{hover_label}: %{{y:.1f}}"
            f"{'%' if metric_name == 'winning_percentage' else ''}"
            "<extra></extra>"
        )
        line_trace_index = len(figure.data)
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=y_values,
                name=series.user_name,
                mode="lines+markers",
                line={
                    "color": color,
                    "width": 3,
                    "dash": line_dash,
                },
                marker={
                    "size": 7,
                    "color": color,
                },
                hovertemplate=hover_template,
            )
        )
        band_trace_index: int | None = None
        if show_interval_band:
            band_trace_index = len(figure.data)
            figure.add_trace(
                go.Scatter(
                    x=x_values + list(reversed(x_values)),
                    y=interval_upper_values + list(reversed(interval_lower_values)),
                    name=f"{series.user_name} 80% interval",
                    mode="lines",
                    line={
                        "color": "rgba(0, 0, 0, 0)",
                        "width": 0,
                    },
                    fill="toself",
                    fillcolor=_prediction_series_fill_color(color),
                    hoverinfo="skip",
                    showlegend=False,
                    visible=False,
                )
            )
        trace_map.append(
            {
                "line": line_trace_index,
                "band": band_trace_index,
            }
        )

    y_axis_range = [y_max, y_min] if invert_y_axis else [y_min, y_max]
    figure.update_layout(
        height=520,
        margin={
            "l": 74,
            "r": 24,
            "t": 24,
            "b": 150,
        },
        paper_bgcolor="rgba(0, 0, 0, 0)",
        plot_bgcolor="rgba(255, 252, 247, 0.78)",
        hovermode="closest",
        showlegend=True,
        legend={
            "orientation": "h",
            "x": 0.0,
            "xanchor": "left",
            "y": -0.34,
            "yanchor": "top",
            "font": {
                "size": 10,
            },
        },
        font={
            "size": 13,
            "color": "#111b16",
        },
        xaxis={
            "title": {
                "text": "Games Completed",
                "font": {
                    "size": 13,
                },
            },
            "type": "category",
            "categoryorder": "array",
            "categoryarray": checkpoint_labels,
            "tickangle": -45,
            "automargin": True,
            "showgrid": True,
            "showline": True,
            "gridcolor": "rgba(17, 27, 22, 0.08)",
            "linecolor": "rgba(17, 27, 22, 0.2)",
            "tickfont": {
                "size": 10,
                "color": "#5e665d",
            },
        },
        yaxis={
            "title": {
                "text": y_axis_label,
                "font": {
                    "size": 13,
                },
            },
            "range": y_axis_range,
            "tickvals": y_tick_values,
            "showgrid": True,
            "showline": True,
            "gridcolor": "rgba(17, 27, 22, 0.08)",
            "linecolor": "rgba(17, 27, 22, 0.2)",
            "zeroline": False,
            "tickfont": {
                "size": 11,
                "color": "#5e665d",
            },
        },
    )

    chart_html = pio.to_html(
        figure,
        config={
            "displayModeBar": False,
            "responsive": True,
        },
        full_html=False,
        include_plotlyjs="inline" if include_plotlyjs else False,
        div_id=chart_id,
    )
    encoded_trace_map = escape(json.dumps(trace_map), quote=True)
    return (
        f'<div class="prediction-plot" data-ci-trace-map="{encoded_trace_map}">'
        f"{chart_html}"
        "</div>"
    )


def _prediction_series_color(index: int) -> str:
    """Return a stable display color for one prediction series."""

    palette = (
        "#0a6a4a",
        "#b85c38",
        "#28536b",
        "#d69c2f",
        "#8b3d3d",
        "#5a7d2b",
        "#6b4e9b",
        "#0e7490",
        "#c2410c",
        "#4f46e5",
        "#047857",
        "#9f1239",
        "#7c2d12",
        "#1d4ed8",
    )
    return palette[index % len(palette)]


def _prediction_series_dash(series: UserPredictionHistorySeries) -> str:
    """Return the Plotly dash pattern for one user's chart line."""

    return "solid"


def _prediction_series_fill_color(color: str) -> str:
    """Return a faint rgba fill color for one series interval band."""

    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    return f"rgba({red}, {green}, {blue}, 0.12)"


def _prediction_interval_fields(metric_name: str) -> tuple[str, str]:
    """Return the history-point field names used for one chart interval."""

    interval_fields_by_metric = {
        "average_score": ("score_interval_lower", "score_interval_upper"),
        "average_finishing_position": (
            "finishing_position_interval_lower",
            "finishing_position_interval_upper",
        ),
        "winning_percentage": (
            "winning_percentage_interval_lower",
            "winning_percentage_interval_upper",
        ),
    }
    try:
        return interval_fields_by_metric[metric_name]
    except KeyError as error:
        msg = f"Unsupported prediction metric: {metric_name!r}"
        raise ValueError(msg) from error


def _average_finish_tick_values(user_count: int) -> list[float]:
    """Return readable y-axis ticks for the average-finish chart."""

    max_finish = max(user_count, 1)
    if max_finish <= 5:
        return [float(value) for value in range(1, max_finish + 1)]

    ticks = [1.0]
    step = max(1, math.ceil((max_finish - 1) / 4))
    current = 1 + step
    while current < max_finish:
        ticks.append(float(current))
        current += step
    ticks.append(float(max_finish))
    return ticks


def _average_score_axis(
    series_list: list[UserPredictionHistorySeries],
) -> tuple[float, float, list[float]]:
    """Return y-axis bounds and ticks for the average-score history chart."""

    score_values = [
        interval_value
        for series in series_list
        for point in series.points
        for interval_value in (point.score_interval_lower, point.score_interval_upper)
    ]
    if not score_values:
        return 0.0, 10.0, [0.0, 5.0, 10.0]

    raw_minimum = min(score_values)
    raw_maximum = max(score_values)
    if math.isclose(raw_minimum, raw_maximum):
        padding = max(4.0, raw_maximum * 0.1)
    else:
        padding = max(4.0, (raw_maximum - raw_minimum) * 0.15)

    y_minimum = max(0.0, math.floor((raw_minimum - padding) / 5.0) * 5.0)
    y_maximum = math.ceil((raw_maximum + padding) / 5.0) * 5.0
    if math.isclose(y_minimum, y_maximum):
        y_maximum = y_minimum + 5.0

    span = y_maximum - y_minimum
    step = max(5.0, math.ceil((span / 4.0) / 5.0) * 5.0)
    tick_values: list[float] = []
    current_value = y_minimum
    while current_value < y_maximum:
        tick_values.append(current_value)
        current_value += step
    tick_values.append(y_maximum)
    return y_minimum, y_maximum, tick_values


def _winning_percentage_axis(
    series_list: list[UserPredictionHistorySeries],
) -> tuple[float, float, list[float]]:
    """Return y-axis bounds and ticks for the winning-percentage history chart."""

    winning_percentage_values = [
        point.winning_percentage
        for series in series_list
        for point in series.points
    ]
    if not winning_percentage_values:
        return 0.0, 10.0, [0.0, 2.5, 5.0, 7.5, 10.0]

    raw_maximum = max(winning_percentage_values)
    padding = max(1.0, raw_maximum * 0.12)
    y_minimum = 0.0
    y_maximum = _next_nice_axis_stop(raw_maximum + padding)
    if math.isclose(y_maximum, y_minimum):
        y_maximum = 5.0

    span = y_maximum - y_minimum
    target_step = span / 4.0
    step = _next_nice_axis_stop(target_step)
    tick_values: list[float] = []
    current_value = y_minimum
    while current_value < y_maximum:
        tick_values.append(current_value)
        current_value += step
    tick_values.append(y_maximum)
    return y_minimum, y_maximum, tick_values


def _next_nice_axis_stop(value: float) -> float:
    """Round one axis value up to a readable chart stop."""

    if value <= 1.0:
        return 1.0
    magnitude = 10.0 ** math.floor(math.log10(value))
    normalized = value / magnitude
    for nice_factor in (1.0, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0):
        if normalized <= nice_factor:
            return nice_factor * magnitude
    return 10.0 * magnitude


def _render_stat_card(label: str, value: str) -> str:
    """Render a compact summary card."""

    return f"""
    <div class="stat-card">
        <p>{escape(label)}</p>
        <strong>{escape(value)}</strong>
    </div>
    """


def _render_standings_filter_tabs(active_filter: str) -> str:
    """Render the standings category-filter tabs."""

    return "".join(
        (
            f'<a class="filter-tab{" is-active" if value == active_filter else ""}" '
            f'href="/standings{"?category=" + value if value != "all" else ""}">'
            f"{escape(_category_filter_label(value))}"
            f"</a>"
        )
        for value in USER_CATEGORY_FILTER_VALUES
    )


def _render_category_badges(user_categories: tuple[str, ...]) -> str:
    """Render category badges for the bracket hero."""

    if not user_categories:
        return ""
    badges_html = "".join(
        f'<span class="category-badge">{escape(category.title())}</span>'
        for category in user_categories
    )
    return f'<div class="category-badge-row">{badges_html}</div>'


def _render_table_category_badges(user_categories: tuple[str, ...]) -> str:
    """Render compact category badges for the standings table."""

    if not user_categories:
        return "—"
    return "".join(
        f'<span class="category-badge table-badge">{escape(category.title())}</span>'
        for category in user_categories
    )


def _filter_standings_rows(standings: tuple[StandingsRow, ...], user_category_filter: str) -> list[StandingsRow]:
    """Return standings rows that match the selected category filter."""

    if user_category_filter == "all":
        return list(standings)
    return [row for row in standings if user_category_filter in row.user_categories]


def _normalize_user_category_filter(value: str) -> str:
    """Normalize the standings category filter to one supported value."""

    normalized = value.strip().lower()
    if normalized not in USER_CATEGORY_FILTER_VALUES:
        return "all"
    return normalized


def _category_filter_label(value: str) -> str:
    """Return the display label for a standings category filter."""

    return {
        "all": "Everyone",
        "student": "Students",
        "staff": "Staff",
    }[value]


def _format_team_label(seed: str, team_name: str) -> str:
    """Format a seeded team label for display."""

    return f"({seed}) {team_name}"


def _format_score(value: float | None) -> str:
    """Render scores as clean whole numbers when possible."""

    if value is None:
        return "—"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"

def _format_percent(value: float | None) -> str:
    """Render a percentage with one decimal place."""

    if value is None:
        return "—"
    return f"{value:.1f}%"


def _render_prediction_page_script(prediction_history: TournamentPredictionHistory) -> str:
    """Render the interactive script for prediction filters, charts, and sorting."""

    prediction_history_json = json.dumps(
        prediction_history.model_dump(mode="json"),
    ).replace("</", "<\\/")
    return (
        '<script id="prediction-history-data" type="application/json">'
        + prediction_history_json
        + "</script>"
        + """
    <script>
    (() => {
        const dataElement = document.getElementById("prediction-history-data");
        if (!dataElement || !window.Plotly) {
            return;
        }

        const predictionHistory = JSON.parse(dataElement.textContent);
        const scopeInputs = Array.from(document.querySelectorAll('input[name="prediction_scope"]'));
        const sortButtons = Array.from(document.querySelectorAll(".table-sort-button"));
        const snapshotBody = document.getElementById("prediction-snapshot-body");
        if (!snapshotBody) {
            return;
        }

        const checkpointLabels = predictionHistory.checkpoints.map((checkpoint) => checkpoint.label);
        const checkpointLabelByGamesCompleted = Object.fromEntries(
            predictionHistory.checkpoints.map((checkpoint) => [checkpoint.games_completed, checkpoint.label]),
        );
        const chartConfigs = [
            {
                chartId: "prediction-average-finish-chart",
                metric: "average_finish",
                yAxisLabel: "Average Finish",
                hoverLabel: "Average Finish",
                invertYAxis: true,
                showIntervalBand: true,
            },
            {
                chartId: "prediction-average-points-chart",
                metric: "average_score",
                yAxisLabel: "Average Points",
                hoverLabel: "Average Points",
                invertYAxis: false,
                showIntervalBand: true,
            },
            {
                chartId: "prediction-winning-percentage-chart",
                metric: "winning_percentage",
                yAxisLabel: "Winning Percentage",
                hoverLabel: "Winning Percentage",
                invertYAxis: false,
                showIntervalBand: false,
            },
        ];
        const palette = [
            "#0a6a4a",
            "#b85c38",
            "#28536b",
            "#d69c2f",
            "#8b3d3d",
            "#5a7d2b",
            "#6b4e9b",
            "#0e7490",
            "#c2410c",
            "#4f46e5",
            "#047857",
            "#9f1239",
            "#7c2d12",
            "#1d4ed8",
        ];
        const plotConfig = {
            displayModeBar: false,
            responsive: true,
        };
        const state = {
            scope: "all",
            sortKey: "winning_percentage",
            descending: true,
        };
        const baseLineWidth = 3;
        const activeLineWidth = 7;
        const baseMarkerSize = 7;
        const activeMarkerSize = 10;

        const escapeHtml = (value) => String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");

        const formatScore = (value) => {
            if (value === null || value === undefined || Number.isNaN(value)) {
                return "—";
            }
            return Number.isInteger(value) ? String(value) : value.toFixed(1);
        };

        const formatPercent = (value) => {
            if (value === null || value === undefined || Number.isNaN(value)) {
                return "—";
            }
            return `${value.toFixed(1)}%`;
        };

        const titleCase = (value) => {
            if (!value) {
                return "";
            }
            return value.charAt(0).toUpperCase() + value.slice(1);
        };

        const latestPoint = (series) => series.points[series.points.length - 1];

        const visibleUsers = (scope) => predictionHistory.users.filter((series) => (
            scope === "all" || (series.user_categories || []).includes(scope)
        ));

        const metricValue = (point, metric, scope) => {
            if (metric === "average_score") {
                return point.average_score;
            }
            if (metric === "average_finish") {
                return scope === "all"
                    ? point.average_finishing_position
                    : point.average_category_finishing_position;
            }
            if (metric === "winning_percentage") {
                return scope === "all"
                    ? point.winning_percentage
                    : point.category_winning_percentage;
            }
            return null;
        };

        const metricIntervalBounds = (point, metric, scope) => {
            if (metric === "average_score") {
                return [point.score_interval_lower, point.score_interval_upper];
            }
            if (metric === "average_finish") {
                if (scope === "all") {
                    return [
                        point.finishing_position_interval_lower,
                        point.finishing_position_interval_upper,
                    ];
                }
                return [
                    point.category_finishing_position_interval_lower,
                    point.category_finishing_position_interval_upper,
                ];
            }
            return [null, null];
        };

        const nextNiceAxisStop = (value) => {
            if (value <= 1) {
                return 1;
            }
            const magnitude = 10 ** Math.floor(Math.log10(value));
            const normalized = value / magnitude;
            for (const niceFactor of [1, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
                if (normalized <= niceFactor) {
                    return niceFactor * magnitude;
                }
            }
            return 10 * magnitude;
        };

        const averageFinishTicks = (userCount) => {
            const maxFinish = Math.max(userCount, 1);
            if (maxFinish <= 5) {
                return Array.from({ length: maxFinish }, (_unused, index) => index + 1);
            }

            const ticks = [1];
            const step = Math.max(1, Math.ceil((maxFinish - 1) / 4));
            let current = 1 + step;
            while (current < maxFinish) {
                ticks.push(current);
                current += step;
            }
            ticks.push(maxFinish);
            return ticks;
        };

        const averageScoreAxis = (users) => {
            const values = users.flatMap((series) => (
                series.points.flatMap((point) => [
                    point.score_interval_lower,
                    point.score_interval_upper,
                ])
            ));
            if (!values.length) {
                return { min: 0, max: 10, ticks: [0, 5, 10] };
            }

            const rawMinimum = Math.min(...values);
            const rawMaximum = Math.max(...values);
            const padding = rawMinimum === rawMaximum
                ? Math.max(4, rawMaximum * 0.1)
                : Math.max(4, (rawMaximum - rawMinimum) * 0.15);
            const minimum = Math.max(0, Math.floor((rawMinimum - padding) / 5) * 5);
            let maximum = Math.ceil((rawMaximum + padding) / 5) * 5;
            if (minimum === maximum) {
                maximum = minimum + 5;
            }
            const span = maximum - minimum;
            const step = Math.max(5, Math.ceil((span / 4) / 5) * 5);
            const ticks = [];
            for (let current = minimum; current < maximum; current += step) {
                ticks.push(current);
            }
            ticks.push(maximum);
            return { min: minimum, max: maximum, ticks };
        };

        const winningPercentageAxis = (users, scope) => {
            const values = users.flatMap((series) => (
                series.points
                    .map((point) => metricValue(point, "winning_percentage", scope))
                    .filter((value) => value !== null && value !== undefined)
            ));
            if (!values.length) {
                return { min: 0, max: 10, ticks: [0, 2.5, 5, 7.5, 10] };
            }

            const rawMaximum = Math.max(...values);
            const padding = Math.max(1, rawMaximum * 0.12);
            const minimum = 0;
            let maximum = nextNiceAxisStop(rawMaximum + padding);
            if (maximum === minimum) {
                maximum = 5;
            }
            const span = maximum - minimum;
            const step = nextNiceAxisStop(span / 4);
            const ticks = [];
            for (let current = minimum; current < maximum; current += step) {
                ticks.push(current);
            }
            ticks.push(maximum);
            return { min: minimum, max: maximum, ticks };
        };

        const axisConfig = (metric, users, scope) => {
            if (metric === "average_finish") {
                return {
                    min: 1,
                    max: Math.max(users.length, 1),
                    ticks: averageFinishTicks(users.length),
                };
            }
            if (metric === "average_score") {
                return averageScoreAxis(users);
            }
            return winningPercentageAxis(users, scope);
        };

        const seriesColor = (index) => palette[index % palette.length];

        const seriesFillColor = (hexColor) => {
            const red = parseInt(hexColor.slice(1, 3), 16);
            const green = parseInt(hexColor.slice(3, 5), 16);
            const blue = parseInt(hexColor.slice(5, 7), 16);
            return `rgba(${red}, ${green}, ${blue}, 0.12)`;
        };

        const buildChartTraces = (metric, users, scope, showIntervalBand) => {
            const traces = [];
            const traceMap = [];

            users.forEach((series, index) => {
                const color = seriesColor(index);
                const xValues = [];
                const yValues = [];
                const lowerValues = [];
                const upperValues = [];

                for (const point of series.points) {
                    const value = metricValue(point, metric, scope);
                    if (value === null || value === undefined) {
                        continue;
                    }
                    xValues.push(checkpointLabelByGamesCompleted[point.games_completed]);
                    yValues.push(value);
                    if (showIntervalBand) {
                        const [intervalLower, intervalUpper] = metricIntervalBounds(point, metric, scope);
                        if (intervalLower === null || intervalUpper === null) {
                            continue;
                        }
                        lowerValues.push(intervalLower);
                        upperValues.push(intervalUpper);
                    }
                }

                if (!xValues.length) {
                    return;
                }

                const lineIndex = traces.length;
                traces.push({
                    x: xValues,
                    y: yValues,
                    name: series.user_name,
                    mode: "lines+markers",
                    line: {
                        color,
                        width: baseLineWidth,
                        dash: "solid",
                    },
                    marker: {
                        size: baseMarkerSize,
                        color,
                    },
                    hovertemplate: `<b>${escapeHtml(series.user_name)}</b><br>%{x}<br>${escapeHtml(metric === "winning_percentage" ? "Winning Percentage" : metric === "average_score" ? "Average Points" : "Average Finish")}: %{y:.1f}${metric === "winning_percentage" ? "%" : ""}<extra></extra>`,
                });

                let bandIndex = null;
                if (showIntervalBand && lowerValues.length === xValues.length && upperValues.length === xValues.length) {
                    bandIndex = traces.length;
                    traces.push({
                        x: xValues.concat([...xValues].reverse()),
                        y: upperValues.concat([...lowerValues].reverse()),
                        name: `${series.user_name} 80% interval`,
                        mode: "lines",
                        line: {
                            color: "rgba(0, 0, 0, 0)",
                            width: 0,
                        },
                        fill: "toself",
                        fillcolor: seriesFillColor(color),
                        hoverinfo: "skip",
                        showlegend: false,
                        visible: false,
                    });
                }

                traceMap.push({
                    line: lineIndex,
                    band: bandIndex,
                });
            });

            return { traces, traceMap };
        };

        const buildChartLayout = (config, users, scope) => {
            const yAxis = axisConfig(config.metric, users, scope);
            return {
                height: 520,
                margin: {
                    l: 74,
                    r: 24,
                    t: 24,
                    b: 150,
                },
                paper_bgcolor: "rgba(0, 0, 0, 0)",
                plot_bgcolor: "rgba(255, 252, 247, 0.78)",
                hovermode: "closest",
                showlegend: true,
                legend: {
                    orientation: "h",
                    x: 0,
                    xanchor: "left",
                    y: -0.34,
                    yanchor: "top",
                    font: {
                        size: 10,
                    },
                },
                font: {
                    size: 13,
                    color: "#111b16",
                },
                xaxis: {
                    title: {
                        text: "Games Completed",
                        font: {
                            size: 13,
                        },
                    },
                    type: "category",
                    categoryorder: "array",
                    categoryarray: checkpointLabels,
                    tickangle: -45,
                    automargin: true,
                    showgrid: true,
                    showline: true,
                    gridcolor: "rgba(17, 27, 22, 0.08)",
                    linecolor: "rgba(17, 27, 22, 0.2)",
                    tickfont: {
                        size: 10,
                        color: "#5e665d",
                    },
                },
                yaxis: {
                    title: {
                        text: config.yAxisLabel,
                        font: {
                            size: 13,
                        },
                    },
                    range: config.invertYAxis ? [yAxis.max, yAxis.min] : [yAxis.min, yAxis.max],
                    tickvals: yAxis.ticks,
                    showgrid: true,
                    showline: true,
                    gridcolor: "rgba(17, 27, 22, 0.08)",
                    linecolor: "rgba(17, 27, 22, 0.2)",
                    zeroline: false,
                    tickfont: {
                        size: 11,
                        color: "#5e665d",
                    },
                },
            };
        };

        const applyHighlight = (wrapper, plotDiv, activeLineIndex) => {
            const traceMap = wrapper.__traceMap || [];
            const bandTraceIndexes = traceMap
                .filter((item) => item.band !== null)
                .map((item) => item.band);
            if (bandTraceIndexes.length) {
                const bandVisibility = traceMap
                    .filter((item) => item.band !== null)
                    .map((item) => item.line === activeLineIndex);
                window.Plotly.restyle(plotDiv, { visible: bandVisibility }, bandTraceIndexes);
            }

            const lineTraceIndexes = traceMap.map((item) => item.line);
            window.Plotly.restyle(
                plotDiv,
                {
                    "line.width": traceMap.map((item) => (
                        item.line === activeLineIndex ? activeLineWidth : baseLineWidth
                    )),
                    "marker.size": traceMap.map((item) => (
                        item.line === activeLineIndex ? activeMarkerSize : baseMarkerSize
                    )),
                },
                lineTraceIndexes,
            );
        };

        const clearHighlight = (wrapper, plotDiv) => {
            const traceMap = wrapper.__traceMap || [];
            const bandTraceIndexes = traceMap
                .filter((item) => item.band !== null)
                .map((item) => item.band);
            if (bandTraceIndexes.length) {
                window.Plotly.restyle(
                    plotDiv,
                    { visible: traceMap.filter((item) => item.band !== null).map(() => false) },
                    bandTraceIndexes,
                );
            }

            const lineTraceIndexes = traceMap.map((item) => item.line);
            window.Plotly.restyle(
                plotDiv,
                {
                    "line.width": traceMap.map(() => baseLineWidth),
                    "marker.size": traceMap.map(() => baseMarkerSize),
                },
                lineTraceIndexes,
            );
        };

        const refreshActiveHighlight = (wrapper, plotDiv) => {
            const activeLineIndex = wrapper.__activeLegendLineIndex ?? wrapper.__activeHoverLineIndex;
            if (activeLineIndex === null || activeLineIndex === undefined) {
                clearHighlight(wrapper, plotDiv);
                return;
            }
            applyHighlight(wrapper, plotDiv, activeLineIndex);
        };

        const bindLegendHover = (wrapper, plotDiv) => {
            const traceMap = wrapper.__traceMap || [];
            const legendItems = plotDiv.querySelectorAll(".legend .traces");
            legendItems.forEach((legendItem, index) => {
                if (legendItem.dataset.predictionLegendBound === "true") {
                    return;
                }
                legendItem.dataset.predictionLegendBound = "true";
                const lineIndex = traceMap[index]?.line;
                if (lineIndex === undefined) {
                    return;
                }
                legendItem.addEventListener("mouseenter", () => {
                    wrapper.__activeLegendLineIndex = lineIndex;
                    refreshActiveHighlight(wrapper, plotDiv);
                });
                legendItem.addEventListener("mouseleave", () => {
                    wrapper.__activeLegendLineIndex = null;
                    refreshActiveHighlight(wrapper, plotDiv);
                });
            });
        };

        const bindChartInteractions = (wrapper, plotDiv) => {
            if (wrapper.dataset.predictionInteractionsBound === "true") {
                bindLegendHover(wrapper, plotDiv);
                return;
            }

            wrapper.dataset.predictionInteractionsBound = "true";
            wrapper.__activeLegendLineIndex = null;
            wrapper.__activeHoverLineIndex = null;

            plotDiv.on("plotly_hover", (eventData) => {
                if (!eventData || !eventData.points || !eventData.points.length) {
                    return;
                }
                wrapper.__activeHoverLineIndex = eventData.points[0].curveNumber;
                refreshActiveHighlight(wrapper, plotDiv);
            });
            plotDiv.on("plotly_unhover", () => {
                wrapper.__activeHoverLineIndex = null;
                refreshActiveHighlight(wrapper, plotDiv);
            });
            plotDiv.on("plotly_afterplot", () => bindLegendHover(wrapper, plotDiv));
            bindLegendHover(wrapper, plotDiv);
        };

        const renderChart = (config) => {
            const plotDiv = document.getElementById(config.chartId);
            if (!plotDiv) {
                return Promise.resolve();
            }
            const wrapper = plotDiv.closest(".prediction-plot");
            if (!wrapper) {
                return Promise.resolve();
            }

            const users = visibleUsers(state.scope);
            const { traces, traceMap } = buildChartTraces(
                config.metric,
                users,
                state.scope,
                config.showIntervalBand,
            );
            const layout = buildChartLayout(config, users, state.scope);

            return window.Plotly.react(plotDiv, traces, layout, plotConfig).then(() => {
                wrapper.__traceMap = traceMap;
                wrapper.__activeLegendLineIndex = null;
                wrapper.__activeHoverLineIndex = null;
                bindChartInteractions(wrapper, plotDiv);
                clearHighlight(wrapper, plotDiv);
            });
        };

        const sortValue = (series, sortKey, scope) => {
            const point = latestPoint(series);
            if (sortKey === "user_name") {
                return series.user_name.toLowerCase();
            }
            if (sortKey === "category") {
                return (series.user_categories || []).join(",").toLowerCase();
            }
            if (sortKey === "average_score") {
                return point.average_score;
            }
            if (sortKey === "average_finish") {
                return metricValue(point, "average_finish", scope) ?? Number.POSITIVE_INFINITY;
            }
            if (sortKey === "winning_percentage") {
                return metricValue(point, "winning_percentage", scope) ?? Number.NEGATIVE_INFINITY;
            }
            return "";
        };

        const compareValues = (leftValue, rightValue, descending) => {
            if (typeof leftValue === "string" || typeof rightValue === "string") {
                const comparison = String(leftValue).localeCompare(String(rightValue));
                return descending ? -comparison : comparison;
            }
            const difference = Number(leftValue) - Number(rightValue);
            return descending ? -difference : difference;
        };

        const renderTableRow = (series, scope) => {
            const point = latestPoint(series);
            const categoryHtml = (series.user_categories || []).map((category) => (
                `<span class="category-badge table-badge">${escapeHtml(titleCase(category))}</span>`
            )).join("") || "—";
            return `
                <tr>
                    <td>
                        <a class="standings-link" href="/brackets/${encodeURIComponent(series.slug)}">${escapeHtml(series.user_name)}</a>
                    </td>
                    <td>${categoryHtml}</td>
                    <td>${formatScore(point.average_score)}</td>
                    <td>${formatScore(metricValue(point, "average_finish", scope))}</td>
                    <td>${formatPercent(metricValue(point, "winning_percentage", scope))}</td>
                </tr>
            `;
        };

        const updateSortButtons = () => {
            sortButtons.forEach((button) => {
                const isActive = button.dataset.sortKey === state.sortKey;
                button.classList.toggle("is-active", isActive);
                button.classList.toggle("is-desc", isActive && state.descending);
                button.classList.toggle("is-asc", isActive && !state.descending);
            });
        };

        const renderTable = () => {
            const users = visibleUsers(state.scope).slice();
            users.sort((leftSeries, rightSeries) => {
                const comparison = compareValues(
                    sortValue(leftSeries, state.sortKey, state.scope),
                    sortValue(rightSeries, state.sortKey, state.scope),
                    state.descending,
                );
                if (comparison !== 0) {
                    return comparison;
                }
                return leftSeries.user_name.localeCompare(rightSeries.user_name);
            });
            snapshotBody.innerHTML = users.map((series) => renderTableRow(series, state.scope)).join("");
            updateSortButtons();
        };

        const renderPredictionView = () => {
            Promise.all(chartConfigs.map((config) => renderChart(config))).then(() => {
                renderTable();
            });
        };

        scopeInputs.forEach((input) => {
            input.addEventListener("change", () => {
                if (!input.checked) {
                    return;
                }
                state.scope = input.value;
                renderPredictionView();
            });
        });

        sortButtons.forEach((button) => {
            button.addEventListener("click", () => {
                const nextSortKey = button.dataset.sortKey;
                const defaultDescending = button.dataset.defaultDirection === "desc";
                if (state.sortKey === nextSortKey) {
                    state.descending = !state.descending;
                } else {
                    state.sortKey = nextSortKey;
                    state.descending = defaultDescending;
                }
                renderTable();
            });
        });

        updateSortButtons();
        renderPredictionView();
    })();
    </script>
    """
    )
