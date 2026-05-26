-- =============================================================================
-- 003_recorrencia.sql
-- Adiciona colunas de contagem a analytics.recorrencia_territorial.
-- Idempotente: pode rodar múltiplas vezes sem erro.
-- =============================================================================

ALTER TABLE analytics.recorrencia_territorial
    ADD COLUMN IF NOT EXISTS qtd_obras_proximas INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS qtd_bairro         INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS flag_recorrencia   BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS raio_metros        NUMERIC(8, 2),
    ADD COLUMN IF NOT EXISTS janela_anos        INTEGER;

CREATE INDEX IF NOT EXISTS idx_recorrencia_flag
    ON analytics.recorrencia_territorial (flag_recorrencia);
