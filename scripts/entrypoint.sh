#!/bin/sh
set -e

case "$1" in
  api)
    # Stateless API: scale via replicas and/or UVICORN_WORKERS.
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
      --workers "${UVICORN_WORKERS:-1}" --proxy-headers
    ;;
  *)
    exec "$@"
    ;;
esac
