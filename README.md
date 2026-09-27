# olx-deals

> **Avisos antes de usar/publicar**
> - **ToS da OLX:** scraping pode violar os termos de uso do site e proteções
>   anti-bot (ex.: Cloudflare). Projeto para **uso pessoal, educacional e de baixo
>   volume**. Respeite `robots.txt`, os ToS, intervalos entre requisições e a
>   legislação aplicável. Sem afiliação com a OLX.
> - **Segurança:** a API é **aberta por padrão** (`OLX_API_TOKEN` não setado) e o
>   default de bind é `127.0.0.1`. Ao expor na LAN (`--host 0.0.0.0`/container),
>   **sempre** sete `OLX_API_TOKEN`. Nunca use `--debug` exposto à rede.
>   Nunca commite `~/.olx/*`, `.env`, tokens Telegram ou `chat_id` reais —
>   use placeholders (`changeme`, `1234567890`).
> - **Privacidade (LGPD):** o parser coleta só metadados públicos do anúncio
>   (`id/título/preço/url`), sem telefone/vendedor. Títulos podem conter dados
>   colados pelo anunciante — minimize retenção e remova sob solicitação.

Scraper do **OLX Brasil (todos os estados)** que procura discos **HD de 6 a 12TB** com
custo **< R$75/TB**, restrito a anúncios com **"Garantia + Entrega"** (filtro `opst=2`).

> **Novidade 2026-08-21**: interface gráfica web em `app.py` para gerenciar pesquisas, forçar buscas e visualizar resultados — veja seção **GUI** abaixo.

Feito e testado em 2026-08-21.

Busca via `https://www.olx.com.br/brasil?q=...&opst=2&page=N` — nacional, só anunciantes
profissionais com garantia e entrega OLX.

## Como funciona

Duas camadas (a ideia do "login headless + curl com cookie"):

1. **`--login`**: abre Chromium headless (Playwright), deixa o Cloudflare resolver e salva
   as cookies (inclui `__cf_bm`, `_cfuvid`) em `~/.olx/cookies.json`.
2. **`--scrape`**: para cada termo de busca, tenta primeiro a via rápida:
   **curl_cffi** (impersonação de TLS/HTTP2 de Chrome) usando as cookies capturadas;
   se retornar 0 cards (cookie expirado/403), faz **fallback automático para Playwright**,
   que abre a página com navegador real e re-captura as cookies (auto-refresh).
   Depois filtra: extracão de capacidade (TB) do título, preço, faixa 6-12TB, R$/TB < 75,
   junk (docks/estações, "recuperação/reparo/peças"), e deduplica por id do anúncio.

## Filtros de busca (topo do arquivo)

- `SEARCH_URL` — `https://www.olx.com.br/brasil?q={query}&opst=2&page={page}`
- `opst=2` = "Garantia + Entrega" (medição feita na UI: Garantia da OLX=`opst=1`, Garantia+Entrega=`opst=2`)
- `MIN_TB/MAX_TB/MAX_PRICE_PER_TB` — 6–12TB e teto R$75/TB
- `JUNK_RE` — rejeita títulos com dock/station/até, recuperação, reparo, conserto, peças

> **Nota (medido em 2026-08-21)**: com o filtro "Garantia + Entrega", HDs 6-12TB reais
> começam em ~**R$125/TB** (ex.: "HD 6TB" a R$750; Seagate 10TB a R$1300). O teto de
> R$75/TB era realista só em anúncios sem garantia (particulares, frequentemente
> "para recuperação/reparo"). Se quiser de fato receber alertas com garantia, convém
> elevar `MAX_PRICE_PER_TB` para ~130.

## Setup

```bash
cd ~/olx-deals
.venv/bin/python -m pip install -r requirements.txt   # se necessário
# precisa rodar UMA vez após instalar playwright:
# .venv/bin/python -m playwright install chromium
```

Projeto usa venv própria (`~/.venv`) criada com `python -m venv --without-pip` + get-pip
(porque `ensurepip`/`python3.12-venv` não estão disponíveis no host sem sudo).

