from __future__ import annotations

from unittest.mock import patch

from src.domain.enums import FontePrincipal
from src.services.clean_service import (
    _build_contract_index,
    _match_obrasgov_com_tcerj,
    _normalize_contract_num,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gov(nome: str = "Obra GOV", contrato: str | None = None) -> dict:
    return {
        "id_obra_geoobras": "gov-uuid-1",
        "id_unico_obrasgov": "GOV001",
        "id_obras_tce": None,
        "nome": nome,
        "data_inicio": None,
        "valor_total_contratado": None,
        "fonte_principal": FontePrincipal.OBRASGOV.value,
        "percentual_fisico": None,
        "_contratos_raw": [{"numero_contrato": contrato}] if contrato else [],
    }


def _tce(nome: str = "Obra TCE", contrato: str | None = None, tce_id: int = 42) -> dict:
    return {
        "id_obra_geoobras": "tce-uuid-1",
        "id_unico_obrasgov": None,
        "id_obras_tce": tce_id,
        "nome": nome,
        "data_inicio": None,
        "valor_total_contratado": None,
        "fonte_principal": FontePrincipal.TCE.value,
        "percentual_fisico": None,
        "_numero_contrato": contrato,
        "_contratos_raw": [],
    }


# ---------------------------------------------------------------------------
# _normalize_contract_num
# ---------------------------------------------------------------------------


def test_normalize_strips_and_uppercases() -> None:
    assert _normalize_contract_num("  ct-2023/001  ") == "CT-2023/001"


def test_normalize_returns_none_for_empty() -> None:
    assert _normalize_contract_num(None) is None
    assert _normalize_contract_num("") is None
    assert _normalize_contract_num("   ") is None


# ---------------------------------------------------------------------------
# _build_contract_index
# ---------------------------------------------------------------------------


def test_build_contract_index_maps_numbers() -> None:
    gov = _gov(contrato="CT-001")
    idx = _build_contract_index([gov])
    assert "CT-001" in idx
    assert idx["CT-001"] is gov


def test_build_contract_index_empty_when_no_contracts() -> None:
    gov = _gov()  # no contracts
    assert _build_contract_index([gov]) == {}


# ---------------------------------------------------------------------------
# _match_obrasgov_com_tcerj — deterministic path
# ---------------------------------------------------------------------------


def test_deterministic_match_by_contract_skips_fuzzy() -> None:
    """Happy path: shared contract number → score=1.0, _match_score never called."""
    gov = _gov(nome="Pavimentação Rua A", contrato="CT-2023/001")
    tce = _gov_tce = _tce(nome="Pavimentação Diferente", contrato="CT-2023/001")

    with patch("src.services.clean_service._match_score") as mock_fuzzy:
        result = _match_obrasgov_com_tcerj([gov], [tce])

    mock_fuzzy.assert_not_called()
    assert len(result) == 1  # merged, not added as separate
    assert gov["id_obras_tce"] == 42
    assert gov["fonte_principal"] == FontePrincipal.MISTA.value


def test_deterministic_match_is_case_insensitive() -> None:
    """Contract numbers differing only in case/whitespace must still match."""
    gov = _gov(contrato=" ct-2023/001 ")
    tce = _tce(contrato="CT-2023/001")

    with patch("src.services.clean_service._match_score") as mock_fuzzy:
        result = _match_obrasgov_com_tcerj([gov], [tce])

    mock_fuzzy.assert_not_called()
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _match_obrasgov_com_tcerj — fuzzy fallback
# ---------------------------------------------------------------------------


def test_no_contract_number_falls_back_to_fuzzy() -> None:
    """Edge case: no contract keys on either side → fuzzy called, no exception."""
    gov = _gov(nome="Obras de Infraestrutura Urbana")
    tce = _tce(nome="Obras de Infraestrutura Urbana")

    with patch("src.services.clean_service._match_score", return_value=0.9) as mock_fuzzy:
        result = _match_obrasgov_com_tcerj([gov], [tce])

    mock_fuzzy.assert_called_once_with(gov, tce)
    assert len(result) == 1  # fuzzy matched → merged
    assert gov["id_obras_tce"] == 42


def test_fuzzy_fallback_below_threshold_adds_tce_separately() -> None:
    """Fuzzy score below threshold → TCE added as independent obra."""
    gov = _gov(nome="Obra A")
    tce = _tce(nome="Obra B")  # completely different names → low score

    with patch("src.services.clean_service._match_score", return_value=0.1):
        result = _match_obrasgov_com_tcerj([gov], [tce])

    assert len(result) == 2  # not merged
    assert gov["id_obras_tce"] is None  # gov untouched
