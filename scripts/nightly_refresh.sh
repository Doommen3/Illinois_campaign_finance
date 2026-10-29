#!/bin/bash
# Nightly data refresh (systemd ilcf-nightly-refresh.timer, 03:00 America/Chicago).
#
# Wraps scripts/refresh_prod_chain.sh (ISBE download -> row-count gate -> sunshine-import ->
# sync-fec-il-federal --refresh-cache -> Chicago Socrata -> cross-matching -> refresh-analytics ->
# warm caches -> restart) with:
#   - an flock on shared/refresh.lock (scripts/manual_load.sh takes the same lock), so two
#     refreshes never overlap;
#   - a wait of up to FEC_LOCK_WAIT_MINUTES (default 60) while the Sunday FEC timer's
#     shared/sync-fec.lock exists (that script uses a PID file, not flock);
#   - a run record in data_refresh_runs (snapshot before/after + diff) via scripts/data_refresh_cli.py;
#   - one log per run: shared/logs/nightly-<stamp>.log (the chain's STEP<N>_START/END lines included).
# The 05:00 check (scripts/nightly_check.py) reads the run record and the log; this script runs the
# check again when a nightly run finishes, so a run that was still going at 05:00 gets its final report.
#
# Usage: bash /srv/illinois_campaign_finance/app/scripts/nightly_refresh.sh
# Env:   REFRESH_KIND=manual for a hand-started run; SKIP_RESTART=1 passes through to the chain.
set -u

APP_DIR="${APP_DIR:-/srv/illinois_campaign_finance/app}"
SHARED_DIR="${SHARED_DIR:-/srv/illinois_campaign_finance/shared}"
PYTHON="${PYTHON:-${SHARED_DIR}/venv/bin/python3}"
LOCK_FILE="${SHARED_DIR}/refresh.lock"
FEC_LOCK="${SHARED_DIR}/sync-fec.lock"
FEC_LOCK_WAIT_MINUTES="${FEC_LOCK_WAIT_MINUTES:-60}"
mkdir -p "${SHARED_DIR}/logs"
LOG="${SHARED_DIR}/logs/nightly-$(date '+%Y%m%d-%H%M%S').log"
RUN_DAY="$(TZ=America/Chicago date '+%Y-%m-%d')"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "NIGHTLY_SKIPPED another refresh holds $LOCK_FILE"
    exit 1
fi

cd "$APP_DIR" || exit 1
if [ -f "${SHARED_DIR}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${SHARED_DIR}/.env"
    set +a
fi

RUN_ID=$("$PYTHON" scripts/data_refresh_cli.py start --kind "${REFRESH_KIND:-nightly}" --log "$LOG" 2>>"$LOG")
if [ -z "$RUN_ID" ]; then
    log "NIGHTLY_WARN could not open a run record; refreshing without one (the 05:00 check will report MISSING)"
fi
log "NIGHTLY_START run_id=${RUN_ID:-none} log=$LOG"

waited=0
while [ -f "$FEC_LOCK" ] && [ "$waited" -lt "$FEC_LOCK_WAIT_MINUTES" ]; do
    [ "$waited" -eq 0 ] && log "NIGHTLY_WAIT $FEC_LOCK exists (Sunday FEC sync running); waiting up to ${FEC_LOCK_WAIT_MINUTES} min"
    sleep 60
    waited=$((waited + 1))
done

if [ -f "$FEC_LOCK" ]; then
    log "NIGHTLY_FAILED $FEC_LOCK still present after ${FEC_LOCK_WAIT_MINUTES} min"
    rc=20  # the chain's own exit codes are 2-11
else
    REFRESH_RUN_ID="${RUN_ID:-}" bash "${APP_DIR}/scripts/refresh_prod_chain.sh" "$LOG"
    rc=$?
fi

if [ -n "$RUN_ID" ]; then
    "$PYTHON" scripts/data_refresh_cli.py finish "$RUN_ID" "$rc" >> "$LOG" 2>&1 || log "NIGHTLY_WARN could not close run $RUN_ID"
fi
if [ -n "$RUN_ID" ] && [ "${REFRESH_KIND:-nightly}" = "nightly" ]; then
    "$PYTHON" scripts/nightly_check.py --report-dir "${SHARED_DIR}/refresh_runs" --date "$RUN_DAY" >> "$LOG" 2>&1 \
        || log "NIGHTLY_WARN the check reports a problem (see ${SHARED_DIR}/refresh_runs/${RUN_DAY}.md)"
fi
log "NIGHTLY_END run_id=${RUN_ID:-none} exit=$rc"
exit "$rc"
