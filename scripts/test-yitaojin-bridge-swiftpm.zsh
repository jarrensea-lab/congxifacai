#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
OUTPUT_FILE="${1:?generated-source path is required}"
EXPECTED_MARKER="74 Swift bridge checks passed"

export CONGXI_YITAOJIN_ENABLED=false
export CONGXI_YITAOJIN_WRITE_ENABLED=false

set +e
SELF_TEST_OUTPUT="$(/bin/zsh "${SCRIPT_DIR}/test-yitaojin-bridge.sh" 2>&1)"
SELF_TEST_STATUS=$?
set -e

print -r -- "${SELF_TEST_OUTPUT}"
if (( SELF_TEST_STATUS != 0 )); then
  print -u2 -- "Existing Yitaojin bridge safety self-tests failed"
  exit "${SELF_TEST_STATUS}"
fi
if [[ "${SELF_TEST_OUTPUT}" != *"${EXPECTED_MARKER}"* ]]; then
  print -u2 -- "Existing Yitaojin bridge safety success marker was missing"
  exit 1
fi

mkdir -p "${OUTPUT_FILE:h}"
GENERATED_TMP="$(mktemp "${OUTPUT_FILE:h}/.YitaojinSafetyChecksPassed74.XXXXXX")"
trap 'rm -f "${GENERATED_TMP}"' EXIT
print -r -- '// Generated only after the existing bridge SelfTests pass.
enum YitaojinSafetyCheckGenerated {}' \
  > "${GENERATED_TMP}"
mv "${GENERATED_TMP}" "${OUTPUT_FILE}"
trap - EXIT
