from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest

from src.services.analytics_service import _calc_aditivo, _calc_iec, _calc_insolvencia, _calc_probabilidade_atraso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _m(
    id_obra: str,
    data_inicio: date | None,
    data_fim_prevista: date | None,
    dias_atraso: int | None,
) -> dict:
    return {
        "id_obra_geoobras": id_obra,
        "data_inicio": data_inicio,
        "data_fim_prevista": data_fim_prevista,
        "dias_atraso": dias_atraso,
    }


_START = date(2020, 1, 1)
_END_100 = date(2020, 4, 10)  # 100-day contractual term


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_high_relative_delay_yields_probability_above_threshold() -> None:
    """
    Population: 4 obras at 5 % relative delay, 1 at 80 %.
    Expected z ≈ +2.0  →  logistic ≈ 0.88, which must exceed 0.7.
    """
    metricas = [
        _m("low1", _START, _END_100, 5),
        _m("low2", _START, _END_100, 5),
        _m("low3", _START, _END_100, 5),
        _m("low4", _START, _END_100, 5),
        _m("high", _START, _END_100, 80),
    ]

    result = _calc_probabilidade_atraso(metricas)

    assert result["high"] is not None
    assert result["high"] > 0.7, f"Expected > 0.7, got {result['high']}"


def test_low_relative_delay_yields_probability_below_average() -> None:
    """An obra below the group average should score < 0.5."""
    metricas = [
        _m("low", _START, _END_100, 2),
        _m("mid1", _START, _END_100, 30),
        _m("mid2", _START, _END_100, 40),
        _m("high", _START, _END_100, 80),
    ]

    result = _calc_probabilidade_atraso(metricas)

    assert result["low"] is not None
    assert result["low"] < 0.5


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_insufficient_sample_returns_all_none(caplog: pytest.LogCaptureFixture) -> None:
    """Fewer than 3 obras with a valid term → all None + 'insufficient sample' log."""
    metricas = [
        _m("a", _START, _END_100, 10),  # valid
        _m("b", _START, _END_100, 20),  # valid — only 2 total
        _m("c", None, None, None),  # no dates → excluded
    ]

    with caplog.at_level(logging.WARNING, logger="src.services.analytics_service"):
        result = _calc_probabilidade_atraso(metricas)

    assert all(v is None for v in result.values()), f"Expected all None, got {result}"
    assert "insufficient sample" in caplog.text


def test_zero_sample_returns_all_none_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    """No obra has dates at all → sample size 0 → all None + log."""
    metricas = [
        _m("x", None, None, None),
        _m("y", None, None, None),
    ]

    with caplog.at_level(logging.WARNING, logger="src.services.analytics_service"):
        result = _calc_probabilidade_atraso(metricas)

    assert all(v is None for v in result.values())
    assert "insufficient sample" in caplog.text


def test_obra_without_dates_gets_none_in_large_sample() -> None:
    """An obra missing dates is excluded from z-score and receives None."""
    metricas = [
        _m("no_dates", None, None, None),
        _m("ok1", _START, _END_100, 10),
        _m("ok2", _START, _END_100, 20),
        _m("ok3", _START, _END_100, 30),
    ]

    result = _calc_probabilidade_atraso(metricas)

    assert result["no_dates"] is None
    assert result["ok1"] is not None
    assert result["ok2"] is not None
    assert result["ok3"] is not None


# ---------------------------------------------------------------------------
# T-04: _calc_aditivo — additive percentage and legal-ceiling flag
# ---------------------------------------------------------------------------


def test_aditivo_above_legal_ceiling_returns_vermelho() -> None:
    """Happy: 30% additive (1_300_000 / 1_000_000) exceeds the 25% ceiling → 'vermelho'."""
    pct, flag = _calc_aditivo(1_000_000.0, 1_300_000.0)
    assert pct == 30.0
    assert flag == "vermelho"


def test_aditivo_at_boundary_returns_amarelo() -> None:
    """Happy: exactly 20% additive sits in the warning band [20, 25] → 'amarelo'."""
    pct, flag = _calc_aditivo(1_000_000.0, 1_200_000.0)
    assert pct == 20.0
    assert flag == "amarelo"


def test_aditivo_below_20_returns_verde() -> None:
    """Happy: 10% additive is below the warning threshold → 'verde'."""
    pct, flag = _calc_aditivo(1_000_000.0, 1_100_000.0)
    assert pct == 10.0
    assert flag == "verde"


def test_aditivo_suppression_returns_verde() -> None:
    """Edge: negative additive (value reduced via suppression) → 'verde'."""
    pct, flag = _calc_aditivo(1_000_000.0, 900_000.0)
    assert pct == -10.0
    assert flag == "verde"


@pytest.mark.parametrize(
    "original, current",
    [
        (None, 1_200_000.0),   # original absent
        (0.0, 1_200_000.0),    # original zero — would cause ZeroDivisionError
        (-500.0, 1_200_000.0), # original negative — nonsensical
        (1_000_000.0, None),   # current absent
    ],
)
def test_aditivo_guard_returns_none_none(original: float | None, current: float | None) -> None:
    """Edge: invalid inputs must return (None, None) without raising."""
    pct, flag = _calc_aditivo(original, current)
    assert pct is None
    assert flag is None


