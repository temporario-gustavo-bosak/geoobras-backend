# GeoObras — Frontend Integration Guide

> **Base URL (local):** `http://localhost:8000`  
> **Swagger interativo:** `http://localhost:8000/docs`  
> **OpenAPI JSON:** `http://localhost:8000/openapi.json`  
> **CORS:** liberado para todas as origens (`*`) — restringir em produção.

---

## Índice

1. [Subir o backend](#1-subir-o-backend)
2. [Autenticação](#2-autenticação)
3. [Endpoints disponíveis](#3-endpoints-disponíveis)
4. [Schemas de resposta](#4-schemas-de-resposta)
5. [Filtros e paginação](#5-filtros-e-paginação)
6. [Mapa (lat/lon e WKT)](#6-mapa-latlon-e-wkt)
7. [IEC Score — Índice de Eficiência Composta](#7-iec-score--índice-de-eficiência-composta)
8. [Insights LLM](#8-insights-llm)
9. [Valores de enum](#9-valores-de-enum)
10. [Exemplos de fetch prontos](#10-exemplos-de-fetch-prontos)
11. [Erros comuns](#11-erros-comuns)

---

## 1. Subir o backend

```bash
# Com Docker (recomendado — já sobe Postgres + API)
docker compose up -d

# Ou só o banco + API local
docker compose up -d db
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

# Health check
curl http://localhost:8000/health
# → {"status":"ok","banco":true}
```

> O banco já está populado com dados reais de Macaé/RJ.  
> Para restaurar o dump de produção localmente:
> ```bash
> # Plain SQL
> psql -U geoobras -d geoobras -f geoobras_producao.sql
> # Ou custom dump
> pg_restore -U geoobras -d geoobras geoobras_producao.backup
> ```

---

## 2. Autenticação

**Nenhuma.** Todos os endpoints são públicos. Sem token, sem header especial.

---

## 3. Endpoints disponíveis

| Método | Path | Tag | Descrição |
|--------|------|-----|-----------|
| `GET` | `/health` | Operação | Liveness check |
| `GET` | `/api/v1/obras` | Obras | Lista paginada de obras (com filtros) |
| `GET` | `/api/v1/obras/{id}` | Obras | Detalhe completo de uma obra |
| `GET` | `/api/v1/obras/{id}/insights` | Insights | Resumo analítico por LLM ou fallback |
| `GET` | `/api/v1/estatisticas` | Estatísticas | Agregados do portfólio |
| `POST` | `/api/v1/refresh` | Operação | Registra intenção de re-execução do ETL |

---

## 4. Schemas de resposta

### `GET /api/v1/obras` → `ObrasListResponse`

```jsonc
{
  "total": 20012,        // total de obras no filtro (para paginação)
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",  // UUID — usar em /obras/{id}
      "nome": "Pavimentação Av. Beira-Mar",
      "status": "em_execucao",                        // ver enum §9
      "data_inicio": "2022-03-01",                    // ISO 8601 ou null
      "data_fim_prevista": "2023-03-01",
      "data_fim_real": null,
      "valor_total_contratado": 1500000.0,            // R$, pode ser null
      "valor_pago_acumulado": 900000.0,
      "percentual_fisico": 42.5,                      // 0–100, pode ser null
      "percentual_desembolso": 60.0,                  // calculado: pago/contratado×100
      "latitude": -22.3765,                           // null se sem coordenada
      "longitude": -41.7869,
      "flag_data_fim_pendente": false,                // prazo ainda não informado
      "flag_populacao_suspeita": false,               // valor estatisticamente atípico
      "flag_empregos_suspeitos": false,
      "flag_possivel_atraso": true,                   // ultrapassou prazo previsto
      "fonte_principal": "obrasgov"                   // ver enum §9
    }
  ]
}
```

---

### `GET /api/v1/obras/{id}` → `ObraDetalhe`

```jsonc
{
  "id_obra_geoobras": "550e8400-e29b-41d4-a716-446655440000",
  "id_unico_obrasgov": "12345.33-01",   // código do ObrasGov, pode ser null
  "id_obras_tce": null,                  // código do TCE-RJ, pode ser null
  "nome": "Pavimentação Av. Beira-Mar",
  "descricao": "Obra de...",
  "municipio": "Macaé",
  "uf": "RJ",
  "bairro": "Centro",                    // pode ser null
  "logradouro": "Av. Beira-Mar, s/n",
  "status_obra": "em_execucao",
  "data_inicio": "2022-03-01",
  "data_fim_prevista": "2023-03-01",
  "data_fim_real": null,
  "flag_data_fim_pendente": false,
  "percentual_fisico": 42.5,
  "percentual_desembolso": 60.0,
  "populacao_beneficiada": 5000,         // pode ser null
  "flag_populacao_suspeita": false,
  "empregos_gerados": 120,
  "flag_empregos_suspeitos": false,
  "valor_total_contratado": 1500000.0,
  "valor_pago_acumulado": 900000.0,
  "latitude": -22.3765,
  "longitude": -41.7869,
  "geom": "POINT (-41.7869 -22.3765)",  // WKT — usar com Leaflet/Turf
  "dias_atraso": 45,                     // int, pode ser null
  "flag_possivel_atraso": true,
  "iec_score": 72.5,                     // 0–100, ver §7. Pode ser null
  "metricas_calculado_em": "2026-05-28T10:28:13",
  "fonte_principal": "obrasgov",
  "atualizado_em": "2026-05-28T10:27:58",
  "contratos": [],                        // lista de ContratoItem (ver abaixo)
  "convenios": []
}
```

**ContratoItem:**
```jsonc
{
  "id": "uuid",
  "numero_contrato": "001/2022",
  "valor_global": 1500000.0,
  "valor_acumulado": 900000.0,
  "vigencia_fim": "2023-03-01",
  "situacao": "em_execucao"
}
```

---

### `GET /api/v1/obras/{id}/insights` → `InsightResponse`

```jsonc
{
  "resumo": "A obra apresenta divergência físico-financeira de +17,5 p.p.: o desembolso (60%) está à frente da execução física (42,5%). Há atraso de 45 dias em relação ao prazo contratual.",
  "flags": {
    "possivel_atraso": true,
    "data_fim_pendente": false,
    "populacao_suspeita": false,
    "empregos_suspeitos": false,
    "dias_atraso": 45
  },
  "iec_score": 72.5,
  "fonte": "llm",      // "llm" ou "fallback"
  "gerado_em": "2026-05-28T10:30:00"
}
```

> **`fonte: "fallback"`** ocorre quando o LLM não está configurado (`LLM_API_KEY` vazia) ou falha. O texto ainda é útil — gerado por regras determinísticas.

---

### `GET /api/v1/estatisticas` → `EstatisticasResponse`

```jsonc
{
  "obras_por_status": [
    {"status_obra": "planejada",    "qtd": 9200},
    {"status_obra": "em_execucao",  "qtd": 4300},
    {"status_obra": "concluida",    "qtd": 5800},
    {"status_obra": "paralisada",   "qtd": 400},
    {"status_obra": "cancelada",    "qtd": 312}
  ],
  "media_percentual_fisico": 47.3,       // null se sem dados
  "distribuicao_atraso": [
    {"flag_possivel_atraso": true,  "qtd": 3400},
    {"flag_possivel_atraso": false, "qtd": 16612}
  ]
}
```

---

## 5. Filtros e paginação

### `GET /api/v1/obras`

| Query param | Tipo | Descrição | Exemplo |
|---|---|---|---|
| `situacao` | string | Filtra por `status_obra` exato | `?situacao=em_execucao` |
| `municipio` | string | ILIKE `%valor%` | `?municipio=macae` |
| `apenas_com_coordenadas` | bool | Só obras com lat/lon | `?apenas_com_coordenadas=true` |
| `apenas_inconsistencias` | bool | Só obras com flags ativos | `?apenas_inconsistencias=true` |
| `valor_minimo` | float | `valor_total_contratado >= X` | `?valor_minimo=1000000` |
| `page` | int ≥ 1 | Página atual | `?page=2` |
| `page_size` | int 1–200 | Itens por página | `?page_size=100` |

### Paginação

```js
// Total de páginas
const totalPages = Math.ceil(response.total / response.page_size);

// Próxima página
fetch(`/api/v1/obras?page=${currentPage + 1}&page_size=50`)
```

---

## 6. Mapa (lat/lon e WKT)

### Leaflet (React/Vue)

```js
// Listagem — usar lat/lon direto
const obras = response.items.filter(o => o.latitude && o.longitude);
obras.forEach(obra => {
  L.marker([obra.latitude, obra.longitude])
    .bindPopup(`<b>${obra.nome}</b><br>${obra.status}`)
    .addTo(map);
});
```

### WKT → GeoJSON (detalhe da obra)

```js
import wellknown from 'wellknown'; // npm install wellknown

const geojson = wellknown.parse(obra.geom); // "POINT (-41.78 -22.37)"
// → { type: "Point", coordinates: [-41.78, -22.37] }

L.geoJSON(geojson).addTo(map);
```

### Bounding box de Macaé (para inicializar o mapa)

```js
// Centro de Macaé/RJ
const MACAE_CENTER = [-22.3765, -41.7869];
const MACAE_ZOOM = 12;
```

---

## 7. IEC Score — Índice de Eficiência Composta

Campo: `iec_score` (0–100, pode ser `null`).

| Faixa | Interpretação | Cor sugerida |
|---|---|---|
| 80–100 | Alta eficiência | `#22c55e` (verde) |
| 60–79 | Eficiência moderada | `#eab308` (amarelo) |
| 40–59 | Atenção | `#f97316` (laranja) |
| 0–39 | Alto risco | `#ef4444` (vermelho) |
| `null` | Dados insuficientes | `#9ca3af` (cinza) |

```js
function iecColor(score) {
  if (score === null) return '#9ca3af';
  if (score >= 80) return '#22c55e';
  if (score >= 60) return '#eab308';
  if (score >= 40) return '#f97316';
  return '#ef4444';
}
```

---

## 8. Insights LLM

```js
// Persona "auditor" (default) — linguagem técnica
GET /api/v1/obras/{id}/insights

// Persona "cidadao" — linguagem acessível para o público geral
GET /api/v1/obras/{id}/insights?persona=cidadao
```

> Latência: ~2–5s quando LLM ativo. Use loading skeleton.  
> Em caso de timeout ou erro do LLM, a API responde normalmente com `"fonte": "fallback"` — nunca retorna 500 por causa do LLM.

---

## 9. Valores de enum

### `status` / `status_obra`

| Valor | Descrição |
|---|---|
| `planejada` | Em planejamento, cadastrada, em licitação |
| `em_execucao` | Em andamento, contratada |
| `concluida` | Finalizada |
| `paralisada` | Paralisada (TCE ou ObrasGov) |
| `cancelada` | Cancelada |
| `inacabada` | Inacabada (TCE) |
| `desconhecida` | Status não mapeado |

### `fonte_principal`

| Valor | Origem |
|---|---|
| `obrasgov` | ObrasGov.br (dados federais) |
| `tce` | TCE-RJ |
| `mista` | Match ObrasGov + TCE-RJ |
| `convenio` | Convênios municipais (CSV) |

### `persona` (query param em `/insights`)

| Valor | Público |
|---|---|
| `auditor` | Linguagem técnica, indicadores numéricos |
| `cidadao` | Linguagem simples, sem jargão |

---

## 10. Exemplos de fetch prontos

### Lista com mapa (só obras com coordenadas)

```js
const res = await fetch(
  'http://localhost:8000/api/v1/obras?apenas_com_coordenadas=true&page_size=200'
);
const { items, total } = await res.json();
```

### Busca obras em atraso acima de R$ 500 mil

```js
const res = await fetch(
  'http://localhost:8000/api/v1/obras?situacao=em_execucao&valor_minimo=500000&apenas_inconsistencias=true'
);
```

### Detalhe de uma obra

```js
const res = await fetch(`http://localhost:8000/api/v1/obras/${id}`);
const obra = await res.json();
// obra.iec_score → número 0–100 ou null
// obra.geom      → WKT string ou null
// obra.contratos → array
```

### Insight para cidadão

```js
const res = await fetch(
  `http://localhost:8000/api/v1/obras/${id}/insights?persona=cidadao`
);
const { resumo, flags, iec_score, fonte } = await res.json();
```

### Estatísticas para dashboard

```js
const res = await fetch('http://localhost:8000/api/v1/estatisticas');
const { obras_por_status, media_percentual_fisico, distribuicao_atraso } = await res.json();
```

### TypeScript — tipos principais

```ts
type ObraListItem = {
  id: string;
  nome: string;
  status: string | null;
  data_inicio: string | null;
  data_fim_prevista: string | null;
  data_fim_real: string | null;
  valor_total_contratado: number | null;
  valor_pago_acumulado: number | null;
  percentual_fisico: number | null;
  percentual_desembolso: number | null;
  latitude: number | null;
  longitude: number | null;
  flag_data_fim_pendente: boolean;
  flag_populacao_suspeita: boolean;
  flag_empregos_suspeitos: boolean;
  flag_possivel_atraso: boolean | null;
  fonte_principal: string | null;
};

type ObrasListResponse = {
  total: number;
  page: number;
  page_size: number;
  items: ObraListItem[];
};

type InsightResponse = {
  resumo: string;
  flags: Record<string, unknown>;
  iec_score: number | null;
  fonte: 'llm' | 'fallback';
  gerado_em: string;
};
```

---

## 11. Erros comuns

| HTTP | Causa | Solução |
|---|---|---|
| `404` | UUID inválido ou obra não existe | Verificar `id` retornado pela listagem |
| `422` | Query param inválido (ex: `page=0`) | `page` mínimo = 1, `page_size` 1–200 |
| `500` | Banco fora do ar | `docker compose up -d db` |
| CORS error | Frontend em porta diferente | CORS já liberado para `*` — verificar URL base |
| `null` em `lat/lon` | Obra sem geometria no ObrasGov | Filtrar com `?apenas_com_coordenadas=true` |
| `iec_score: null` | Dados insuficientes para cálculo | Exibir como "sem dados" no frontend |

---

## Dados de produção

| Arquivo | Formato | Tamanho | Conteúdo |
|---|---|---|---|
| `geoobras_producao.backup` | pg_dump custom | ~4.2 MB | Dump binário completo |
| `geoobras_producao.sql` | pg_dump plain SQL | ~26 MB | Dump SQL legível |

Gerados em 2026-05-28 com ~20.000 obras processadas (Macaé + RJ completo via TCE-RJ).
