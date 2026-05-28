# GeoObras

**Transparência ativa sobre obras públicas no Brasil — dados, análise e IA a serviço do cidadão.**

GeoObras é um backend open-source que consolida, normaliza e analisa obras públicas de múltiplas fontes governamentais, expondo uma API REST com métricas de risco, alertas de irregularidade e resumos gerados por IA. O projeto nasceu como trabalho acadêmico na FIAP e evoluiu para um sistema de produção com 20.000+ obras processadas no estado do Rio de Janeiro.

---

## Por que o GeoObras existe

Obras públicas são a forma mais concreta de alocação do orçamento público — e também uma das mais opacas. Os dados existem, mas estão espalhados em portais diferentes, em formatos incompatíveis, sem cruzamento entre fontes e sem análise de risco automática. O resultado: cidadãos, vereadores e jornalistas precisam garimpar manualmente informações que deveriam estar a um clique de distância.

O GeoObras resolve isso:

- **Coleta** dados do ObrasGov.br (federal) e TCE-RJ (estadual), mais convênios municipais de Macaé em CSV
- **Cruza** as fontes usando similaridade de nome (SequenceMatcher ≥ 60%) para identificar a mesma obra em dois sistemas diferentes
- **Calcula** métricas de risco: atraso, sobrecusto, insolvência orçamentária, recorrência territorial e conformidade legal de aditivos
- **Pontua** cada obra com o IEC — Índice de Eficiência Composta (0–100)
- **Explica** a situação de cada obra em linguagem de auditor ou em linguagem de cidadão, via LLM

---

## Cobertura de dados

| Dimensão | Cobertura |
|---|---|
| Estado principal | Rio de Janeiro (RJ) |
| Município de foco | Macaé/RJ (dados mais completos) |
| Fontes cruzadas | ObrasGov.br (federal) + TCE-RJ (estadual) + convênios municipais (CSV) |
| Obras processadas | **~20.012 obras** (snapshot de 2026-05-28) |
| Contratos vinculados | Múltiplos por obra (relação N:M) |
| Período coberto | Obras com início desde 2010; paralisadas de 2020 em diante |
| Geometria | WKT (lat/lon) para obras com coordenada no ObrasGov |

### Status das obras monitoradas

| Status | Descrição |
|---|---|
| `planejada` | Em planejamento, cadastrada ou em processo licitatório |
| `em_execucao` | Contratada, em andamento |
| `concluida` | Finalizada com data de encerramento |
| `paralisada` | Paralisada (registrada no TCE-RJ ou ObrasGov) |
| `inacabada` | Abandonada sem conclusão formal (TCE-RJ) |
| `cancelada` | Cancelada oficialmente |
| `desconhecida` | Status não mapeado na fonte |

---

## Inteligência analítica

O coração do GeoObras é o motor de analytics que calcula, para cada obra, um conjunto de métricas derivadas a partir dos dados brutos. Tudo isso roda automaticamente no pipeline ETL.

### IEC — Índice de Eficiência Composta (0–100)

O IEC é um índice composto que sintetiza o estado de risco de uma obra em um único número. Quanto menor, pior.

```
IEC = max(0, 100 − penalidade_total × 100/max_possível)
```

**Componentes da penalidade:**

| Componente | Peso máximo | Fonte do dado |
|---|---|---|
| Risco de sobrecusto (z-score populacional) | 35 pts | Análise estatística do portfólio |
| Probabilidade de atraso (z-score logístico) | 30 pts | Atraso relativo ao prazo contratual |
| Conformidade de aditivos (teto legal 25%) | 25 pts | Lei 14.133/2021 art. 125 |
| Risco de insolvência orçamentária | 10 pts | Projeção de burn-rate mensal |
| Recorrência territorial (obras no mesmo ponto) | 10 pts | Haversine < 50 m, janela 10 anos |

**Interpretação:**

| Faixa | Significado | Cor sugerida |
|---|---|---|
| 80–100 | Alta eficiência | Verde `#22c55e` |
| 60–79 | Eficiência moderada | Amarelo `#eab308` |
| 40–59 | Atenção | Laranja `#f97316` |
| 0–39 | Alto risco / Crítica | Vermelho `#ef4444` |
| `null` | Dados insuficientes | Cinza `#9ca3af` |

