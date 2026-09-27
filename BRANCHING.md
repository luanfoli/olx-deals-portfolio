# Estratégia de Branches — olx-deals

> **Modelo adotado: GitHub Flow enxuto (Trunk-Based com branches curtas)**
> Decisão: 2026-08-29. `main` é sempre deployável. Todo trabalho acontece em branch curta com PR squash.

## 1. Mapa de branches

| Branch | Vida | Origem | Propósito |
|---|---|---|---|
| `main` | permanente | — | Produção. Deploy direto (host `s6` + container `olx-gui:local`). Protegido. Só recebe squash merge de PR. |
| `feat/<id>-<slug>` | 1–3 dias | `main` | Feature do `BACKLOG.md` (ex: `feat/9-lifecycle-status`, `feat/12-tuner-ia`) |
| `fix/<slug>` | horas–1 dia | `main` | Correção (ex: `fix/mask-telegram-token`, `fix/purge-ui-hoje`) |
| `chore/<slug>` | <1 dia | `main` | Infra/docs/build (ex: `chore/branching-strategy`, `chore/pin-deps`) |
| `docs/<slug>` | <1 dia | `main` | Só documentação |
| `exp/<slug>` | descartável | `main` | Experimento/spike (pode não mergear, ex: `exp/proxy-resiliencia` item 2) |
| `hotfix/<slug>` | horas | `main` (tag) | Correção crítica em produção |

Não há `develop`, `release/*` ou `stable`. Se precisar homologar, use `feat/*` + deploy efêmero; não mantenha branch longa.

## 2. Convenção de nomes

```
<tipo>/<id-opcional>-<slug-kebab-case>
```

* `tipo`: `feat` | `fix` | `chore` | `docs` | `exp` | `hotfix`
* `id`: número do `BACKLOG.md` quando aplicável (`9`, `10`, `12`)
* `slug`: curto, sem acentos, max 40 chars

Exemplos válidos:
```
feat/9-lifecycle-status
feat/10-snapshot-ingestao
feat/12-tuner-ia
fix/cloudflare-403-fallback
chore/branching-strategy
exp/proxy-https
```

Commits seguem Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`) — já em uso no `git log --oneline`.

## 3. Fluxo (passo a passo)

### 3.1 Criar

```bash
git checkout main && git pull --ff-only
git checkout -b feat/9-lifecycle-status
# ou para agente em paralelo sem trocar de branch:
git worktree add ../olx-deals-9-lifecycle feat/9-lifecycle-status
```

### 3.2 Desenvolver

* Commits pequenos e com testes (`pytest`). Push frequente:
  ```bash
  git push -u origin feat/9-lifecycle-status
  ```
* Rebase diário para evitar drift:
  ```bash
  git fetch origin && git rebase origin/main
  ```

### 3.3 PR → Squash Merge → Tag

1. Abrir PR no GitHub: `feat/9-lifecycle-status` → `main` (template em `.github/PULL_REQUEST_TEMPLATE.md`)
2. CI local: `pytest` deve passar; review do dono (`luanfoli`) — mesmo solo, abre PR para ter histórico auditável.
3. Merge: **Squash and merge** (1 commit limpo na `main`, mensagem preserva `feat(9): ...`).
4. Pós-merge:
   ```bash
   git checkout main && git pull --ff-only
   git tag -a v0.12.0 -m "feat(9): lifecycle status active/removed"
   git push origin main --tags
   git branch -d feat/9-lifecycle-status
   git push origin --delete feat/9-lifecycle-status
   git worktree remove ../olx-deals-9-lifecycle  # se usou worktree
   ```

### 3.4 Hotfix

```bash
git checkout main && git pull --ff-only
git checkout -b hotfix/cloudflare-block
# fix ...
git push -u origin hotfix/cloudflare-block
# PR → squash → tag v0.12.1 → delete branch
```

## 4. Versionamento (tags)

Semver `v0.<BACKLOG_ID>.<patch>` enquanto `0.x` (pre-1.0):

* `v0.11.0` = item 11 (expurgo) — base atual `deace91`
* `v0.11.1` = fix subsequente (`deace91` fix/purge UI)
* `v0.12.0` = próximo feat (item 9 ou 12)

Tag anotada obrigatória: `git tag -a v0.X.Y -m "feat(...): ..."`. `git describe --tags` deve funcionar.

## 5. Proteção de `main` (GitHub)

**Status 2026-08-29 (verificado via API):** `GET /repos/luanfoli/olx-deals/branches/main/protection` e `GET /rulesets` com token `ghp_***` retornam `403 "Upgrade to GitHub Pro or make this repository public"` — repo é `private` e plano Free não permite branch protection/rulesets server-side.

**Aplicado via API (permitido no Free):**
```bash
PATCH /repos/luanfoli/olx-deals
  allow_merge_commit=false, allow_rebase_merge=false,
  allow_squash_merge=true, delete_branch_on_merge=true
