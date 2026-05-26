from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.services.insights_service import _build_fallback, _build_user_message, get_obra_insight


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_obra() -> dict:
    return {
        "id_obra_geoobras": "aaaaaaaa-0000-0000-0000-000000000001",
        "nome": "Escola Municipal Centro",
        "status_obra": "em_execucao",
        "data_inicio": date(2022, 1, 1),
        "data_fim_prevista": date(2023, 1, 1),
        "data_fim_real": None,
        "valor_total_contratado": 1_000_000.0,
        "valor_pago_acumulado": 800_000.0,
        "percentual_fisico": 40.0,
        "percentual_desembolso": 80.0,
        # analytics convention: desembolso - fisico (positive = payment ahead of physical)
        "divergencia_fisico_financeira": 40.0,
        "dias_atraso": 120,
        "flag_possivel_atraso": True,
        "risco_sobrecusto": 0.75,
        "classe_alerta": "vermelho",
        "metodo_score": "heuristica_zscore_v1",
    }


def _mock_settings(api_key: str = "sk-test") -> MagicMock:
    s = MagicMock()
    s.LLM_API_KEY = api_key
    s.LLM_MODEL = "claude-test"
    s.LLM_BASE_URL = "https://api.test/messages"
    s.LLM_TIMEOUT = 5.0
    s.LLM_MAX_TOKENS = 256
    return s


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_llm_success_returns_fonte_llm_and_references_divergencia() -> None:
    """
    Happy path: LLM responds successfully.
    Result must carry fonte='llm' and the summary must reference the
    physical-financial divergence the LLM was told to highlight.
    """
    llm_text = (
        "A obra apresenta divergência físico-financeira de -40 p.p.: "
        "execução física em 40% enquanto desembolso atingiu 80%. "
        "O atraso de 120 dias sinaliza risco elevado."
    )
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"content": [{"text": llm_text}]}
    mock_resp.raise_for_status.return_value = None

    with (
        patch("src.services.insights_service._fetch_obra_data", return_value=_mock_obra()),
        patch("src.services.insights_service.get_settings", return_value=_mock_settings()),
        patch("src.services.insights_service.httpx.post", return_value=mock_resp),
    ):
        result = get_obra_insight("aaaaaaaa-0000-0000-0000-000000000001")

    assert result["fonte"] == "llm"
    assert "divergência" in result["resumo"].lower()


def test_llm_called_with_obra_metrics_in_payload() -> None:
    """LLM request payload must include the user message built from obra metrics."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"content": [{"text": "ok"}]}
    mock_resp.raise_for_status.return_value = None

    with (
        patch("src.services.insights_service._fetch_obra_data", return_value=_mock_obra()),
        patch("src.services.insights_service.get_settings", return_value=_mock_settings()),
        patch("src.services.insights_service.httpx.post", return_value=mock_resp) as mock_post,
    ):
        get_obra_insight("aaaaaaaa-0000-0000-0000-000000000001")

    payload = mock_post.call_args.kwargs["json"]
    user_content = payload["messages"][0]["content"]
    assert "Escola Municipal Centro" in user_content
    assert "Divergência" in user_content


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_llm_timeout_returns_fallback_without_raising() -> None:
    """
    Edge case: httpx raises TimeoutException.
    Must return fonte='fallback' and must NOT propagate the exception.
    """
    with (
        patch("src.services.insights_service._fetch_obra_data", return_value=_mock_obra()),
        patch("src.services.insights_service.get_settings", return_value=_mock_settings()),
        patch(
            "src.services.insights_service.httpx.post",
            side_effect=httpx.TimeoutException("timed out"),
        ),
    ):
        result = get_obra_insight("aaaaaaaa-0000-0000-0000-000000000001")

    assert result["fonte"] == "fallback"
    assert "resumo" in result


def test_llm_http_error_returns_fallback() -> None:
    """Edge case: LLM returns 4xx/5xx → raise_for_status raises → fallback."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("429", request=MagicMock(), response=MagicMock())

    with (
        patch("src.services.insights_service._fetch_obra_data", return_value=_mock_obra()),
        patch("src.services.insights_service.get_settings", return_value=_mock_settings()),
        patch("src.services.insights_service.httpx.post", return_value=mock_resp),
    ):
        result = get_obra_insight("aaaaaaaa-0000-0000-0000-000000000001")

    assert result["fonte"] == "fallback"


