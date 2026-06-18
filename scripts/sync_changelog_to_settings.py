#!/usr/bin/env python3
"""
将 CHANGELOG.md 的最新版本信息同步到前端设置页面的系统信息区

用法：
  python scripts/sync_changelog_to_settings.py

每次项目优化更新后执行此脚本，自动：
1. 解析 CHANGELOG.md 获取最新版本号、日期、变更摘要
2. 生成 frontend/src/data/system-info.json
3. 前端 Settings.vue 自动展示最新数据
"""

import json
import re
import os
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
OUTPUT_PATH = PROJECT_ROOT / "frontend" / "src" / "data" / "system-info.json"

# 系统架构信息（手动维护，大版本升级时更新）
SYSTEM_META = {
    "ai_models": "多模型分工: 35b(猎手) + 9b(账房) + 4b(守夜人) + deepseek-r1:14b(裁判+代码) + 2b(校验)",
    "data_sources": "腾讯财经、东方财富、AKShare(同花顺/新浪/财联社)",
    "database": "SQLite (本地)",
    "architecture": "FastAPI + Vue3 + Ollama + APScheduler + Backtrader",
    "project_repo": "本地工作流项目",
}


def parse_changelog(path: Path) -> list:
    """解析 CHANGELOG.md 返回所有版本条目（状态机逐行解析）"""
    if not path.exists():
        print(f"❌ 未找到 CHANGELOG.md: {path}")
        return []

    lines = path.read_text(encoding="utf-8").split("\n")
    versions = []
    current = None
    current_section = None

    for line in lines:
        # 匹配版本标题: ## v3.0.0 (2026-05-27)
        ver_match = re.match(r"^##\s+(v?\d+\.\d+\.\d+)\s*\((\d{4}-\d{2}-\d{2})\)", line)
        if ver_match:
            if current:
                versions.append(current)
            current = {
                "version": ver_match.group(1),
                "date": ver_match.group(2),
                "sections": {},
            }
            current_section = None
            continue

        if not current:
            continue

        # 匹配分类标题: ### 新增功能
        section_match = re.match(r"^###\s+(.+)$", line)
        if section_match:
            current_section = section_match.group(1).strip()
            if current_section not in current["sections"]:
                current["sections"][current_section] = []
            continue

        # 匹配变更条目: - xxx
        item_match = re.match(r"^-\s+(.+)$", line)
        if item_match and current_section:
            text = item_match.group(1).strip()
            # 提取粗体标签: **标签**: 内容 或 **标签**：内容
            label_match = re.match(r"\*\*(.+?)\*\*[：:]\s*(.*)", text)
            if label_match:
                text = f"{label_match.group(1)}: {label_match.group(2)}"
            current["sections"][current_section].append(text)

    if current:
        versions.append(current)

    # 计算变更总数
    for v in versions:
        v["total_changes"] = sum(len(items) for items in v["sections"].values())

    return versions


def generate_system_info(versions: list) -> dict:
    """生成前端可用的系统信息 JSON"""
    if not versions:
        return {
            "version": "未知",
            "update_date": datetime.now().strftime("%Y-%m-%d"),
            "latest_changes": {"说明": ["无法解析 CHANGELOG.md"]},
            "change_count": 0,
            "recent_versions": [],
            **SYSTEM_META,
        }

    latest = versions[0]

    # 最新版本摘要
    latest_changes = {}
    for section_name, items in latest["sections"].items():
        # 每项最多 3 条，超过的加省略提示
        if len(items) > 3:
            items = items[:3] + [f"... 及其他 {len(items) - 3} 项"]
        latest_changes[section_name] = items

    # 最近 3 个版本
    recent = []
    for v in versions[:3]:
        recent.append({
            "version": v["version"],
            "date": v["date"],
            "summary": _version_summary(v),
        })

    return {
        "version": latest["version"],
        "update_date": latest["date"],
        "latest_changes": latest_changes,
        "change_count": latest["total_changes"],
        "recent_versions": recent,
        "all_versions_count": len(versions),
        "generated_at": datetime.now().isoformat(),
        **SYSTEM_META,
    }


def _version_summary(version: dict) -> str:
    """生成版本一句话摘要"""
    parts = []
    for section_name, items in version["sections"].items():
        if section_name in ("新增功能", "架构重构"):
            parts.append(f"{section_name}{len(items)}项" if len(items) > 1 else section_name)
        elif section_name in ("Bug 修复", "破坏性变更"):
            parts.append(f"{section_name}{len(items)}项" if len(items) > 1 else section_name)
    return "，".join(parts) if parts else f"共{version['total_changes']}项变更"


def main():
    print(f"📖 读取 CHANGELOG: {CHANGELOG_PATH}")
    versions = parse_changelog(CHANGELOG_PATH)

    if not versions:
        print("⚠️  未解析到版本条目")
        return

    print(f"   找到 {len(versions)} 个版本条目")
    print(f"   最新: {versions[0]['version']} ({versions[0]['date']}) — {versions[0]['total_changes']} 项变更")

    info = generate_system_info(versions)

    # 确保输出目录存在
    os.makedirs(OUTPUT_PATH.parent, exist_ok=True)

    # 写入 JSON
    OUTPUT_PATH.write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ 系统信息已同步: {OUTPUT_PATH}")
    print(f"   版本: {info['version']}")
    print(f"   变更项: {info['change_count']}")
    print(f"   分类: {list(info['latest_changes'].keys())}")

    # 自动重新构建前端，确保 dist/ 与源码一致
    rebuild_frontend()


def rebuild_frontend():
    """自动重新构建前端，避免 dist/ 与源码不一致导致页面信息误导"""
    frontend_dir = PROJECT_ROOT / "frontend"
    if not (frontend_dir / "package.json").exists():
        return

    print("\n🔨 重新构建前端（避免 dist/ 过期导致信息误导）...")
    import subprocess
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=frontend_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        # 提取构建输出摘要
        for line in result.stdout.split("\n"):
            if "building" in line or "built" in line or "dist/" in line:
                print(f"   {line.strip()}")
        print("✅ 前端构建完成")
    else:
        print(f"❌ 前端构建失败: {result.stderr[:200]}")


if __name__ == "__main__":
    main()
