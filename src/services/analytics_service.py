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


def _calc_aditivo(
    valor_previsto_original: float | None,
    valor_total_contratado: float | None,
) -> tuple[float | None, str | None]:
    """
    Calcula percentual de aditivo e sinaliza conformidade com o teto legal
    de 25% (Lei 14.133/2021 art. 125).
    Retorna (pct_aditivo, flag_alerta_aditivo).
    """
    if valor_previsto_original is None or valor_previsto_original <= 0 or valor_total_contratado is None:
        return None, None
    pct = round((valor_total_contratado - valor_previsto_original) / valor_previsto_original * 100, 2)
    if pct < 20:
        flag = "verde"
    elif pct <= 25:
        flag = "amarelo"
    else:
        flag = "vermelho"
    return pct, flag


def _calc_insolvencia(
    data_inicio: date | None,
    percentual_desembolso: float | None,
    percentual_fisico: float | None,
    status: str | None,
) -> dict:
    """
    Linear average-rate projection of financial exhaustion (ritmo médio).

    NOTE: execucao_financeira empenhos lack per-event dates, so there is no
    disbursement time-series available. This is a single-snapshot projection —
    not a time-series forecast — that assumes the average monthly spend rate
    observed since data_inicio will continue unchanged until budget exhaustion.

    Returns a dict with keys:
        burn_rate_mensal_pct, meses_para_exaustao,
        pct_fisico_estimado_exaustao, flag_risco_insolvencia.
    Never raises; missing/invalid inputs yield None fields and flag=False.
    """
    _null: dict = {
        "burn_rate_mensal_pct": None,
        "meses_para_exaustao": None,
        "pct_fisico_estimado_exaustao": None,
        "flag_risco_insolvencia": False,
    }

    if data_inicio is None or percentual_desembolso is None or percentual_fisico is None:
        return _null

    elapsed_months = (date.today() - data_inicio).days / 30.44
    if elapsed_months <= 0:
        return _null

    burn_rate_mensal_pct = round(percentual_desembolso / elapsed_months, 2)
    fisico_rate_mensal = percentual_fisico / elapsed_months
    pct_restante = 100.0 - percentual_desembolso

    if burn_rate_mensal_pct <= 0:
        return {
            "burn_rate_mensal_pct": burn_rate_mensal_pct,
            "meses_para_exaustao": None,
            "pct_fisico_estimado_exaustao": None,
            "flag_risco_insolvencia": False,
        }

    meses_para_exaustao = round(pct_restante / burn_rate_mensal_pct, 1)
    pct_fisico_estimado_exaustao = round(
        min(100.0, percentual_fisico + fisico_rate_mensal * meses_para_exaustao), 2
    )
    flag_risco_insolvencia = (
        status not in ("concluida", "cancelada")
        and pct_fisico_estimado_exaustao < 100
    )

    return {
        "burn_rate_mensal_pct": burn_rate_mensal_pct,
        "meses_para_exaustao": meses_para_exaustao,
        "pct_fisico_estimado_exaustao": pct_fisico_estimado_exaustao,
        "flag_risco_insolvencia": flag_risco_insolvencia,
    }


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
# Índice de Eficiência Composta — IEC (0–100)
# ---------------------------------------------------------------------------


def _calc_iec(
    risco_sobrecusto: float | None,
    probabilidade_atraso: float | None,
    pct_aditivo: float | None,
    flag_risco_insolvencia: bool,
    recorrencia_count: int = 0,
) -> float | None:
    """
    Computes the Índice de Eficiência Composta (IEC) on a 0–100 scale.

    IEC = max(0, round(100 - actual_penalty, 1))
    actual_penalty = total_penalty × (100 / max_possible)

    Penalty components:
      risco_sobrecusto     → risco_sobrecusto (0..1) × 35             (max 35 pts)
      probabilidade_atraso → probabilidade_atraso (0..1) × 30         (max 30 pts)
      conformidade_aditivo → min(max(pct_aditivo, 0) / 25, 1.0) × 25 (max 25 pts)
      insolvencia          → 10.0 if flag_risco_insolvencia else 0     (max 10 pts)
      recorrencia          → count==1 → 5 pts; count>=2 → 10 pts      (max 10 pts)

    max_possible:
      100 when recorrencia_count == 0 (identity rescaling).
      110 when recorrencia_count > 0  (rescale: × 100/110).

    Rules:
    - None inputs are skipped (no penalty for missing data).
    - flag_risco_insolvencia=False contributes 0 penalty (not absent).
    - Returns None when ALL inputs carry no signal (all None / False / 0).
    """
    if (
        risco_sobrecusto is None
        and probabilidade_atraso is None
        and pct_aditivo is None
        and not flag_risco_insolvencia
        and recorrencia_count == 0
    ):
        return None

    total_penalty = 0.0
    max_possible = 100.0

    if risco_sobrecusto is not None:
        total_penalty += risco_sobrecusto * 35

    if probabilidade_atraso is not None:
        total_penalty += probabilidade_atraso * 30

    if pct_aditivo is not None:
        total_penalty += min(max(pct_aditivo, 0.0) / 25.0, 1.0) * 25

    if flag_risco_insolvencia:
        total_penalty += 10.0

    if recorrencia_count >= 2:
        total_penalty += 10.0
        max_possible = 110.0
    elif recorrencia_count == 1:
        total_penalty += 5.0
        max_possible = 110.0

    actual_penalty = total_penalty * (100.0 / max_possible)
    return max(0.0, round(100.0 - actual_penalty, 1))


