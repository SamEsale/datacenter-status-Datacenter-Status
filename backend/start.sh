#!/usr/bin/env bash
set -euo pipefail

echo "Running Alembic migrations..."
python -m alembic -c backend/alembic.ini upgrade head

echo "Starting API..."
exec uvicorn backend.app:app --host 0.0.0.0 --port "${PORT:-8000}"