## Uso

```bash
# 1a vez (captura cookies do Cloudflare)
.venv/bin/python olx_scraper.py --login

# busca padrão (queries por HD 6/8/10/12TB, 2 páginas, curl+fallback)
.venv/bin/python olx_scraper.py --scrape

# busca específica, mais páginas, só Playwright
.venv/bin/python olx_scraper.py --scrape --queries "hd 8tb" "western digital 10tb" --pages 3
.venv/bin/python olx_scraper.py --scrape --playwright-only

# notificação (opcional) — novos achados viram POST ntfy
OLX_NTFY_URL=https://ntfy.sh/meu-topico-hd .venv/bin/python olx_scraper.py --scrape
```

Resultados: console + histórico em `~/.olx/deals.json` (mantém só a entrada mais nova por anúncio).

## GUI web (app.py) — gerenciar / buscar / visualizar

Interface em **Flask** (`templates/index.html` SPA vanilla) que roda em `http://127.0.0.1:8765` por padrão. Mesma lógica do `olx_scraper.py`, mas com persistência em `~/.olx/config.json`.

**3 abas:**

1. **⚙️ Pesquisas** — edita o que será pesquisado: lista de `queries` (chips + lote), faixa `min_tb`/`max_tb`, teto `max_price_per_tb`, `pages` por query, `search_url` (template com `{query}`/`{page}`), regex `junk_pattern`, `playwright_only`, credenciais Telegram, e status/renew de cookies Cloudflare (`~/.olx/cookies.json`).
2. **▶️ Buscar** — força uma varredura imediata (usa config salva por padrão, com overrides opcionais só para esta execução: queries custom, páginas, teto, playwright). Mostra progresso por query/página, barra %, contadores e **log ao vivo** (poll de `/api/scrape/status`). Bloqueia segunda execução concorrente (409).
3. **📦 Resultados** — tabela de `~/.olx/deals.json` com filtros: texto livre (`q` em título/url/id), toggle `só matches`, ordenação (`fetched_at`/`price_per_tb`/`price`/`tb`/`title`), paginação (`limit`+“carregar mais”), export JSON/CSV, e destaque de `R$/TB` (verde <75, amarelo <120). Auto-refresh a cada 30 s.

**API REST** (usada pela SPA, mas chamável via curl):

```
GET  /api/health
GET  /api/config                # load_config() + telegram.json compat
PUT  /api/config                # valida e salva em ~/.olx/config.json
GET  /api/deals?matched_only=1&q=seagate&sort=price_per_tb&order=asc&limit=80
GET  /api/deals/raw
POST /api/scrape                # body json opcional: {queries, pages, max_price_per_tb, min_tb, max_tb, playwright_only, search_url, junk_pattern}
GET  /api/scrape/status         # {running, logs, progress, matched_count, ...}
GET  /api/scrape/logs?since=0
POST /api/login                 # força playwright_login
GET  /api/cookies/status
POST /api/config/telegram/test  # {token, chat_id}
```

> Lista completa e executável: spec OpenAPI em `GET /api/openapi.yaml` + Swagger UI em **`/docs`** — ver seção **API para agentes** abaixo.

**Rodar (host direto):**

```bash
cd ~/olx-deals
.venv/bin/pip install -r requirements.txt   # agora inclui Flask
.venv/bin/python app.py                     # http://127.0.0.1:8765 (só local)
.venv/bin/python app.py --host 0.0.0.0 --port 8765  # LAN: http://<seu-host-lan>:8765 (use com OLX_API_TOKEN)
OLX_GUI_PORT=8765 OLX_GUI_HOST=0.0.0.0 .venv/bin/python app.py --debug
```

**Rodar como container (integrado ao seu stack — recomendado para LAN):**

O stack principal é `docker compose` (ex.: `/path/to/seu-stack` via systemd).
Este repo já traz `Dockerfile` + `docker-compose.yml` prontos para `olx-gui` na mesma rede.

