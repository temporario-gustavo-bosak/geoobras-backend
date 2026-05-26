"""
tests/test_analytics_delay.py
Testes unitários para Task 06: probabilidade_atraso via z-score.

Estratégia: mesma da Task 05 — testamos funções puras sem banco.
O run_analytics() é coberto por um teste de integração com mocks.
"""

from __future__ import annotations

import math
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.services.analytics_service import (
    _calc_atraso_relativo,
    _calc_pop_stats,
    _calc_probabilidade_atraso,
    _logistic,
    _prazo_contratual_dias,
    run_analytics,
)


# ===========================================================================
# _prazo_contratual_dias
# ===========================================================================

class TestPrazoContratualDias:

    def test_calcula_prazo_correto(self):
        prazo = _prazo_contratual_dias(date(2023, 1, 1), date(2023, 7, 1))
        assert prazo == 181  # 31+28+31+30+31+30 = 181 dias

    def test_data_inicio_none_retorna_none(self):
        assert _prazo_contratual_dias(None, date(2023, 12, 31)) is None

    def test_data_fim_none_retorna_none(self):
        assert _prazo_contratual_dias(date(2023, 1, 1), None) is None

    def test_data_invertida_retorna_none(self):
        """fim antes do início → prazo negativo → None (dado inválido, não entra no z-score)."""
        assert _prazo_contratual_dias(date(2023, 12, 31), date(2023, 1, 1)) is None

    def test_mesmo_dia_retorna_none(self):
        """Prazo de 0 dias → inválido."""
        assert _prazo_contratual_dias(date(2023, 6, 1), date(2023, 6, 1)) is None


# ===========================================================================
# _calc_atraso_relativo
# ===========================================================================

class TestCalcAtrasoRelativo:

    def test_obra_no_prazo_retorna_zero(self):
        """
        dias_atraso = None significa obra no prazo, não dado ausente.
        Deve retornar 0.0 e participar da distribuição como "sem atraso".
        """
        result = _calc_atraso_relativo(dias_atraso=None, prazo_contratual_dias=200)
        assert result == 0.0

    def test_obra_no_prazo_dias_zero(self):
        """dias_atraso = 0 → mesmo que None, sem atraso."""
        result = _calc_atraso_relativo(dias_atraso=0, prazo_contratual_dias=200)
        assert result == 0.0

    def test_obra_atrasada(self):
        """30 dias de atraso num prazo de 100 dias → 0.30."""
        result = _calc_atraso_relativo(dias_atraso=30, prazo_contratual_dias=100)
        assert result == pytest.approx(0.30)

    def test_atraso_maior_que_prazo(self):
        """
        Obra atrasada por mais do que a duração planejada — situação real comum.
        atraso_relativo > 1.0 é válido e será um z-score alto.
        """
        result = _calc_atraso_relativo(dias_atraso=365, prazo_contratual_dias=200)
        assert result == pytest.approx(1.825)

    def test_prazo_none_retorna_none(self):
        """Sem prazo válido → obra excluída da distribuição."""
        result = _calc_atraso_relativo(dias_atraso=30, prazo_contratual_dias=None)
        assert result is None


# ===========================================================================
# _calc_pop_stats
# ===========================================================================

class TestCalcPopStats:

    def test_calcula_media_e_std_corretamente(self):
        """
        Distribuição simples: [0.0, 0.5, 1.0]
        média = 0.5
        variância = ((0-0.5)² + (0.5-0.5)² + (1-0.5)²) / 3 = 0.5/3 ≈ 0.1667
        std = sqrt(0.1667) ≈ 0.4082
        """
        result = _calc_pop_stats([0.0, 0.5, 1.0])
        assert result is not None
        mean, std = result
        assert mean == pytest.approx(0.5)
        assert std == pytest.approx(math.sqrt((0.25 + 0.0 + 0.25) / 3))

    def test_amostra_insuficiente_retorna_none(self):
        """Menos de 3 obras → z-score sem sentido estatístico → None."""
        assert _calc_pop_stats([0.0, 0.5]) is None
        assert _calc_pop_stats([0.0]) is None
        assert _calc_pop_stats([]) is None

    def test_std_zero_retorna_none(self):
        """
        Todas as obras com o mesmo atraso relativo → std = 0 → z-score indefinido.
        Não deve dividir por zero.
        """
        result = _calc_pop_stats([0.3, 0.3, 0.3, 0.3])
        assert result is None

    def test_exatamente_tres_obras(self):
        """Três obras é o mínimo aceito."""
        result = _calc_pop_stats([0.0, 0.5, 1.5])
        assert result is not None


# ===========================================================================
# _logistic
# ===========================================================================

class TestLogistic:

    def test_z_zero_retorna_meio(self):
        """z = 0 → obra na média da população → prob = 0.5."""
        assert _logistic(0.0) == pytest.approx(0.5)

    def test_z_positivo_acima_de_meio(self):
        """z > 0 → obra mais atrasada que a média → prob > 0.5."""
        assert _logistic(2.0) > 0.5
        assert _logistic(2.0) == pytest.approx(1 / (1 + math.exp(-2.0)))

    def test_z_negativo_abaixo_de_meio(self):
        """z < 0 → obra menos atrasada que a média → prob < 0.5."""
        assert _logistic(-2.0) < 0.5

    def test_z_extremamente_positivo_nao_explode(self):
        """z >> 0 → exp overflow → deve retornar 1.0, sem exceção."""
        result = _logistic(1000.0)
        assert result == pytest.approx(1.0)

    def test_z_extremamente_negativo_nao_explode(self):
        """z << 0 → exp overflow → deve retornar 0.0, sem exceção."""
        result = _logistic(-1000.0)
        assert result == pytest.approx(0.0)

    def test_output_sempre_entre_zero_e_um(self):
        """Invariante: logística ∈ (0, 1) para qualquer z finito."""
        for z in [-100, -2, -0.5, 0, 0.5, 2, 100]:
            result = _logistic(float(z))
            assert 0.0 <= result <= 1.0, f"logistica({z}) = {result} fora do intervalo"


