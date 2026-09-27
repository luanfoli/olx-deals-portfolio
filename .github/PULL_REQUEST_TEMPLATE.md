# PR — olx-deals

## Tipo
<!-- feat / fix / chore / docs / exp / hotfix -->
- [ ] `feat` — novo comportamento (BACKLOG item #___)
- [ ] `fix` — correção
- [ ] `chore` / `docs` / `exp`

## Branch
`tipo/id-slug` → `main` (ex: `feat/9-lifecycle-status`)

## O que muda
<!-- 1–3 linhas. Link para BACKLOG.md# e PLANO-*.md se houver -->

## Como testar
```bash
pytest -q
# ou manual:
# curl http://127.0.0.1:8765/api/health
# GET /api/deals?matched_only=1
```

## Checklist
- [ ] Branch curta (<3 dias) e rebased em `origin/main` (`git fetch && git rebase origin/main`)
- [ ] Commits seguem Conventional Commits (`feat:`, `fix:`)
- [ ] Testes passam (`pytest`)
- [ ] `openapi.yaml` atualizado se mudou `/api/*`
- [ ] Sem push direto em `main` — PR com squash merge
- [ ] Após merge: tag `v0.X.Y` criada e branch deletada

## Risco / Rollback
<!-- impacto em deals.json/notified.json/runs.json? Como reverter? tag anterior? -->
