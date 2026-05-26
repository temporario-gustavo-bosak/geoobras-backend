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

_OPENAPI_TAGS = [
    {
        "name": "Obras",
        "description": "Consulta e listagem de obras públicas monitoradas em Macaé/RJ.",
    },
    {
        "name": "Insights",
        "description": (
            "Resumos analíticos gerados por IA a partir dos indicadores de cada obra. "
            "Suporta os modos **auditor** (linguagem técnica) e **cidadão** (linguagem acessível)."
        ),
    },
    {
        "name": "Estatísticas",
        "description": "Métricas agregadas e distribuições sobre o portfólio de obras.",
    },
    {
        "name": "Operação",
        "description": "Endpoints de saúde, monitoramento e disparo manual do pipeline ETL.",
    },
]

app = FastAPI(
    title="GeoObras API",
    description=(
        "## Plataforma de Dados Abertos — Obras Públicas de Macaé/RJ\n\n"
        "A **GeoObras API** consolida dados do ObrasGov.br e do TCE-RJ para oferecer acesso "
        "transparente ao andamento de obras públicas no município de Macaé/RJ.\n\n"
        "### Para quem é esta API?\n"
        "- **Jornalistas e pesquisadores** — consulte, filtre e exporte dados com indicadores "
        "financeiros e físicos.\n"
        "- **Cidadãos** — acompanhe obras no seu bairro em linguagem simples via "
        "`/insights?persona=cidadao`.\n"
        "- **Gestores públicos e auditores** — acesse alertas de atraso, divergência "
        "físico-financeira e risco de sobrecusto.\n\n"
        "### Dados e Licença\n"
        "Dados provenientes de fontes públicas brasileiras, disponibilizados sob "
        "Creative Commons CC BY 4.0.\n\n"
        "### Pipeline ETL\n"
        "Atualizado diariamente via pipeline `raw → clean → analytics`. "
        "Use `POST /api/v1/refresh` para registrar uma solicitação de atualização manual."
    ),
    version="1.0.0",
    contact={
        "name": "GeoObras — Hackathon Duopen",
        "url": "https://github.com/duopen/geoobras-backend",
        "email": "geoobras@duopen.dev",
    },
    license_info={
        "name": "Creative Commons Attribution 4.0 International",
        "url": "https://creativecommons.org/licenses/by/4.0/",
    },
    openapi_tags=_OPENAPI_TAGS,
    swagger_ui_parameters={
        "docExpansion": "list",
        "defaultModelsExpandDepth": 1,
        "tryItOutEnabled": False,
    },
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


@app.get(
    "/health",
    tags=["Operação"],
    response_description="Status da API e conectividade com o banco de dados",
    responses={200: {"content": {"application/json": {"example": {"status": "ok", "banco": True}}}}},
)
def health_check():
    return {"status": "ok", "banco": test_connection()}


@app.get(
    "/api/v1/obras",
    response_model=ObrasListResponse,
    summary="Lista obras públicas (com filtros opcionais)",
    response_description="Lista paginada de obras com indicadores financeiros e físicos",
    tags=["Obras"],
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "total": 127,
                        "page": 1,
                        "page_size": 50,
                        "items": [
                            {
                                "id": "550e8400-e29b-41d4-a716-446655440000",
                                "nome": "Pavimentação Av. Beira-Mar",
                                "status": "em_execucao",
                                "data_inicio": "2022-03-01",
                                "data_fim_prevista": "2023-03-01",
                                "data_fim_real": None,
                                "valor_total_contratado": 1500000.0,
                                "valor_pago_acumulado": 900000.0,
                                "percentual_fisico": 42.5,
                                "percentual_desembolso": 60.0,
                                "latitude": -22.3765,
                                "longitude": -41.7869,
                                "flag_data_fim_pendente": False,
                                "flag_populacao_suspeita": False,
                                "flag_empregos_suspeitos": False,
                                "flag_possivel_atraso": True,
                                "fonte_principal": "obrasgov",
                            }
                        ],
                    }
                }
            }
        }
    },
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
    response_description="Todos os campos da obra, incluindo contratos vinculados e métricas analytics",
    tags=["Obras"],
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "id_obra_geoobras": "550e8400-e29b-41d4-a716-446655440000",
                        "nome": "Pavimentação Av. Beira-Mar",
                        "municipio": "Macaé",
                        "uf": "RJ",
                        "status_obra": "em_execucao",
                        "valor_total_contratado": 1500000.0,
                        "percentual_fisico": 42.5,
                        "percentual_desembolso": 60.0,
                        "dias_atraso": 45,
                        "flag_possivel_atraso": True,
                        "contratos": [],
                        "convenios": [],
                    }
                }
            }
        },
        404: {"description": "Obra não encontrada"},
    },
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
    response_description="Resumo em texto, sinalizadores de alerta e fonte de geração",
    tags=["Insights"],
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "resumo": (
                            "A obra apresenta divergência físico-financeira de +17,5 p.p.: "
                            "o desembolso (60%) está à frente da execução física (42,5%). "
                            "Há atraso de 45 dias em relação ao prazo contratual."
                        ),
                        "flags": {
                            "possivel_atraso": True,
                            "data_fim_pendente": False,
                            "populacao_suspeita": False,
                            "empregos_suspeitos": False,
                            "dias_atraso": 45,
                        },
                        "fonte": "llm",
                        "gerado_em": "2025-05-26T10:00:00",
                    }
                }
            }
        },
        404: {"description": "Obra não encontrada"},
    },
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
    response_description="Distribuição por status, média de execução física e distribuição de atrasos",
    tags=["Estatísticas"],
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "obras_por_status": [
                            {"status_obra": "em_execucao", "qtd": 58},
                            {"status_obra": "concluida", "qtd": 41},
                            {"status_obra": "paralisada", "qtd": 12},
                        ],
                        "media_percentual_fisico": 47.3,
                        "distribuicao_atraso": [
                            {"flag_possivel_atraso": True, "qtd": 34},
                            {"flag_possivel_atraso": False, "qtd": 77},
                        ],
                    }
                }
            }
        }
    },
)
def get_estatisticas(db: Session = Depends(get_db)):
    stats = query_estatisticas(db)
    return EstatisticasResponse(**stats)


@app.post(
    "/api/v1/refresh",
    status_code=202,
    response_model=RefreshResponse,
    summary="Registra intenção de refresh do ETL (execução via cron)",
    tags=["Operação"],
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
