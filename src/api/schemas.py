"""
api/schemas.py
Schemas Pydantic v2 para request/response da API REST.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Obras – listagem
# ---------------------------------------------------------------------------


class ObraListItem(BaseModel):
    id: UUID = Field(..., description="Identificador único da obra no GeoObras")
    nome: str = Field(..., description="Nome oficial da obra pública", examples=["Pavimentação Av. Beira-Mar"])
    status: Optional[str] = Field(None, description="Situação atual da obra", examples=["em_execucao"])
    data_inicio: Optional[date] = None
    data_fim_prevista: Optional[date] = None
    data_fim_real: Optional[date] = None
    valor_total_contratado: Optional[float] = Field(
        None, description="Valor total contratado em Reais (R$)", examples=[1_500_000.0]
    )
    valor_pago_acumulado: Optional[float] = Field(
        None, description="Valor acumulado pago até a data de referência (R$)", examples=[900_000.0]
    )
    percentual_fisico: Optional[float] = Field(
        None, description="Percentual de execução física concluída (0–100)", examples=[42.5]
    )
    percentual_desembolso: Optional[float] = Field(
        None, description="Percentual desembolsado sobre o valor contratado (0–100)", examples=[60.0]
    )
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    flag_data_fim_pendente: bool = False
    flag_populacao_suspeita: bool = False
    flag_empregos_suspeitos: bool = False
    flag_possivel_atraso: Optional[bool] = Field(
        None, description="True quando a obra ultrapassou o prazo contratual previsto", examples=[True]
    )
    fonte_principal: Optional[str] = Field(
        None, description="Fonte primária dos dados: obrasgov | tce | mista | convenio", examples=["obrasgov"]
    )

    model_config = {"from_attributes": True}


class ObrasListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ObraListItem]


# ---------------------------------------------------------------------------
# Obras – detalhe
# ---------------------------------------------------------------------------


class ContratoItem(BaseModel):
    id_contrato_geoobras: int
    numero_contrato: Optional[str] = None
    objeto: Optional[str] = None
    valor_contratado: Optional[float] = None
    valor_total_atualizado: Optional[float] = None
    data_inicio_vigencia: Optional[date] = None
    data_fim_vigencia: Optional[date] = None

    model_config = {"from_attributes": True}


class ObraDetalhe(BaseModel):
    id_obra_geoobras: UUID = Field(..., description="Identificador único da obra no GeoObras")
    id_unico_obrasgov: Optional[str] = None
    id_obras_tce: Optional[int] = None
    nome: str = Field(..., description="Nome oficial da obra pública")
    descricao: Optional[str] = None
    municipio: Optional[str] = Field(None, description="Município onde a obra está localizada", examples=["Macaé"])
    uf: Optional[str] = Field(None, description="Unidade Federativa", examples=["RJ"])
    bairro: Optional[str] = None
    logradouro: Optional[str] = None
    status_obra: Optional[str] = Field(None, description="Situação atual da obra", examples=["em_execucao"])
    data_inicio: Optional[date] = None
    data_fim_prevista: Optional[date] = None
    data_fim_real: Optional[date] = None
    flag_data_fim_pendente: bool = False
    percentual_fisico: Optional[float] = Field(
        None, description="Percentual de execução física concluída (0–100)", examples=[65.0]
    )
    percentual_desembolso: Optional[float] = Field(
        None, description="Percentual desembolsado sobre o valor contratado (0–100)", examples=[72.0]
    )
    populacao_beneficiada: Optional[int] = None
    flag_populacao_suspeita: bool = False
    empregos_gerados: Optional[int] = None
    flag_empregos_suspeitos: bool = False
    valor_total_contratado: Optional[float] = Field(
        None, description="Valor total contratado em Reais (R$)", examples=[2_750_000.0]
    )
    valor_pago_acumulado: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geom: Optional[str] = Field(None, description="Geometria em formato WKT (Well-Known Text)")
    dias_atraso: Optional[int] = Field(
        None, description="Número de dias de atraso em relação ao prazo previsto", examples=[45]
    )
    flag_possivel_atraso: Optional[bool] = Field(
        None, description="True quando a obra ultrapassou o prazo contratual previsto"
    )
    iec_score: Optional[float] = Field(
        None,
        description=(
            "Índice de Eficiência Composta (0–100). "
            "100 = máxima eficiência. Calculado a partir de risco financeiro, atraso e aditivos."
        ),
        examples=[72.5],
    )
    metricas_calculado_em: Optional[datetime] = None
    fonte_principal: Optional[str] = None
    data_ultima_atualizacao: Optional[datetime] = None
    contratos: list[ContratoItem] = []
    convenios: list[Any] = []

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Estatísticas
# ---------------------------------------------------------------------------


class ObrasPorStatus(BaseModel):
    status_obra: Optional[str] = None
    qtd: int


class DistribuicaoAtraso(BaseModel):
    flag_possivel_atraso: Optional[bool] = None
    qtd: int


class EstatisticasResponse(BaseModel):
    obras_por_status: list[ObrasPorStatus]
    media_percentual_fisico: Optional[float] = Field(
        None,
        description="Média do percentual de execução física entre todas as obras com dado disponível (0–100)",
        examples=[47.3],
    )
    distribuicao_atraso: list[DistribuicaoAtraso]


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class RefreshResponse(BaseModel):
    message: str
    registrado_em: datetime


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


class InsightResponse(BaseModel):
    resumo: str = Field(
        ...,
        description="Texto analítico gerado por IA ou pelo fallback determinístico",
        examples=["A obra apresenta divergência físico-financeira de +20 p.p.: desembolso à frente da execução."],
    )
    flags: dict[str, Any] = Field(
        ...,
        description="Sinalizadores de alerta extraídos dos dados da obra",
        examples=[{"possivel_atraso": True, "data_fim_pendente": False, "dias_atraso": 45}],
    )
    iec_score: Optional[float] = Field(
        None,
        description="Índice de Eficiência Composta (0–100) da obra no momento da geração do insight.",
        examples=[72.5],
    )
    fonte: Literal["llm", "fallback"] = Field(
        ...,
        description="Indica se o resumo foi gerado pelo LLM ('llm') ou pelo fallback determinístico ('fallback')",
        examples=["llm"],
    )
    gerado_em: datetime = Field(..., description="Timestamp UTC de geração do insight")
