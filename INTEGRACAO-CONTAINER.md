# Integração olx-gui ao stack Docker (exemplo genérico)

> Exemplo com paths e hosts fictícios (`/path/to/...`, `<seu-host-lan>`).
> Ajuste para o seu ambiente. Nunca commite IPs, hostnames DuckDNS ou paths reais.

O stack principal roda via `systemd` + `docker compose` (ex.: `/path/to/seu-stack/docker-compose.yml`).

O usuário do app não tem permissão para escrever no dir do stack nem falar com `docker.sock`
(grupo `docker` restrito), então aplique os passos abaixo como usuário dono do stack.

## Arquivos prontos

- `Dockerfile` — imagem `olx-gui:local` baseada em `mcr.microsoft.com/playwright/python:v1.62.0-jammy`
  (já traz Chromium, evita `playwright install` no host)
- `docker-compose.yml` (standalone) — define `services.olx-gui` com `ports: 8765:8765` e volume `${HOME}/.olx:/root/.olx`
- `templates/index.html` + `app.py` — já bindam em `0.0.0.0` quando via container

## Opção A — integrar ao stack principal (recomendado: mesmo `docker network` / `up -d` único)

Como dono do stack:

```bash
cd /path/to/seu-stack
cp docker-compose.yml docker-compose.yml.bak.$(date +%F)

# edite e cole o bloco abaixo dentro de `services:` (mesma indentação dos outros)
nano docker-compose.yml
```

Bloco para colar:

```yaml
  olx-gui:
    build: /path/to/olx-deals
    image: olx-gui:local
    container_name: olx-gui
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      - ${HOME}/.olx:/root/.olx
      # opcional: hot-reload de templates sem rebuild
      - /path/to/olx-deals/templates:/app/templates:ro
      - /path/to/olx-deals/static:/app/static:ro
    environment:
      - OLX_GUI_HOST=0.0.0.0
      - OLX_GUI_PORT=8765
      # auth opt-in: com valor não vazio no host, toda /api/* (menos GET /api/health)
      # passa a exigir header X-API-Token; sem a var, API segue aberta
      - OLX_API_TOKEN=${OLX_API_TOKEN:-}
```

Então:

```bash
docker compose build olx-gui
docker compose up -d olx-gui
docker compose ps          # deve mostrar olx-gui Up
docker logs -f olx-gui     # [olx-gui] http://0.0.0.0:8765
curl http://127.0.0.1:8765/api/health
curl http://<seu-host-lan>:8765/api/health  # acessível na LAN
```

De outro PC na mesma LAN: `http://<seu-host-lan>:8765/`

Para expor via reverse proxy (ex.: Nginx Proxy Manager): crie Proxy Host `olx.seu-dominio-exemplo.com` → `<seu-host-lan>:8765` com ACL.

### Auth opt-in por token (OLX_API_TOKEN)

Com o app em `0.0.0.0`, qualquer host da LAN alcança a API. Para fechar:

```bash
# no ambiente do host (ex.: ~/.bashrc do solo, ou .env ao lado do compose)
export OLX_API_TOKEN="um-token-longo-e-aleatório"
docker compose up -d olx-gui
```

- Setado → toda rota `/api/*` exige header `X-API-Token` (exceto `GET /api/health`); falha responde `401 {"ok": false, "error": "X-API-Token ausente ou inválido"}`.
- Não setado → comportamento atual (aberto); GUI/cron/testes intocados.
- A SPA pede o token num prompt e guarda em `localStorage.olx_api_token`.
- Cron/CLI (`olx_scraper.py --scrape`) não passa por HTTP → imune.
- Mudar o token exige restart do container (lido uma vez no startup).

## Opção B — stack separado (sem mexer no compose principal)

Também funciona deixar o olx-gui num compose à parte (mesma porta no host):

```bash
cd /path/to/olx-deals
docker compose up -d --build
docker ps | grep olx-gui
```

A vantagem da Opção A é `docker compose up -d` único no serviço systemd.

## Por que antes não funcionava

`app.py` por padrão binda `127.0.0.1` (`app.py:483` → `OLX_GUI_HOST`). Só o próprio host acessa.
Com `--host 0.0.0.0` (já no `Dockerfile`/`docker-compose.yml`) o proxy `docker-proxy 0.0.0.0:8765`
encaminha para o container e fica visível na LAN. Teste local validado:

```
[olx-gui] http://0.0.0.0:8765  (templates=/app/templates)
* Running on all addresses (0.0.0.0)
LISTEN 0.0.0.0:8765
{"ok":true}  # via <seu-host-lan>:8765
```

## Notas

- Estado compartilhado: `~/.olx` é o mesmo do CLI (`olx_scraper.py --scrape` lê `config.json`/`deals.json`/`cookies.json`).
- Sem permissão no `docker.sock`/dir do stack não é possível automatizar via compose principal.
- Se preferir rodar sem Docker (host direto): `OLX_GUI_HOST=0.0.0.0 /path/to/olx-deals/.venv/bin/python /path/to/olx-deals/app.py` — mas morre no reboot (sem serviço).
