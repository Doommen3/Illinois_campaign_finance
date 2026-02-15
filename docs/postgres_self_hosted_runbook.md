# Self-Hosted PostgreSQL Runbook (Illinois Campaign Finance)

This runbook is for running PostgreSQL on the existing Hetzner VPS and migrating data from the current SQLite database.

## Scope and Expectations

- This runbook covers:
  - Installing and securing PostgreSQL on Ubuntu.
  - Creating DB/user/roles for this project.
  - Running SQLite -> PostgreSQL migration scripts in this repo.
  - Verifying migration integrity.
- This runbook does **not** claim that every SQLite-specific SQL pattern in the app is already ported.
  - You can migrate data now.
  - Full runtime cutover should be done after SQL compatibility validation in a staging pass.

---

## 1) Install PostgreSQL on the VPS

```bash
apt-get update
apt-get install -y postgresql postgresql-contrib
systemctl enable postgresql
systemctl start postgresql
systemctl status postgresql --no-pager
```

Check version:

```bash
sudo -u postgres psql -c "SELECT version();"
```

---

## 2) Create DB, user, and privileges

Use strong values for password and names as needed.

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE ilcf_app WITH LOGIN PASSWORD 'REPLACE_WITH_STRONG_PASSWORD';
CREATE DATABASE ilcf OWNER ilcf_app;
\c ilcf
GRANT ALL ON SCHEMA public TO ilcf_app;
ALTER ROLE ilcf_app SET search_path = public;
SQL
```

Connection smoke test:

```bash
psql "postgresql://ilcf_app:REPLACE_WITH_STRONG_PASSWORD@127.0.0.1:5432/ilcf" -c "SELECT current_database(), current_user;"
```

---

## 3) Recommended baseline config (single-node VPS)

Edit `postgresql.conf` (path depends on version, usually `/etc/postgresql/<version>/main/postgresql.conf`):

- `max_connections = 50`
- `shared_buffers = 1GB` (adjust for server RAM)
- `effective_cache_size = 4GB`
- `work_mem = 16MB`
- `maintenance_work_mem = 512MB`
- `wal_compression = on`
- `checkpoint_timeout = 15min`
- `max_wal_size = 4GB`

Edit `pg_hba.conf` to restrict local/known hosts only.

Then restart:

```bash
systemctl restart postgresql
```

---

## 4) Add project environment variables

In `/srv/illinois_campaign_finance/shared/.env` add:

```bash
DATABASE_URL=postgresql://ilcf_app:REPLACE_WITH_STRONG_PASSWORD@127.0.0.1:5432/ilcf
```

(Keep existing `DATABASE_PATH` until full app SQL compatibility cutover is complete.)

---

## 5) Install Python dependency

From app root:

```bash
/srv/illinois_campaign_finance/shared/venv/bin/pip install -r /srv/illinois_campaign_finance/app/requirements.txt
```

---

## 6) Run schema+data migration (SQLite -> Postgres)

From app root (`/srv/illinois_campaign_finance/app`):

```bash
/srv/illinois_campaign_finance/shared/venv/bin/python3 scripts/postgres/migrate_sqlite_to_postgres.py \
  --sqlite-path /srv/illinois_campaign_finance/shared/data/campaign_finance.db \
  --pg-dsn "postgresql://ilcf_app:REPLACE_WITH_STRONG_PASSWORD@127.0.0.1:5432/ilcf" \
  --truncate-first
```

Notes:
- First migration can be long on large tables.
- Re-running with `--truncate-first` gives idempotent refresh behavior.

---

## 7) Verify migration

```bash
/srv/illinois_campaign_finance/shared/venv/bin/python3 scripts/postgres/verify_sqlite_postgres_counts.py \
  --sqlite-path /srv/illinois_campaign_finance/shared/data/campaign_finance.db \
  --pg-dsn "postgresql://ilcf_app:REPLACE_WITH_STRONG_PASSWORD@127.0.0.1:5432/ilcf"
```

If mismatches are reported:
- Re-run migration for affected tables.
- Inspect table-specific defaults/types and constraints.

---

## 8) Backup strategy (required for self-hosted)

### Logical daily backup

```bash
pg_dump "postgresql://ilcf_app:REPLACE_WITH_STRONG_PASSWORD@127.0.0.1:5432/ilcf" \
  --format=custom --file /var/backups/ilcf_$(date +%F).dump
```

### Restore test (monthly)

```bash
createdb ilcf_restore_test
pg_restore -d ilcf_restore_test /var/backups/ilcf_YYYY-MM-DD.dump
```

---

## 9) Monitoring and operations checklist

- Disk usage (`df -h`) and Postgres data directory growth.
- Backup job success/failure alerts.
- Checkpoint/WAL growth.
- Slow query review via `pg_stat_statements`.
- Monthly patching (`apt-get upgrade postgresql*`) with maintenance window.

---

## 10) Full cutover readiness checklist

Before switching app runtime DB from SQLite to Postgres:

- [ ] All critical CLI commands tested against Postgres.
- [ ] Web routes tested against Postgres in staging.
- [ ] SQL compatibility pass complete (`INSERT OR REPLACE`, `PRAGMA`, rowid assumptions removed).
- [ ] Rollback plan documented (switch env var back to SQLite).
- [ ] Fresh backup taken just before cutover.