# ---------------------------------------------------------------------------
# Territorial recurrence count (spatial, Euclidean approximation)
# ---------------------------------------------------------------------------

_RECORRENCIA_RADIUS_M: float = 50.0
_DEGREES_PER_METRE: float = 1.0 / 111_000.0


def _build_recorrencia_map(
    obras: list[dict],
    radius_m: float = _RECORRENCIA_RADIUS_M,
) -> dict[str, int]:
    """
    For each obra with valid coords, count how many OTHER obras are within radius_m metres.
    Returns {id_obra_geoobras: recorrencia_count}.
    O(n²) — fine for hackathon scale.

    Distance approximation: at Macaé's latitude (~22°S), 1 degree ≈ 111 km.
    Convert radius_m to degrees: radius_deg = radius_m / 111_000.
    Use Euclidean distance on (lat, lon) — accurate enough within a city.
    """
    radius_deg = radius_m * _DEGREES_PER_METRE
    result: dict[str, int] = {}
    com_coords: list[tuple[str, float, float]] = []

    for obra in obras:
        id_obra = str(obra["id_obra_geoobras"])
        lat: float | None = obra.get("latitude")
        lon: float | None = obra.get("longitude")
        result[id_obra] = 0
        if lat is not None and lon is not None:
            com_coords.append((id_obra, lat, lon))

    for i, (id_a, lat_a, lon_a) in enumerate(com_coords):
        for id_b, lat_b, lon_b in com_coords[i + 1:]:
            dlat = lat_a - lat_b
            dlon = lon_a - lon_b
            if (dlat * dlat + dlon * dlon) ** 0.5 <= radius_deg:
                result[id_a] += 1
                result[id_b] += 1

    return result


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
# Territorial recurrence (population-level, spatial + temporal)
# ---------------------------------------------------------------------------

_RAIO_METROS: float = 50.0
_JANELA_ANOS: int = 10
_EARTH_RADIUS_M: float = 6_371_000.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2.0 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _within_window(d1: date | None, d2: date | None, janela_anos: int) -> bool:
    """True if both dates are present and within the window, or if either is absent."""
    if d1 is None or d2 is None:
        return True  # conservative: include when dates unknown
    return abs((d1 - d2).days) / 365.25 <= janela_anos


