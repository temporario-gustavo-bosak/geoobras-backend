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
    "3. Se o percentual de aditivos superar 25% (teto legal — Lei 14.133/2021 art. 125), "
    "destaque explicitamente essa irregularidade com o valor exato.\n"
    "4. Se houver risco de insolvência financeira (orçamento estimado para esgotar antes da "
    "conclusão física), mencione os meses projetados até esgotamento e o percentual físico esperado.\n"
    "5. Se houver alerta de atraso, mencione-o explicitamente com o número de dias.\n"
    "6. Responda exclusivamente em português do Brasil, linguagem técnica e objetiva.\n"
    "7. Não emita julgamentos jurídicos nem recomendações sem base direta nos dados.\n"
    "8. Limite a resposta a 3–4 parágrafos concisos."
)

_SYSTEM_PROMPT_CIDADAO = (
    "Você explica obras públicas para cidadãos comuns, sem usar termos técnicos ou jurídicos. "
    "Analise os dados fornecidos e produza um resumo simples e acessível.\n\n"
    "Regras obrigatórias:\n"
    "1. Use SOMENTE os dados fornecidos. Nunca invente ou suponha informações ausentes.\n"
    "2. Explique o que os números significam na prática — por exemplo, o que significa "
    "40% de execução física para quem mora no bairro.\n"
    "3. Se a obra estiver atrasada, explique o impacto para os moradores e a comunidade, "
    "mencionando o número de dias de atraso.\n"
    "4. Se o contrato aumentou além do valor original, explique em linguagem simples: "
    "'o contrato desta obra já aumentou X% além do valor combinado inicialmente'.\n"
    "5. Se houver risco de o dinheiro acabar antes da obra terminar, explique assim: "
    "'com o ritmo atual de gastos, o orçamento pode se esgotar antes de a obra ser concluída'.\n"
    "6. Use linguagem simples, direta e acessível ao público geral. Evite jargão técnico e termos de auditoria.\n"
    "7. Não faça julgamentos jurídicos nem acusações sem base direta nos dados.\n"
    "8. Limite a resposta a 3–4 parágrafos curtos."
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
    pct_aditivo: float | None = obra.get("pct_aditivo")
    flag_alerta_aditivo: str | None = obra.get("flag_alerta_aditivo")
    meses_exaustao: float | None = obra.get("meses_para_exaustao")
    pct_fisico_exaustao: float | None = obra.get("pct_fisico_estimado_exaustao")
    flag_insolvencia: bool | None = obra.get("flag_risco_insolvencia")

    div_str = (
        f"Divergência físico-financeira: {divergencia:+.1f} p.p. (positivo = desembolso à frente da execução física)"
        if divergencia is not None
        else "Divergência: N/D"
    )

    if pct_aditivo is not None:
        aditivo_str = (
            f"Aditivos contratuais: {pct_aditivo:.1f}% do valor original (teto legal 25%)"
            f" — alerta: {flag_alerta_aditivo or 'N/D'}"
        )
    else:
        aditivo_str = "Aditivos contratuais: N/D"

    if meses_exaustao is not None and pct_fisico_exaustao is not None:
        insolvencia_str = (
            f"Projeção (ritmo médio): orçamento esgota em ~{meses_exaustao:.1f} meses,"
            f" obra em ~{pct_fisico_exaustao:.1f}% físico"
            f" — risco de insolvência: {'sim' if flag_insolvencia else 'não'}"
        )
    else:
        insolvencia_str = "Projeção (ritmo médio): N/D — risco de insolvência: N/D"

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
        aditivo_str,
        insolvencia_str,
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

    pct_aditivo: float | None = obra.get("pct_aditivo")
    flag_alerta_aditivo: str | None = obra.get("flag_alerta_aditivo")
    if pct_aditivo is not None:
        partes.append(
            f"Aditivo contratual: {pct_aditivo:.1f}% do valor original"
            f" — alerta: {flag_alerta_aditivo or 'N/D'}."
        )

    flag_insolvencia: bool | None = obra.get("flag_risco_insolvencia")
    meses_exaustao: float | None = obra.get("meses_para_exaustao")
    pct_fisico_exaustao: float | None = obra.get("pct_fisico_estimado_exaustao")
    if flag_insolvencia and meses_exaustao is not None:
        partes.append(
            f"Risco de insolvência: orçamento estimado para esgotar em {meses_exaustao:.1f} meses,"
            f" com obra em ~{pct_fisico_exaustao:.1f}% físico."
        )

    return {
        "resumo": " ".join(partes),
        "fonte": "fallback",
        "id_obra_geoobras": str(obra.get("id_obra_geoobras", "")),
    }


def _build_fallback_cidadao(obra: dict) -> dict:
    nome = obra.get("nome") or "Obra sem nome"
    partes: list[str] = [f"Informações sobre a obra: {nome}."]

    pct_fisico: float | None = obra.get("percentual_fisico")
    divergencia = _resolve_divergencia(obra)

    if pct_fisico is not None:
        partes.append(f"Até o momento, {pct_fisico:.0f}% da obra foi concluído fisicamente.")

    if divergencia is not None:
        if divergencia > 0:
            partes.append(
                f"O município já pagou mais do que o progresso físico da obra justifica "
                f"({divergencia:+.1f} pontos percentuais a mais no pagamento do que na execução)."
            )
        elif divergencia < 0:
            partes.append(
                f"A obra avançou mais fisicamente do que o valor pago até agora "
                f"({abs(divergencia):.1f} pontos percentuais à frente no físico)."
            )

    dias: int | None = obra.get("dias_atraso")
    if dias and dias > 0:
        partes.append(
            f"A obra está {dias} dias atrasada em relação ao prazo original, "
            f"o que pode adiar os benefícios esperados pela comunidade."
        )

    pct_aditivo: float | None = obra.get("pct_aditivo")
    if pct_aditivo is not None and pct_aditivo > 0:
        partes.append(
            f"O contrato desta obra já aumentou {pct_aditivo:.1f}% além do valor original contratado."
        )

    flag_insolvencia: bool | None = obra.get("flag_risco_insolvencia")
    meses_exaustao: float | None = obra.get("meses_para_exaustao")
    if flag_insolvencia and meses_exaustao is not None:
        partes.append(
            f"Com o ritmo atual de gastos, o orçamento pode se esgotar em cerca de "
            f"{meses_exaustao:.1f} meses, antes de a obra ser concluída."
        )

    return {
        "resumo": " ".join(partes),
        "fonte": "fallback",
        "id_obra_geoobras": str(obra.get("id_obra_geoobras", "")),
    }


def _call_llm(obra: dict, settings: Any, persona: str = "auditor") -> str:
    system_prompt = _SYSTEM_PROMPT_CIDADAO if persona == "cidadao" else _SYSTEM_PROMPT
    payload: dict[str, Any] = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_user_message(obra)},
        ],
        "max_tokens": settings.LLM_MAX_TOKENS,
    }
    resp = httpx.post(
        settings.LLM_BASE_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {settings.LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=settings.LLM_TIMEOUT,
    )
    resp.raise_for_status()
    return str(resp.json()["choices"][0]["message"]["content"])


def _fetch_obra_data(id_obra: str) -> dict | None:
    with get_session() as session:
        return fetch_obra_insights(session, id_obra)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_obra_insight(id_obra: str, obra: dict | None = None, persona: str = "auditor") -> dict:
    """
    Returns a LLM-generated audit insight or a deterministic fallback.
    Never raises — callers always receive a dict with 'fonte' in {'llm', 'fallback'}.

    Pass a pre-fetched obra dict to avoid a redundant DB round-trip; if omitted,
    the function fetches internally (backward-compatible).
    `persona` is accepted for future prompt differentiation (unused in this iteration).
    """
    if obra is None:
        obra = _fetch_obra_data(id_obra)
    if not obra:
        return {"erro": "obra não encontrada", "fonte": "fallback", "id_obra_geoobras": id_obra}

    settings = get_settings()
    if not settings.LLM_API_KEY:
        logger.info("LLM_API_KEY ausente — fallback para obra %s", id_obra)
        return _build_fallback_cidadao(obra) if persona == "cidadao" else _build_fallback(obra)

    try:
        resumo = _call_llm(obra, settings, persona=persona)
        return {"resumo": resumo, "fonte": "llm", "id_obra_geoobras": id_obra}
    except Exception as exc:
        logger.warning("Falha na chamada LLM (obra %s): %s — usando fallback", id_obra, exc)
        return _build_fallback_cidadao(obra) if persona == "cidadao" else _build_fallback(obra)
