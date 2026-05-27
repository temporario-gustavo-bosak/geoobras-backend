from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from src.infra.repositories.analytics_repository import fetch_obras_para_analytics, upsert_metrica

MIGRATION_PATH = Path(__file__).parents[2] / "sql" / "002_analytics_risco.sql"
MIGRATION_FINANCIAL_HEALTH_PATH = Path(__file__).parents[2] / "sql" / "004_financial_health.sql"

_FINANCIAL_HEALTH_COLUMNS = {
    "pct_aditivo",
    "flag_alerta_aditivo",
    "burn_rate_mensal_pct",
    "meses_para_exaustao",
    "pct_fisico_estimado_exaustao",
    "flag_risco_insolvencia",
}

NEW_COLUMNS = {
    "divergencia_fisico_financeira",
    "risco_sobrecusto",
    "probabilidade_atraso",
    "classe_alerta",
    "metodo_score",
}


# ---------------------------------------------------------------------------
# Migration idempotency
# ---------------------------------------------------------------------------


def test_migration_uses_add_column_if_not_exists() -> None:
    """Every ADD COLUMN in the migration must carry IF NOT EXISTS."""
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    bare = re.findall(r"ADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)", sql, re.IGNORECASE)
    assert bare == [], f"Found ADD COLUMN without IF NOT EXISTS: {bare}"


def test_migration_uses_create_index_if_not_exists() -> None:
    """The index creation must carry IF NOT EXISTS."""
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    bare = re.findall(r"CREATE\s+INDEX\s+(?!IF\s+NOT\s+EXISTS)", sql, re.IGNORECASE)
    assert bare == [], f"Found CREATE INDEX without IF NOT EXISTS: {bare}"


def test_migration_declares_all_five_new_columns() -> None:
    """All five expected column names appear in the migration file."""
    sql = MIGRATION_PATH.read_text(encoding="utf-8").lower()
    for col in NEW_COLUMNS:
        assert col in sql, f"Column '{col}' not found in migration"


# ---------------------------------------------------------------------------
# upsert_metrica passes all five new keys
# ---------------------------------------------------------------------------


def _base_metrica() -> dict:
    return {
        "id_obra_geoobras": "aaaaaaaa-0000-0000-0000-000000000001",
        "valor_total_contratado": 1_000_000.0,
        "valor_pago_acumulado": 500_000.0,
        "percentual_desembolso": 50.0,
        "percentual_fisico": 45.0,
        "data_inicio": None,
        "data_fim_prevista": None,
        "data_fim_real": None,
        "dias_atraso": None,
        "flag_possivel_atraso": False,
    }


def test_upsert_metrica_passes_all_five_risk_keys() -> None:
    """Happy path: upsert_metrica includes all 5 new risk params in the execute call."""
    session = MagicMock()
    m = {
        **_base_metrica(),
        "divergencia_fisico_financeira": 5.0,
        "risco_sobrecusto": 0.25,
        "probabilidade_atraso": 0.10,
        "classe_alerta": "amarelo",
        "metodo_score": "linear_v1",
    }

    upsert_metrica(session, m)

    params = session.execute.call_args.args[1]
    assert params["divergencia"] == 5.0
    assert params["risco_sobrecusto"] == 0.25
    assert params["prob_atraso"] == 0.10
    assert params["classe_alerta"] == "amarelo"
    assert params["metodo_score"] == "linear_v1"


def test_upsert_metrica_risk_keys_default_to_none() -> None:
    """Edge case: missing risk fields default to None — existing fields are unaffected."""
    session = MagicMock()
    upsert_metrica(session, _base_metrica())

    params = session.execute.call_args.args[1]
    assert params["divergencia"] is None
    assert params["risco_sobrecusto"] is None
    assert params["prob_atraso"] is None
    assert params["classe_alerta"] is None
    assert params["metodo_score"] is None
    # existing params still present
    assert params["flag_atraso"] is False


# ---------------------------------------------------------------------------
# T-01: fetch_obras_para_analytics exposes valor_previsto_original
# ---------------------------------------------------------------------------


