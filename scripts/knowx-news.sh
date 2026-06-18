#!/usr/bin/env bash
# knowx-news.sh — 新闻抓取脚本
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "$0")/../" && pwd)"
CACHE_DIR="$AGENT_DIR/data/news_cache"
mkdir -p "$CACHE_DIR"

fetch_hn() {
    local cache_file="$CACHE_DIR/hn_top.json"
    if [ -s "$cache_file" ] && [ "$(find "$cache_file" -mmin -60 2>/dev/null || echo 0)" ]; then
        cat "$cache_file"; return
    fi
    local result
    result=$(curl -s --max-time 10 "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=10" || echo '{"hits":[]}')
    echo "$result" | jq -c '[.hits[] | {source:"hackernews", title:.title, url:(.url // ""), summary:((.points|tostring) + " pts, " + (.num_comments|tostring) + " comments")}]' > "$cache_file" 2>/dev/null || echo '[]' > "$cache_file"
    cat "$cache_file"
}

fetch_github() {
    local cache_file="$CACHE_DIR/github_trending.json"
    if [ -s "$cache_file" ] && [ "$(find "$cache_file" -mmin -60 2>/dev/null || echo 0)" ]; then
        cat "$cache_file"; return
    fi
    local last_week
    last_week=$(date -v-7d +%Y-%m-%d 2>/dev/null || date -d '7 days ago' +%Y-%m-%d 2>/dev/null || echo "")
    if [ -z "$last_week" ]; then echo '[]'>"$cache_file"; cat "$cache_file"; return; fi
    local result
    result=$(curl -s --max-time 10 "https://api.github.com/search/repositories?q=pushed:>${last_week}+language:python&sort=stars&order=desc&per_page=5" || echo '{"items":[]}')
    echo "$result" | jq -c '[.items[] | {source:"github", title:.full_name, url:.html_url, summary:(.description // "")}]' > "$cache_file" 2>/dev/null || echo '[]' > "$cache_file"
    cat "$cache_file"
}

case "${1:-}" in
    --hn)  fetch_hn ;;
    --gh)  fetch_github ;;
    *)     echo "{\"hackernews\":$(fetch_hn),\"github\":$(fetch_github)}" | jq -c '.' ;;
esac
