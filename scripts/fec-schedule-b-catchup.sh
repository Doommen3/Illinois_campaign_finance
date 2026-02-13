#!/usr/bin/env bash
# fec-schedule-b-catchup.sh — Hourly Schedule B backfill for federal committee disbursements.
#
# Usage:  bash scripts/fec-schedule-b-catchup.sh
# Env:    Expects FEC_API_KEY in /srv/illinois_campaign_finance/shared/.env

set -euo pipefail

APP_DIR="${APP_DIR:-/srv/illinois_campaign_finance/app}"
SHARED_DIR="${SHARED_DIR:-/srv/illinois_campaign_finance/shared}"
VENV_DIR="${VENV_DIR:-/srv/illinois_campaign_finance/shared/venv}"
ENV_FILE="${SHARED_DIR}/.env"
LOG_DIR="${SHARED_DIR}/logs"
LOCK_FILE="${SHARED_DIR}/fec-schedule-b-catchup.lock"

CYCLE="${FEC_CYCLE:-2026}"
MAX_CALLS="${FEC_SCHEDULE_B_MAX_CALLS:-1000}"
MAX_PAGES_PER_COMMITTEE="${FEC_SCHEDULE_B_MAX_PAGES_PER_COMMITTEE:-25}"
PRINCIPAL_ONLY="${FEC_SCHEDULE_B_PRINCIPAL_ONLY:-false}"
PYTHON="${VENV_DIR}/bin/python3"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

cleanup() {
    rm -f "$LOCK_FILE"
    log "Lock released."
}

mkdir -p "$LOG_DIR"

if [ -f "$LOCK_FILE" ]; then
    log "ERROR: Lock file exists ($LOCK_FILE). Another catch-up may be running."
    log "If this is stale, remove it manually: rm $LOCK_FILE"
    exit 1
fi

trap cleanup EXIT
echo $$ > "$LOCK_FILE"
log "Lock acquired (PID $$)."

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
    log "Sourced $ENV_FILE"
fi

if [ -z "${FEC_API_KEY:-}" ]; then
    log "ERROR: FEC_API_KEY is not set. Add it to $ENV_FILE or export it."
    exit 1
fi

cd "$APP_DIR"

if [ ! -x "$PYTHON" ]; then
    log "WARN: venv not found at $VENV_DIR — falling back to system python3"
    PYTHON="python3"
fi

LOG_FILE="${LOG_DIR}/fec-schedule-b-catchup-$(date '+%Y%m%d-%H%M%S').log"
log "Starting Schedule B catch-up (cycle=$CYCLE, max_calls=$MAX_CALLS, python=$PYTHON). Log: $LOG_FILE"

PRINCIPAL_ARGS=()
if [ "${PRINCIPAL_ONLY,,}" = "true" ] || [ "${PRINCIPAL_ONLY}" = "1" ]; then
    PRINCIPAL_ARGS+=(--principal-only)
fi

"$PYTHON" run.py backfill-fec-schedule-b \
    --cycle "$CYCLE" \
    --max-calls "$MAX_CALLS" \
    --max-pages-per-committee "$MAX_PAGES_PER_COMMITTEE" \
    --refresh-cache \
    "${PRINCIPAL_ARGS[@]}" \
    >> "$LOG_FILE" 2>&1

log "Schedule B catch-up complete. Full log: $LOG_FILE"
