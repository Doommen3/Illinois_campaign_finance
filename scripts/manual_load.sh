#!/bin/bash
# Hand load of a dataset with no automated download, recorded in data_manual_loads so /data-status
# and the 05:00 report know how old it is.
#
# Usage (on the server; files are copied from the laptop's data/manual/<dataset>/ into
# shared/manual/<dataset>/ first, see docs/runbooks/scheduled_refresh.md):
#   bash scripts/manual_load.sh lobbying /srv/illinois_campaign_finance/shared/manual/lobbying/<extract>.csv
#   bash scripts/manual_load.sh irs527   /srv/illinois_campaign_finance/shared/manual/irs527/<FullDataFile>.txt
#   bash scripts/manual_load.sh openbook -     # a scrape, no file
#   bash scripts/manual_load.sh fec_b -        # the Schedule B backfill (scripts/fec-schedule-b-catchup.sh)
# Optional third argument: the file's data date (YYYY-MM-DD); default is the first YYYYMMDD in the name.
# Takes shared/refresh.lock, so it never overlaps the nightly refresh.
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/illinois_campaign_finance/app}"
SHARED_DIR="${SHARED_DIR:-/srv/illinois_campaign_finance/shared}"
PYTHON="${PYTHON:-${SHARED_DIR}/venv/bin/python3}"

usage() { echo "usage: $0 <lobbying|irs527|openbook|fec_b> <file|-> [YYYY-MM-DD]" >&2; exit 2; }
[ $# -ge 2 ] || usage
dataset="$1"
file="$2"
file_date="${3:-}"

if [ "$file" != "-" ]; then
    [ -f "$file" ] || { echo "no such file: $file" >&2; exit 2; }
    file="$(realpath "$file")"
fi

case "$dataset" in
    lobbying) [ "$file" != "-" ] || usage; cmd=("$PYTHON" run.py import-lobbying --file "$file") ;;
    irs527)   [ "$file" != "-" ] || usage; cmd=("$PYTHON" run.py import-irs527 --file "$file" --illinois-only) ;;
    openbook) cmd=("$PYTHON" run.py import-openbook-batch --generate-seeds) ;;
    fec_b)    cmd=(bash scripts/fec-schedule-b-catchup.sh) ;;
    *) usage ;;
esac

exec 9>"${SHARED_DIR}/refresh.lock"
flock -n 9 || { echo "another refresh holds ${SHARED_DIR}/refresh.lock; try again later" >&2; exit 1; }

cd "$APP_DIR"
if [ -f "${SHARED_DIR}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${SHARED_DIR}/.env"
    set +a
fi

before=$("$PYTHON" scripts/data_refresh_cli.py count "$dataset")
"${cmd[@]}"
record=("$PYTHON" scripts/data_refresh_cli.py record-manual "$dataset" "$file" --rows-before "$before")
[ -n "$file_date" ] && record+=(--file-date "$file_date")
"${record[@]}"
