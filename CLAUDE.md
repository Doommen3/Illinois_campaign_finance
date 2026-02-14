# CLAUDE.md - Illinois Campaign Finance Intelligence Platform

## Project Overview

Full-stack political finance transparency platform that aggregates, analyzes, and visualizes Illinois campaign finance data from state (ISBE) and federal (FEC) sources, plus IL SOS lobbying data and IRS 527 political organization filings. ~200K lines of code, 53-table SQLite schema, 5.5GB+ database.

## Quick Start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run.py init-db
python run.py runserver --port 5000
```

## Architecture

- **Backend**: Python 3.12, Flask 3.0, SQLite (WAL mode)
- **Frontend**: Jinja2 templates, vanilla JS, CSS-only tabbed interfaces
- **Scraping**: Playwright async with ASP.NET ViewState handling
- **CLI**: Click framework via `run.py`
- **Testing**: pytest (20+ test modules)

### Key Directories

| Path | Purpose |
|------|---------|
| `cli/commands.py` | All CLI commands |
| `database/schema.sql` | 53-table DDL schema |
| `database/models.py` | Dataclass-based ORM |
| `database/analytics.py` | Network, anomaly, concentration analytics |
| `database/cross_matching.py` | Jaccard-based cross-dataset matching engine |
| `database/irs527_loader.py` | IRS 527 FullDataFile parser |
| `database/federal_fec.py` | FEC API integration |
| `webapp/routes/` | 15 Flask route modules |
| `webapp/templates/` | 50+ Jinja2 templates |
| `tests/` | pytest suite |

### Data Sources

1. **ISBE** (state) - Committees, candidates, receipts, expenditures, D-2 reports
2. **FEC** (federal) - IL candidates, Schedule A/B/E contributions/disbursements
3. **IL SOS** - Lobbying entities and clients
4. **IRS 527** - Political org registrations, reports, directors, expenditures

### Cross-Matching Engine

All matching uses Jaccard similarity with sparse inverted-index candidate generation (`database/cross_matching.py`). Current matches:
- Lobbying client -> donor (0.80 threshold)
- Lobbying client/entity -> expenditure payee (0.80)
- 527 org -> IL committee (0.80)
- 527 expenditure recipient -> committee/candidate (0.80)
- 527 director -> donor (0.80)
- Lobbying client -> 527 org (0.80)
- Federal donor -> local donor (zip+state+name)

## Testing

- Run all tests: `pytest -q`
- Run specific module: `pytest -q tests/test_cross_matching.py`
- Run fast pre-deploy subset: `pytest -q tests/test_federal_fec.py tests/test_lobbying_routes.py tests/test_uiux_improvements.py tests/test_webapp.py::TestWebApp::test_federal_finance_page_loads_with_synced_rows`
- After generating test data, always verify NOT NULL constraints and required fields match the actual schema.
- Run tests after every implementation change before presenting work as complete.

## Code Style

- Primary stack: Python (backend), HTML/CSS/JavaScript (frontend).
- Always use SQLite-safe threading patterns (check_same_thread=False or connection-per-request) in Flask apps.
- When writing shell commands for the user to copy, ensure they are single-line or properly escaped.
- Follow existing patterns: dataclass models, Jinja2 templates extending `base.html`, route blueprints.
- Use `_table_exists()` checks before querying cross-matching tables (they may not exist in fresh DBs).
- Use `_scalar()` helper for safe single-value queries with defaults.
- Prefer `INSERT OR REPLACE` for idempotent upserts.
- Use chunked batch inserts (`_chunked()` helper) for large data loads.

## Deployment

- When asked about deployment, always assume REMOTE/PRODUCTION server unless explicitly told otherwise.
- Always verify actual server paths, service names, and directory structures before generating deployment commands — never assume defaults.
- Use `python3` (not `python`) in all server scripts and systemd files.

### Server Details

| Item | Value |
|------|-------|
| Host | 178.156.162.56 |
| SSH | `ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56` |
| App root | `/srv/illinois_campaign_finance/app` |
| Python | `/srv/illinois_campaign_finance/shared/venv/bin/python3` |
| Pip | `/srv/illinois_campaign_finance/shared/venv/bin/pip` |
| Env file | `/srv/illinois_campaign_finance/shared/.env` |
| DB | `/srv/illinois_campaign_finance/shared/data/campaign_finance.db` |
| Web service | `ilcf-web.service` |
| Legacy service (keep disabled) | `illinois-web.service` |

### Deploy Steps

1. Run local tests: `pytest -q`
2. Push to main: `git push origin main`
3. SSH to server and pull:
   ```bash
   cd /srv/illinois_campaign_finance/app && git pull --ff-only origin main
   ```
4. Install deps:
   ```bash
   /srv/illinois_campaign_finance/shared/venv/bin/pip install -r /srv/illinois_campaign_finance/app/requirements.txt
   ```
5. Init DB (schema migrations):
   ```bash
   /srv/illinois_campaign_finance/shared/venv/bin/python3 run.py init-db
   ```
6. Refresh analytics (if needed):
   ```bash
   /srv/illinois_campaign_finance/shared/venv/bin/python3 run.py refresh-analytics --with-snapshot
   ```
7. Restart service:
   ```bash
   systemctl restart ilcf-web.service
   ```
8. Verify:
   ```bash
   systemctl status ilcf-web.service --no-pager
   curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/
   ```

### Server Command Pattern

```bash
cd /srv/illinois_campaign_finance/app
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3
PIP=/srv/illinois_campaign_finance/shared/venv/bin/pip
set -a; source /srv/illinois_campaign_finance/shared/.env; set +a
$PYTHON run.py <command>
```

### GitHub

- Username: Doommen3
- Remote: HTTPS (credentials needed for push/pull on server)

### Endpoint Sweep

After deploy, sweep key routes (see `codex.md` for full endpoint sweep script). Critical routes:
- `/`, `/search?q=Chicago`, `/candidates`
- `/federal-finance/`, `/analytics/`, `/lobbying/`, `/527/`
- `/527/dark-money`, `/527/<ein>`

## Common Operations

### Import IRS 527 data
```bash
python run.py import-irs527 --file Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt --illinois-only
```

### Run cross-matching
```bash
python run.py run-cross-matching --only all
```

### Refresh analytics
```bash
python run.py refresh-analytics --with-snapshot
```

## Database Notes

- 53-table schema in `database/schema.sql`
- WAL mode enabled for concurrent reads
- Materialized views refreshed via `refresh-analytics` CLI command
- Cross-matching results stored in dedicated match tables (e.g., `irs527_director_donor_matches`)
- All IRS 527 tables prefixed with `irs527_`
