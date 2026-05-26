"""
tests/test_analytics_risk.py
Testes unitários para Task 05: cálculo de risco_sobrecusto.

Estratégia:
  - Testamos as funções PURAS diretamente (sem banco, sem HTTP).
    Funções puras = entram tipos primitivos, saem primitivos. Zero I/O.
  - Para run_analytics(), mockamos o Session para verificar que o
    upsert_metrica recebe as chaves corretas.

Por que testar função "privada" (_calc_risco_sobrecusto)?
  Porque ela contém a lógica de negócio crítica (normalização, override de estouro).
  Esconder um bug aqui atrás de "só teste a interface pública" seria imprudente
  numa ferramenta de auditoria. Importamos direto do módulo.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.services.analytics_service import (
    _calc_dias_atraso,
    _calc_pct_desembolso,
    _calc_risco_sobrecusto,
    run_analytics,
)


# =============================================================================
# _calc_risco_sobrecusto
# =============================================================================

class TestCalcRiscoSobrecusto:

    # ------------------------------------------------------------------
    # Happy paths
    # ------------------------------------------------------------------

    def test_alta_divergencia_retorna_vermelho(self):
        """
        Caso da persona do auditor: 80% do orçamento pago, 30% construído.
        Esperado: divergência = 50 pp, risco = 0.5, classe = 'vermelho'.
        """
        divergencia, risco, classe = _calc_risco_sobrecusto(
            pct_desembolso=80.0,
            pct_fisico=30.0,
            valor_pago=800_000.0,
            valor_contratado=1_000_000.0,
        )

        assert divergencia == 50.0
        assert risco == 0.5
        assert classe == "vermelho"

    def test_dentro_tolerancia_retorna_verde(self):
        """Pago 51%, físico 50% → divergência 1 pp → verde."""
        divergencia, risco, classe = _calc_risco_sobrecusto(
            pct_desembolso=51.0,
            pct_fisico=50.0,
            valor_pago=510_000.0,
            valor_contratado=1_000_000.0,
        )

        assert divergencia == 1.0
        assert risco == pytest.approx(0.01)
        assert classe == "verde"

    def test_divergencia_limiar_amarelo(self):
        """Exatamente 10 pp de divergência → amarelo."""
        divergencia, risco, classe = _calc_risco_sobrecusto(
            pct_desembolso=60.0,
            pct_fisico=50.0,
            valor_pago=None,
            valor_contratado=None,
        )

        assert divergencia == 10.0
        assert classe == "amarelo"

    def test_fisico_maior_que_desembolso_risco_zero(self):
        """
        Construiu mais do que pagou: divergência negativa.
        Para risco_sobrecusto (custo), o risco é 0 — não é desvio financeiro.
        Mas o |divergência| = 20 → 'amarelo' (sinal de situação incomum).
        """
        divergencia, risco, classe = _calc_risco_sobrecusto(
            pct_desembolso=30.0,
            pct_fisico=50.0,
            valor_pago=300_000.0,
            valor_contratado=1_000_000.0,
        )

        assert divergencia == -20.0
        assert risco == 0.0           # negativo → risco de sobrecusto = 0
        assert classe == "amarelo"    # |20| está no limiar 5..20

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_valor_pago_acima_do_contrato_forca_risco_maximo(self):
        """
        Pagou mais do que o contrato previa → estouro real → risco = 1.0.
        Mesmo que a divergência físico-financeira indique baixo desvio.
        """
        divergencia, risco, classe = _calc_risco_sobrecusto(
            pct_desembolso=52.0,
            pct_fisico=50.0,
            valor_pago=1_200_000.0,   # 20% acima do contrato
            valor_contratado=1_000_000.0,
        )

        assert risco == 1.0

    def test_pct_fisico_none_retorna_nones(self):
        """Sem percentual físico não há divergência calculável → todos None."""
        result = _calc_risco_sobrecusto(
            pct_desembolso=80.0,
            pct_fisico=None,
            valor_pago=800_000.0,
            valor_contratado=1_000_000.0,
        )
        assert result == (None, None, None)

    def test_pct_desembolso_none_retorna_nones(self):
        """Sem percentual de desembolso → todos None."""
        result = _calc_risco_sobrecusto(
            pct_desembolso=None,
            pct_fisico=50.0,
            valor_pago=None,
            valor_contratado=1_000_000.0,
        )
        assert result == (None, None, None)

    def test_valor_contratado_zero_nao_divide(self):
        """
        valor_contratado = 0 não deve gerar ZeroDivisionError.
        O guard `valor_contratado > 0` protege o override de estouro.
        """
        try:
            _calc_risco_sobrecusto(
                pct_desembolso=80.0,
                pct_fisico=30.0,
                valor_pago=500_000.0,
                valor_contratado=0.0,
            )
        except ZeroDivisionError:
            pytest.fail("_calc_risco_sobrecusto levantou ZeroDivisionError com contratado=0")

    def test_ambos_none_retorna_nones(self):
        """Caso degenerlado: sem nenhum dado."""
        result = _calc_risco_sobrecusto(None, None, None, None)
        assert result == (None, None, None)

    def test_risco_maximo_sem_ultrapassar_um(self):
        """
        Divergência de 100 pp (100% pago, 0% construído) → risco = 1.0, não > 1.
        """
        _, risco, _ = _calc_risco_sobrecusto(
            pct_desembolso=100.0,
            pct_fisico=0.0,
            valor_pago=None,
            valor_contratado=None,
        )
        assert risco == 1.0


# =============================================================================
# _calc_pct_desembolso (regressão — não deve ter quebrado)
# =============================================================================

class TestCalcPctDesembolso:
    def test_calculo_basico(self):
        assert _calc_pct_desembolso(1_000_000, 800_000) == 80.0

    def test_contratado_zero_retorna_none(self):
        assert _calc_pct_desembolso(0, 500_000) is None

    def test_contratado_none_retorna_none(self):
        assert _calc_pct_desembolso(None, 500_000) is None


# =============================================================================
# run_analytics — verifica que upsert recebe as chaves novas (Task 05)
# =============================================================================

class TestRunAnalyticsRiskFields:
    """
    Não testa banco — testa que run_analytics() popula os campos corretos
    no dicionário que passa para upsert_metrica.

    Por que isso importa?
    Se alguém renomear um campo no analytics_service mas esquecer de atualizar
    o repositório, o upsert silenciosamente ignora o dado novo. Este teste captura isso.
    """

    def test_upsert_recebe_campos_de_risco(self):
        """
        Dado uma obra com percentuais válidos, run_analytics deve chamar
        upsert_metrica com as chaves: divergencia_fisico_financeira,
        risco_sobrecusto, classe_alerta, metodo_score.
        """
        obra_fake = {
            "id_obra_geoobras": "00000000-0000-0000-0000-000000000001",
            "valor_total_contratado": 1_000_000.0,
            "valor_pago_acumulado": 800_000.0,
            "percentual_fisico": 30.0,        # 80% pago, 30% físico → divergência 50
            "data_inicio": date(2023, 1, 1),
            "data_fim_prevista": date(2023, 12, 31),
            "data_fim_real": None,
            "status_obra": "em_execucao",
        }

        with (
            patch(
                "src.services.analytics_service.fetch_obras_para_analytics",
                return_value=[obra_fake],
            ),
            patch("src.services.analytics_service.upsert_metrica") as mock_upsert,
            patch("src.services.analytics_service.upsert_recorrencia_territorial"),
            patch("src.services.analytics_service.get_session") as mock_ctx,
        ):
            # get_session() é um context manager (with get_session() as session)
            mock_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            run_analytics()

        assert mock_upsert.called, "upsert_metrica nunca foi chamado"

        _, kwargs = mock_upsert.call_args
        # upsert_metrica(session, metrica) → arg posicional
        metrica_enviada: dict = mock_upsert.call_args[0][1]

        assert "divergencia_fisico_financeira" in metrica_enviada
        assert "risco_sobrecusto" in metrica_enviada
        assert "classe_alerta" in metrica_enviada
        assert "metodo_score" in metrica_enviada

        assert metrica_enviada["divergencia_fisico_financeira"] == 50.0
        assert metrica_enviada["classe_alerta"] == "vermelho"
        assert metrica_enviada["risco_sobrecusto"] == 0.5

    def test_upsert_recebe_none_quando_sem_percentuais(self):
        """
        Obra sem percentual físico → campos de risco devem ser None.
        O upsert ainda é chamado (a obra existe, só sem métricas de risco).
        """
        obra_fake = {
            "id_obra_geoobras": "00000000-0000-0000-0000-000000000002",
            "valor_total_contratado": None,
            "valor_pago_acumulado": None,
            "percentual_fisico": None,
            "data_inicio": None,
            "data_fim_prevista": None,
            "data_fim_real": None,
            "status_obra": "planejada",
        }

        with (
            patch(
                "src.services.analytics_service.fetch_obras_para_analytics",
                return_value=[obra_fake],
            ),
            patch("src.services.analytics_service.upsert_metrica") as mock_upsert,
            patch("src.services.analytics_service.upsert_recorrencia_territorial"),
            patch("src.services.analytics_service.get_session") as mock_ctx,
        ):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            run_analytics()

        metrica_enviada = mock_upsert.call_args[0][1]

        assert metrica_enviada["divergencia_fisico_financeira"] is None
        assert metrica_enviada["risco_sobrecusto"] is None
        assert metrica_enviada["classe_alerta"] is None
        assert metrica_enviada["metodo_score"] is None
