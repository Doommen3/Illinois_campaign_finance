# Deployment Runbook

Detailed deployment + auth reference. The short version lives in `CLAUDE.md` under "Deployment". This file covers the auth plumbing and recovery paths.

## Server inventory

| Item | Value |
|------|-------|
| Host | 178.156.162.56 |
| SSH | `ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56` |
| App root | `/srv/illinois_campaign_finance/app` |
| Python | `/srv/illinois_campaign_finance/shared/venv/bin/python3` |
| Pip | `/srv/illinois_campaign_finance/shared/venv/bin/pip` |
| Env file | `/srv/illinois_campaign_finance/shared/.env` |
| DB | PostgreSQL `ilcf` (prod via `DATABASE_URL` in `.env`) |
| Web service | `ilcf-web.service` |
| Legacy service (keep disabled) | `illinois-web.service` |

## SSH authentication

The SSH key `~/.ssh/hetzner_ed25519` passphrase is stored in **macOS Keychain via ssh-agent**, configured in `~/.zshrc`:

```bash
ssh-add --apple-use-keychain ~/.ssh/hetzner_ed25519 2>/dev/null
```

SSH, rsync, scp, and Claude Code autonomous SSH all work without passphrase prompts. If the agent loses the key (e.g., after OS update), re-run `ssh-add --apple-use-keychain ~/.ssh/hetzner_ed25519` and enter the passphrase once.

## Server-side GitHub auth (deploy key)

The server uses a **GitHub deploy key** for git pull access. No PAT needed.

- Deploy key (read-only): `/root/.ssh/github_deploy` (private), `/root/.ssh/github_deploy.pub`
- SSH config (`/root/.ssh/config`):
  ```
  Host github.com
    User git
    IdentityFile /root/.ssh/github_deploy
    IdentitiesOnly yes
  ```
- Remote URL: `git@github.com:Doommen3/Illinois_campaign_finance.git` (SSH, not HTTPS)
- `git pull --ff-only origin main` works directly on the server with no credentials prompt.

**Do NOT** switch the remote URL to HTTPS or embed a PAT. If the deploy key stops working: check that `/root/.ssh/github_deploy` exists (permissions `0600`) and that the public key is still registered under **Settings > Deploy keys** in the GitHub repo.

## Local GitHub auth

- Username: Doommen3
- Remote: HTTPS, auth via classic PAT in macOS Keychain (`credential.helper = osxkeychain` in `.gitconfig`)
- `gh` CLI is installed and authenticated (fine-grained PAT) for API operations (`gh api`, `gh pr`, etc.)
- Git push uses the classic PAT stored in osxkeychain (the fine-grained PAT in `gh` doesn't have git push scope)
- If push returns 403, re-store the classic PAT:
  ```bash
  printf 'protocol=https\nhost=github.com\nusername=Doommen3\npassword=<PAT>\n' | git credential-osxkeychain store
  ```

## Server command pattern

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
PIP=/srv/illinois_campaign_finance/shared/venv/bin/pip
set -a; source /srv/illinois_campaign_finance/shared/.env; set +a
$PYTHON run.py <command>
```

## Syncing production DB to local

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 \
  "pg_dump -Fc ilcf" > data/ilcf_prod.dump

pg_restore --clean --if-exists -d ilcf data/ilcf_prod.dump
```

**Do NOT** run `init-db`, `run-cross-matching`, or other DB-writing commands on the server while a dump is in progress.

Local DB: `postgresql://devin@localhost/ilcf` (matches `config.DATABASE_URL` default).

## Weekly data refresh (canonical chain)

The full prod refresh is orchestrated by `scripts/refresh_prod_chain.sh` (in this repo). Companion sanity-check: `scripts/check_row_counts.py`. After `git pull`, both are available at `/srv/illinois_campaign_finance/app/scripts/`.

Run inside `tmux` so the chain survives SSH disconnects:

```bash
ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56
tmux new -s refresh
cd /srv/illinois_campaign_finance/app
LOG=/srv/illinois_campaign_finance/shared/refresh_$(date +%Y%m%d_%H%M%S).log
nohup bash scripts/refresh_prod_chain.sh "$LOG" > /dev/null 2>&1 &
echo "PID=$!  LOG=$LOG"
```

The chain runs:

1. Snapshot prior row counts → `Bulk_download/.row_counts_prev`
2. Download 12 ISBE bulk files via hardened curl (retry/timeout/User-Agent)
3. Sanity-check row counts vs. baseline (aborts if any file dropped >5% — the 2026-05-13 silent-truncation signature)
4. `sunshine-import --bulk-dir Bulk_download` (with the loader's own >1% FK-reject integrity gate)
5. `sync-fec-il-federal`
6. `run-cross-matching --only all` (longest step, ~6–60 min depending on data volume)
7. `refresh-analytics --with-snapshot`
8. Restart `ilcf-web.service` + smoke-test HTTP 200 on `/`

Each step emits `STEP_N_START` / `STEP_N_END` markers to the log; failures emit `STEP_FAILED` + `CHAIN_FAILED`. Watch with `tail -f "$LOG"`.

Total runtime: ~60–120 min. Detach tmux during cross-matching with `Ctrl-b d`; reattach later with `tmux attach -t refresh`.

## Endpoint sweep after deploy

```
/                                                          # homepage
/search?q=Chicago
/candidates
/federal-finance/
/analytics/
/lobbying/
/527/
/federal-finance/geo-drilldown?geo_type=state&geo_value=IL&cycle=2026
/analytics/geo-drilldown?geo_type=state&geo_value=IL&period=all
/federal-finance/races/H/01/outside-spending?cycle=2026
/527/dark-money
/527/<ein>
```

Full sweep script: see `codex.md`.