# ===========================================================================
# _calc_probabilidade_atraso
# ===========================================================================

class TestCalcProbabilidadeAtraso:

    def test_obra_muito_acima_da_media(self):
        """Atraso relativo 2 std acima da média → probabilidade alta (> 0.85)."""
        mean, std = 0.2, 0.3
        atraso_rel = mean + 2 * std  # exatamente z=2
        result = _calc_probabilidade_atraso(atraso_rel, mean, std)
        assert result is not None
        assert result > 0.85

    def test_obra_na_media_retorna_meio(self):
        """Obra com atraso relativo igual à média → z=0 → prob ≈ 0.5."""
        mean, std = 0.3, 0.1
        result = _calc_probabilidade_atraso(mean, mean, std)
        assert result == pytest.approx(0.5, abs=0.001)

    def test_atraso_relativo_none_retorna_none(self):
        """Obra sem prazo contratual → atraso_relativo=None → prob=None."""
        assert _calc_probabilidade_atraso(None, 0.2, 0.1) is None

    def test_mean_none_retorna_none(self):
        """Amostra insuficiente → mean=None → prob=None."""
        assert _calc_probabilidade_atraso(0.5, None, 0.1) is None

    def test_std_none_retorna_none(self):
        """std=None (std≈0 ou amostra pequena) → prob=None."""
        assert _calc_probabilidade_atraso(0.5, 0.2, None) is None

    def test_todos_none_retorna_none(self):
        assert _calc_probabilidade_atraso(None, None, None) is None


# ===========================================================================
# run_analytics — integração com mocks (Task 06 foco: dois passes)
# ===========================================================================

class TestRunAnalyticsZScore:
    """
    Verifica que run_analytics:
    1. Faz o Pass 1 (calcula atraso_relativo e pop stats).
    2. Passa probabilidade_atraso ao upsert_metrica.
    """

    def _make_obra(self, id_: str, dias_atraso_esperado: int, prazo: int) -> dict:
        """
        Cria uma obra fake cujos campos produzem o dias_atraso desejado.
        Para simplificar: usa data_fim_real = data_fim_prevista + dias_atraso.
        """
        from datetime import timedelta
        inicio = date(2022, 1, 1)
        fim_previsto = inicio + timedelta(days=prazo)
        fim_real = fim_previsto + timedelta(days=dias_atraso_esperado)
        return {
            "id_obra_geoobras": id_,
            "valor_total_contratado": 1_000_000.0,
            "valor_pago_acumulado": 500_000.0,
            "percentual_fisico": 50.0,
            "data_inicio": inicio,
            "data_fim_prevista": fim_previsto,
            "data_fim_real": fim_real,
            "status_obra": "concluida",
        }

    def test_probabilidade_atraso_presente_no_upsert(self):
        """
        Três obras com prazos e atrasos distintos → z-score calculável.
        upsert_metrica deve ser chamado com probabilidade_atraso não-None.
        """
        obras_fake = [
            self._make_obra("obra-01", dias_atraso_esperado=0,   prazo=100),
            self._make_obra("obra-02", dias_atraso_esperado=30,  prazo=100),
            self._make_obra("obra-03", dias_atraso_esperado=200, prazo=100),  # outlier
        ]

        captured_metricas: list[dict] = []

        def fake_upsert(session, m):
            captured_metricas.append(dict(m))

        with (
            patch(
                "src.services.analytics_service.fetch_obras_para_analytics",
                return_value=obras_fake,
            ),
            patch("src.services.analytics_service.upsert_metrica", side_effect=fake_upsert),
            patch("src.services.analytics_service.upsert_recorrencia_territorial"),
            patch("src.services.analytics_service.get_session") as mock_ctx,
        ):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            run_analytics()

        assert len(captured_metricas) == 3

        probs = [m["probabilidade_atraso"] for m in captured_metricas]
        assert all(p is not None for p in probs), (
            f"Esperava probabilidade_atraso não-None para as 3 obras, got: {probs}"
        )

        # A obra com atraso 0 deve ter menor probabilidade que a obra outlier (200 dias)
        prob_sem_atraso  = captured_metricas[0]["probabilidade_atraso"]
        prob_outlier     = captured_metricas[2]["probabilidade_atraso"]
        assert prob_sem_atraso < prob_outlier, (
            f"Esperava prob_outlier > prob_sem_atraso, "
            f"got {prob_outlier} vs {prob_sem_atraso}"
        )

    def test_probabilidade_atraso_none_quando_amostra_insuficiente(self):
        """
        Apenas 2 obras com prazo válido → z-score impossível → probabilidade_atraso = None.
        """
        obras_fake = [
            self._make_obra("obra-01", dias_atraso_esperado=0,  prazo=100),
            self._make_obra("obra-02", dias_atraso_esperado=50, prazo=100),
        ]

        captured_metricas: list[dict] = []

        def fake_upsert(session, m):
            captured_metricas.append(dict(m))

        with (
            patch(
                "src.services.analytics_service.fetch_obras_para_analytics",
                return_value=obras_fake,
            ),
            patch("src.services.analytics_service.upsert_metrica", side_effect=fake_upsert),
            patch("src.services.analytics_service.upsert_recorrencia_territorial"),
            patch("src.services.analytics_service.get_session") as mock_ctx,
        ):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            run_analytics()

        for m in captured_metricas:
            assert m["probabilidade_atraso"] is None, (
                "Com apenas 2 obras, probabilidade_atraso deve ser None"
            )
