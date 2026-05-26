"""
api/main.py
Aplicação FastAPI do GeoObras.
Expõe os endpoints REST /api/v1/*.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

import src.logging_config as _lc

_lc.setup_logging()

from src.api.schemas import (
    EstatisticasResponse,
    InsightResponse,
    ObraDetalhe,
    ObrasListResponse,
    ObraListItem,
    RefreshResponse,
)
from src.domain.enums import Persona
from src.services.insights_service import get_obra_insight
from src.infra.db import SessionLocal, test_connection
from src.infra.repositories.analytics_repository import (
    fetch_obra_insights,
    query_estatisticas,
    query_obra_detalhe,
    query_obras_list,
    insert_etl_log,
)

logger = logging.getLogger("api.main")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    if test_connection():
        logger.info("GeoObras API iniciada – conexão com banco OK")
    else:
        logger.error("GeoObras API: FALHA ao conectar ao banco de dados!")
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="GeoObras API",
    description="API REST para monitoramento de obras públicas – Macaé/RJ",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restringir em produção
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Dependency – DB session
# ---------------------------------------------------------------------------


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health_check():
    return {"status": "ok", "banco": test_connection()}


@app.get(
    "/api/v1/obras",
    response_model=ObrasListResponse,
    summary="Lista obras públicas (com filtros opcionais)",
)
def list_obras(
    situacao: Optional[str] = Query(None, description="Filtra por status_obra"),
    municipio: Optional[str] = Query(None, description="Filtra por município"),
    apenas_com_coordenadas: bool = Query(False, description="Retorna só obras com lat/lon"),
    apenas_inconsistencias: bool = Query(False, description="Retorna só obras com flags de dados pendentes/suspeitos"),
    valor_minimo: Optional[float] = Query(None, description="Filtro por valor_total_contratado mínimo"),
    # Parâmetros placeholder – documentados mas sem implementação completa no Mês 1
    eficiencia_minima: Optional[float] = Query(
        None, description="[NÃO IMPLEMENTADO] Proxy: percentual_fisico mínimo. Reservado para Mês 2."
    ),
    risco: Optional[str] = Query(None, description="[NÃO IMPLEMENTADO] Filtro de risco. Reservado para Mês 2."),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    if eficiencia_minima is not None or risco is not None:
        logger.info("Parâmetros eficiencia_minima/risco recebidos mas não implementados no Mês 1 – ignorados.")

    rows, total = query_obras_list(
        db,
        situacao=situacao,
        municipio=municipio,
        apenas_com_coordenadas=apenas_com_coordenadas,
        apenas_inconsistencias=apenas_inconsistencias,
        valor_minimo=valor_minimo,
        page=page,
        page_size=page_size,
    )

    items = [ObraListItem(**r) for r in rows]
    return ObrasListResponse(total=total, page=page, page_size=page_size, items=items)


@app.get(
    "/api/v1/obras/{id}",
    response_model=ObraDetalhe,
    summary="Detalhe completo de uma obra",
)
def get_obra(id: UUID, db: Session = Depends(get_db)):
    obra = query_obra_detalhe(db, str(id))
    if not obra:
        raise HTTPException(status_code=404, detail=f"Obra {id} não encontrada.")

    # Mapear contratos aninhados
    contratos = obra.pop("contratos", [])
    convenios = obra.pop("convenios", [])
    detalhe = ObraDetalhe(**obra, contratos=contratos, convenios=convenios)
    return detalhe


@app.get(
    "/api/v1/obras/{id}/insights",
    response_model=InsightResponse,
    summary="Resumo analítico de uma obra gerado por LLM (com fallback determinístico)",
)
def get_obra_insights(
    id: UUID,
    persona: Persona = Query(Persona.AUDITOR, description="auditor (default) | cidadao"),
    db: Session = Depends(get_db),
):
    obra = fetch_obra_insights(db, str(id))
    if not obra:
        raise HTTPException(status_code=404, detail=f"Obra {id} não encontrada.")

    insight = get_obra_insight(str(id), obra=obra, persona=persona.value)

    flags: dict = {
        "possivel_atraso": obra.get("flag_possivel_atraso"),
        "data_fim_pendente": obra.get("flag_data_fim_pendente"),
        "populacao_suspeita": obra.get("flag_populacao_suspeita"),
        "empregos_suspeitos": obra.get("flag_empregos_suspeitos"),
        "dias_atraso": obra.get("dias_atraso"),
    }

    return InsightResponse(
        resumo=insight.get("resumo", ""),
        flags=flags,
        fonte=insight.get("fonte", "fallback"),
        gerado_em=datetime.now(),
    )


@app.get(
    "/api/v1/estatisticas",
    response_model=EstatisticasResponse,
    summary="Estatísticas agregadas das obras de Macaé",
)
def get_estatisticas(db: Session = Depends(get_db)):
    stats = query_estatisticas(db)
    return EstatisticasResponse(**stats)


@app.post(
    "/api/v1/refresh",
    status_code=202,
    response_model=RefreshResponse,
    summary="Registra intenção de refresh do ETL (execução via cron)",
)
def refresh_etl(db: Session = Depends(get_db)):
    """
    Mês 1: apenas registra a intenção em etl_execucao e retorna 202.
    Para execução síncrona em background, descomente o bloco abaixo
    e adicione `from fastapi import BackgroundTasks` + injeção no endpoint.

    # background_tasks.add_task(run_full_etl)
    """
    agora = datetime.now()
    try:
        insert_etl_log(
            db,
            fonte="completa",
            status="sucesso",
            detalhes={"origem": "api_refresh", "registrado_em": agora.isoformat()},
        )
        db.commit()
    except Exception as exc:
        logger.warning("Falha ao registrar intenção de refresh: %s", exc)

    return RefreshResponse(
        message="ETL será executado via cron. Intenção registrada.",
        registrado_em=agora,
    )
