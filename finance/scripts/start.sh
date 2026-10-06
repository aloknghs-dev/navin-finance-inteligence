#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn app.main:app --host "${NAVIN_HOST:-127.0.0.1}" --port "${PORT:-8000}" --no-access-log --proxy-headers --forwarded-allow-ips=127.0.0.1
