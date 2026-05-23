---
description: Print the full production data refresh chain (commands only — user runs them on the server, since the chain takes 1-2 hours).
---

Generate the full production refresh chain for the user to run themselves on the server. **Do not execute autonomously** — per CLAUDE.md, long-running server tasks (>10 min) must be handed off to the user. This command is documentation, not execution.

Output structure:

1. **Estimated total runtime** — typically 60-120 min depending on data volume. Call out which step is the longest (`run-cross-matching --only all`, usually 30-60 min).
2. **Pre-flight check** — verify the user has a `tmux` or `screen` session ready (these steps must not die on SSH disconnect).
3. **The command chain**, formatted as one fenced block per step with a one-line description. Use the server command pattern from CLAUDE.md (`PYTHON=...venv/bin/python3`, `set -a; source .env; set +a`).
4. **Abort/recovery** notes — if step X fails, what to do.

The chain (use the hardened curl from CLAUDE.md "ISBE 403 workaround"):

a. SSH to server, change to app root, set up env vars
b. Capture current row counts in `Bulk_download/.row_counts_prev` for post-download sanity check
c. Download 12 ISBE bulk files using the hardened curl (retry/timeout/max-time flags)
d. Compare new row counts to previous; **abort if any file dropped >5%** — that's the silent-truncation signature (memory: `pending_refresh_followups`)
e. `python3 run.py sunshine-import --bulk-dir Bulk_download` (auto-runs `swap_bulk_to_isbe.py`)
f. `python3 run.py sync-fec-il-federal` (skip if FEC_API_KEY not set or `$ARGUMENTS` contains `skip-fec`)
g. `python3 run.py run-cross-matching --only all` — LONG (~30-60 min)
h. `python3 run.py refresh-analytics --with-snapshot` — ~5-10 min
i. `systemctl restart ilcf-web.service`
j. After service restart, ask the user to run `/endpoint-sweep` to verify

Recovery notes:
- If sunshine-import fails partway, no cleanup needed (idempotent upserts) — re-run
- If cross-matching fails partway, `--only all` is idempotent — re-run
- If analytics fails, check disk space (materialized views can be large)
- If service won't restart, `journalctl -u ilcf-web.service --since '5 min ago' --no-pager | tail -30`

After printing the chain, ask the user if they want it as a single copy-pasteable script (one big `tmux new -s refresh` command + heredoc) or as separate steps to run one-by-one.

Do not execute any of these commands yourself, even if the user pastes "go" — say "ready, please run it on the server" and stop. This is a documentation-generation command only.

If `$ARGUMENTS` is `skip-fec`, omit step (f). If `$ARGUMENTS` is `dry-run`, also print what each command would touch (rows expected, files written).
