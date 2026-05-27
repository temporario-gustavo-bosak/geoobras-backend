-- =============================================================================
-- GeoObras - Schema Completo e Consolidado
-- Arquivo: sql/000_schema_completo.sql
--
-- USE ESTE ARQUIVO para iniciar o banco do zero (Docker / DBeaver).
-- E IDEMPOTENTE: pode rodar múltiplas vezes sem erro.
-- Substitui: 001_create_schemas.sql + 002 + 003 + 004 + 005
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 0. SCHEMAS
-- ---------------------------------------------------------------------------
CREATE SCHEMA raw;
CREATE SCHEMA clean;
CREATE SCHEMA analytics;

-- ---------------------------------------------------------------------------
-- 1. CAMADA RAW - mirrors das APIs externas
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw.obrasgov_projetos (
    id_unico                TEXT PRIMARY KEY,
    nome                    TEXT,
    situacao                TEXT,
    data_inicial_prevista   TEXT,
    data_inicial_efetiva    TEXT,
    data_final_prevista     TEXT,
    data_final_efetiva      TEXT,
    descricao               TEXT,
    endereco                TEXT,
    municipio               TEXT,
    uf                      TEXT,
    populacao_beneficiada   NUMERIC,
    qtd_empregos_gerados    NUMERIC,
    tipos                   JSONB,
    sub_tipos               JSONB,
    fontes_de_recurso       JSONB,
    payload_json            JSONB,
    ingestado_em            TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.obrasgov_execucao_fisica (
    id              BIGSERIAL PRIMARY KEY,
    id_unico        TEXT NOT NULL,
    data_situacao   TEXT,
    percentual      NUMERIC(6,2),
    situacao        TEXT,
    observacoes     TEXT,
    ingestado_em    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_raw_ef_id_unico
    ON raw.obrasgov_execucao_fisica (id_unico);

CREATE TABLE IF NOT EXISTS raw.obrasgov_execucao_financeira (
    id                          BIGSERIAL PRIMARY KEY,
    id_projeto_investimento     TEXT NOT NULL,
    valor_empenho               NUMERIC(18,2),
    ingestado_em                TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_raw_efin_id_projeto
    ON raw.obrasgov_execucao_financeira (id_projeto_investimento);

CREATE TABLE IF NOT EXISTS raw.obrasgov_contratos (
    id                          BIGSERIAL PRIMARY KEY,
    id_projeto_investimento     TEXT NOT NULL,
    numero_contrato             TEXT,
    valor_global                NUMERIC(18,2),
    valor_acumulado             NUMERIC(18,2),
    vigencia_fim                TEXT,
    situacao                    TEXT,
    payload_json                JSONB,
    ingestado_em                TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_raw_contratos_id_projeto
    ON raw.obrasgov_contratos (id_projeto_investimento);

CREATE TABLE IF NOT EXISTS raw.obrasgov_geometria (
    id_unico        TEXT PRIMARY KEY,
    geometria_raw   TEXT,
    geometria_wkt   TEXT,
    ingestado_em    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.tcerj_obras (
    id                  BIGSERIAL PRIMARY KEY,
    codigo_obra         TEXT,
    nome                TEXT,
    municipio           TEXT,
    uf                  TEXT,
    tipo_obra           TEXT,
    situacao            TEXT,
    valor_contratado    NUMERIC(18,2),
    valor_aditivos      NUMERIC(18,2),
    valor_pago          NUMERIC(18,2),
    data_inicio         TEXT,
    data_fim_prevista   TEXT,
    data_fim_real       TEXT,
    percentual_concluido NUMERIC(6,2),
    ente                TEXT,
    ingestado_em        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.tcerj_obras_paralisadas (
    id                      BIGSERIAL PRIMARY KEY,
    nome                    TEXT,
    valor_total_contrato    NUMERIC(18,2),
    valor_pago_obra         NUMERIC(18,2),
    motivo_paralisacao      TEXT,
    ente                    TEXT,
    ano_referencia          INTEGER,
    ingestado_em            TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.macae_convenios (
    id                  BIGSERIAL PRIMARY KEY,
    numero_convenio     TEXT,
    objeto              TEXT,
    valor_repasse       NUMERIC(18,2),
    valor_contrapartida NUMERIC(18,2),
    data_inicio         TEXT,
    data_fim            TEXT,
    situacao            TEXT,
    ingestado_em        TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 2. CAMADA CLEAN - modelo normalizado, filtrado para o município alvo
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS clean.obras (
    id_obra_geoobras        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_unico_obrasgov       TEXT UNIQUE,
    id_obras_tce            TEXT,
    nome                    TEXT NOT NULL,
    descricao               TEXT,
    municipio               TEXT,
    uf                      TEXT,
    codigo_municipio        TEXT,
    bairro                  TEXT,
    logradouro              TEXT,
    status_obra             TEXT,
    data_inicio             DATE,
    data_fim_prevista       DATE,
    data_fim_real           DATE,
    flag_data_fim_pendente  BOOLEAN DEFAULT FALSE,
    percentual_fisico       NUMERIC(6,2),
    populacao_beneficiada   NUMERIC,
    flag_populacao_suspeita BOOLEAN DEFAULT FALSE,
    empregos_gerados        NUMERIC,
    flag_empregos_suspeitos BOOLEAN DEFAULT FALSE,
    valor_total_contratado  NUMERIC(18,2),
    valor_pago_acumulado    NUMERIC(18,2),
    valor_previsto_original NUMERIC(18,2),   -- soma de fontesDeRecurso[].valorInvestimentoPrevisto
    latitude                NUMERIC(10,7),
    longitude               NUMERIC(10,7),
    geom                    TEXT,            -- WKT (substituto do PostGIS)
    fonte_principal         TEXT,
    flag_inconsistencia_geral BOOLEAN DEFAULT FALSE,
    atualizado_em           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clean_obras_status
    ON clean.obras (status_obra);
CREATE INDEX IF NOT EXISTS idx_clean_obras_municipio
    ON clean.obras (municipio);
CREATE INDEX IF NOT EXISTS idx_clean_obras_coords
    ON clean.obras (latitude, longitude)
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL;

CREATE TABLE IF NOT EXISTS clean.contratos (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_obra_geoobras UUID NOT NULL REFERENCES clean.obras (id_obra_geoobras) ON DELETE CASCADE,
    numero_contrato TEXT,
    valor_global    NUMERIC(18,2),
    valor_acumulado NUMERIC(18,2),
    vigencia_fim    DATE,
    situacao        TEXT,
    ingestado_em    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_clean_contratos_obra
    ON clean.contratos (id_obra_geoobras);

CREATE TABLE IF NOT EXISTS clean.obras_contratos (
    id_obra     UUID NOT NULL REFERENCES clean.obras (id_obra_geoobras) ON DELETE CASCADE,
    id_contrato UUID NOT NULL REFERENCES clean.contratos (id) ON DELETE CASCADE,
    PRIMARY KEY (id_obra, id_contrato)
);

CREATE TABLE IF NOT EXISTS clean.convenios (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_obra_geoobras    UUID REFERENCES clean.obras (id_obra_geoobras) ON DELETE SET NULL,
    numero_convenio     TEXT,
    objeto              TEXT,
    valor_repasse       NUMERIC(18,2),
    valor_contrapartida NUMERIC(18,2),
    data_inicio         DATE,
    data_fim            DATE,
    situacao            TEXT,
    ingestado_em        TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 3. CAMADA ANALYTICS - métricas derivadas
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS analytics.metricas_obra (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_obra_geoobras                UUID UNIQUE NOT NULL
                                        REFERENCES clean.obras (id_obra_geoobras) ON DELETE CASCADE,

    -- financeiro básico
    valor_total_contratado          NUMERIC(18,2),
    valor_pago_acumulado            NUMERIC(18,2),
    percentual_desembolso           NUMERIC(6,2),
    percentual_fisico               NUMERIC(6,2),

    -- datas
    data_inicio                     DATE,
    data_fim_prevista               DATE,
    data_fim_real                   DATE,

    -- atraso (migration 001)
    dias_atraso                     INTEGER,
    flag_possivel_atraso            BOOLEAN DEFAULT FALSE,

    -- risco financeiro (migration 002)
    divergencia_fisico_financeira   NUMERIC(6,2),
    risco_sobrecusto                NUMERIC(5,4),
    probabilidade_atraso            NUMERIC(5,4),
    classe_alerta                   TEXT CHECK (classe_alerta IN ('verde', 'amarelo', 'vermelho')),
    metodo_score                    TEXT,

    -- aditivos e insolvência (migration 004)
    pct_aditivo                     NUMERIC(6,2),
    flag_alerta_aditivo             TEXT CHECK (flag_alerta_aditivo IN ('verde', 'amarelo', 'vermelho')),
    burn_rate_mensal_pct            NUMERIC(6,2),
    meses_para_exaustao             NUMERIC(6,1),
    pct_fisico_estimado_exaustao    NUMERIC(5,2),
    flag_risco_insolvencia          BOOLEAN DEFAULT FALSE,

    -- IEC - Índice de Eficiência Composta (migration 005)
    iec_score                       NUMERIC(5,1),

    calculado_em                    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_metricas_obra_alerta
    ON analytics.metricas_obra (classe_alerta);
CREATE INDEX IF NOT EXISTS idx_metricas_obra_iec
    ON analytics.metricas_obra (iec_score);
CREATE INDEX IF NOT EXISTS idx_metricas_obra_insolvencia
    ON analytics.metricas_obra (flag_risco_insolvencia)
    WHERE flag_risco_insolvencia = TRUE;

CREATE TABLE IF NOT EXISTS analytics.recorrencia_territorial (
    id_obra_geoobras    UUID PRIMARY KEY
                            REFERENCES clean.obras (id_obra_geoobras) ON DELETE CASCADE,
    bairro              TEXT,
    geom                TEXT,
    recorrencia_count   INTEGER DEFAULT 0,
    calculado_em        TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 4. AUDIT LOG DO ETL (schema public)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.etl_execucao (
    id              BIGSERIAL PRIMARY KEY,
    iniciado_em     TIMESTAMPTZ DEFAULT NOW(),
    finalizado_em   TIMESTAMPTZ,
    status          TEXT,
    fonte           TEXT,
    registros_raw   INTEGER,
    registros_clean INTEGER,
    erros           TEXT,
    duracao_s       NUMERIC(10,2)
);

-- ---------------------------------------------------------------------------
-- 5. EXTENSÕES NECESSÁRIAS
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- FIM
-- Após rodar este script no DBeaver, execute:
--   python -m src.etl.run_etl --skip-raw   (se raw já foi populado)
--   python -m src.etl.run_etl              (ETL completo do zero)
-- ---------------------------------------------------------------------------
