from __future__ import annotations

import logging
from datetime import date

import pytest

from src.services.analytics_service import _calc_recorrencia

# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------
# Reference point: Macaé city centre (~22.37°S, 41.78°W)
_LAT_A = -22.3700
_LON_A = -41.7800

# 0.0004° latitude ≈ 44 m north of A — within the 50 m radius
_LAT_B_NEAR = -22.3704
_LON_B_NEAR = -41.7800

# 0.0010° latitude ≈ 111 m north of A — outside the 50 m radius
_LAT_C_FAR = -22.3710
_LON_C_FAR = -41.7800

_TODAY = date(2022, 6, 1)
_TEN_YEARS_AGO = date(2012, 6, 1)
_FIFTEEN_YEARS_AGO = date(2007, 6, 1)


def _obra(
    id_obra: str,
    lat: float | None = None,
    lon: float | None = None,
    bairro: str | None = None,
    data_inicio: date | None = None,
) -> dict:
    return {
        "id_obra_geoobras": id_obra,
        "latitude": lat,
        "longitude": lon,
        "geom": f"POINT({lon} {lat})" if lat is not None else None,
        "bairro": bairro,
        "data_inicio": data_inicio,
    }


# ---------------------------------------------------------------------------
# Happy path: spatial proximity
# ---------------------------------------------------------------------------


def test_two_obras_within_50m_counted_as_recurrence() -> None:
    """Happy path: two obras ~44 m apart in the same time window → recurrence."""
    obras = [
        _obra("a", lat=_LAT_A, lon=_LON_A, bairro="Centro", data_inicio=_TODAY),
        _obra("b", lat=_LAT_B_NEAR, lon=_LON_B_NEAR, bairro="Centro", data_inicio=_TODAY),
    ]

    result = _calc_recorrencia(obras)

    assert result["a"]["qtd_obras_proximas"] == 2
    assert result["b"]["qtd_obras_proximas"] == 2
    assert result["a"]["flag_recorrencia"] is True
    assert result["b"]["flag_recorrencia"] is True


def test_obras_beyond_radius_not_counted_as_spatial_recurrence() -> None:
    """Two obras ~111 m apart must NOT count as spatial recurrence."""
    obras = [
        _obra("a", lat=_LAT_A, lon=_LON_A, data_inicio=_TODAY),
        _obra("c", lat=_LAT_C_FAR, lon=_LON_C_FAR, data_inicio=_TODAY),
    ]

    result = _calc_recorrencia(obras)

    assert result["a"]["qtd_obras_proximas"] == 1
    assert result["c"]["qtd_obras_proximas"] == 1


# ---------------------------------------------------------------------------
# Edge: obra without coordinates
# ---------------------------------------------------------------------------


def test_obra_without_coordinates_excluded_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Edge case: obra with no lat/lon is excluded from spatial count and a warning is logged."""
    obras = [
        _obra("no_coord", lat=None, lon=None, data_inicio=_TODAY),
        _obra("with_coord", lat=_LAT_A, lon=_LON_A, data_inicio=_TODAY),
    ]

    with caplog.at_level(logging.WARNING, logger="src.services.analytics_service"):
        result = _calc_recorrencia(obras)

    # no_coord is present in result but not counted spatially
    assert "no_coord" in result
    assert result["no_coord"]["qtd_obras_proximas"] == 1
    # warning logged for the excluded obra
    assert "no_coord" in caplog.text or "sem coordenadas" in caplog.text.lower()


def test_obra_without_coordinates_still_participates_in_bairro_count() -> None:
    """An obra without coordinates but with a bairro still counts toward bairro recurrence."""
    obras = [
        _obra("no_coord", lat=None, lon=None, bairro="Imbetiba", data_inicio=_TODAY),
        _obra("with_coord", lat=_LAT_A, lon=_LON_A, bairro="Imbetiba", data_inicio=_TODAY),
    ]

    result = _calc_recorrencia(obras)

    assert result["no_coord"]["qtd_bairro"] == 2
    assert result["no_coord"]["flag_recorrencia"] is True


# ---------------------------------------------------------------------------
# Time window
# ---------------------------------------------------------------------------


def test_obras_outside_time_window_not_counted() -> None:
    """Two obras within 50 m but >10 years apart must NOT count as recurrence."""
    obras = [
        _obra("old", lat=_LAT_A, lon=_LON_A, bairro="Centro", data_inicio=_FIFTEEN_YEARS_AGO),
        _obra("new", lat=_LAT_B_NEAR, lon=_LON_B_NEAR, bairro="Centro", data_inicio=_TODAY),
    ]

    result = _calc_recorrencia(obras, janela_anos=10)

    assert result["old"]["qtd_obras_proximas"] == 1, "15-year gap exceeds 10-year spatial window"
    assert result["old"]["qtd_bairro"] == 1, "15-year gap exceeds 10-year bairro window"
    assert result["old"]["flag_recorrencia"] is False


def test_obras_within_time_window_at_boundary_counted() -> None:
    """Obras exactly at the window boundary (10 years apart) must be counted."""
    obras = [
        _obra("older", lat=_LAT_A, lon=_LON_A, bairro="Centro", data_inicio=_TEN_YEARS_AGO),
        _obra("newer", lat=_LAT_B_NEAR, lon=_LON_B_NEAR, bairro="Centro", data_inicio=_TODAY),
    ]

    result = _calc_recorrencia(obras, janela_anos=10)

    # exactly 10 years → within window
    assert result["older"]["qtd_obras_proximas"] == 2
    assert result["older"]["flag_recorrencia"] is True


# ---------------------------------------------------------------------------
# Bairro-only recurrence (no coordinates)
# ---------------------------------------------------------------------------


def test_same_bairro_within_window_counted() -> None:
    """Obras in the same bairro within the window → bairro recurrence flagged."""
    obras = [
        _obra("b1", bairro="Lagomar", data_inicio=date(2018, 1, 1)),
        _obra("b2", bairro="Lagomar", data_inicio=date(2021, 3, 15)),
        _obra("b3", bairro="Imbetiba", data_inicio=date(2018, 1, 1)),
    ]

    result = _calc_recorrencia(obras)

    assert result["b1"]["qtd_bairro"] == 2
    assert result["b2"]["qtd_bairro"] == 2
    assert result["b3"]["qtd_bairro"] == 1  # different bairro
    assert result["b1"]["flag_recorrencia"] is True
    assert result["b3"]["flag_recorrencia"] is False
