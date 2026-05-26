"""
services/analytics_service.py
Calcula métricas simples a partir das tabelas CLEAN e preenche analytics.*.
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

from src.infra.db import get_session
from src.infra.repositories.analytics_repository import (
    fetch_obras_para_analytics,
    upsert_metrica,
    upsert_recorrencia_territorial,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cálculos
# ---------------------------------------------------------------------------


def _calc_pct_desembolso(valor_contratado: float | None, valor_pago: float | None) -> float | None:
    if valor_contratado and valor_contratado > 0 and valor_pago is not None:
        return round(valor_pago / valor_contratado * 100, 2)
    return None


def _calc_dias_atraso(
    data_fim_prevista: date | None,
    data_fim_real: date | None,
    status: str | None,
) -> tuple[int | None, bool]:
    """
    Retorna (dias_atraso, flag_possivel_atraso).
    - dias_atraso > 0 → atrasada (concluída tardiamente ou ainda em execução além do prazo)
    - flag_possivel_atraso → True se atrasada
    """
    hoje = date.today()

    if not data_fim_prevista:
        return None, False

    if data_fim_real:
        # obra concluída: compara data real com prevista
        delta = (data_fim_real - data_fim_prevista).days
        return max(delta, 0) or None, delta > 0

    # obra não concluída: compara hoje com prevista
    if status not in ("concluida", "cancelada"):
        delta = (hoje - data_fim_prevista).days
        return max(delta, 0) or None, delta > 0

    return None, False


# ---------------------------------------------------------------------------
# Z-score delay probability (population-level)
# ---------------------------------------------------------------------------

_MIN_SAMPLE = 3


def _calc_probabilidade_atraso(metricas: list[dict]) -> dict[Any, float | None]:
    """
    Maps relative delay (dias_atraso / contractual_term_days) through a
    population z-score + logistic function to produce a [0,1] probability.

    Returns None for every obra when the sample is too small to form a
    meaningful distribution (< _MIN_SAMPLE obras with a valid contractual term).
    """
    sample: list[tuple[Any, float]] = []
    for m in metricas:
        data_inicio: date | None = m.get("data_inicio")
        data_fim: date | None = m.get("data_fim_prevista")
        if not data_inicio or not data_fim:
            continue
        term_days = (data_fim - data_inicio).days
        if term_days <= 0:
            continue
        dias = m.get("dias_atraso") or 0
        sample.append((m["id_obra_geoobras"], dias / term_days))

    if len(sample) < _MIN_SAMPLE:
        logger.warning(
            "probabilidade_atraso: insufficient sample (%d obras with valid term, min=%d)",
            len(sample),
            _MIN_SAMPLE,
        )
        return {m["id_obra_geoobras"]: None for m in metricas}

    delays = [rd for _, rd in sample]
    mean = sum(delays) / len(delays)
    variance = sum((d - mean) ** 2 for d in delays) / len(delays)
    std = math.sqrt(variance)

    result: dict[Any, float | None] = {m["id_obra_geoobras"]: None for m in metricas}
    for id_obra, rd in sample:
        z = (rd - mean) / std if std > 0 else 0.0
        result[id_obra] = round(1.0 / (1.0 + math.exp(-z)), 4)

    return result


# ---------------------------------------------------------------------------
# Pipeline principal Analytics
# ---------------------------------------------------------------------------


def run_analytics() -> dict:
    """
    Preenche analytics.metricas_obra e analytics.recorrencia_territorial
    a partir de clean.obras.
    """
    counters = {"metricas": 0, "recorrencia": 0}

    with get_session() as session:
        obras = fetch_obras_para_analytics(session)

    logger.info("Analytics: processando %d obras…", len(obras))

    # Pass 1: compute per-obra metrics (needed as input to the population z-score)
    metricas: list[dict[str, Any]] = []
    for obra in obras:
        id_obra = obra["id_obra_geoobras"]
        dias_atraso, flag_atraso = _calc_dias_atraso(
            obra.get("data_fim_prevista"),
            obra.get("data_fim_real"),
            obra.get("status_obra"),
        )
        metricas.append(
            {
                "id_obra_geoobras": id_obra,
                "valor_total_contratado": obra.get("valor_total_contratado"),
                "valor_pago_acumulado": obra.get("valor_pago_acumulado"),
                "percentual_desembolso": _calc_pct_desembolso(
                    obra.get("valor_total_contratado"),
                    obra.get("valor_pago_acumulado"),
                ),
                "percentual_fisico": obra.get("percentual_fisico"),
                "data_inicio": obra.get("data_inicio"),
                "data_fim_prevista": obra.get("data_fim_prevista"),
                "data_fim_real": obra.get("data_fim_real"),
                "dias_atraso": dias_atraso,
                "flag_possivel_atraso": flag_atraso,
            }
        )

    # Pass 2: population-level z-score across all Macaé obras
    proba_map = _calc_probabilidade_atraso(metricas)

    # Pass 3: upsert with complete metrics (including risk columns)
    with get_session() as session:
        for metrica in metricas:
            id_obra = metrica["id_obra_geoobras"]
            prob = proba_map.get(id_obra)
            metrica["probabilidade_atraso"] = prob
            metrica["metodo_score"] = "heuristica_zscore_v1" if prob is not None else None

            upsert_metrica(session, metrica)
            counters["metricas"] += 1

            upsert_recorrencia_territorial(
                session,
                id_obra=id_obra,
                bairro=None,
                geom=None,
            )
            counters["recorrencia"] += 1

    logger.info("Analytics concluído: %s", counters)
    return counters
