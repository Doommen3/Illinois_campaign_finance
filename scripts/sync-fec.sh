#!/usr/bin/env bash
# sync-fec.sh — Weekly FEC data sync for Illinois Campaign Finance Tracker
# Intended to run via systemd timer or cron (e.g. Sundays at 3 AM UTC).
#
# Usage:  bash scripts/sync-fec.sh
# Env:    Expects FEC_API_KEY in the env-file or exported before invocation.

set -euo pipefail

# --- Configuration -----------------------------------------------------------
APP_DIR="${APP_DIR:-/srv/illinois_campaign_finance/app}"
SHARED_DIR="${SHARED_DIR:-/srv/illinois_campaign_finance/shared}"
ENV_FILE="${SHARED_DIR}/.env"
LOG_DIR="${SHARED_DIR}/logs"
LOCK_FILE="${SHARED_DIR}/sync-fec.lock"
CYCLE="${FEC_CYCLE:-2026}"
MAX_CALLS="${FEC_MAX_CALLS:-900}"

# --- Helpers -----------------------------------------------------------------
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

cleanup() {
    rm -f "$LOCK_FILE"
    log "Lock released."
}

# --- Pre-flight --------------------------------------------------------------
mkdir -p "$LOG_DIR"

if [ -f "$LOCK_FILE" ]; then
    log "ERROR: Lock file exists ($LOCK_FILE). Another sync may be running."
    log "If this is stale, remove it manually: rm $LOCK_FILE"
    exit 1
fi

trap cleanup EXIT
echo $$ > "$LOCK_FILE"
log "Lock acquired (PID $$)."

# Source env file for FEC_API_KEY if present
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

LOG_FILE="${LOG_DIR}/sync-fec-$(date '+%Y%m%d-%H%M%S').log"
log "Starting FEC sync (cycle=$CYCLE, max_calls=$MAX_CALLS). Log: $LOG_FILE"

# --- Step 1: Sync FEC data ---------------------------------------------------
log "Step 1/3: sync-fec-il-federal"
python run.py sync-fec-il-federal \
    --cycle "$CYCLE" \
    --contributor-state IL \
    --max-calls "$MAX_CALLS" \
    >> "$LOG_FILE" 2>&1

# --- Step 2: Rebuild donor identities ----------------------------------------
log "Step 2/3: rebuild-fec-donor-identities"
python run.py rebuild-fec-donor-identities \
    >> "$LOG_FILE" 2>&1

# --- Step 3: Refresh analytics -----------------------------------------------
log "Step 3/3: refresh-analytics --with-snapshot"
python run.py refresh-analytics --with-snapshot \
    >> "$LOG_FILE" 2>&1

log "FEC sync complete. Full log: $LOG_FILE"
