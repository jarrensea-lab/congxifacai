#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Volumes/Aino Kishi/AI/workflows/恭喜发财"
WRAPPER_DIR="${HOME}/Library/Application Support/congxicai-v7"
LOG_DIR="${HOME}/Library/Logs/congxicai-v7"
AGENT_DIR="${HOME}/Library/LaunchAgents"
WRAPPER_PATH="${WRAPPER_DIR}/congxicai-v7-service.sh"
PLIST_PATH="${AGENT_DIR}/com.zhuchenyuan.congxicai-v7.plist"

mkdir -p "${WRAPPER_DIR}" "${LOG_DIR}" "${AGENT_DIR}"

cat > "${WRAPPER_PATH}" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Volumes/Aino Kishi/AI/workflows/恭喜发财"
REPORT_DIR="/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告"
LOG_DIR="${HOME}/Library/Logs/congxicai-v7"
HOST="127.0.0.1"
PORT="8000"

mkdir -p "${LOG_DIR}"

if ! cd "${PROJECT_DIR}"; then
  echo "ERROR: launchd cannot enter project directory: ${PROJECT_DIR}" >&2
  exit 126
fi

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
WRAPPER

chmod +x "${WRAPPER_PATH}"
cp "${PROJECT_DIR}/scripts/com.zhuchenyuan.congxicai-v7.plist" "${PLIST_PATH}"
plutil -lint "${PLIST_PATH}"

launchctl bootout "gui/$(id -u)" "${PLIST_PATH}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "${PLIST_PATH}"
launchctl kickstart -k "gui/$(id -u)/com.zhuchenyuan.congxicai-v7"
launchctl print "gui/$(id -u)/com.zhuchenyuan.congxicai-v7" | sed -n '1,90p'