def test_fetch_obras_para_analytics_includes_valor_previsto_original() -> None:
    """Happy path: the returned dict must contain 'valor_previsto_original' (needed for KF-B additive calc)."""
    session = MagicMock()
    fake_row = {
        "id_obra_geoobras": "aaaaaaaa-0000-0000-0000-000000000001",
        "valor_total_contratado": 1_200_000.0,
        "valor_pago_acumulado": 600_000.0,
        "valor_previsto_original": 1_000_000.0,
        "percentual_fisico": 50.0,
        "data_inicio": None,
        "data_fim_prevista": None,
        "data_fim_real": None,
        "status_obra": "em_execucao",
        "latitude": None,
        "longitude": None,
        "geom": None,
        "bairro": None,
    }
    session.execute.return_value.mappings.return_value.all.return_value = [fake_row]

    result = fetch_obras_para_analytics(session)

    assert len(result) == 1
    assert "valor_previsto_original" in result[0]
    assert result[0]["valor_previsto_original"] == 1_000_000.0


# ---------------------------------------------------------------------------
# T-03: 004_financial_health.sql migration idempotency
# ---------------------------------------------------------------------------


def test_financial_health_migration_uses_add_column_if_not_exists() -> None:
    """Every ADD COLUMN in 004_financial_health.sql must carry IF NOT EXISTS."""
    sql = MIGRATION_FINANCIAL_HEALTH_PATH.read_text(encoding="utf-8")
    bare = re.findall(r"ADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)", sql, re.IGNORECASE)
    assert bare == [], f"Found ADD COLUMN without IF NOT EXISTS: {bare}"


def test_financial_health_migration_uses_create_index_if_not_exists() -> None:
    """Any index in 004_financial_health.sql must carry IF NOT EXISTS."""
    sql = MIGRATION_FINANCIAL_HEALTH_PATH.read_text(encoding="utf-8")
    bare = re.findall(r"CREATE\s+INDEX\s+(?!IF\s+NOT\s+EXISTS)", sql, re.IGNORECASE)
    assert bare == [], f"Found CREATE INDEX without IF NOT EXISTS: {bare}"


def test_financial_health_migration_declares_all_six_new_columns() -> None:
    """All six expected column names appear in 004_financial_health.sql."""
    sql = MIGRATION_FINANCIAL_HEALTH_PATH.read_text(encoding="utf-8").lower()
    for col in _FINANCIAL_HEALTH_COLUMNS:
        assert col in sql, f"Column '{col}' not found in migration"


# ---------------------------------------------------------------------------
# T-03: upsert_metrica passes the 6 new financial-health keys
# ---------------------------------------------------------------------------


def test_upsert_metrica_passes_all_six_financial_health_keys() -> None:
    """Happy path: upsert_metrica forwards all 6 financial-health params to session.execute."""
    session = MagicMock()
    m = {
        **_base_metrica(),
        "pct_aditivo": 12.5,
        "flag_alerta_aditivo": "amarelo",
        "burn_rate_mensal_pct": 8.3,
        "meses_para_exaustao": 6.0,
        "pct_fisico_estimado_exaustao": 45.0,
        "flag_risco_insolvencia": True,
    }

    upsert_metrica(session, m)

    params = session.execute.call_args.args[1]
    assert params["pct_aditivo"] == 12.5
    assert params["flag_alerta_aditivo"] == "amarelo"
    assert params["burn_rate_mensal_pct"] == 8.3
    assert params["meses_para_exaustao"] == 6.0
    assert params["pct_fisico_estimado_exaustao"] == 45.0
    assert params["flag_risco_insolvencia"] is True


def test_upsert_metrica_financial_health_keys_default_correctly() -> None:
    """Edge case: missing financial-health fields default to None or False — existing fields unaffected."""
    session = MagicMock()
    upsert_metrica(session, _base_metrica())

    params = session.execute.call_args.args[1]
    assert params["pct_aditivo"] is None
    assert params["flag_alerta_aditivo"] is None
    assert params["burn_rate_mensal_pct"] is None
    assert params["meses_para_exaustao"] is None
    assert params["pct_fisico_estimado_exaustao"] is None
    assert params["flag_risco_insolvencia"] is False
    # existing params unaffected
    assert params["flag_atraso"] is False
    assert params["divergencia"] is None