def test_no_api_key_returns_fallback_without_calling_llm() -> None:
    """Edge case: LLM_API_KEY is empty → skip LLM, return fallback immediately."""
    with (
        patch("src.services.insights_service._fetch_obra_data", return_value=_mock_obra()),
        patch("src.services.insights_service.get_settings", return_value=_mock_settings(api_key="")),
        patch("src.services.insights_service.httpx.post") as mock_post,
    ):
        result = get_obra_insight("aaaaaaaa-0000-0000-0000-000000000001")

    mock_post.assert_not_called()
    assert result["fonte"] == "fallback"


def test_obra_not_found_returns_fallback_dict() -> None:
    """Edge case: obra absent from DB → structured error dict, fonte='fallback'."""
    with patch("src.services.insights_service._fetch_obra_data", return_value=None):
        result = get_obra_insight("nonexistent-id")

    assert result["fonte"] == "fallback"
    assert "erro" in result


# ---------------------------------------------------------------------------
# Fallback content
# ---------------------------------------------------------------------------


def test_fallback_includes_divergencia_text() -> None:
    """_build_fallback must mention the divergence using the analytics sign convention."""
    # divergencia_fisico_financeira=+40 (desembolso 80 - fisico 40, positive = overpayment risk)
    obra = _mock_obra()
    result = _build_fallback(obra)

    assert result["fonte"] == "fallback"
    assert "divergência" in result["resumo"].lower()
    assert "+40" in result["resumo"]


def test_fallback_includes_alert_class() -> None:
    """_build_fallback must include the classe_alerta when present."""
    result = _build_fallback(_mock_obra())
    assert "VERMELHO" in result["resumo"]


@pytest.mark.parametrize("missing_field", ["percentual_fisico", "percentual_desembolso"])
def test_fallback_handles_missing_metrics_gracefully(missing_field: str) -> None:
    """_build_fallback must not raise when financial metrics are absent."""
    obra = {**_mock_obra(), missing_field: None}
    result = _build_fallback(obra)
    assert result["fonte"] == "fallback"
    assert isinstance(result["resumo"], str)


# ---------------------------------------------------------------------------
# HF-01: divergence sign and column-priority tests
# ---------------------------------------------------------------------------


def test_divergencia_persisted_column_takes_priority() -> None:
    """
    Happy path: obra 80 % paid / 30 % physical, persisted column = +50.0.
    Both _build_user_message and _build_fallback must emit +50.0 p.p.
    """
    obra = {
        "id_obra_geoobras": "bbbbbbbb-0000-0000-0000-000000000002",
        "nome": "Ponte Nova",
        "status_obra": "em_execucao",
        "percentual_fisico": 30.0,
        "percentual_desembolso": 80.0,
        "divergencia_fisico_financeira": 50.0,  # persisted by analytics
        "dias_atraso": None,
        "risco_sobrecusto": None,
        "classe_alerta": None,
        "valor_total_contratado": None,
        "valor_pago_acumulado": None,
    }

    msg = _build_user_message(obra)
    assert "+50.0 p.p." in msg

    result = _build_fallback(obra)
    assert "+50" in result["resumo"]
    assert "divergência" in result["resumo"].lower()


def test_divergencia_recomputed_with_correct_sign_when_column_null() -> None:
    """
    Edge: divergencia_fisico_financeira is None (analytics not yet run).
    Both helpers must recompute as desembolso - fisico (positive when desembolso > fisico),
    NOT as fisico - desembolso.
    """
    obra = {
        "id_obra_geoobras": "cccccccc-0000-0000-0000-000000000003",
        "nome": "Escola Sul",
        "status_obra": "em_execucao",
        "percentual_fisico": 30.0,
        "percentual_desembolso": 80.0,
        "divergencia_fisico_financeira": None,  # column absent
        "dias_atraso": None,
        "risco_sobrecusto": None,
        "classe_alerta": None,
        "valor_total_contratado": None,
        "valor_pago_acumulado": None,
    }

    msg = _build_user_message(obra)
    assert "+50.0 p.p." in msg
    assert "-50" not in msg

    result = _build_fallback(obra)
    assert "+50" in result["resumo"]
    assert "-50" not in result["resumo"]