# verificado 2026-08-29: allow_merge_commit=False, allow_squash_merge=True, delete_branch_on_merge=True
```
Isso já força **só squash merge** em PRs e auto-delete de branch.

**Opções para proteção completa de `main` (escolha 1):**

| Opção | Ação | Custo | Efeito |
|---|---|---|---|
| **A. Tornar público** (recomendado se código não é sensível) | `PATCH {"private":false}` ou UI Settings → Danger Zone → Make public | grátis | Libera `Require PR + linear history` |
| **B. Upgrade para Pro** | github.com/settings/billing | $4/mês | Libera protection em private |
| **C. Manter private Free** | já aplicado acima + hook local bloqueante | grátis | Proteção só local + squash forced |

**Workaround local ativo:** `.git/hooks/pre-push` bloqueia `git push origin main` (exit 1, libera com `--no-verify` em emergência). É a proteção efetiva enquanto `private` free.

Para aplicar **A** + proteção total (use `gh`, nunca exponha token em shell history):
```bash
gh repo edit <owner>/<repo> --visibility public
gh api repos/<owner>/<repo>/branches/main/protection -X PUT -f required_status_checks=null -f enforce_admins=false -f required_linear_history=true -f allow_force_pushes=false -f allow_deletions=false
```

## 6. Para agentes (opencode / MCP)

* Cada `PLANO-*.md` vira um `feat/<id>-<slug>` isolado. Agente cria branch, implementa, roda `pytest`, abre PR — `main` nunca quebra.
* Use `git worktree` para 2 planos em paralelo sem `stash`.
* MCP `createProduct`/`updateProduct` não precisam branch; mudanças de código sim.
* Branch `exp/*` pode ser apagada sem merge se a ideia for descartada.

## 7. O que NÃO fazer

* Commit direto em `main` (exceto `docs:` trivial ou `hotfix` via PR)
* Branch longa (>5 dias) sem rebase — prefira quebrar o item do `BACKLOG.md:248`
* `git merge main` dentro da feature — use `git rebase origin/main`
* Reutilizar branch após merge — sempre `feat/<novo-slug>`
* Manter `olx-deals-remote.git` dessincronizado — é mirror bare local, sincronizar com `git --git-dir=... fetch origin main:main`
* Reescrever `Issue` apagando critério original sem `Aditamento` — ver `§7.1`

### 7.1 Política de Aditamento de Issues (Issue é contrato)

`Issue` é contrato congelado após `feat/N` aberto. `PLANO-ISSUE-N.md` é spec executável. Nunca apague critério original — preserve histórico.

Se descobrir ponto adicional durante implementação, classifique:

| Tipo | Característica | Fluxo |
|---|---|---|
| **A. Errata/omissão** | critério impossível/contraditório, bug factual sem o qual implementação falha | **Adita mesma Issue #N**: acrescenta `### Aditamento YYYY-MM-DD` ao body + comentário linkando + atualiza `PLANO`, commit `fix:` na mesma `feat/N` |
| **B. Clarificação compatível** | detalhe que não cria critério novo nem quebra existente, <2h | Mesmo que A, só se não atrasar merge |
| **C. Extensão/nova feature** | novo critério, novo `Fora de escopo`, UI/infra/billing | **Não adita #N**. Cria Issue #M nova (`BACKLOG.md`), `PLANO-ISSUE-M.md` e `feat/M-` após merge de N |

Comunicação: comente na `Issue` (`Aditamento incorporado em PLANO-ISSUE-N.md@<sha>`) + no `PR` (`Adequado a Aditamento #N <link> — commit <sha>`) + `git fetch origin && git rebase origin/main` + `pytest` antes do push.

Regra de ouro: **muda ou adiciona critério de aceite = nova Issue**. Evita `scope creep` e branch longa.

## 8. Comandos úteis

```bash
# ver branches ativas e idade
git for-each-ref --sort=-committerdate refs/heads/ --format='%(refname:short) %(committerdate:short) %(subject)' | head

# limpar branches já mergeadas localmente
git branch --merged main | grep -v "^\*\|main" | xargs -r git branch -d

# limpar branches remotas órfãs
git fetch --prune

# criar branch a partir de tag (hotfix)
git checkout -b hotfix/xyz v0.11.1
```

## 9. Histórico anterior

Até `2026-08-29` o repo usou trunk puro (39 commits direto em `main`). A partir deste documento, todo novo trabalho segue o fluxo acima. Tags retroativas foram criadas para marcar o baseline.

---
Referências: `README.md:1`, `BACKLOG.md:1`, `app.py:319` (scrape), `olx_scraper.py:1475` (save_history), `openapi.yaml:1`.
