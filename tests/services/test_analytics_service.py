from __future__ import annotations

import logging
from datetime import date

import pytest

from src.services.analytics_service import _calc_probabilidade_atraso


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
