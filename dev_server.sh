#!/usr/bin/env bash
# Dev launcher for kimodo-motion-api — run this ON THE GPU BOX (10.0.0.36).
#
# Gives a CLEAN, FAST, local store + AUTO-RELOAD on code changes, so as we build
# the stance library (new server endpoints), the server restarts itself whenever
# you `git pull` our changes. The web app's VITE_KIMODO_URL should point at this
# host:7862 (already does).
#
#   - Fresh local fs store (KIMODO_STORE_PATH) -> empty + fast (no GCS/HF).
#   - --reload -> uvicorn watches the .py files and restarts the worker on change.
#   - The model lazy-loads on the first /generate, so reloads are instant until
#     you generate again.
set -uo pipefail

export KIMODO_STORE_PATH="${KIMODO_STORE_PATH:-./.kimodo-dev-store}"   # clean local store
unset KIMODO_GCS_BUCKET 2>/dev/null || true                            # local fs, not GCS
PORT="${SERVER_PORT:-7862}"

echo "kimodo dev server  store=$KIMODO_STORE_PATH  port=$PORT  (auto-reload ON)"
exec uvicorn kimodo.scripts.run_motion_api:build_app --factory --reload \
  --host 0.0.0.0 --port "$PORT"
