from __future__ import annotations

from unittest.mock import patch

from src.domain.enums import FontePrincipal
from src.services.clean_service import _match_obrasgov_com_tcerj


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gov(
    nome: str = "Obra GOV",
    valor_contratado: float | None = None,
    valor_pago: float | None = None,
    pct_fisico: float | None = None,
) -> dict:
    return {
        "id_obra_geoobras": "gov-uuid",
        "id_unico_obrasgov": "GOV001",
        "id_obras_tce": None,
        "nome": nome,
        "data_inicio": None,
        "valor_total_contratado": valor_contratado,
        "valor_pago_acumulado": valor_pago,
        "percentual_fisico": pct_fisico,
        "fonte_principal": FontePrincipal.OBRASGOV.value,
        "_contratos_raw": [],
    }


def _tce(
    nome: str = "Obra TCE",
    tce_id: int = 1,
    valor_contratado: float | None = None,
    valor_pago: float | None = None,
    pct_fisico: float | None = None,
) -> dict:
    return {
        "id_obra_geoobras": f"tce-{tce_id}",
        "id_unico_obrasgov": None,
        "id_obras_tce": tce_id,
        "nome": nome,
        "data_inicio": None,
        "valor_total_contratado": valor_contratado,
        "valor_pago_acumulado": valor_pago,
        "percentual_fisico": pct_fisico,
        "fonte_principal": FontePrincipal.TCE.value,
        "_numero_contrato": None,
        "_contratos_raw": [],
    }


# ---------------------------------------------------------------------------
# Fix (c): non-destructive financial enrichment
# ---------------------------------------------------------------------------


def test_null_gov_values_filled_from_tce() -> None:
    """Happy path: null gov financials inherit the TCE values after match."""
    gov = _gov(nome="Escola Estadual Centro", valor_contratado=None, valor_pago=None)
    tce = _tce(nome="Escola Estadual Centro", valor_contratado=500_000.0, valor_pago=250_000.0)

    with patch("src.services.clean_service._match_score", return_value=0.9):
        _match_obrasgov_com_tcerj([gov], [tce])

    assert gov["valor_total_contratado"] == 500_000.0
    assert gov["valor_pago_acumulado"] == 250_000.0


def test_non_null_gov_values_not_overwritten_by_tce() -> None:
    """Happy path: non-null gov financials must NOT be overwritten by TCE values."""
    gov = _gov(nome="Escola Estadual Centro", valor_contratado=999_000.0, valor_pago=100_000.0)
    tce = _tce(nome="Escola Estadual Centro", valor_contratado=500_000.0, valor_pago=250_000.0)

    with patch("src.services.clean_service._match_score", return_value=0.9):
        _match_obrasgov_com_tcerj([gov], [tce])

    assert gov["valor_total_contratado"] == 999_000.0
    assert gov["valor_pago_acumulado"] == 100_000.0


# ---------------------------------------------------------------------------
# Fix (a): 1:1 integrity
# ---------------------------------------------------------------------------


def test_higher_score_wins_one_to_one_conflict() -> None:
    """Edge case: two TCE obras compete for same gov → highest score claims it, loser appended."""
    gov = _gov(nome="Pavimentação Rua Principal")
    tce_strong = _tce(nome="Pavimentação Rua Principal", tce_id=10)
    tce_weak = _tce(nome="Pavimentação Rua Principal", tce_id=20)

    score_map = {id(tce_strong): 0.9, id(tce_weak): 0.6}

    with patch(
        "src.services.clean_service._match_score",
        side_effect=lambda g, t: score_map.get(id(t), 0.0),
    ):
        result = _match_obrasgov_com_tcerj([gov], [tce_strong, tce_weak])

    assert gov["id_obras_tce"] == 10, "tce_strong (score 0.9) should win"
    assert len(result) == 2, "gov merged + losing tce_weak as new obra"
    assert any(o.get("id_obras_tce") == 20 for o in result), "tce_weak must be in result"


def test_lower_score_contender_appended_as_independent_obra() -> None:
    """Edge case: the loser of a 1:1 conflict becomes a standalone obra, not discarded."""
    gov = _gov(nome="Drenagem Urbana Centro")
    tce_first = _tce(nome="Drenagem Urbana Centro", tce_id=5)
    tce_second = _tce(nome="Drenagem Urbana Centro", tce_id=6)

    # first wins (0.8 > 0.7)
    score_map = {id(tce_first): 0.8, id(tce_second): 0.7}

    with patch(
        "src.services.clean_service._match_score",
        side_effect=lambda g, t: score_map.get(id(t), 0.0),
    ):
        result = _match_obrasgov_com_tcerj([gov], [tce_first, tce_second])

    losing_obra = next((o for o in result if o.get("id_obras_tce") == 6), None)
    assert losing_obra is not None, "loser must appear in result"
    assert losing_obra.get("fonte_principal") == FontePrincipal.TCE.value


# ---------------------------------------------------------------------------
# Fix (b): name Jaccard guard
# ---------------------------------------------------------------------------


def test_low_name_jaccard_blocks_match_despite_high_total_score() -> None:
    """Edge case: completely different names → guard blocks match, _match_score never called."""
    gov = _gov(nome="Escola Municipal Centro")
    tce = _tce(nome="Viaduto Norte")

    with patch("src.services.clean_service._match_score", return_value=0.4) as mock_score:
        result = _match_obrasgov_com_tcerj([gov], [tce])

    mock_score.assert_not_called()
    assert len(result) == 2
    assert gov["id_obras_tce"] is None
