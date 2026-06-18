#!/usr/bin/env python3
"""
通用 CHANGELOG 自动更新脚本 — 适用于 Claude Code 下所有项目

功能：
1. 自动检测项目变更（基于 git diff 或手动指定）
2. 生成规范的 changelog 条目（保持现有 CHANGELOG.md 格式）
3. 追加到任意项目的 CHANGELOG.md

用法：
  # 在项目根目录执行
  python ~/.claude/scripts/update_changelog.py -i

  # 指定项目目录
  python ~/.claude/scripts/update_changelog.py -p /path/to/project -i

  # 命令行模式（适合 Claude 自动调用）
  python ~/.claude/scripts/update_changelog.py \
      -v v1.2.0 \
      --feature "新增用户登录" \
      --fix "修复缓存过期问题" \
      --refactor "重构数据访问层"

  # 自动检测 git 变更
  python ~/.claude/scripts/update_changelog.py --auto
"""

import os
import sys
import re
import subprocess
import argparse
from datetime import datetime
from pathlib import Path

# 脚本自身路径（可被安装到 ~/.claude/scripts/）
SCRIPT_DIR = Path(__file__).resolve().parent


def detect_project_root(path: str = None) -> Path:
    """智能检测项目根目录"""
    if path:
        root = Path(path).resolve()
        if root.is_dir():
            return root
        print(f"❌ 指定路径不存在: {path}")
        sys.exit(1)

    # 默认：当前工作目录
    cwd = Path.cwd()
    # 如果当前在 scripts/ 子目录中，自动上跳
    if cwd.name == "scripts" and (cwd.parent / "CHANGELOG.md").exists():
        return cwd.parent
    return cwd


def detect_project_name(project_root: Path) -> str:
    """从 README 或目录名推断项目名称"""
    # 尝试从 README 第一行获取
    for readme_name in ["README.md", "readme.md", "README.txt"]:
        readme = project_root / readme_name
        if readme.exists():
            first_line = readme.read_text(encoding="utf-8").strip().split("\n")[0]
            # 去掉 Markdown 标题标记
            first_line = first_line.lstrip("#").strip()
            if first_line:
                return first_line
    # 回退：使用目录名
    return project_root.name


def get_git_changes(project_root: Path) -> dict:
    """从 git 获取变更摘要"""
    if not (project_root / ".git").exists():
        return {}

    try:
        # 未跟踪 + 已暂存 + 未暂存
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, cwd=project_root
        )
        changed = [f for f in result.stdout.strip().split("\n") if f]

        if not changed:
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True, text=True, cwd=project_root
            )
            changed = [f for f in result.stdout.strip().split("\n") if f]

        # 也检查未跟踪文件
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=project_root
        )
        untracked = [f for f in result.stdout.strip().split("\n") if f]
        all_files = list(set(changed + untracked))

        # 最近 commit
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            capture_output=True, text=True, cwd=project_root
        )
        last_commit = result.stdout.strip()

        # diff 统计
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True, text=True, cwd=project_root
        )
        diff_stat = result.stdout.strip()

        return {
            "files": all_files,
            "last_commit": last_commit,
            "diff_stat": diff_stat,
        }
    except Exception:
        return {}


def classify_changes(files: list) -> dict:
    """按通用模式分类变更文件（不依赖具体项目结构）"""
    categories = {
        "新增功能": [],
        "Bug 修复": [],
        "架构重构": [],
        "代码优化": [],
        "破坏性变更": [],
        "文档": [],
        "前端": [],
        "配置/脚本": [],
    }

    # 通用分类模式
    for f in files:
        lower = f.lower()
        if lower.endswith(".md") or lower.endswith(".rst"):
            categories["文档"].append(f)
            continue
        if lower.endswith(".json") or lower.startswith("."):
            categories["配置/脚本"].append(f)
            continue
        if "frontend" in lower or lower.endswith((".vue", ".jsx", ".tsx", ".css", ".scss")):
            categories["前端"].append(f)
            continue
        if lower.endswith((".py", ".js", ".ts", ".go", ".rs", ".java", ".rb")):
            # 文件名中包含 fix/bug/hotfix 关键词 → Bug 修复
            if any(w in lower for w in ["fix", "bug", "hotfix", "patch"]):
                categories["Bug 修复"].append(f)
            # 文件名中包含 refactor/rewrite/migrate → 架构重构
            elif any(w in lower for w in ["refactor", "rewrite", "migrat", "model"]):
                categories["架构重构"].append(f)
            # 文件名中包含 test/spec → 测试
            elif "test" in lower or "spec" in lower:
                categories["代码优化"].append(f)
            # 删除文件 → 破坏性变更
            elif not os.path.exists(f):
                categories["破坏性变更"].append(f)
            else:
                categories["代码优化"].append(f)
        else:
            categories["配置/脚本"].append(f)

    return {k: v for k, v in categories.items() if v}


