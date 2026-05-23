---
description: Sync production ilcf database to local via pg_dump | pg_restore. ~5-10 min on the 5.5GB DB. Clobbers local.
---

Sync the production `ilcf` PostgreSQL database to local. This **overwrites the local `ilcf` database**, so confirm with the user before starting.

Pre-flight checks (state these, then ask the user to confirm before continuing):

1. No other DB-writing operations should be running on the server right now — `pg_dump` against a live DB during `init-db` or `run-cross-matching` may produce an inconsistent snapshot. Ask the user to confirm the server is idle.
2. Local `ilcf` will be clobbered (`pg_restore --clean --if-exists`).
3. The dump file lands at `data/ilcf_prod.dump` and will overwrite any existing file there.

If the user confirms, run in two stages and report wall-clock for each:

Stage 1 — stream dump from prod to local file:

```bash
mkdir -p data
time ssh -i ~/.ssh/hetzner_ed25519 root@178.156.162.56 "pg_dump -Fc ilcf" > data/ilcf_prod.dump
ls -lh data/ilcf_prod.dump
```

Stage 2 — restore locally (parallel jobs for speed):

```bash
time pg_restore --clean --if-exists --no-owner --no-acl -j 4 -d ilcf data/ilcf_prod.dump
```

Stage 3 — sanity check row counts and warn if anything looks off:

```bash
psql ilcf -c "SELECT
  (SELECT COUNT(*) FROM isbe_committees)            AS committees,
  (SELECT COUNT(*) FROM isbe_condensed_receipts)    AS receipts,
  (SELECT COUNT(*) FROM analytics_donor_summary)    AS donor_summary,
  (SELECT COUNT(*) FROM irs527_orgs)                AS irs527_orgs;"
```

Expected magnitudes (as of mid-2026): committees ~34K, condensed receipts ~6.5M+, donor_summary 100K+, irs527_orgs 100K+. Flag if any are zero or off by 90%+ — that's the silent-truncation signature from the 2026-05-13 incident (see memory `pending_refresh_followups`).

If `pg_restore` reports errors that aren't just "object does not exist" (those are expected with `--clean --if-exists`), pause and report them — do not retry.
