# olx-deals — espelho portfólio (sanitizado)

Espelho público do projeto privado `olx-deals`, criado para avaliação técnica.

Garantias deste espelho:
- Histórico Git zerado: 1 commit único, sem `git log` anterior, sem branches, sem PRs/issues.
- Sem segredos: nenhum `~/.olx/{config,cookies,deals,notified,runs,schedules}.json`, nenhum `.env`, nenhum token hardcoded. Verificado com busca por `AA...` (Telegram) e `sk-`.
- Sem dados locais: `deals.json`, `cookies.json`, `*.bak-purge-*` nunca commitados (ver `.gitignore`).
- Exemplo de credencial interna removido de `BRANCHING.md` (troca `~/.git-credentials` por `gh`).
- Configure via `.env.example` — nunca commite valores reais.

O que é o projeto:
- Monitor de ofertas OLX Brasil: scraper `curl_cffi` + fallback `Playwright`, filtros de preço/junk/exclude, histórico, notify Telegram, scheduler `APScheduler`, tuner assistido por LLM, API REST documentada em `openapi.yaml` + MCP.

Uso:
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env  # preencha localmente, não commite
.venv/bin/python app.py --host 127.0.0.1 --port 8765
```

Aviso de segurança para produção:
- Defina `OLX_API_TOKEN` forte; por padrão a API é aberta em LAN se exposta com `--host 0.0.0.0`.
- Não exponha sem reverse proxy + TLS + auth. Veja `README.md` e `INTEGRACAO-CONTAINER.md`.