def format_entry(version: str, date: str, sections: dict) -> str:
    """格式化一条 changelog 条目"""
    today = date or datetime.now().strftime("%Y-%m-%d")
    lines = [f"## {version} ({today})", ""]

    section_order = [
        "架构重构", "新增功能", "Bug 修复",
        "代码优化", "破坏性变更", "前端", "文档", "配置/脚本",
    ]

    for section_name in section_order:
        if section_name not in sections or not sections[section_name]:
            continue
        lines.append(f"### {section_name}")
        for item in sections[section_name]:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def update_changelog(project_root: Path, entry: str):
    """将条目写入项目 CHANGELOG.md"""
    changelog_path = project_root / "CHANGELOG.md"

    if changelog_path.exists():
        content = changelog_path.read_text(encoding="utf-8")
    else:
        # 自动生成文件头
        project_name = detect_project_name(project_root)
        content = f"# {project_name} 更新日志\n\n"

    lines = content.split("\n")

    # 找到第一个 ## 标题位置，在它之前插入
    insert_pos = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_pos = i
            break

    if insert_pos == len(lines):
        new_content = content.rstrip() + "\n\n" + entry + "\n"
    else:
        new_lines = lines[:insert_pos] + [entry] + lines[insert_pos:]
        new_content = "\n".join(new_lines)

    # 备份原文件
    backup_path = changelog_path.with_suffix(".md.bak")
    if changelog_path.exists():
        changelog_path.rename(backup_path)

    changelog_path.write_text(new_content, encoding="utf-8")
    print(f"✅ CHANGELOG 已更新: {changelog_path}")
    print(f"   备份: {backup_path}")


def get_current_version(project_root: Path) -> str:
    """从现有 CHANGELOG 解析最新版本号并递增"""
    changelog_path = project_root / "CHANGELOG.md"
    if not changelog_path.exists():
        return "v0.1.0"

    content = changelog_path.read_text(encoding="utf-8")
    match = re.search(r"## (v?\d+)\.(\d+)\.(\d+)", content)
    if match:
        major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return f"v{major}.{minor}.{patch + 1}"
    return "v0.1.0"


def interactive_mode(project_root: Path) -> dict:
    """交互式输入变更内容"""
    project_name = detect_project_name(project_root)
    print(f"\n📝 CHANGELOG 编辑 — {project_name}")
    print("=" * 50)

    default_ver = get_current_version(project_root)
    version = input(f"版本号 [{default_ver}]: ").strip()
    if not version:
        version = default_ver

    sections = {}
    prompts = [
        ("新增功能", "新增功能（; 分隔多条，回车跳过）: "),
        ("Bug 修复", "Bug 修复（; 分隔多条，回车跳过）: "),
        ("架构重构", "架构调整（; 分隔多条，回车跳过）: "),
        ("代码优化", "代码优化（; 分隔多条，回车跳过）: "),
        ("破坏性变更", "破坏性变更（; 分隔多条，回车跳过）: "),
        ("前端", "前端变更（; 分隔多条，回车跳过）: "),
        ("文档", "文档更新（; 分隔多条，回车跳过）: "),
    ]

    for section_name, prompt in prompts:
        text = input(prompt).strip()
        if text:
            items = [item.strip() for item in text.split(";") if item.strip()]
            sections[section_name] = items

    return {"version": version, "sections": sections}


