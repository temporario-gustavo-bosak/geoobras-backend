"""
services/insights_service.py
LLM-powered audit insight for a single obra, with a deterministic fallback.
The public entry point (get_obra_insight) never raises — LLM failures silently
degrade to a rule-based summary.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import get_settings
from src.infra.db import get_session
from src.infra.repositories.analytics_repository import fetch_obra_insights

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "Você é um auditor de obras públicas especializado em controle fiscal e gestão de contratos. "
    "Analise os indicadores fornecidos e produza um resumo executivo objetivo.\n\n"
    "Regras obrigatórias:\n"
    "1. Use SOMENTE os dados fornecidos. Nunca invente ou suponha informações ausentes.\n"
    "2. Destaque obrigatoriamente a divergência físico-financeira "
    "(diferença entre execução física e desembolso financeiro).\n"
    "3. Se houver alerta de atraso, mencione-o explicitamente com o número de dias.\n"
    "4. Responda exclusivamente em português do Brasil, linguagem técnica e objetiva.\n"
    "5. Não emita julgamentos jurídicos nem recomendações sem base direta nos dados.\n"
    "6. Limite a resposta a 3–4 parágrafos concisos."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fmt_pct(value: float | None, label: str) -> str:
    return f"{label}: {value:.1f}%" if value is not None else f"{label}: N/D"


def _resolve_divergencia(obra: dict) -> float | None:
    """
    Returns divergencia_fisico_financeira using the analytics convention:
    desembolso - fisico (positive = payment ahead of physical progress).
    Reads the persisted column first; recomputes only when the column is absent.
    """
    div = obra.get("divergencia_fisico_financeira")
    if div is not None:
        return div
    pct_d = obra.get("percentual_desembolso")
    pct_f = obra.get("percentual_fisico")
    if pct_d is not None and pct_f is not None:
        return round(pct_d - pct_f, 2)
    return None


def _build_user_message(obra: dict) -> str:
    pct_fisico: float | None = obra.get("percentual_fisico")
    pct_desembolso: float | None = obra.get("percentual_desembolso")
    divergencia = _resolve_divergencia(obra)
    risco: float | None = obra.get("risco_sobrecusto")
    valor_contratado: float | None = obra.get("valor_total_contratado")
    valor_pago: float | None = obra.get("valor_pago_acumulado")

    div_str = (
        f"Divergência físico-financeira: {divergencia:+.1f} p.p. (positivo = desembolso à frente da execução física)"
        if divergencia is not None
        else "Divergência: N/D"
    )
    linhas = [
        f"Obra: {obra.get('nome') or 'sem nome'}",
        f"Status: {obra.get('status_obra') or 'desconhecido'}",
        _fmt_pct(pct_fisico, "Execução física"),
        _fmt_pct(pct_desembolso, "Desembolso financeiro"),
        div_str,
        f"Dias de atraso: {obra.get('dias_atraso') or 0}",
        f"Risco de sobrecusto: {risco * 100:.1f}%" if risco is not None else "Risco de sobrecusto: N/D",
        f"Classe de alerta: {obra.get('classe_alerta') or 'N/D'}",
        f"Valor contratado: R$ {valor_contratado:,.2f}" if valor_contratado is not None else "Valor contratado: N/D",
        f"Valor pago acumulado: R$ {valor_pago:,.2f}" if valor_pago is not None else "Valor pago: N/D",
    ]
    return "\n".join(linhas)


def _build_fallback(obra: dict) -> dict:
    nome = obra.get("nome") or "Obra sem nome"
    partes: list[str] = [f"Resumo automático — {nome}."]

    pct_fisico: float | None = obra.get("percentual_fisico")
    pct_desembolso: float | None = obra.get("percentual_desembolso")
    divergencia = _resolve_divergencia(obra)
    if divergencia is not None:
        fisico_str = f"{pct_fisico:.1f}%" if pct_fisico is not None else "N/D"
        desemb_str = f"{pct_desembolso:.1f}%" if pct_desembolso is not None else "N/D"
        partes.append(
            f"Execução física: {fisico_str} | Desembolso: {desemb_str}"
            f" | Divergência físico-financeira: {divergencia:+.1f} p.p. (desembolso à frente)"
        )

    dias: int | None = obra.get("dias_atraso")
    if dias and dias > 0:
        partes.append(f"Atraso: {dias} dias além do prazo contratual.")

    classe: str | None = obra.get("classe_alerta")
    if classe:
        partes.append(f"Classe de alerta: {classe.upper()}.")

    risco: float | None = obra.get("risco_sobrecusto")
    if risco is not None:
        partes.append(f"Risco de sobrecusto estimado: {risco * 100:.1f}%.")

    return {
        "resumo": " ".join(partes),
        "fonte": "fallback",
        "id_obra_geoobras": str(obra.get("id_obra_geoobras", "")),
    }


def _call_llm(obra: dict, settings: Any) -> str:
    payload: dict[str, Any] = {
        "model": settings.LLM_MODEL,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _build_user_message(obra)}],
    }
    resp = httpx.post(
        settings.LLM_BASE_URL,
        json=payload,
        headers={
            "x-api-key": settings.LLM_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        timeout=settings.LLM_TIMEOUT,
    )
    resp.raise_for_status()
    return str(resp.json()["content"][0]["text"])


def _fetch_obra_data(id_obra: str) -> dict | None:
    with get_session() as session:
        return fetch_obra_insights(session, id_obra)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_obra_insight(id_obra: str) -> dict:
    """
    Returns a LLM-generated audit insight or a deterministic fallback.
    Never raises — callers always receive a dict with 'fonte' in {'llm', 'fallback'}.
    """
    obra = _fetch_obra_data(id_obra)
    if not obra:
        return {"erro": "obra não encontrada", "fonte": "fallback", "id_obra_geoobras": id_obra}

    settings = get_settings()
    if not settings.LLM_API_KEY:
        logger.info("LLM_API_KEY ausente — fallback para obra %s", id_obra)
        return _build_fallback(obra)

    try:
        resumo = _call_llm(obra, settings)
        return {"resumo": resumo, "fonte": "llm", "id_obra_geoobras": id_obra}
    except Exception as exc:
        logger.warning("Falha na chamada LLM (obra %s): %s — usando fallback", id_obra, exc)
        return _build_fallback(obra)