### Probabilidade de atraso (z-score logístico)

Compara o atraso relativo de cada obra (dias_atraso / prazo_contratual) com a distribuição populacional de todas as obras do portfólio. O z-score é mapeado via função logística para produzir uma probabilidade [0, 1]. Requer mínimo de 3 obras com prazo válido para ser calculado.

### Recorrência territorial

Detecta sobreposição espacial de obras: para cada obra georreferenciada, conta quantas outras obras estão dentro de um raio de **50 metros** nos últimos **10 anos**. Alta recorrência no mesmo ponto pode indicar manutenção recorrente sem solução definitiva, superfaturamento ou planejamento deficiente.

### Risco de sobrecusto

Z-score do valor contratado em relação à média do portfólio, normalizado para [0, 1] via sigmoid. Identifica obras com contratos atipicamente caros para o tipo e porte.

### Divergência físico-financeira

Diferença em pontos percentuais entre o desembolso financeiro acumulado e a execução física declarada. Um valor positivo alto (ex: +30 p.p.) indica que o contratante recebeu proporcionalmente mais do que entregou.

### Projeção de insolvência orçamentária

Com base no percentual desembolsado e no tempo decorrido desde o início da obra, calcula o burn-rate mensal médio e projeta em quantos meses o orçamento se esgotará. Se a obra estiver projetada para terminar o orçamento antes de atingir 100% de execução física, o flag `flag_risco_insolvencia` é ativado.

### Conformidade de aditivos contratuais

Calcula o percentual de aumento do valor do contrato em relação ao valor original, e sinaliza:
- **Verde**: < 20% (dentro do limite legal)
- **Amarelo**: 20–25% (próximo do teto)
- **Vermelho**: > 25% (acima do teto legal — Lei 14.133/2021, art. 125)

---

## Insights por IA

O endpoint `/api/v1/obras/{id}/insights` gera um resumo analítico usando LLM (Claude Opus via Anthropic API). O sistema suporta dois perfis de audiência:

**`auditor`** — linguagem técnica, indicadores numéricos, referências à lei:
> "A obra apresenta divergência físico-financeira de +17,5 p.p. O percentual de aditivos (28,3%) supera o teto legal de 25% previsto no art. 125 da Lei 14.133/2021. Risco de insolvência detectado: orçamento estimado para esgotar em 4,2 meses com ~73% de execução física."

**`cidadao`** — linguagem simples, impacto concreto para o morador:
> "Até agora, 42% da obra foi concluída fisicamente. O município já pagou proporcionalmente mais do que o progresso da obra justifica. O contrato aumentou 28% além do valor original combinado."

O sistema nunca retorna erro por causa do LLM: se a chave não estiver configurada ou a chamada falhar, um resumo determinístico de qualidade equivalente é gerado automaticamente (`"fonte": "fallback"`).

---

## Arquitetura técnica

### Stack

| Camada | Tecnologia |
|---|---|
| API | FastAPI + Uvicorn |
| Banco de dados | PostgreSQL (compatível com PostGIS) |
| ORM / queries | SQLAlchemy Core — SQL puro via `text()`, sem ORM pesado |
| Schemas / validação | Pydantic v2 |
| HTTP clients | httpx com retry automático |
| Geometria | Shapely (WKT/WKB → lat/lon, sem PostGIS obrigatório) |
| ETL / CSV | pandas |
| LLM | Anthropic API (Claude Opus) — opcional, com fallback determinístico |
| Config | pydantic-settings + `.env` |
| Lint/format | Ruff |
| Type check | mypy |
| Testes | pytest + unittest.mock |
| CI | GitHub Actions |
| Container | Docker + docker-compose |

### Schema de banco de dados (três camadas)

```
raw.*          → Espelhos dos dados brutos das APIs (JSONB preservado para reprocessamento)
clean.*        → Modelo unificado e normalizado, filtrado para Macaé
analytics.*    → Métricas derivadas, flags e IEC score calculados
etl_execucao   → Log de auditoria de cada execução do ETL
```

