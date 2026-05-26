from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.main import app


def test_health_returns_ok_when_db_up() -> None:
    """Happy path: test_connection returns True → banco: true in payload."""
    with patch("src.api.main.test_connection", return_value=True):
        with TestClient(app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "banco": True}


def test_health_returns_ok_when_db_down() -> None:
    """Edge case: test_connection returns False → endpoint still 200, banco: false."""
    with patch("src.api.main.test_connection", return_value=False):
        with TestClient(app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "banco": False}


# ---------------------------------------------------------------------------
# Lifespan migration tests (task 11)
# ---------------------------------------------------------------------------


def test_app_has_no_on_event_startup_handlers() -> None:
    """
    After migrating to lifespan, the app must not have any handlers
    registered via the deprecated @app.on_event API.
    """
    startup_handlers = getattr(app.router, "on_startup", [])
    assert startup_handlers == [], (
        f"Found deprecated on_event startup handlers: {startup_handlers}. Use lifespan instead."
    )


def test_health_triggered_through_lifespan_context_manager() -> None:
    """
    TestClient used as a context manager exercises the lifespan (startup/shutdown).
    /health must return the correct DB status — proving the lifespan-based
    startup runs without error.
    """
    with patch("src.api.main.test_connection", return_value=True):
        with TestClient(app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "banco": True}