def main():
    parser = argparse.ArgumentParser(
        description="通用 CHANGELOG.md 自动更新工具 — 适用于所有 Claude 项目",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 在当前项目目录使用交互模式
  python ~/.claude/scripts/update_changelog.py -i

  # 指定项目路径
  python ~/.claude/scripts/update_changelog.py -p ~/my-project -i

  # 命令行模式（Claude 自动调用）
  python ~/.claude/scripts/update_changelog.py \\
      -v v1.2.0 \\
      --feature "新增用户认证模块;新增文件上传接口" \\
      --fix "修复 SQL 注入漏洞;修复内存泄漏" \\
      --refactor "重构数据库连接池"

  # 自动检测 git 变更
  python ~/.claude/scripts/update_changelog.py --auto

  # 预览不写入
  python ~/.claude/scripts/update_changelog.py --dry-run --auto
        """,
    )

    parser.add_argument("-p", "--project", help="项目根目录路径（默认当前目录）")
    parser.add_argument("-v", "--version", help="版本号（如 v1.2.0）")
    parser.add_argument("-i", "--interactive", action="store_true", help="交互模式")
    parser.add_argument("--auto", action="store_true", help="自动检测 git 变更")
    parser.add_argument("--feature", help="新增功能（; 分隔多条）")
    parser.add_argument("--fix", help="Bug 修复（; 分隔多条）")
    parser.add_argument("--refactor", help="架构重构（; 分隔多条）")
    parser.add_argument("--optimize", help="代码优化（; 分隔多条）")
    parser.add_argument("--breaking", help="破坏性变更（; 分隔多条）")
    parser.add_argument("--frontend", help="前端变更（; 分隔多条）")
    parser.add_argument("--docs", help="文档变更（; 分隔多条）")
    parser.add_argument("--title", help="版本标题/主题")
    parser.add_argument("--date", help="日期（默认今天, YYYY-MM-DD）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不写入")

    args = parser.parse_args()
    project_root = detect_project_root(args.project)

    # 检查项目根目录下 CHANGELOG.md 是否存在
    if not (project_root / "CHANGELOG.md").exists() and not args.dry_run:
        print("ℹ️  未找到 CHANGELOG.md，将自动创建")

    # 交互模式
    if args.interactive:
        data = interactive_mode(project_root)
        version = data["version"]
        sections = data["sections"]

    # 自动模式
    elif args.auto:
        git_info = get_git_changes(project_root)
        if not git_info:
            print("❌ 无法获取 git 变更信息（项目未初始化为 git 仓库）")
            print("   请使用 -i 交互模式或手动指定参数")
            sys.exit(1)

        version = args.version or get_current_version(project_root)
        sections = classify_changes(git_info["files"])

        if git_info.get("last_commit"):
            sections.setdefault("变更说明", []).append(git_info["last_commit"])

        print(f"\n📋 检测到 {len(git_info['files'])} 个变更文件:")
        for f in git_info["files"][:20]:
            print(f"   {f}")
        if len(git_info["files"]) > 20:
            print(f"   ... 共 {len(git_info['files'])} 个文件")

    # 手动模式（命令行参数）
    else:
        version = args.version or get_current_version(project_root)
        sections = {}

        category_map = {
            "feature": "新增功能",
            "fix": "Bug 修复",
            "refactor": "架构重构",
            "optimize": "代码优化",
            "breaking": "破坏性变更",
            "frontend": "前端",
            "docs": "文档",
        }
        for arg_key, section_name in category_map.items():
            value = getattr(args, arg_key)
            if value:
                items = [item.strip() for item in value.split(";") if item.strip()]
                sections[section_name] = items

        if args.title:
            sections.setdefault("变更说明", []).append(args.title)

    # 检查内容
    if not sections:
        print("❌ 未提供任何变更内容，请使用 -i / --auto / --feature 等参数")
        sys.exit(1)

    # 预览
    entry = format_entry(version, args.date, sections)

    print(f"\n{'='*50}")
    print(f"📄 {version} CHANGELOG 预览 ({detect_project_name(project_root)})")
    print(f"{'='*50}")
    print(entry)

    if args.dry_run:
        print("🔍 预览模式 — 未写入文件")
    else:
        confirm = input(f"\n确认写入 {project_root}/CHANGELOG.md？[Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            update_changelog(project_root, entry)
        else:
            print("已取消")


if __name__ == "__main__":
    main()
