#!/bin/sh
# Container start-up for the app and pipeline images.
#
# 1. Create or upgrade the database schema (db/init/*.sql through `python -m app.db_tools init`).
#    The scripts are idempotent and run in one transaction, so this is safe on every start: an empty
#    database is initialised, an existing one is upgraded, data is kept. Waits for the database first.
#    Set DB_AUTO_INIT=false to skip (for example when an operator manages the schema).
# 2. Run the container's command (the web server, or the pipeline).
set -eu

if [ "${DB_AUTO_INIT:-true}" = "true" ]; then
  attempts="${DB_INIT_ATTEMPTS:-30}"
  n=0
  until python -m app.db_tools init > /tmp/db-init.log 2>&1; do
    n=$((n + 1))
    if [ "$n" -ge "$attempts" ]; then
      echo "aquamap: database initialisation failed after $n attempts:" >&2
      cat /tmp/db-init.log >&2
      exit 1
    fi
    echo "aquamap: waiting for the database ($n/$attempts)..." >&2
    sleep 2
  done
  echo "aquamap: $(tail -n 1 /tmp/db-init.log)"
fi

exec "$@"
