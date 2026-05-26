from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, get_db

VALID_ID = "aaaaaaaa-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_obra() -> dict:
    return {
        "id_obra_geoobras": VALID_ID,
        "nome": "Escola Municipal Centro",
        "status_obra": "em_execucao",
        "flag_possivel_atraso": True,
        "flag_data_fim_pendente": False,
        "flag_populacao_suspeita": False,
        "flag_empregos_suspeitos": False,
        "dias_atraso": 120,
        "percentual_fisico": 40.0,
        "percentual_desembolso": 80.0,
        "divergencia_fisico_financeira": 40.0,
    }


def _insight_llm() -> dict:
    return {
        "resumo": "A obra apresenta divergência físico-financeira de -40 p.p.",
        "fonte": "llm",
        "id_obra_geoobras": VALID_ID,
    }


def _insight_fallback() -> dict:
    return {
        "resumo": "Resumo automático — Escola Municipal Centro. Divergência físico-financeira: -40.0 p.p.",
        "fonte": "fallback",
        "id_obra_geoobras": VALID_ID,
    }


@pytest.fixture()
def client() -> TestClient:
    """TestClient with the DB dependency replaced by a MagicMock session."""

    def _fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = _fake_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_insights_returns_200_with_matching_schema(client: TestClient) -> None:
    """Happy path: valid obra → 200, body matches InsightResponse schema."""
    with (
        patch("src.api.main.fetch_obra_insights", return_value=_mock_obra()),
        patch("src.api.main.get_obra_insight", return_value=_insight_llm()),
    ):
        response = client.get(f"/api/v1/obras/{VALID_ID}/insights")

    assert response.status_code == 200
    body = response.json()
    assert body["fonte"] == "llm"
    assert isinstance(body["resumo"], str) and body["resumo"]
    assert "flags" in body
    assert "gerado_em" in body


def test_insights_flags_reflect_obra_alert_state(client: TestClient) -> None:
    """Flags in the response must carry the obra's alert values."""
    with (
        patch("src.api.main.fetch_obra_insights", return_value=_mock_obra()),
        patch("src.api.main.get_obra_insight", return_value=_insight_llm()),
    ):
        response = client.get(f"/api/v1/obras/{VALID_ID}/insights")

    flags = response.json()["flags"]
    assert flags["possivel_atraso"] is True
    assert flags["data_fim_pendente"] is False
    assert flags["dias_atraso"] == 120


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_insights_nonexistent_obra_returns_404(client: TestClient) -> None:
    """Edge case: obra not in DB → 404, get_obra_insight never called."""
    with (
        patch("src.api.main.fetch_obra_insights", return_value=None),
        patch("src.api.main.get_obra_insight") as mock_insight,
    ):
        response = client.get(f"/api/v1/obras/{VALID_ID}/insights")

    assert response.status_code == 404
    mock_insight.assert_not_called()


def test_insights_llm_failure_returns_200_with_fallback(client: TestClient) -> None:
    """Edge case: LLM fails (simulated by fallback dict) → still 200, fonte='fallback'."""
    with (
        patch("src.api.main.fetch_obra_insights", return_value=_mock_obra()),
        patch("src.api.main.get_obra_insight", return_value=_insight_fallback()),
    ):
        response = client.get(f"/api/v1/obras/{VALID_ID}/insights")

    assert response.status_code == 200
    assert response.json()["fonte"] == "fallback"


def test_insights_invalid_uuid_returns_422(client: TestClient) -> None:
    """Edge case: malformed UUID in path → FastAPI returns 422 Unprocessable Entity."""
    response = client.get("/api/v1/obras/not-a-uuid/insights")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# HF-02: single consolidated fetch tests
# ---------------------------------------------------------------------------


def test_insights_triggers_exactly_one_fetch(client: TestClient) -> None:
    """
    Happy path: one request to /insights must call fetch_obra_insights exactly once
    and pass the obra dict into get_obra_insight (no second internal DB round-trip).
    """
    obra = _mock_obra()
    with (
        patch("src.api.main.fetch_obra_insights", return_value=obra) as mock_fetch,
        patch("src.api.main.get_obra_insight", return_value=_insight_llm()) as mock_insight,
    ):
        response = client.get(f"/api/v1/obras/{VALID_ID}/insights")

    assert response.status_code == 200
    mock_fetch.assert_called_once()
    # Service must receive the pre-fetched obra — no separate self-fetch needed
    mock_insight.assert_called_once_with(VALID_ID, obra=obra)


def test_insights_missing_obra_returns_404_and_service_not_called(client: TestClient) -> None:
    """
    Edge: fetch_obra_insights returns None → 404 before the service is invoked.
    """
    with (
        patch("src.api.main.fetch_obra_insights", return_value=None),
        patch("src.api.main.get_obra_insight") as mock_insight,
    ):
        response = client.get(f"/api/v1/obras/{VALID_ID}/insights")

    assert response.status_code == 404
    mock_insight.assert_not_called()


def test_insights_llm_down_returns_200_fallback_via_consolidated_fetch(client: TestClient) -> None:
    """
    Edge: LLM failure (fonte='fallback') → still 200 with single fetch.
    """
    with (
        patch("src.api.main.fetch_obra_insights", return_value=_mock_obra()),
        patch("src.api.main.get_obra_insight", return_value=_insight_fallback()),
    ):
        response = client.get(f"/api/v1/obras/{VALID_ID}/insights")

    assert response.status_code == 200
    assert response.json()["fonte"] == "fallback"
