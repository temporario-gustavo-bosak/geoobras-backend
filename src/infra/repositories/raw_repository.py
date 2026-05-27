"""
infra/repositories/raw_repository.py
Operações de escrita (upsert/insert) nas tabelas do esquema RAW.
Usa SQLAlchemy Core para queries explícitas (sem ORM pesado).
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _clean_nan(obj: Any) -> Any:
    """Substitui float NaN/Inf por None recursivamente (pandas usa NaN para células vazias)."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    return obj


def _jsonb(obj: Any) -> str | None:
    """Serializa para string JSON, aceito pelo psycopg2 em campos JSONB."""
    if obj is None:
        return None
    return json.dumps(_clean_nan(obj), ensure_ascii=False, default=str)


def _int(v: Any) -> int | None:
    """Converte valor para int, retornando None para strings vazias ou inválidas."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _ts_ms_to_date(v: Any) -> str | None:
    """Converte timestamp em milissegundos (int) para string ISO 'YYYY-MM-DD'."""
    if v is None or v == "":
        return None
    try:
        from datetime import datetime, timezone

        ts = int(v) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return None


def _date_br(v: Any) -> str | None:
    """Converte 'DD/MM/YYYY' → 'YYYY-MM-DD'; retorna None se inválido."""
    if v is None or v == "":
        return None
    try:
        from datetime import datetime

        return datetime.strptime(str(v).strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _pct(v: Any) -> float | None:
    """Converte '41,10%' ou '100%' → float; retorna None se inválido."""
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("%", "").replace(",", ".").strip())
    except (ValueError, TypeError):
        return None


def _bool_sim_nao(v: Any) -> bool | None:
    """Converte 'SIM'/'NÃO' (string) → bool."""
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in ("SIM", "S", "TRUE", "1", "YES"):
        return True
    if s in ("NÃO", "NAO", "N", "FALSE", "0", "NO"):
        return False
    return None


# ---------------------------------------------------------------------------
# ObrasGov – Projetos
# ---------------------------------------------------------------------------

UPSERT_PROJETO = text("""
    INSERT INTO raw.obrasgov_projetos (
        id_unico, nome, situacao,
        data_inicial_prevista, data_inicial_efetiva,
        data_final_prevista, data_final_efetiva,
        descricao, endereco, municipio, uf,
        populacao_beneficiada, qtd_empregos_gerados,
        tipos, sub_tipos, fontes_de_recurso, payload_json
    ) VALUES (
        :id_unico, :nome, :situacao,
        :data_inicial_prevista, :data_inicial_efetiva,
        :data_final_prevista, :data_final_efetiva,
        :descricao, :endereco, :municipio, :uf,
        :populacao_beneficiada, :qtd_empregos_gerados,
        CAST(:tipos AS jsonb), CAST(:sub_tipos AS jsonb),
        CAST(:fontes_de_recurso AS jsonb), CAST(:payload_json AS jsonb)
    )
    ON CONFLICT (id_unico) DO UPDATE SET
        nome = EXCLUDED.nome,
        situacao = EXCLUDED.situacao,
        municipio = EXCLUDED.municipio,
        data_final_efetiva = EXCLUDED.data_final_efetiva,
        populacao_beneficiada = EXCLUDED.populacao_beneficiada,
        qtd_empregos_gerados = EXCLUDED.qtd_empregos_gerados,
        payload_json = EXCLUDED.payload_json,
        ingestado_em = NOW()
""")


def upsert_projeto(session: Session, row: dict[str, Any]) -> None:
    session.execute(
        UPSERT_PROJETO,
        {
            "id_unico": row.get("idUnico"),
            "nome": row.get("nome"),
            "situacao": row.get("situacao"),
            "data_inicial_prevista": row.get("dataInicialPrevista"),
            "data_inicial_efetiva": row.get("dataInicialEfetiva"),
            "data_final_prevista": row.get("dataFinalPrevista"),
            "data_final_efetiva": row.get("dataFinalEfetiva"),
            "descricao": row.get("descricao"),
            "endereco": row.get("endereco"),
            "municipio": row.get("municipio") or row.get("nomeMunicipio"),
            "uf": row.get("uf"),
            "populacao_beneficiada": _int(row.get("populacaoBeneficiada")),
            "qtd_empregos_gerados": _int(row.get("qdtEmpregosGerados")),
            "tipos": _jsonb(row.get("tipos")),
            "sub_tipos": _jsonb(row.get("subTipos")),
            "fontes_de_recurso": _jsonb(row.get("fontesDeRecurso")),
            "payload_json": _jsonb(row),
        },
    )


# ---------------------------------------------------------------------------
# ObrasGov – Execução Física
# ---------------------------------------------------------------------------

INSERT_EF = text("""
    INSERT INTO raw.obrasgov_execucao_fisica (
        id_unico, data_situacao, percentual, situacao, observacoes
    ) VALUES (
        :id_unico, :data_situacao, :percentual, :situacao, :observacoes
    )
""")


def upsert_execucao_fisica(session: Session, id_unico: str, row: dict[str, Any]) -> None:
    data_sit = row.get("dataSituacao") or row.get("data_situacao")
    if not data_sit:
        logger.warning("execucao_fisica sem data_situacao para %s – ignorado", id_unico)
        return
    session.execute(
        INSERT_EF,
        {
            "id_unico": id_unico,
            "data_situacao": data_sit,
            "percentual": row.get("percentual"),
            "situacao": row.get("situacao"),
            "observacoes": row.get("observacoes"),
        },
    )


# ---------------------------------------------------------------------------
# ObrasGov – Execução Financeira (empenhos)
# ---------------------------------------------------------------------------

INSERT_FINANCEIRA = text("""
    INSERT INTO raw.obrasgov_execucao_financeira (
        id_projeto_investimento, valor_empenho
    ) VALUES (
        :id_projeto, :valor_empenho
    )