**Tabelas principais:**

```
raw.obrasgov_projetos          → projetos do ObrasGov.br (payload completo em JSONB)
raw.obrasgov_execucao_fisica   → histórico de execução física por obra
raw.obrasgov_execucao_financeira → empenhos financeiros
raw.obrasgov_contratos         → contratos vinculados
raw.obrasgov_geometria         → coordenadas WKT/WKB
raw.tcerj_obras                → obras do TCE-RJ
raw.tcerj_obras_paralisadas    → obras paralisadas por ano
raw.macae_convenios            → convênios municipais (CSV)
clean.obras                    → obra unificada (UUID PK id_obra_geoobras)
clean.contratos                → contratos normalizados
clean.obras_contratos          → relação N:M obras ↔ contratos
analytics.metricas_obra        → todos os indicadores calculados (IEC, flags, z-scores)
analytics.recorrencia_territorial → contagem de sobreposição espacial por obra
```

### ETL Pipeline

```
Fase 1 — RAW ingestion
  ├── ObrasGov API (todo o estado do RJ, paginado, com retry e throttle)
  ├── TCE-RJ API (obras e paralisadas, por ano)
  └── CSV convênios municipais (Macaé, encoding latin-1)

Fase 2 — CLEAN normalization
  ├── Filtro por município (Macaé) — busca case-insensitive + NFD em
  │   endereço, nome, município e descrição
  ├── Normalização de campos e datas (múltiplos formatos de entrada)
  ├── Deduplicação ObrasGov ↔ TCE-RJ (SequenceMatcher ≥ 0.60)
  └── Geração de flags de qualidade (população suspeita, empregos atípicos)

Fase 3 — Analytics (4 passes)
  ├── Pass 1: percentual_desembolso, dias_atraso, flag_possivel_atraso
  ├── Pass 2: probabilidade_atraso (z-score logístico populacional)
  ├── Pass 3: recorrência territorial (Haversine, raio 50 m, janela 10 anos)
  └── Pass 4: IEC score + upsert em analytics.*
```

### Decisões de design notáveis

- **Sem PostGIS obrigatório** — geometrias armazenadas como WKT `TEXT`. Shapely extrai lat/lon em Python puro; suporte a WKB hex como fallback. Pontos de substituição para PostGIS marcados com `[POSTGIS]` no DDL.
- **Sem ORM** — todo acesso ao banco usa SQLAlchemy Core com `text()`. Pydantic é usado apenas para schemas de entrada/saída da API.
- **ETL idempotente** — `ON CONFLICT (id_unico_obrasgov) DO UPDATE` garante reruns seguros sem duplicatas. Cada execução completa é registrada em `etl_execucao`.
- **LLM sempre degradável** — `get_obra_insight()` nunca lança exceção. Ausência de `LLM_API_KEY` ou falha de rede aciona fallback determinístico automaticamente.
- **Filtragem heurística de município** — a API do ObrasGov não filtra por município; o GeoObras baixa todo o estado do RJ e filtra Macaé na camada CLEAN por busca case-insensitive + normalização de acentos (NFD) em múltiplos campos.
- **Rate limiting respeitoso** — o ETL dorme 1,5 s entre projetos e 10 s a cada 5 projetos para não sobrecarregar as APIs públicas.

---

## API REST

Base URL: `http://localhost:8000`
Documentação interativa: `http://localhost:8000/docs`
Autenticação: **nenhuma** — todos os endpoints são públicos.

### Endpoints

| Método | Path | Descrição |
|---|---|---|
| `GET` | `/health` | Liveness check + status do banco |
| `GET` | `/api/v1/obras` | Lista paginada de obras com filtros |
| `GET` | `/api/v1/obras/{id}` | Detalhe completo de uma obra (UUID) |
| `GET` | `/api/v1/obras/{id}/insights` | Resumo analítico por IA ou fallback determinístico |
| `GET` | `/api/v1/estatisticas` | Agregados do portfólio para dashboard |
| `POST` | `/api/v1/refresh` | Registra intenção de re-execução do ETL |

### Filtros disponíveis em `GET /api/v1/obras`

