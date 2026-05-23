#!/bin/bash
# Prod refresh chain — orchestrates the weekly data refresh on the Hetzner box.
#
# Usage (run on the server, inside tmux):
#   LOG=/srv/illinois_campaign_finance/shared/refresh_$(date +%Y%m%d_%H%M%S).log
#   nohup bash /srv/illinois_campaign_finance/app/scripts/refresh_prod_chain.sh "$LOG" > /dev/null 2>&1 &
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

log "CHAIN_START host=$(hostname) python=$PYTHON"

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
if ! $PYTHON run.py sync-fec-il-federal >> "$LOG" 2>&1; then
  fail "sync_fec_il_federal" 5
fi
log "STEP5_END sync_fec_il_federal"

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

log "CHAIN_END"