""")


def upsert_execucao_financeira(session: Session, id_projeto: str, row: dict[str, Any]) -> None:
    session.execute(
        INSERT_FINANCEIRA,
        {
            "id_projeto": id_projeto,
            "valor_empenho": row.get("valorEmpenho"),
        },
    )


# ---------------------------------------------------------------------------
# ObrasGov – Contratos
# ---------------------------------------------------------------------------

INSERT_CONTRATO = text("""
    INSERT INTO raw.obrasgov_contratos (
        id_projeto_investimento, numero_contrato,
        valor_global, valor_acumulado, vigencia_fim, situacao, payload_json
    ) VALUES (
        :id_projeto, :numero_contrato,
        :valor_global, :valor_acumulado, :vigencia_fim, :situacao, CAST(:payload_json AS jsonb)
    )
""")


def upsert_contrato(session: Session, id_projeto: str, row: dict[str, Any]) -> None:
    numero = row.get("numeroContrato") or row.get("numero_contrato")
    if not numero:
        logger.warning("contrato sem numeroContrato para %s – ignorado", id_projeto)
        return
    session.execute(
        INSERT_CONTRATO,
        {
            "id_projeto": id_projeto,
            "numero_contrato": numero,
            "valor_global": row.get("valorGlobal"),
            "valor_acumulado": row.get("valorAcumulado"),
            "vigencia_fim": row.get("vigenciaFim"),
            "situacao": row.get("situacao"),
            "payload_json": _jsonb(row),
        },
    )


# ---------------------------------------------------------------------------
# ObrasGov – Geometria
# ---------------------------------------------------------------------------

INSERT_GEOMETRIA = text("""
    INSERT INTO raw.obrasgov_geometria (
        id_unico, geometria_wkt, geometria_raw
    ) VALUES (
        :id_unico, :geometria_wkt, :geometria_raw
    )
    ON CONFLICT (id_unico) DO UPDATE SET
        geometria_wkt = EXCLUDED.geometria_wkt,
        geometria_raw = EXCLUDED.geometria_raw,
        ingestado_em  = NOW()
""")


def insert_geometria(session: Session, id_unico: str, row: dict[str, Any]) -> None:
    wkt_value = row.get("geometria") or row.get("geometriaWkt") or row.get("wkt")
    session.execute(
        INSERT_GEOMETRIA,
        {
            "id_unico": id_unico,
            "geometria_wkt": wkt_value,
            "geometria_raw": _jsonb(row),
        },
    )


# ---------------------------------------------------------------------------
# TCE-RJ – Obras
# ---------------------------------------------------------------------------

INSERT_TCERJ_OBRA = text("""
    INSERT INTO raw.tcerj_obras (
        nome, situacao, percentual_concluido, data_inicio, data_fim_prevista
    ) VALUES (
        :nome, :situacao, :percentual_concluido, :data_inicio, :data_fim_prevista
    )
    RETURNING id
""")


def insert_tcerj_obra(session: Session, row: dict[str, Any]) -> int:
    result = session.execute(
        INSERT_TCERJ_OBRA,
        {
            "nome": row.get("Objeto"),
            "situacao": row.get("Situacao"),
            "percentual_concluido": _pct(row.get("PercentualConcluido")),
            "data_inicio": _ts_ms_to_date(row.get("DataInicio")),
            "data_fim_prevista": _date_br(row.get("PrevisaoConclusao")),
        },
    )
    return result.scalar()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# TCE-RJ – Obras Paralisadas
# ---------------------------------------------------------------------------

INSERT_TCERJ_PARALISADA = text("""
    INSERT INTO raw.tcerj_obras_paralisadas (
        nome, valor_total_contrato, valor_pago_obra,
        motivo_paralisacao, ente, ano_referencia
    ) VALUES (
        :nome, :valor_total_contrato, :valor_pago_obra,
        :motivo_paralisacao, :ente, :ano_referencia
    )
""")


def insert_tcerj_paralisada(session: Session, row: dict[str, Any]) -> None:
    session.execute(
        INSERT_TCERJ_PARALISADA,
        {
            "nome": row.get("Nome"),
            "valor_total_contrato": row.get("ValorTotalContrato"),
            "valor_pago_obra": row.get("ValorPagoObra"),
            "motivo_paralisacao": row.get("MotivoParalisacao"),
            "ente": row.get("Ente"),
            "ano_referencia": _int(row.get("AnoParalisacao")),
        },
    )


# ---------------------------------------------------------------------------
# Macaé – Convênios (CSV)
# ---------------------------------------------------------------------------

INSERT_CONVENIO = text("""
    INSERT INTO raw.macae_convenios (
        numero_convenio, valor_repasse, valor_contrapartida
    ) VALUES (
        :numero_convenio, :valor_repasse, :valor_contrapartida
    )
""")


def insert_convenio(session: Session, row: dict[str, Any], arquivo: str, linha: int) -> None:
    def _money(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(str(v).replace(".", "").replace(",", ".").strip())
        except (ValueError, AttributeError):
            return None

    session.execute(
        INSERT_CONVENIO,
        {
            "numero_convenio": row.get("Nº Instrumento"),
            "valor_repasse": _money(row.get("Valor Concedente")),
            "valor_contrapartida": _money(row.get("Valor Convenente")),
        },
    )