| Parâmetro | Tipo | Exemplo |
|---|---|---|
| `situacao` | string | `?situacao=em_execucao` |
| `municipio` | string (ILIKE) | `?municipio=macae` |
| `apenas_com_coordenadas` | bool | `?apenas_com_coordenadas=true` |
| `apenas_inconsistencias` | bool | `?apenas_inconsistencias=true` |
| `valor_minimo` | float | `?valor_minimo=500000` |
| `page` | int ≥ 1 | `?page=2` |
| `page_size` | int 1–200 | `?page_size=100` |

### Exemplos rápidos

```bash
# Obras em execução acima de R$ 1M com algum flag de inconsistência
curl "http://localhost:8000/api/v1/obras?situacao=em_execucao&valor_minimo=1000000&apenas_inconsistencias=true"

# Obras com coordenada (prontas para mapa)
curl "http://localhost:8000/api/v1/obras?apenas_com_coordenadas=true&page_size=200"

# Insight para cidadão de uma obra específica
curl "http://localhost:8000/api/v1/obras/{uuid}/insights?persona=cidadao"

# Insight técnico para auditor
curl "http://localhost:8000/api/v1/obras/{uuid}/insights?persona=auditor"

# Estatísticas do portfólio
curl "http://localhost:8000/api/v1/estatisticas"
```

Para integração frontend completa — TypeScript types, Leaflet, IEC Score, exemplos de fetch prontos — consulte [FRONTEND_INTEGRATION_GUIDE.md](./FRONTEND_INTEGRATION_GUIDE.md).

---

## Início rápido

### Pré-requisitos

- Docker + Docker Compose
- Python 3.11+ (apenas se rodar sem Docker)

### Com Docker (recomendado)

```bash
# 1. Clone o repositório
git clone https://github.com/seu-usuario/geoobras-backend.git
cd geoobras-backend

# 2. Configure o ambiente
cp .env.example .env
# Edite .env — LLM_API_KEY é opcional (funciona sem ele, usa fallback)

# 3. Suba banco + API
docker compose up -d

# 4. Verifique
curl http://localhost:8000/health
# → {"status":"ok","banco":true}
```

### Restaurar dados de produção

O repositório inclui um dump completo com ~20.000 obras reais (Macaé/RJ + TCE-RJ):

```bash
# Formato custom (recomendado — mais rápido)
pg_restore -U geoobras -d geoobras -h localhost geoobras_producao.backup

# Ou formato SQL plano
psql -U geoobras -d geoobras -h localhost -f geoobras_producao.sql
```

### Sem Docker

```bash
# Ambiente virtual
python -m venv .venv
source .venv/Scripts/activate   # Windows bash
# .venv\Scripts\Activate.ps1   # PowerShell

pip install -r requirements.txt -r requirements-dev.txt

# Banco (necessário Postgres rodando localmente)
psql -U geoobras -d geoobras -f sql/000_schema_completo.sql

# API
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Executar o ETL

```bash
# Pipeline completo (RAW → CLEAN → Analytics)
python -m src.etl.run_etl

# Apenas uma fonte de dados
python -m src.etl.run_etl --fonte obrasgov
python -m src.etl.run_etl --fonte tcerj
python -m src.etl.run_etl --fonte convenios

# Apenas reprocessar CLEAN + Analytics (sem re-ingerir as APIs)
python -m src.etl.run_etl --skip-raw
```

> **Dev rápido:** Defina `OBRASGOV_MAX_PAGES=3` e `TCERJ_MAX_PAGES=3` no `.env` para limitar a paginação durante desenvolvimento.

---

## Desenvolvimento

```bash
# Lint
ruff check src tests

# Formatação (sem --check = auto-corrige)
ruff format src tests

# Type check
mypy src

# Testes (sem banco de dados necessário)
pytest

