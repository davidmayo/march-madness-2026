"""Regression tests for the FastAPI frontend page renderers."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from march_madness.frontend.app import bracket_index_redirect
from march_madness.frontend.app import bracket_page
from march_madness.frontend.app import historical_page
from march_madness.frontend.app import prediction_page
from march_madness.frontend.app import root_redirect
from march_madness.frontend.app import standings_page


def test_root_redirects_to_standings() -> None:
    """The site root should redirect to the standings page."""

    response = root_redirect()
    assert response.status_code == 307
    assert response.headers["location"] == "/standings"


def test_bracket_index_redirects_to_default_bracket() -> None:
    """The generic bracket route should redirect to the default bracket page."""

    response = bracket_index_redirect()
    assert response.status_code == 307
    assert response.headers["location"] == "/brackets/david-mayo"

    selected_response = bracket_index_redirect("austin-jude")
    assert selected_response.status_code == 307
    assert selected_response.headers["location"] == "/brackets/austin-jude"


def test_standings_page_renders_html() -> None:
    """The standings page should include the current table and bracket links."""

    response = standings_page()
    html = response.body.decode()

    assert response.status_code == 200
    assert "Current Standings" in html
    assert "Standings Board" in html
    assert "/brackets/david-mayo" in html
    assert "Darren Boyd" in html
    assert "Everyone" in html
    assert "Switch bracket" not in html


def test_standings_page_can_filter_students_and_staff() -> None:
    """The standings page should support category-based filtering."""

    student_html = standings_page("student").body.decode()
    staff_html = standings_page("staff").body.decode()

    assert "Austin Music" in student_html
    assert "Darren Boyd" not in student_html

    assert "Darren Boyd" in staff_html
    assert "Austin Music" not in staff_html


def test_bracket_page_renders_david_mayo() -> None:
    """The bracket viewer should render the requested user's picks."""

    response = bracket_page("david-mayo")
    html = response.body.decode()

    assert response.status_code == 200
    assert "David Mayo" in html
    assert "Basketball!" in html
    assert "Champion Pick" in html
    assert "Road To Indianapolis" in html
    assert "Switch bracket" in html


def test_invalid_bracket_page_returns_html_404() -> None:
    """Unknown bracket slugs should return a styled HTML 404 page."""

    response = bracket_page("not-a-real-user")
    html = response.body.decode()

    assert response.status_code == 404
    assert "Bracket Not Found" in html


def test_prediction_and_historical_pages_render() -> None:
    """Prediction and historical pages should render cleanly."""

    prediction_response = prediction_page()
    historical_response = historical_page()

    assert prediction_response.status_code == 200
    prediction_html = prediction_response.body.decode()
    assert "Prediction Engine" in prediction_html
    assert "Monte Carlo history" in prediction_html
    assert "Average Finish Over Time" in prediction_html
    assert "Winning Percentage Over Time" in prediction_html
    assert "Darren Boyd" in prediction_html

    assert historical_response.status_code == 200
    assert "Historical Results" in historical_response.body.decode()
