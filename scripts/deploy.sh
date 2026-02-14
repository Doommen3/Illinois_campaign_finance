#!/usr/bin/env bash
# deploy.sh — Deploy latest code to production server.
#
# Usage:
#   ./scripts/deploy.sh                # Full deploy (pull, deps, init-db, restart, sweep)
#   ./scripts/deploy.sh --skip-db      # Skip init-db (no schema changes)
#   ./scripts/deploy.sh --dry-run      # Show what would happen without executing
#
# Prerequisites:
#   - SSH key loaded in ssh-agent (macOS Keychain handles this)
#   - GitHub deploy PAT stored in macOS Keychain
#     (service: illinois-campaign-finance-deploy-pat, account: github-deploy)
#
# The script:
#   1. Retrieves the GitHub PAT from macOS Keychain
#   2. SSHes to server and pulls latest code (PAT used transiently, never stored on server)
#   3. Installs pip dependencies
#   4. Runs init-db for schema migrations (unless --skip-db)
#   5. Restarts the web service
#   6. Sweeps key endpoints and reports status

set -euo pipefail

# --- Configuration -----------------------------------------------------------
REMOTE_HOST="178.156.162.56"
REMOTE_USER="root"
SSH_KEY="$HOME/.ssh/hetzner_ed25519"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -i $SSH_KEY"

APP_ROOT="/srv/illinois_campaign_finance/app"
PYTHON="/srv/illinois_campaign_finance/shared/venv/bin/python3"
PIP="/srv/illinois_campaign_finance/shared/venv/bin/pip"
ENV_FILE="/srv/illinois_campaign_finance/shared/.env"
SERVICE="ilcf-web.service"

GITHUB_USER="Doommen3"
GITHUB_REPO="Illinois_campaign_finance"
KEYCHAIN_SERVICE="illinois-campaign-finance-deploy-pat"
KEYCHAIN_ACCOUNT="github-deploy"

SKIP_DB=false
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --skip-db) SKIP_DB=true ;;
        --dry-run) DRY_RUN=true ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

# --- Helper functions ---------------------------------------------------------
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
fail() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; exit 1; }

ssh_cmd() {
    ssh $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" "$@"
}

run_or_echo() {
    if [ "$DRY_RUN" = true ]; then
        log "[dry-run] $*"
    else
        "$@"
    fi
}

# --- Step 1: Retrieve PAT from macOS Keychain --------------------------------
log "Retrieving GitHub PAT from macOS Keychain..."
PAT=$(security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "$KEYCHAIN_SERVICE" -w 2>/dev/null) \
    || fail "Could not retrieve PAT from Keychain. Store it with:
  security add-generic-password -a '$KEYCHAIN_ACCOUNT' -s '$KEYCHAIN_SERVICE' -w '<PAT>' -U"

log "PAT retrieved (${#PAT} chars)"

# --- Step 2: Pull latest code on server --------------------------------------
log "Pulling latest code on server..."
run_or_echo ssh_cmd bash -s <<REMOTE_SCRIPT
set -euo pipefail
cd $APP_ROOT

# Temporarily set authenticated remote URL
git remote set-url origin "https://${PAT}@github.com/${GITHUB_USER}/${GITHUB_REPO}.git"

# Pull
git pull --ff-only origin main
PULL_STATUS=\$?

# Immediately restore clean remote URL (PAT never persists on server)
git remote set-url origin "https://github.com/${GITHUB_USER}/${GITHUB_REPO}.git"

exit \$PULL_STATUS
REMOTE_SCRIPT

log "Code pulled successfully."

# --- Step 3: Install dependencies --------------------------------------------
log "Installing pip dependencies..."
run_or_echo ssh_cmd "$PIP install -q -r $APP_ROOT/requirements.txt"
log "Dependencies installed."

# --- Step 4: Run init-db (schema migrations) ----------------------------------
if [ "$SKIP_DB" = true ]; then
    log "Skipping init-db (--skip-db flag)."
else
    log "Running init-db (schema migrations)..."
    run_or_echo ssh_cmd "cd $APP_ROOT && set -a && source $ENV_FILE && set +a && $PYTHON run.py init-db"
    log "Schema migrations complete."
fi

# --- Step 5: Restart web service ----------------------------------------------
log "Restarting $SERVICE..."
run_or_echo ssh_cmd "systemctl restart $SERVICE && sleep 2"
log "Service restarted."

# --- Step 6: Verify service health --------------------------------------------
log "Checking service status..."
ssh_cmd "systemctl is-active $SERVICE" || fail "Service is not active!"
log "Service is active."

# --- Step 7: Endpoint sweep ---------------------------------------------------
log "Sweeping key endpoints..."
ENDPOINTS=(
    "/"
    "/search?q=Chicago"
    "/candidates"
    "/federal-finance/"
    "/analytics/"
    "/lobbying/"
    "/527/"
    "/527/dark-money"
    "/person-intelligence"
)

FAILED=0
for endpoint in "${ENDPOINTS[@]}"; do
    STATUS=$(ssh_cmd "curl -sS -o /dev/null -w '%{http_code}' 'http://127.0.0.1:5000${endpoint}'" 2>/dev/null)
    if [ "$STATUS" = "200" ]; then
        log "  $endpoint -> $STATUS OK"
    else
        log "  $endpoint -> $STATUS FAIL"
        FAILED=$((FAILED + 1))
    fi
done

if [ "$FAILED" -gt 0 ]; then
    log "WARNING: $FAILED endpoint(s) returned non-200 status."
else
    log "All endpoints healthy."
fi

log "Deploy complete."
