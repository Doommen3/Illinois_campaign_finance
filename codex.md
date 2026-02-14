# Codex Deployment Runbook (Production)

This file documents the exact production deployment workflow for this project.

## Server Identity

- Host: `178.156.162.56`
- SSH user: `root`
- SSH key: `~/.ssh/hetzner_ed25519`
- App root: `/srv/illinois_campaign_finance/app`
- Shared venv:
  - Python: `/srv/illinois_campaign_finance/shared/venv/bin/python3`
  - Pip: `/srv/illinois_campaign_finance/shared/venv/bin/pip`
- Runtime env file: `/srv/illinois_campaign_finance/shared/.env`
- DB: `/srv/illinois_campaign_finance/shared/data/campaign_finance.db`
- Primary service: `ilcf-web.service`
- Legacy service (keep disabled): `illinois-web.service`

## Required Credentials / Access

- SSH key passphrase for `~/.ssh/hetzner_ed25519`.
- GitHub access for HTTPS remote:
  - Username: `Doommen3`
  - Personal Access Token with repo read access.
- Do not store tokens/passphrases in this repo.

## Preflight (Run First)

```bash
ssh -tt -i ~/.ssh/hetzner_ed25519 root@178.156.162.56
cd /srv/illinois_campaign_finance/app

hostname
date
whoami
git rev-parse --abbrev-ref HEAD
git status --short
/srv/illinois_campaign_finance/shared/venv/bin/python3 -V
/srv/illinois_campaign_finance/shared/venv/bin/pip -V
systemctl status ilcf-web.service --no-pager
systemctl status illinois-web.service --no-pager || true
systemctl cat ilcf-web.service
```

Expected:
- Branch `main`
- `ilcf-web.service` active
- `illinois-web.service` inactive/disabled

## Standard Deploy Steps

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
PIP=/srv/illinois_campaign_finance/shared/venv/bin/pip

# If git prompts for credentials, use GitHub username + PAT.
git fetch origin
git pull --ff-only origin main

$PIP install -r /srv/illinois_campaign_finance/app/requirements.txt
$PYTHON run.py init-db
```

## 527 Totals Repair / Backfill (Run When Needed)

Use this after 527 parsing/deployment changes or if `/527/` shows unexpected `$0` totals.

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3

# Preferred production file locations (choose existing path):
# 1) /srv/illinois_campaign_finance/app/Bulk_download/FullDataFile.txt
# 2) /srv/illinois_campaign_finance/shared/downloads/FullDataFile.txt

$PYTHON run.py repair-irs527-reports \
  --file /srv/illinois_campaign_finance/app/Bulk_download/FullDataFile.txt \
  --illinois-only \
  --replace-existing
```

Verification:

```bash
sqlite3 /srv/illinois_campaign_finance/shared/data/campaign_finance.db <<'SQL'
SELECT COUNT(*) AS reports, COUNT(DISTINCT ein) AS distinct_eins,
       SUM(COALESCE(total_contributions,0)) AS sum_contrib,
       SUM(COALESCE(total_expenditures,0)) AS sum_exp
FROM irs527_reports;
SQL
```

## Restart + Service Check

```bash
systemctl daemon-reload
systemctl restart ilcf-web.service
sleep 2
systemctl status ilcf-web.service --no-pager
journalctl -u ilcf-web.service --since '10 minutes ago' --no-pager | tail -n 200
```

## Health / Endpoint Validation

There is currently no explicit `/health` route (404 is expected).
Use `/` as liveness and then sweep key endpoints.

```bash
curl -sS -o /dev/null -w "ROOT_HTTP:%{http_code}\n" http://127.0.0.1:5000/
```

### Endpoint Sweep Script

Use DB-derived IDs to avoid false 404s:

