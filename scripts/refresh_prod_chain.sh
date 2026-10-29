#!/bin/bash
# Prod refresh chain — orchestrates the weekly data refresh on the Hetzner box.
# Runs nightly under scripts/nightly_refresh.sh (systemd ilcf-nightly-refresh.timer), which
# records the run in data_refresh_runs; the usage below is the by-hand form.
#
# Usage (run on the server, inside tmux):
#   LOG=/srv/illinois_campaign_finance/shared/refresh_$(date +%Y%m%d_%H%M%S).log
#   nohup bash /srv/illinois_campaign_finance/app/scripts/refresh_prod_chain.sh "$LOG" > /dev/null 2>&1 &
# Set SKIP_RESTART=1 to leave the web service untouched at the end.
#
# Each step emits STEP_N_START/STEP_N_END markers so a Monitor (or `tail -f`)
# can react per-step. Failures emit STEP_FAILED + CHAIN_FAILED and exit non-zero.
# The companion sanity-check (`scripts/check_row_counts.py`) is called by step 3.
#
# Total runtime: ~60-120 min depending on ISBE download speed and FEC data volume.
LOG="$1"
if [ -z "$LOG" ]; then
  echo "usage: $0 <log_path>" >&2; exit 2
fi

cd /srv/illinois_campaign_finance/app
set -a; source /srv/illinois_campaign_finance/shared/.env; set +a
PYTHON=/srv/illinois_campaign_finance/shared/venv/bin/python3

ts()   { date '+%Y-%m-%d %H:%M:%S'; }
log()  { echo "[$(ts)] $*" | tee -a "$LOG"; }
fail() { log "STEP_FAILED $1 (exit=$2)"; log "CHAIN_FAILED"; exit "$2"; }

trap 'rc=$?; [ $rc -ne 0 ] && echo "[$(ts)] CHAIN_TRAP exit=$rc" >> "$LOG"' EXIT

log "CHAIN_START host=$(hostname) python=$PYTHON run_id=${REFRESH_RUN_ID:-none}"

log "STEP1_START snapshot_row_counts"
mkdir -p Bulk_download
> Bulk_download/.row_counts_prev
for f in Bulk_download/*.txt; do
  [ -f "$f" ] && printf "%s\t%s\n" "$(basename "$f")" "$(wc -l < "$f")" >> Bulk_download/.row_counts_prev
done
log "STEP1_END baselines=$(wc -l < Bulk_download/.row_counts_prev)"

log "STEP2_START download_isbe"
for f in Candidates.txt CanElections.txt Committees.txt Officers.txt PrevOfficers.txt D2Totals.txt Receipts.txt Expenditures.txt Investments.txt FiledDocs.txt CmteCandidateLinks.txt CmteOfficerLinks.txt; do
  if ! curl -fsS --retry 5 --retry-all-errors --connect-timeout 30 --max-time 1800 -A "Mozilla/5.0" -o "Bulk_download/$f" "https://elections.il.gov/campaigndisclosuredatafiles/$f" 2>>"$LOG"; then
    fail "download_$f" 2
  fi
  log "  downloaded $f size=$(stat -c%s Bulk_download/$f)"
done
log "STEP2_END download_isbe files=12"

log "STEP3_START sanity_check"
if ! python3 scripts/check_row_counts.py >> "$LOG" 2>&1; then
  fail "sanity_check" 3
fi
log "STEP3_END sanity_check OK"

log "STEP4_START sunshine_import"
if ! $PYTHON run.py sunshine-import --bulk-dir Bulk_download >> "$LOG" 2>&1; then
  fail "sunshine_import" 4
fi
log "STEP4_END sunshine_import"

log "STEP5_START sync_fec_il_federal"
# --refresh-cache is required: without it the sync replays the payload cache and refreshes nothing (CLAUDE.md "FEC silent no-op").
if ! $PYTHON run.py sync-fec-il-federal --refresh-cache >> "$LOG" 2>&1; then
  fail "sync_fec_il_federal" 5
fi
log "STEP5_END sync_fec_il_federal"

# City of Chicago Socrata pull (non-fatal; needs SOCRATA_APP_TOKEN in shared/.env).
if [ -n "${SOCRATA_APP_TOKEN:-}" ]; then
  log "STEP5B_START import_chicago_phase1"
  if ! $PYTHON run.py import-chicago-phase1 --app-token "$SOCRATA_APP_TOKEN" --upsert >> "$LOG" 2>&1; then
    log "STEP5B_WARN import_chicago_phase1 failed (non-fatal; Chicago tables keep their previous rows)"
  fi
  log "STEP5B_END import_chicago_phase1"
else
  log "STEP5B_SKIPPED import_chicago_phase1 (SOCRATA_APP_TOKEN not set)"
fi

log "STEP6_START run_cross_matching (long ~30-60min)"
if ! $PYTHON run.py run-cross-matching --only all >> "$LOG" 2>&1; then
  fail "run_cross_matching" 6
fi
log "STEP6_END run_cross_matching"

log "STEP7_START refresh_analytics"
if ! $PYTHON run.py refresh-analytics --with-snapshot >> "$LOG" 2>&1; then
  fail "refresh_analytics" 7
fi
log "STEP7_END refresh_analytics"

# refresh-analytics flushed the route caches: warm them before readers hit cold pages (load-time plan L8).
log "STEP7B_START warm_caches"
if ! $PYTHON scripts/warm_caches.py >> "$LOG" 2>&1; then
  log "STEP7B_WARN warm_caches failed (pages build on first request instead)"
fi
log "STEP7B_END warm_caches"

if [ "${SKIP_RESTART:-0}" = "1" ]; then
  # SKIP_RESTART=1 keeps the site offline (e.g. a refresh that must land before a deploy goes live).
  log "STEP8_SKIPPED restart_service (SKIP_RESTART=1)"
else
log "STEP8_START restart_service"
systemctl restart ilcf-web.service
sleep 5
if ! systemctl is-active ilcf-web.service > /dev/null 2>&1; then
  fail "service_not_active" 8
fi
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 60 http://127.0.0.1:5000/ 2>>"$LOG")
if [ "$HTTP" != "200" ]; then
  fail "smoke_test_http_$HTTP" 9
fi
log "STEP8_END restart_service http=$HTTP"
fi

log "CHAIN_END"
