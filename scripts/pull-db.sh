#!/usr/bin/env bash
# pull-db.sh — Pull production SQLite database to local machine.
#
# Usage:
#   ./scripts/pull-db.sh              # Full sync (first time or periodic refresh)
#   ./scripts/pull-db.sh --dry-run    # Show what would be transferred
#
# Prerequisites:
#   - SSH key at ~/.ssh/hetzner_ed25519 (passphrase-protected; ssh-agent or prompt)
#   - sshpass installed if running non-interactively (brew install sshpass)
#
# The script:
#   1. Checkpoints the WAL on the server (flushes pending writes to main DB file)
#   2. Uses rsync to transfer only changed blocks (~5.5GB DB, fast for incremental)
#   3. Backs up the existing local DB before overwriting
#
# Add to crontab for automated weekly sync:
#   0 3 * * 0 /path/to/scripts/pull-db.sh >> /tmp/pull-db.log 2>&1

set -euo pipefail

# --- Configuration -----------------------------------------------------------
REMOTE_HOST="178.156.162.56"
REMOTE_USER="root"
SSH_KEY="$HOME/.ssh/hetzner_ed25519"
REMOTE_DB="/srv/illinois_campaign_finance/shared/data/campaign_finance.db"
REMOTE_PYTHON="/srv/illinois_campaign_finance/shared/venv/bin/python3"

# Local paths — resolve relative to project root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_DB="${LOCAL_DB_PATH:-$PROJECT_ROOT/data/campaign_finance.db}"
LOCAL_BACKUP_DIR="$PROJECT_ROOT/data/backups"

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -i $SSH_KEY"
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

# --- Helper functions ---------------------------------------------------------
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

ssh_cmd() {
    # Use sshpass if SSHPASS is set (non-interactive), otherwise interactive prompt
    if [ -n "${SSHPASS:-}" ]; then
        sshpass -P 'passphrase' -e ssh $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" "$@"
    else
        ssh $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" "$@"
    fi
}

# --- Pre-flight checks --------------------------------------------------------
if [ ! -f "$SSH_KEY" ]; then
    echo "ERROR: SSH key not found at $SSH_KEY" >&2
    exit 1
fi

mkdir -p "$(dirname "$LOCAL_DB")"
mkdir -p "$LOCAL_BACKUP_DIR"

# --- Step 1: Checkpoint WAL on server ----------------------------------------
log "Checkpointing WAL on production database..."
ssh_cmd "$REMOTE_PYTHON -c \"
import sqlite3
conn = sqlite3.connect('$REMOTE_DB')
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.close()
print('WAL checkpoint complete')
\""

# --- Step 2: Backup existing local DB ----------------------------------------
if [ -f "$LOCAL_DB" ]; then
    BACKUP_NAME="campaign_finance_$(date '+%Y%m%d_%H%M%S').db"
    log "Backing up existing local DB to $LOCAL_BACKUP_DIR/$BACKUP_NAME"
    if [ "$DRY_RUN" = true ]; then
        log "  [dry-run] Would back up $LOCAL_DB"
    else
        cp "$LOCAL_DB" "$LOCAL_BACKUP_DIR/$BACKUP_NAME"
        # Keep only the 3 most recent backups to save disk space
        ls -t "$LOCAL_BACKUP_DIR"/campaign_finance_*.db 2>/dev/null | tail -n +4 | xargs rm -f 2>/dev/null || true
        log "  Backup saved. $(ls "$LOCAL_BACKUP_DIR"/campaign_finance_*.db 2>/dev/null | wc -l | tr -d ' ') backups retained."
    fi
else
    log "No existing local DB — first sync."
fi

# --- Step 3: Rsync the database ----------------------------------------------
log "Syncing production DB to local ($REMOTE_DB -> $LOCAL_DB)..."

RSYNC_OPTS=(
    -avz                          # archive, verbose, compress
    --progress                    # show transfer progress
    --partial                     # keep partial transfers for resume
    -e "ssh $SSH_OPTS"            # SSH transport with key
)

if [ "$DRY_RUN" = true ]; then
    RSYNC_OPTS+=(--dry-run)
    log "  [dry-run mode]"
fi

rsync "${RSYNC_OPTS[@]}" \
    "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DB" \
    "$LOCAL_DB"

# Also grab WAL/SHM if they exist (shouldn't after checkpoint, but safety)
rsync "${RSYNC_OPTS[@]}" \
    --ignore-missing-args \
    "$REMOTE_USER@$REMOTE_HOST:${REMOTE_DB}-wal" \
    "$REMOTE_USER@$REMOTE_HOST:${REMOTE_DB}-shm" \
    "$(dirname "$LOCAL_DB")/" 2>/dev/null || true

# --- Step 4: Verify local DB -------------------------------------------------
if [ "$DRY_RUN" = false ]; then
    log "Verifying local database integrity..."
    python3 -c "
import sqlite3, os
db = '$LOCAL_DB'
size_mb = os.path.getsize(db) / (1024*1024)
conn = sqlite3.connect(db)
result = conn.execute('PRAGMA integrity_check').fetchone()[0]
tables = conn.execute('SELECT COUNT(*) FROM sqlite_master WHERE type=\"table\"').fetchone()[0]
conn.close()
print(f'  Size: {size_mb:.1f} MB')
print(f'  Integrity: {result}')
print(f'  Tables: {tables}')
"
    log "Local DB sync complete: $LOCAL_DB"
else
    log "[dry-run] Sync preview complete."
fi
