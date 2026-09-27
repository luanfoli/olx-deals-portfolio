# AGENTS — olx-deals

> **Leia `BRANCHING.md` antes de qualquer branch/commit.** Este repo usa **GitHub Flow enxuto** — `main` sempre deployável.

## Regras para agentes (LLM, opencode, MCP, humano)

1. **Nunca commit direto em `main`.** Sempre:
   ```bash
   git checkout main && git pull --ff-only
   git checkout -b feat/<id>-<slug>   # ex: feat/12-tuner-ia
   # ... edita, testa com pytest ...
   git push -u origin feat/<id>-<slug>  # abre PR → squash merge → tag
   ```
   Tipos permitidos: `feat|fix|chore|docs|exp|hotfix` (ver `BRANCHING.md:2`). Hook `pre-commit` bloqueia `main` e nome fora do padrão.

2. **Commits Conventional:** `feat(api): ...`, `fix(scraper): ...`, `chore(githooks): ...` — hook `commit-msg` valida. Bypass só com `--no-verify` em emergência.

3. **PR squash obrigatório:** GitHub está configurado para só `squash merge` (`allow_merge_commit=false`). CI `branch-guard.yml` valida branch, commits e linear history.

4. **Push em `main` bloqueado:** hook `pre-push` (`.githooks/pre-push`) + `opencode.jsonc:permission` negam `git push origin main` (libera ` --no-verify` só com `ask` humano).

5. **Worktree para paralelo:** `git worktree add ../olx-deals-<id> feat/<id>-slug` (ver `BRANCHING.md:3`).

## Onde estão as regras completas
- `BRANCHING.md:1` — mapa de branches, convenção `feat/<id>-<slug>`, fluxo PR→squash→tag, proteção `private Free` (403) e workaround local.
- `README.md:207` — seção "Branches e contribuição" (resumo).
- `.githooks/` — hooks versionados (`pre-commit`, `commit-msg`, `pre-push`, `core.hooksPath=.githooks`).
- `.github/workflows/branch-guard.yml:1` — CI que valida PRs.

## Checklist rápido do agente
- [ ] Li `BRANCHING.md` (incluindo `§7.1` aditamento de Issue)?
- [ ] Branch criada a partir de `main` atualizado?
- [ ] Nome segue `feat|fix|.../<slug>`?
- [ ] `pytest` passou?
- [ ] Push foi para `feat/*`, não para `main`?
- [ ] Se alterei `Issue` após `feat/N` aberto, classifiquei como `A/B/C` (`BRANCHING.md:7.1`)?

## Setup pós-clone (humano)
```bash
git clone https://github.com/luanfoli/olx-deals.git && cd olx-deals
git config core.hooksPath .githooks   # ativa hooks versionados
```

> Dúvidas: `BRANCHING.md:5` explica por que `private Free` não tem branch protection server-side e como tornar público para proteção total.