def _calc_recorrencia(
    obras: list[dict],
    raio_metros: float = _RAIO_METROS,
    janela_anos: int = _JANELA_ANOS,
) -> dict[Any, dict]:
    """
    Groups obras by spatial proximity (Haversine, configurable radius) and by bairro,
    counting repeated interventions within a time window.

    Returns {id_obra: {qtd_obras_proximas, qtd_bairro, flag_recorrencia, ...}}.
    Obras without coordinates are excluded from the spatial count and logged.
    """
    # Initialise result and separate spatially valid obras
    result: dict[Any, dict] = {}
    com_coords: list[dict] = []

    for obra in obras:
        id_obra = obra["id_obra_geoobras"]
        lat: float | None = obra.get("latitude")
        lon: float | None = obra.get("longitude")

        result[id_obra] = {
            "bairro": obra.get("bairro"),
            "geom": obra.get("geom"),
            "qtd_obras_proximas": 1,
            "qtd_bairro": 1,
            "flag_recorrencia": False,
            "raio_metros": raio_metros,
            "janela_anos": janela_anos,
        }

        if lat is not None and lon is not None:
            com_coords.append(obra)
        else:
            logger.warning(
                "recorrencia: obra %s excluída do cálculo espacial (sem coordenadas)",
                id_obra,
            )

    # Spatial proximity counts (O(N²), acceptable for Macaé dataset size)
    for i, obra_a in enumerate(com_coords):
        id_a = obra_a["id_obra_geoobras"]
        lat_a: float = obra_a["latitude"]
        lon_a: float = obra_a["longitude"]
        inicio_a: date | None = obra_a.get("data_inicio")

        for obra_b in com_coords[i + 1 :]:
            id_b = obra_b["id_obra_geoobras"]
            inicio_b: date | None = obra_b.get("data_inicio")

            if not _within_window(inicio_a, inicio_b, janela_anos):
                continue
            if _haversine_m(lat_a, lon_a, obra_b["latitude"], obra_b["longitude"]) <= raio_metros:
                result[id_a]["qtd_obras_proximas"] += 1
                result[id_b]["qtd_obras_proximas"] += 1

    # Bairro counts
    bairro_groups: dict[str, list[dict]] = {}
    for obra in obras:
        b = obra.get("bairro")
        if b:
            bairro_groups.setdefault(b.strip().lower(), []).append(obra)

    for group in bairro_groups.values():
        for i, obra_a in enumerate(group):
            id_a = obra_a["id_obra_geoobras"]
            inicio_a = obra_a.get("data_inicio")
            for obra_b in group[i + 1 :]:
                if not _within_window(inicio_a, obra_b.get("data_inicio"), janela_anos):
                    continue
                result[id_a]["qtd_bairro"] += 1
                result[obra_b["id_obra_geoobras"]]["qtd_bairro"] += 1

    # Set composite flag
    for rec in result.values():
        rec["flag_recorrencia"] = rec["qtd_obras_proximas"] > 1 or rec["qtd_bairro"] > 1

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

    # Pre-pass: spatial recurrence counts (feeds IEC and territorial upsert)
    recorrencia_count_map = _build_recorrencia_map(obras)

    # Pass 1: compute per-obra metrics (needed as input to the population z-score)
    metricas: list[dict[str, Any]] = []
    for obra in obras:
        id_obra = obra["id_obra_geoobras"]
        dias_atraso, flag_atraso = _calc_dias_atraso(
            obra.get("data_fim_prevista"),
            obra.get("data_fim_real"),
            obra.get("status_obra"),
        )
        pct_aditivo, flag_alerta_aditivo = _calc_aditivo(
            obra.get("valor_previsto_original"),
            obra.get("valor_total_contratado"),
        )
        pct_desembolso = _calc_pct_desembolso(
            obra.get("valor_total_contratado"),
            obra.get("valor_pago_acumulado"),
        )
        insolvencia = _calc_insolvencia(
            obra.get("data_inicio"),
            pct_desembolso,
            obra.get("percentual_fisico"),
            obra.get("status_obra"),
        )
        metricas.append(
            {
                "id_obra_geoobras": id_obra,
                "valor_total_contratado": obra.get("valor_total_contratado"),
                "valor_pago_acumulado": obra.get("valor_pago_acumulado"),
                "percentual_desembolso": pct_desembolso,
                "percentual_fisico": obra.get("percentual_fisico"),
                "data_inicio": obra.get("data_inicio"),
                "data_fim_prevista": obra.get("data_fim_prevista"),
                "data_fim_real": obra.get("data_fim_real"),
                "dias_atraso": dias_atraso,
                "flag_possivel_atraso": flag_atraso,
                "pct_aditivo": pct_aditivo,
                "flag_alerta_aditivo": flag_alerta_aditivo,
                "burn_rate_mensal_pct": insolvencia["burn_rate_mensal_pct"],
                "meses_para_exaustao": insolvencia["meses_para_exaustao"],
                "pct_fisico_estimado_exaustao": insolvencia["pct_fisico_estimado_exaustao"],
                "flag_risco_insolvencia": insolvencia["flag_risco_insolvencia"],
            }
        )

    # Pass 2: population-level z-score across all Macaé obras
    proba_map = _calc_probabilidade_atraso(metricas)

    # Pass 3: population-level territorial recurrence
    recorrencia_map = _calc_recorrencia(obras)

    # Pass 4: upsert with complete metrics + recorrência
    with get_session() as session:
        for metrica, obra in zip(metricas, obras):
            id_obra = metrica["id_obra_geoobras"]
            prob = proba_map.get(id_obra)
            recorrencia_count = recorrencia_count_map.get(str(id_obra), 0)
            metrica["probabilidade_atraso"] = prob
            metrica["metodo_score"] = "heuristica_zscore_v1" if prob is not None else None
            metrica["iec_score"] = _calc_iec(
                metrica.get("risco_sobrecusto"),
                prob,
                metrica.get("pct_aditivo"),
                metrica.get("flag_risco_insolvencia", False),
                recorrencia_count=recorrencia_count,
            )

            upsert_metrica(session, metrica)
            counters["metricas"] += 1

            rec = recorrencia_map.get(id_obra, {})
            upsert_recorrencia_territorial(
                session,
                id_obra=id_obra,
                bairro=rec.get("bairro"),
                geom=obra.get("geom"),
                qtd_proximas=recorrencia_count,
                qtd_bairro=rec.get("qtd_bairro", 1),
                flag_recorrencia=rec.get("flag_recorrencia", False),
                raio_metros=rec.get("raio_metros", _RAIO_METROS),
                janela_anos=rec.get("janela_anos", _JANELA_ANOS),
            )
            counters["recorrencia"] += 1

    logger.info("Analytics concluído: %s", counters)
    return counters
