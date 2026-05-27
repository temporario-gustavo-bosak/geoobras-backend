-- =============================================================================
-- 004_financial_health.sql
-- Adiciona colunas de saúde financeira em analytics.metricas_obra.
-- Idempotente: pode rodar múltiplas vezes sem erro.
-- =============================================================================

ALTER TABLE analytics.metricas_obra
    ADD COLUMN IF NOT EXISTS pct_aditivo                  NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS flag_alerta_aditivo          TEXT
        CHECK (flag_alerta_aditivo IN ('verde', 'amarelo', 'vermelho')),
    ADD COLUMN IF NOT EXISTS burn_rate_mensal_pct         NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS meses_para_exaustao          NUMERIC(6, 1),
    ADD COLUMN IF NOT EXISTS pct_fisico_estimado_exaustao NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS flag_risco_insolvencia       BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_metricas_obra_flag_alerta_aditivo
    ON analytics.metricas_obra (flag_alerta_aditivo);
