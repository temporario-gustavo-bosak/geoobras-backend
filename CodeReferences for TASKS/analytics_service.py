"""
services/analytics_service.py
Calcula métricas a partir das tabelas CLEAN e preenche analytics.*.

Tasks implementadas:
  Task 05 — risco_sobrecusto (divergência físico-financeira, determinístico)
  Task 06 — probabilidade_atraso (z-score do atraso relativo, heurística estatística)
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


# ===========================================================================
# Helpers existentes (Task 05 em diante — não alterados)
# ===========================================================================

def _calc_pct_desembolso(valor_contratado: float | None, valor_pago: float | None) -> float | None:
    if valor_contratado and valor_contratado > 0 and valor_pago is not None:
        return round(valor_pago / valor_contratado * 100, 2)
    return None


def _calc_dias_atraso(
    data_fim_prevista: date | None,
    data_fim_real: date | None,
    status: str | None,
) -> tuple[int | None, bool]:
    hoje = date.today()
    if not data_fim_prevista:
        return None, False
    if data_fim_real:
        delta = (data_fim_real - data_fim_prevista).days
        return max(delta, 0) or None, delta > 0
    if status not in ("concluida", "cancelada"):
        delta = (hoje - data_fim_prevista).days
        return max(delta, 0) or None, delta > 0
    return None, False


# ===========================================================================
# Task 05 — Risco de Sobrecusto (determinístico, explicável)
# ===========================================================================

_LIMIAR_AMARELO  = 5.0    # pp abaixo → verde
_LIMIAR_VERMELHO = 20.0   # pp acima → vermelho
_DIV_MAX_PP      = 100.0  # normalização: 100 pp divergência = risco 1.0

METODO_RISCO_V1 = "divergencia_fisico_financeira_v1"


def _calc_risco_sobrecusto(
    pct_desembolso: float | None,
    pct_fisico: float | None,
    valor_pago: float | None,
    valor_contratado: float | None,
) -> tuple[float | None, float | None, str | None]:
    """
    Retorna (divergencia_pp, risco_sobrecusto ∈ [0,1], classe_alerta).

    divergencia  = %desembolso − %físico  (em pontos percentuais)
    risco        = clamp(max(divergencia, 0) / 100, 0, 1)
                   Divergência negativa (físico > financeiro) → risco = 0.
    override     = se valor_pago > valor_contratado → risco = 1.0 (estouro real).
    classe_alerta baseia-se em |divergencia|:
      < 5 pp  → 'verde'
      5..20   → 'amarelo'
      > 20    → 'vermelho'
    """
    if pct_desembolso is None or pct_fisico is None:
        return None, None, None

    divergencia = pct_desembolso - pct_fisico

    risco = max(0.0, min(1.0, max(divergencia, 0.0) / _DIV_MAX_PP))

    if (
        valor_pago is not None
        and valor_contratado is not None
        and valor_contratado > 0
        and valor_pago > valor_contratado
    ):
        risco = 1.0

    abs_div = abs(divergencia)
    if abs_div < _LIMIAR_AMARELO:
        classe = "verde"
    elif abs_div <= _LIMIAR_VERMELHO:
        classe = "amarelo"
    else:
        classe = "vermelho"

    return round(divergencia, 2), round(risco, 4), classe


# ===========================================================================
# Task 06 — Probabilidade de Atraso (z-score do atraso relativo)
# ===========================================================================

METODO_ATRASO_V1 = "heuristica_zscore_v1"
_POP_MINIMA = 3  # mínimo de obras com prazo válido para calcular distribuição


def _prazo_contratual_dias(
    data_inicio: date | None,
    data_fim_prevista: date | None,
) -> int | None:
    """Duração planejada em dias. None se alguma data ausente ou prazo ≤ 0."""
    if data_inicio is None or data_fim_prevista is None:
        return None
    prazo = (data_fim_prevista - data_inicio).days
    return prazo if prazo > 0 else None


def _calc_atraso_relativo(
    dias_atraso: int | None,
    prazo_contratual_dias: int | None,
) -> float | None:
    """
    Atraso relativo = dias_atraso / prazo_contratual (adimensional).

    Por que incluir obras no prazo (valor = 0.0) na distribuição?
    Porque o z-score precisa da distribuição completa. Excluir obras no
    prazo infla a média e distorce o score de quem está atrasado.

    Por que retornar 0.0 (não None) para obra sem dias_atraso?
    dias_atraso = None significa "sem atraso detectado", não "dado ausente".
    A obra participou com atraso zero.

    Por que retornar None quando prazo é None?
    Sem prazo contratual não há base para medir atraso relativo.
    A obra é excluída da distribuição e recebe probabilidade_atraso = None.
    """
    if prazo_contratual_dias is None:
        return None
    atraso = dias_atraso if dias_atraso is not None else 0
    return atraso / prazo_contratual_dias


def _calc_pop_stats(values: list[float]) -> tuple[float, float] | None:
    """
    Retorna (média, desvio-padrão) da lista, ou None em dois casos:
      1. Amostra menor que _POP_MINIMA → z-score sem sentido estatístico.
      2. Desvio-padrão ≈ 0 → todas as obras têm atraso idêntico → z indefinido.

    Usa variância populacional (divisão por n, não n-1) porque estamos
    calculando sobre a totalidade das obras de Macaé, não sobre uma amostra.
    """
    n = len(values)
    if n < _POP_MINIMA:
        logger.warning(
            "Analytics z-score: amostra insuficiente (%d obra(s) com prazo válido, "
            "mínimo=%d). probabilidade_atraso será NULL.",
            n, _POP_MINIMA,
        )
        return None

    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(variance)

    if std < 1e-9:
        logger.warning(
            "Analytics z-score: desvio-padrão ≈ 0 (todas as obras com mesmo atraso "
            "relativo). z-score indefinido. probabilidade_atraso será NULL."
        )
        return None

    return mean, std


def _logistic(z: float) -> float:
    """
    Mapeia z ∈ ℝ → (0, 1).

    Valores de referência:
      z =  0  →  0.50  (obra na média da população)
      z = +2  →  0.88  (muito acima da média em atraso)
      z = -2  →  0.12  (bem abaixo da média em atraso)

    OverflowError: math.exp estoura para |z| > ~710.
    Para z << 0: exp(-z) → +inf, divisão → 0.0  →  retorna 0.0.
    Para z >> 0: exp(-z) → 0,    divisão → 1.0  →  retorna 1.0.
    """
    try:
        return 1.0 / (1.0 + math.exp(-z))
    except OverflowError:
        return 0.0 if z < 0 else 1.0


def _calc_probabilidade_atraso(
    atraso_relativo: float | None,
    mean: float | None,
    std: float | None,
) -> float | None:
    """
    z-score do atraso relativo mapeado para [0, 1] via logística.
    Retorna None quando qualquer entrada for None (sem propagação de erro).
    """
    if atraso_relativo is None or mean is None or std is None:
        return None
    z = (atraso_relativo - mean) / std
    return round(_logistic(z), 4)


# ===========================================================================
# Pipeline principal Analytics
# ===========================================================================

def run_analytics() -> dict:
    """
    Preenche analytics.metricas_obra e analytics.recorrencia_territorial.

    Duas passagens (necessárias por Task 06):
      Pass 1 — pré-computa atraso_relativo para cada obra e deriva estatísticas
               populacionais (média + std). O z-score exige a distribuição toda
               antes de calcular o score individual.
      Pass 2 — com as estatísticas prontas, calcula todos os campos e faz upsert.
    """
    counters: dict[str, int] = {"metricas": 0, "recorrencia": 0}

    with get_session() as session:
        obras = fetch_obras_para_analytics(session)

    logger.info("Analytics: processando %d obras…", len(obras))

    # ------------------------------------------------------------------
    # Pass 1: atraso relativo por obra + estatísticas populacionais
    # ------------------------------------------------------------------
    atraso_rel_map: dict[str, float | None] = {}
    for obra in obras:
        dias_atraso, _ = _calc_dias_atraso(
            obra.get("data_fim_prevista"),
            obra.get("data_fim_real"),
            obra.get("status_obra"),
        )
        prazo = _prazo_contratual_dias(
            obra.get("data_inicio"),
            obra.get("data_fim_prevista"),
        )
        atraso_rel_map[obra["id_obra_geoobras"]] = _calc_atraso_relativo(dias_atraso, prazo)

    valores_validos = [v for v in atraso_rel_map.values() if v is not None]
    pop_stats = _calc_pop_stats(valores_validos)
    mean_ar, std_ar = pop_stats if pop_stats else (None, None)

    logger.info(
        "Analytics z-score: %d obras com prazo válido. mean=%.3f std=%.3f",
        len(valores_validos),
        mean_ar if mean_ar is not None else float("nan"),
        std_ar if std_ar is not None else float("nan"),
    )

    # ------------------------------------------------------------------
    # Pass 2: calcular métricas completas + upsert
    # ------------------------------------------------------------------
    with get_session() as session:
        for obra in obras:
            id_obra = obra["id_obra_geoobras"]

            pct_desembolso = _calc_pct_desembolso(
                obra.get("valor_total_contratado"),
                obra.get("valor_pago_acumulado"),
            )
            dias_atraso, flag_atraso = _calc_dias_atraso(
                obra.get("data_fim_prevista"),
                obra.get("data_fim_real"),
                obra.get("status_obra"),
            )
            divergencia, risco_sobrecusto, classe_alerta = _calc_risco_sobrecusto(
                pct_desembolso=pct_desembolso,
                pct_fisico=obra.get("percentual_fisico"),
                valor_pago=obra.get("valor_pago_acumulado"),
                valor_contratado=obra.get("valor_total_contratado"),
            )
            prob_atraso = _calc_probabilidade_atraso(
                atraso_rel_map.get(id_obra),
                mean_ar,
                std_ar,
            )

            # Rastreabilidade: só registra métodos que produziram resultado
            metodos = []
            if risco_sobrecusto is not None:
                metodos.append(METODO_RISCO_V1)
            if prob_atraso is not None:
                metodos.append(METODO_ATRASO_V1)

            metrica: dict[str, Any] = {
                # --- campos existentes ---
                "id_obra_geoobras":        id_obra,
                "valor_total_contratado":  obra.get("valor_total_contratado"),
                "valor_pago_acumulado":    obra.get("valor_pago_acumulado"),
                "percentual_desembolso":   pct_desembolso,
                "percentual_fisico":       obra.get("percentual_fisico"),
                "data_inicio":             obra.get("data_inicio"),
                "data_fim_prevista":       obra.get("data_fim_prevista"),
                "data_fim_real":           obra.get("data_fim_real"),
                "dias_atraso":             dias_atraso,
                "flag_possivel_atraso":    flag_atraso,
                # --- Task 05 ---
                "divergencia_fisico_financeira": divergencia,
                "risco_sobrecusto":              risco_sobrecusto,
                "classe_alerta":                 classe_alerta,
                # --- Task 06 ---
                "probabilidade_atraso":          prob_atraso,
                # --- rastreabilidade ---
                "metodo_score": "|".join(metodos) if metodos else None,
            }

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
