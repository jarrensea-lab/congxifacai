#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
repo_root="${script_dir:h}"
source_dir="${repo_root}/tools/yitaojin-bridge/Sources/YitaojinBridge"
self_test="${repo_root}/tools/yitaojin-bridge/SelfTests/main.swift"
self_test_dir="$(mktemp -d)"
self_test_binary="${self_test_dir}/yitaojin-bridge-self-tests"

trap 'rm -rf "${self_test_dir}"' EXIT

swiftc \
  "${source_dir}/BridgeModels.swift" \
  "${source_dir}/SafetyPolicy.swift" \
  "${source_dir}/AXClient.swift" \
  "${source_dir}/YitaojinReader.swift" \
  "${source_dir}/YitaojinWriter.swift" \
  "${self_test}" \
  -framework AppKit \
  -framework ApplicationServices \
  -o "${self_test_binary}"

"${self_test_binary}"