# Teste específico
pytest tests/services/test_analytics_service.py
pytest -k "test_calc_iec"
```

CI roda lint + format + mypy + pytest em sequência (`.github/workflows/ci.yml`).

---

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `DATABASE_URL` | `postgresql://geoobras:geoobras@localhost:5432/geoobras` | DSN do banco |
| `LLM_API_KEY` | *(vazio)* | Chave da Anthropic API — opcional, fallback automático |
| `LLM_MODEL` | `claude-opus-4-7` | Modelo LLM para insights |
| `OBRASGOV_UF` | `RJ` | UF para coleta no ObrasGov |
| `OBRASGOV_MAX_PAGES` | `5` | Limite de páginas da API (usar `null` para ilimitado em prod) |
| `TCERJ_MAX_PAGES` | `5` | Limite de páginas TCE-RJ |
| `CONVENIOS_DIR` | `data/input/macae_convenios` | Diretório dos CSVs de convênios |
| `HTTP_TIMEOUT` | `30.0` | Timeout por requisição HTTP (segundos) |
| `HTTP_MAX_RETRIES` | `3` | Tentativas em caso de falha |
| `LOG_LEVEL` | `INFO` | Nível de log |

---

## Estrutura do projeto

```
src/
├── api/
│   ├── main.py               # FastAPI app, rotas, middleware CORS
│   └── schemas.py            # Schemas de request/response
├── config/
│   └── settings.py           # Configuração centralizada (pydantic-settings)
├── domain/
│   ├── models.py             # Pydantic models (não são ORM)
│   └── enums.py              # Enums de status, fonte, persona
├── etl/
│   └── run_etl.py            # Orquestrador do pipeline ETL
├── infra/
│   ├── db.py                 # Conexão SQLAlchemy
│   ├── http_clients/         # Clientes HTTP ObrasGov + TCE-RJ (httpx + retry)
│   └── repositories/         # Acesso ao banco (SQL puro via text())
│       ├── raw_repository.py
│       ├── clean_repository.py
│       └── analytics_repository.py
└── services/
    ├── ingestion_service.py  # Orquestração da fase RAW
    ├── clean_service.py      # Normalização + deduplicação (CLEAN)
    ├── analytics_service.py  # Cálculos de métricas, IEC e recorrência
    ├── geometry_service.py   # Parse WKT/WKB → lat/lon com validação
    └── insights_service.py   # Geração de insights LLM + fallback determinístico

sql/
└── 000_schema_completo.sql   # DDL completo do banco (todas as camadas)

tests/
├── conftest.py               # Fixtures compartilhadas (mock DB session)
├── services/                 # Testes de lógica de negócio
└── repositories/             # Testes de queries

geoobras_producao.backup      # Dump PostgreSQL custom (~4.2 MB)
geoobras_producao.sql         # Dump PostgreSQL plain SQL (~26 MB)
FRONTEND_INTEGRATION_GUIDE.md # Guia de integração para o frontend
```

---

## Fontes de dados

| Fonte | Tipo | Cobertura |
|---|---|---|
| ObrasGov.br | API REST paginada (HTTPS) | Obras com financiamento federal — estado do RJ completo |
| TCE-RJ | API REST paginada (HTTPS) | Obras estaduais e municipais fiscalizadas pelo Tribunal de Contas |
| Convênios Macaé | CSV (governo municipal) | Convênios celebrados pelo município de Macaé/RJ |

Todas as fontes são públicas e de acesso aberto. O GeoObras não coleta nem armazena dados pessoais.

---

## Roadmap

- [ ] Suporte a outros municípios do RJ (Campos dos Goytacazes, Nova Friburgo)
- [ ] Endpoint GeoJSON para integração direta com Leaflet/Mapbox
- [ ] Histórico de IEC (série temporal por obra)
- [ ] Alertas por webhook quando IEC cai abaixo de limiar configurável
- [ ] Autenticação opcional para ambientes institucionais
- [ ] Expansão para outros estados (MG, SP, ES)

---

## Licença

MIT License — uso livre para fins cívicos, educacionais e institucionais.

---

## Contexto acadêmico

Este projeto foi desenvolvido como trabalho de conclusão de módulo na **FIAP** (Faculdade de Informática e Administração Paulista), com foco em Engenharia de Dados aplicada à transparência pública. O sistema foi construído do zero e evoluído para produção com dados reais.

**Snapshot de produção:** 2026-05-28 — 20.012 obras processadas — Macaé/RJ + estado do Rio de Janeiro via TCE-RJ.
