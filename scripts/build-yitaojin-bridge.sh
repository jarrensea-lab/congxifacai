#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
repo_root="${script_dir:h}"
package_path="${repo_root}/tools/yitaojin-bridge"
runtime_state_dir="${CONGXI_STATE_DIR:-${HOME}/Library/Application Support/congxicai-v7}"
destination_dir="${runtime_state_dir}/bin"
destination_path="${destination_dir}/yitaojin-bridge"

swift build --package-path "${package_path}" -c release
binary_dir="$(swift build --package-path "${package_path}" -c release --show-bin-path)"

mkdir -p "${destination_dir}"
temporary_path="$(mktemp "${destination_dir}/.yitaojin-bridge.XXXXXX")"
trap 'rm -f "${temporary_path}"' EXIT
cp "${binary_dir}/yitaojin-bridge" "${temporary_path}"
chmod 0755 "${temporary_path}"
codesign --force --sign - "${temporary_path}"
mv -f "${temporary_path}" "${destination_path}"
trap - EXIT

print -r -- "易淘金桥已构建：${destination_path}"
print -r -- "未自动修改 macOS 辅助功能权限。"
print -r -- "请在系统设置中手工授权上述二进制后运行："
print -r -- "printf '%s\\n' '{\"schemaVersion\":1,\"command\":\"probe\",\"payload\":{}}' | \"${destination_path}\""