```bash
DB=/srv/illinois_campaign_finance/shared/data/campaign_finance.db
CAND=$(sqlite3 "$DB" "SELECT candidate_id FROM bulk_candidates_clean WHERE candidate_id IS NOT NULL LIMIT 1;")
CMTE=$(sqlite3 "$DB" "SELECT committee_id_sbe FROM bulk_committee_candidate_links WHERE candidate_id='${CAND}' LIMIT 1;")
if [ -z "$CMTE" ]; then CMTE=$(sqlite3 "$DB" "SELECT committee_id_sbe FROM bulk_committees_clean WHERE committee_id_sbe IS NOT NULL LIMIT 1;"); fi
FEDCAND=$(sqlite3 "$DB" "SELECT fec_candidate_id FROM fec_candidate_match WHERE fec_candidate_id IS NOT NULL LIMIT 1;")
FEDEINCMTE=$(sqlite3 "$DB" "SELECT committee_id FROM fec_schedule_a_contributions WHERE committee_id IS NOT NULL LIMIT 1;")
DONORKEY=$(sqlite3 "$DB" "SELECT donor_entity_key FROM fec_schedule_a_contributions WHERE donor_entity_key IS NOT NULL AND donor_entity_key != '' LIMIT 1;")
LOBBY_ENTITY=$(sqlite3 "$DB" "SELECT entity_id FROM lobbying_entities LIMIT 1;")
LOBBY_CLIENT=$(sqlite3 "$DB" "SELECT client_id FROM lobbying_clients LIMIT 1;")
EIN527=$(sqlite3 "$DB" "SELECT ein FROM irs527_organizations LIMIT 1;")
SBEID=$(sqlite3 "$DB" "SELECT committee_id_sbe FROM bulk_committees_clean WHERE committee_id_sbe IS NOT NULL LIMIT 1;")

BASE=http://127.0.0.1:5000
for p in \
  "/" \
  "/search?q=Chicago" \
  "/compare" \
  "/candidates" \
  "/candidate-finance" \
  "/candidate-finance/${CAND}/${CMTE}/itemized" \
  "/candidate-finance/${CAND}/${CMTE}/itemized-expenditures" \
  "/d2-reconciliation" \
  "/d2-expenditures-reconciliation" \
  "/federal-finance/?cycle=2026" \
  "/federal-finance/candidates?cycle=2026" \
  "/federal-finance/networks?cycle=2026" \
  "/federal-finance/money-flow?cycle=2026" \
  "/federal-finance/donor-intelligence?cycle=2026" \
  "/federal-finance/influence?cycle=2026" \
  "/federal-finance/follow-the-money?cycle=2026" \
  "/federal-finance/geography?cycle=2026" \
  "/federal-finance/matching?cycle=2026" \
  "/federal-finance/${FEDCAND}?cycle=2026" \
  "/federal-finance/committees/${FEDEINCMTE}/receipts?cycle=2026" \
  "/federal-finance/donors/${DONORKEY}?cycle=2026" \
  "/analytics/" \
  "/analytics/networks" \
  "/analytics/relationships" \
  "/analytics/risk" \
  "/donors" \
  "/lobbying/" \
  "/lobbying/${LOBBY_ENTITY}" \
  "/lobbying/client/${LOBBY_CLIENT}" \
  "/committees/sbe/${SBEID}" \
  "/527/" \
  "/527/${EIN527}" \
  "/527/dark-money" \
  "/live-feed" \
  "/admin/" \
; do
  code=$(curl -sS -L -o /dev/null -w "%{http_code}" "$BASE$p")
  echo "$p|$code"
done
```

Pass criteria:
- No `5xx`
- Core pages return `200`

## Git Credential Safety Cleanup (If You Temporarily Stored Credentials)

If you enabled `credential.helper store` during deploy, clean up afterwards:

```bash
git config --global --unset credential.helper || true
rm -f /root/.git-credentials
```

## Known Pitfalls

- `rg` may not be installed on server; use `grep`/`find` fallback.
- If `git fetch/pull` prompts for GitHub auth, HTTPS credentials are missing.
- If `/federal-finance/committees/<id>/receipts` returns `404`, that committee may not have Schedule A rows for that cycle; choose `committee_id` from `fec_schedule_a_contributions`.
- Existing untracked files may be present in production (`wsgi.py.bak`, etc.); do not delete unless explicitly requested.
