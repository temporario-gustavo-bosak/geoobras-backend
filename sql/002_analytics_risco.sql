-- =============================================================================
-- 002_analytics_risco.sql
-- Adiciona colunas de risco em analytics.metricas_obra.
-- Idempotente: pode rodar múltiplas vezes sem erro.
-- =============================================================================

ALTER TABLE analytics.metricas_obra
    ADD COLUMN IF NOT EXISTS divergencia_fisico_financeira NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS risco_sobrecusto              NUMERIC(5, 4),
    ADD COLUMN IF NOT EXISTS probabilidade_atraso          NUMERIC(5, 4),
    ADD COLUMN IF NOT EXISTS classe_alerta                 TEXT
        CHECK (classe_alerta IN ('verde', 'amarelo', 'vermelho')),
    ADD COLUMN IF NOT EXISTS metodo_score                  TEXT;

CREATE INDEX IF NOT EXISTS idx_metricas_obra_classe_alerta
    ON analytics.metricas_obra (classe_alerta);
