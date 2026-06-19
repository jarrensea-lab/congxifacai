#!/usr/bin/env bash
# knowX polling-agent.sh — Claude Code knowX 轮询模式
# 与 lark-agent 共享同一个飞书群 "内阁"，通过消息前缀 "knowX" 分流
#
# Usage:
#    ./scripts/polling-agent.sh start      # 启动轮询循环
#    ./scripts/polling-agent.sh show-new   # 显示新消息（含 knowX 过滤）
#    ./scripts/polling-agent.sh status     # 查看状态

set -euo pipefail

AGENT_DIR="$(cd "$(dirname "$0")/../" && pwd)"
CHAT_ID="oc_82a46b190f223590dfbbd0709b527758"
PROFILE="baobao"
STATE_FILE="$AGENT_DIR/data/last_processed_count"
BRIEFING_STATE="$AGENT_DIR/data/briefing_state.json"

init_state() {
    mkdir -p "$AGENT_DIR/data"
    if [ ! -f "$STATE_FILE" ]; then echo "0" > "$STATE_FILE"; fi
    if [ ! -f "$BRIEFING_STATE" ]; then echo '{"last_briefing_date":""}' > "$BRIEFING_STATE"; fi
}

poll_messages() {
    lark-cli im +chat-messages-list \
             --chat-id "$CHAT_ID" \
             --format json --page-size 20 \
             --profile "$PROFILE" \
             --sort desc 2>/dev/null || true
}

# 检查是否应该推送早间简报 (北京时间 7:00-7:05)
should_send_briefing() {
    local today
    today=$(TZ=Asia/Shanghai date +%Y-%m-%d)
    local hour
    hour=$(TZ=Asia/Shanghai date +%H)

    if [ "$hour" != "07" ]; then return 1; fi

    local last
    last=$(python3 -c "import json; print(json.load(open('$BRIEFING_STATE')).get('last_briefing_date',''))" 2>/dev/null || echo "")
     [ "$last" != "$today" ]
}

mark_briefing_sent() {
    local today
    today=$(TZ=Asia/Shanghai date +%Y-%m-%d)
    echo "{\"last_briefing_date\":\"$today\"}" > "$BRIEFING_STATE"
}

show_new() {
    init_state
    local last_count
    last_count=$(cat "$STATE_FILE")

    local messages_json
    messages_json=$(poll_messages)

    local current_count
    current_count=$(echo "$messages_json" | jq '.data.messages | length' 2>/dev/null || echo "0")

    if [ "$current_count" -le "$last_count" ]; then
         # 检查自动简报推送
        if should_send_briefing; then
            echo "TRIGGER:BRIEFING"
            mark_briefing_sent
        fi
        return 0
    fi

     # 提取新消息（从 old index 到 new index）
    local start_idx=$last_count
    local new_msgs
    new_msgs=$(echo "$messages_json" | jq -r --argjson start "$start_idx" '
         .data.messages[$start:] | to_entries[] |
         "ID: \(.value.message_id)\nSender: \(.value.sender.id)\nType: \(.value.msg_type)\nContent: \(.value.content)\n---"
     ' 2>/dev/null)

    echo "$current_count" > "$STATE_FILE"

    if [ -n "$new_msgs" ]; then
        echo "$new_msgs"
    fi
}

should_process_message() {
    local content="$1"
     # 检查消息是否包含 knowX 指令（不区分大小写），或者是文本类型消息需要进一步解析
    echo "$content" | grep -qi 'knowx'
}

start_polling() {
    init_state
    local interval="${1:-5}"

    echo "=== knowX Polling Agent ==="
    echo "Chat: $CHAT_ID"
    echo "Interval: ${interval}s"
    echo "Filter: only messages containing 'knowX'"
    echo ""

    while true; do
        show_new > /dev/null 2>&1
        sleep "$interval"
    done
}

case "${1:-}" in
    start)
        start_polling "${2:-5}"
         ;;
    status)
        init_state
        last=$(cat "$STATE_FILE")
        current=$(poll_messages | jq '.data.messages | length' 2>/dev/null || echo "0")
        echo "Last processed: $last msgs | Current: $current msgs | Pending: $((current > last ? current - last : 0))"
         ;;
    show-new)
        show_new
         ;;
    reset)
        echo "0" > "$STATE_FILE"
        echo "State reset"
         ;;
     *)
        echo "knowX Polling Agent — Claude Code Learning Assistant"
        echo ""
        echo "Usage: $0 {start|status|show-new|reset} [interval]"
        exit 1
         ;;
esac