# ---------------------------------------------------------------------------
# T-05: _calc_insolvencia — linear burn-rate insolvency projection
# ---------------------------------------------------------------------------

# 12 months ago expressed in days using the same 30.44-day month constant
_12_MONTHS_AGO = date.today() - timedelta(days=round(12 * 30.44))


def test_insolvencia_happy_path_flags_risk_and_approx_burn_rate() -> None:
    """
    Happy: 12 months elapsed, 60% spent, 20% physical, active obra.
    burn_rate ≈ 5%/month, exhaustion before 100% physical → flag True.
    """
    result = _calc_insolvencia(
        data_inicio=_12_MONTHS_AGO,
        percentual_desembolso=60.0,
        percentual_fisico=20.0,
        status="em_execucao",
    )

    assert result["burn_rate_mensal_pct"] == pytest.approx(5.0, abs=0.1)
    assert result["meses_para_exaustao"] is not None
    assert result["pct_fisico_estimado_exaustao"] is not None
    assert result["pct_fisico_estimado_exaustao"] < 100.0
    assert result["flag_risco_insolvencia"] is True


def test_insolvencia_data_inicio_none_returns_all_null() -> None:
    """Edge 1: data_inicio None → all fields None, flag False, no exception."""
    result = _calc_insolvencia(
        data_inicio=None,
        percentual_desembolso=60.0,
        percentual_fisico=20.0,
        status="em_execucao",
    )

    assert result["burn_rate_mensal_pct"] is None
    assert result["meses_para_exaustao"] is None
    assert result["pct_fisico_estimado_exaustao"] is None
    assert result["flag_risco_insolvencia"] is False


def test_insolvencia_zero_burn_rate_no_div_by_zero() -> None:
    """Edge 2: percentual_desembolso 0 → burn_rate 0 → meses_para_exaustao None, flag False."""
    result = _calc_insolvencia(
        data_inicio=_12_MONTHS_AGO,
        percentual_desembolso=0.0,
        percentual_fisico=20.0,
        status="em_execucao",
    )

    assert result["burn_rate_mensal_pct"] == 0.0
    assert result["meses_para_exaustao"] is None
    assert result["pct_fisico_estimado_exaustao"] is None
    assert result["flag_risco_insolvencia"] is False


def test_insolvencia_concluida_flag_false_regardless_of_projection() -> None:
    """Edge 3: status 'concluida' → flag False even when projection would otherwise flag risk."""
    result = _calc_insolvencia(
        data_inicio=_12_MONTHS_AGO,
        percentual_desembolso=60.0,
        percentual_fisico=20.0,
        status="concluida",
    )

    assert result["flag_risco_insolvencia"] is False
    # projection values are still computed
    assert result["burn_rate_mensal_pct"] is not None
    assert result["meses_para_exaustao"] is not None


# ---------------------------------------------------------------------------
# T-02: _calc_iec
# ---------------------------------------------------------------------------


def test_calc_iec_all_worst_gives_zero() -> None:
    """Happy path: all components at maximum penalty → IEC == 0.0."""
    result = _calc_iec(
        risco_sobrecusto=1.0,
        probabilidade_atraso=1.0,
        pct_aditivo=50.0,
        flag_risco_insolvencia=True,
    )
    assert result == 0.0


def test_calc_iec_all_best_gives_100() -> None:
    """Happy path: all components at zero penalty → IEC == 100.0."""
    result = _calc_iec(
        risco_sobrecusto=0.0,
        probabilidade_atraso=0.0,
        pct_aditivo=0.0,
        flag_risco_insolvencia=False,
    )
    assert result == 100.0


def test_calc_iec_partial_only_risco_sobrecusto() -> None:
    """Partial: only risco_sobrecusto=0.5 present, rest None/False → IEC == 82.5."""
    result = _calc_iec(
        risco_sobrecusto=0.5,
        probabilidade_atraso=None,
        pct_aditivo=None,
        flag_risco_insolvencia=False,
    )
    assert result == 82.5


def test_calc_iec_all_none_false_returns_none() -> None:
    """Edge: all inputs carry no signal → None (no score)."""
    result = _calc_iec(
        risco_sobrecusto=None,
        probabilidade_atraso=None,
        pct_aditivo=None,
        flag_risco_insolvencia=False,
    )
    assert result is None


def test_calc_iec_clamp_never_below_zero_or_above_100() -> None:
    """Edge: IEC is always in [0, 100] regardless of extreme inputs."""
    # pct_aditivo=1000 → capped at 25 pts by formula; total penalty = 100 → IEC = 0
    at_floor = _calc_iec(1.0, 1.0, 1000.0, True)
    assert at_floor == 0.0
    assert at_floor >= 0.0

    # Negative pct_aditivo (contract reduction) → 0 additive penalty; IEC = 100
    at_ceiling = _calc_iec(0.0, 0.0, -50.0, False)
    assert at_ceiling == 100.0
    assert at_ceiling <= 100.0