```bash
# como usuário dono do stack:
cd /path/to/seu-stack
cp docker-compose.yml docker-compose.yml.bak

# cole em services: (ver INTEGRACAO-CONTAINER.md para bloco exato)
nano docker-compose.yml
#   olx-gui:
#     build: /path/to/olx-deals
#     image: olx-gui:local
#     container_name: olx-gui
#     restart: unless-stopped
#     ports: ["8765:8765"]
#     volumes: ["${HOME}/.olx:/root/.olx"]
#     environment: [OLX_GUI_HOST=0.0.0.0]

docker compose build olx-gui
docker compose up -d olx-gui
curl http://<seu-host-lan>:8765/api/health
```

Detalhes e opção standalone em `INTEGRACAO-CONTAINER.md:1` + `docker-compose.yml:1` + `Dockerfile:1`.

**Persistência:** `~/.olx/config.json` (queries, filtros, search_url, junk_pattern, telegram) — lido tanto pela GUI quanto pelo CLI (`olx_scraper.py --scrape` agora usa `load_config()` quando `--queries`/`--pages` não são passados). `telegram.json` legado continua suportado (merge).

**Porta:** 8765 é o default local. Ajuste se colidir com outros serviços da sua LAN.

## API para agentes (OpenAPI + MCP)

A API REST é documentada por uma spec **OpenAPI 3.0.3** que serve como fonte única de verdade:

- **Spec:** `GET /api/openapi.yaml` (mesmo arquivo versionado em `openapi.yaml`)
- **Swagger UI:** `http://127.0.0.1:8765/docs` — "Try it out" executa de verdade
- Descriptions escritas para leitura por agentes/LLM: constraints reais (máx 100 produtos, `queries` ≤ 50, `pages` 1–10, `id` slugificado, 409 em duplicado), unidades (`max_price` em BRL absoluto), formato dos erros (`{ok:false, error}`)

### Auth opt-in por token

| Estado | Comportamento |
|---|---|
| `OLX_API_TOKEN` **não setado** | API **aberta** — GUI/cron/testes intocados |
| `OLX_API_TOKEN` setado | Toda `/api/*` exige header `X-API-Token` (exceto `GET /api/health`); falha = `401 {"ok": false, "error": "X-API-Token ausente ou inválido"}` |

> ⚠️ **Risco honesto**: auth é opt-in. Sem a env var, a API segue aberta no bind escolhido. O default é `127.0.0.1`; ao expor na LAN (`--host 0.0.0.0`, container ou NPM), sete o token junto (ver `INTEGRACAO-CONTAINER.md`). A SPA pede o token num prompt e guarda em `localStorage.olx_api_token`.

### MCP server gerado da spec

`mcp_server.py` usa `FastMCP.from_openapi()` para expor cada operação da spec como tool MCP, chamando a API por HTTP — **zero duplicação de lógica**. Registro em `~/.config/opencode/opencode.json` (requer restart da sessão opencode):

```jsonc
"mcp": {
  "olx-deals": {
    "type": "local",
    "command": ["/path/to/olx-deals/.venv/bin/python",
                "/path/to/olx-deals/mcp_server.py"],
    "environment": {
      "OLX_DEALS_URL": "http://127.0.0.1:8765"
      // se auth ativa: "OLX_API_TOKEN": "<mesmo valor do servidor>"
    },
    "enabled": true
  }
}
```

- **`PUT /api/config` não vira tool** (substituição destrutiva da lista inteira de products) — agentes usam o CRUD de products.
- Timeout de 120s; para varreduras longas, dispare `triggerScrape` e faça polling de `getScrapeStatus` até `running=false`.
- Smoke/E2E do server: `pytest tests/mcp_smoke.py` (sobe app real em porta efêmera e valida tools + ciclo create→update→delete).

### Tools expostas (24)

