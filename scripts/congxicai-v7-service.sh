#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Volumes/Aino Kishi/AI/workflows/恭喜发财"
REPORT_DIR="/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告"
LOG_DIR="${PROJECT_DIR}/logs/launchd"
HOST="127.0.0.1"
PORT="8000"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}"

probe_file="${REPORT_DIR}/.congxicai-launchd-write-probe"
if ! mkdir -p "${REPORT_DIR}" || ! printf '%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" > "${probe_file}"; then
  echo "ERROR: launchd cannot write to report archive: ${REPORT_DIR}" >&2
  exit 78
fi
rm -f "${probe_file}"

if [ ! -x "${PROJECT_DIR}/.venv/bin/python" ]; then
  echo "ERROR: missing Python runtime: ${PROJECT_DIR}/.venv/bin/python" >&2
  exit 78
fi

export PYTHONPATH="${PROJECT_DIR}/backend"
export CONGXI_PROJECT_DIR="${PROJECT_DIR}"

exec "${PROJECT_DIR}/.venv/bin/python" -m uvicorn app.main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --log-level info
