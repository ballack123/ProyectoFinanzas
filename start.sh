#!/usr/bin/env bash
set -o errexit

exec python -m gunicorn contabilidad.wsgi:application \
  --bind 0.0.0.0:${PORT:-8000} \
  --log-file -