| Categoria | Tools |
|---|---|
| System | `getHealth`, `testTelegram`, `getOpenapiSpec` |
| Config | `getConfig` *(PUT excluído de propósito)* |
| Products (CRUD preferido p/ agentes) | `listProducts`, `createProduct`, `getProduct`, `updateProduct`, `deleteProduct` |
| Deals | `getDeals`, `getRawDeals`, `renotifyDeals`, `listNotified`, `clearNotified` |
| Scrape | `triggerScrape`, `getScrapeStatus`, `getScrapeLogs`, `olxLogin`, `getCookiesStatus` |
| Agendamento | `getSchedulesStatus`, `triggerScheduleById`, `getScheduleQueue`, `clearScheduleQueue`, `runDueSchedules` |

## Branches e contribuição

Estratégia: **GitHub Flow enxuto** — `main` sempre deployável, branches curtas por item do `BACKLOG.md:1`, PR com squash merge e tag `v0.X.Y`.
Ver `BRANCHING.md:1` para convenção de nomes (`feat/9-lifecycle`, `fix/cloudflare-403`), fluxo completo, `git worktree` para agentes e proteção de `main`.

```bash
git checkout main && git pull --ff-only
git checkout -b feat/12-tuner-ia
# ... commits ...
git push -u origin feat/12-tuner-ia  # abre PR -> squash merge -> tag
```

## Agendamento (cron — daemon já roda no host)

```bash
crontab -e
# rodar 3x/dia, quieto:
0 7,13,19 * * * cd /path/to/olx-deals && .venv/bin/python olx_scraper.py --scrape >/tmp/olx_deals.log 2>&1
```

## Ajustes no código (topo do arquivo)

- `DEFAULT_QUERIES` — termos de busca
- `MIN_TB / MAX_TB / MAX_PRICE_PER_TB` — 6–12TB e teto R$/TB
- `SEARCH_URL` — base da busca (hoje nacional: `https://www.olx.com.br/brasil?q={query}&opst=2&page={page}`); para limitar a uma UF, use `/estado-sp?q={query}&opst=2&page={page}`

## Cuidados

- **Cloudflare**: cookies `__cf_bm` expiram em ~30 min; o script re-loginiza sozinho no fallback.
- **Rate limit**: o script já dorme 1.5–3.5s entre páginas. Não aumente muito a frequência
  (IP é residencial real; não termine em lista escura).
- **ToS**: scraping de OLX viola os termos do site; uso pessoal + baixa frequência = baixo risco.
- **Filtro é só matemático**: título pode conter "Para Recuperação/Reparo" (sucateado),
  lotes, ou capacidade mal informada — confira o anúncio antes de pagar.

## Rota alternativa (proxy/WARP) — resiliência a bloqueio do IP

Quando a OLX/Cloudflare passa a responder 403/0 cards no IP residencial, as coletas
podem sair por uma rota alternativa (ex.: Cloudflare WARP em modo proxy). Opt-in:
sem as variáveis abaixo, o comportamento é idêntico ao atual (rota direta).

```bash
# habilitar (vale para curl + Playwright; aceita http/https/socks5)
export OLX_ALL_PROXY=http://127.0.0.1:8888
# ou granular: OLX_HTTP_PROXY / OLX_HTTPS_PROXY

# testar o diagnóstico (qual IP de saída, direto vs rota)
PYTHONPATH=src .venv/bin/python -c \
  "from olx_deals.infrastructure.scraping.proxy import get_egress_ip; print(get_egress_ip()); print(get_egress_ip('http://127.0.0.1:8888'))"

# desabilitar = remover as variáveis e reiniciar o serviço
```

Regras: proxy inválido aborta a coleta com erro claro em status/logs (sem loop
silencioso); falha da rota no meio da coleta cai para Playwright com log acionável;
toda coleta registra a rota usada nos logs (`rota alternativa ativa ...` / `rota direta`).
WARP em modo proxy no host: `warp-cli mode proxy && warp-cli proxy port 8888 && warp-cli connect`
(nunca modo túnel total neste host — sequestraria a rota de todo o stack).
