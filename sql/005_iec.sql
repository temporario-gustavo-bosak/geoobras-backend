-- =============================================================================
-- 005_iec.sql
-- Adiciona coluna iec_score em analytics.metricas_obra.
-- Idempotente: pode rodar múltiplas vezes sem erro.
-- =============================================================================

ALTER TABLE analytics.metricas_obra
    ADD COLUMN IF NOT EXISTS iec_score NUMERIC(5,1);

CREATE INDEX IF NOT EXISTS idx_metricas_obra_iec
    ON analytics.metricas_obra (iec_score);
