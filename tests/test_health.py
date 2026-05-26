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
