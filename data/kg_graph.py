#!/usr/bin/env python3
"""Render the knowledge graph as a dark-themed SVG diagram - v3."""

import sqlite3
import math

DB = "/Users/zhuchenyuan/工作流/knowX/data/graph.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

nodes = conn.execute("SELECT * FROM nodes").fetchall()
edges = conn.execute("""
    SELECT n1.title as src, n2.title as dst
    FROM edges e
    JOIN nodes n1 ON e.from_node=n1.id
    JOIN nodes n2 ON e.to_node=n2.id
""").fetchall()
progress = {r["node_id"]: r["status"] for r in
    conn.execute("SELECT node_id, status FROM progress").fetchall()}

# Build node lookup
node_map = {}
for n in nodes:
    node_map[n["id"]] = {
        "id": n["id"],
        "title": n["title"],
        "domain": n["domain"],
        "level": n["level"],
    }

# Hand-crafted positions for clean layout
# Engineering: top band, left to right
# Agent: middle band, left to right
# AI Creation: bottom band, centered
positions = {
    # Engineering level 1
    "git-branch": (180, 100),
    "python-import": (420, 100),
    "single-responsibility": (660, 100),
    "test-basics": (900, 100),
    "module-boundary": (1140, 100),

    # Engineering level 2
    "git-workflow": (300, 160),
    "project-structure": (540, 160),
    "coupling-cohesion": (780, 160),
    "docker-basics": (1020, 160),
    "ci-cd-basics": (420, 220),
    "logging-struct": (660, 220),
    "state-management": (900, 220),

    # Agent level 1
    "prompt-design": (420, 340),
    "structured-output": (660, 340),

    # Agent level 2
    "agent-debugging": (300, 400),
    "agent-workflow": (540, 400),
    "skill-packaging": (780, 400),

   # AI Creation
    "comfyui-workflow": (420, 540),
    "seedance-pipeline": (660, 540),

    # Automation (n8n) level 1
    "workflow-automation": (240, 660),
    "n8n-platform": (480, 660),
    "n8n-installation": (720, 660),
    "n8n-core-concepts": (960, 660),

    # Automation level 2
    "n8n-ai-agent": (360, 750),
    "n8n-multi-app": (600, 750),
    "n8n-enterprise": (840, 750),
    "n8n-social-media": (1080, 750),

    # Course materials reference node
    "n8n-course-materials": (660, 850),
}

# SVG params
W, H = 1320, 940
NW, NH = 155, 44

DOMAIN_COLORS = {
    "agent": "#e74c3c",
    "engineering": "#3498db",
    "ai_creation": "#2ecc71",
    "automation": "#f39c12",
}

svg = []
svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
svg.append(f'<rect width="{W}" height="{H}" fill="#0d1117"/>')

# Title
svg.append(f'<text x="{W/2}" y="24" text-anchor="middle" fill="#c9d1d9" font-size="16" font-family="system-ui" font-weight="bold">knowX 知识图谱 — 学习路径图</text>')

# Legend
ly = 42
svg.append(f'<text x="12" y="{ly}" fill="#8b949e" font-size="9" font-family="system-ui">图例:</text>')
svg.append(f'<rect x="50" y="{ly-9}" width="9" height="9" rx="2" fill="#e74c3c" opacity="0.7"/>')
svg.append(f'<text x="{62}" y="{ly-1}" fill="#c9d1d9" font-size="8" font-family="system-ui">agent</text>')
svg.append(f'<rect x="{62+50}" y="{ly-9}" width="9" height="9" rx="2" fill="#3498db" opacity="0.7"/>')
svg.append(f'<text x="{74+50}" y="{ly-1}" fill="#c9d1d9" font-size="8" font-family="system-ui">engineering</text>')
svg.append(f'<rect x="{112+50}" y="{ly-9}" width="9" height="9" rx="2" fill="#2ecc71" opacity="0.7"/>')
svg.append(f'<text x="{124+50}" y="{ly-1}" fill="#c9d1d9" font-size="8" font-family="system-ui">ai_creation</text>')
svg.append(f'<rect x="{220+50}" y="{ly-9}" width="9" height="9" rx="2" fill="#f39c12" opacity="0.7"/>')
svg.append(f'<text x="{232+50}" y="{ly-1}" fill="#c9d1d9" font-size="8" font-family="system-ui">automation</text>')
svg.append(f'<rect x="{340+50}" y="{ly-9}" width="9" height="9" rx="2" fill="#2ecc71" opacity="0.85"/>')
svg.append(f'<text x="{352+50}" y="{ly-1}" fill="#c9d1d9" font-size="8" font-family="system-ui">已掌握</text>')
svg.append(f'<rect x="{430+50}" y="{ly-9}" width="9" height="9" rx="2" fill="none" stroke="#555" stroke-width="1"/>')
svg.append(f'<text x="{442+50}" y="{ly-1}" fill="#c9d1d9" font-size="8" font-family="system-ui">学习中</text>')

# Draw edges
def draw_edges():
    svg.append('<defs><marker id="a" markerWidth="7" markerHeight="7" refX="7" refY="3.5" orient="auto"><polygon points="0 0, 7 3.5, 0 7" fill="#30363d"/></marker></defs>')

    for e in edges:
        # Find node ids by title
        sid = did = None
        for n in nodes:
            if n["title"] == e["src"]:
                sid = n["id"]
            if n["title"] == e["dst"]:
                did = n["id"]
        if not sid or not did or sid not in positions or did not in positions:
            continue

        x1, y1 = positions[sid]
        x2, y2 = positions[did]

        dx, dy = x2 - x1, y2 - y1
        dist = math.sqrt(dx*dx + dy*dy) or 1
        nx, ny = dx/dist, dy/dist

        # Offset to node boundary
        hw = NW / 2 + 4
        hh = NH / 2 + 4
        # Approximate intersection with rectangle
        if abs(nx) > 0.001:
            t = hw / abs(nx)
        else:
            t = 1e9
        if abs(ny) > 0.001:
            t2 = hh / abs(ny)
            t = min(t, t2)
        sx, sy = x1 + nx * t, y1 + ny * t

        if abs(nx) > 0.001:
            t = hw / abs(nx)
        else:
            t = 1e9
        if abs(ny) > 0.001:
            t2 = hh / abs(ny)
            t = min(t, t2)
        ex = x2 - nx * t
        ey = y2 - ny * t

        svg.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="#30363d" stroke-width="1.2" marker-end="url(#a)"/>')

draw_edges()

# Draw nodes
mastered_count = sum(1 for s in progress.values() if s == "mastered")

for n in nodes:
    nid = n["id"]
    if nid not in positions:
        continue
    x, y = positions[nid]
    nc = DOMAIN_COLORS.get(n["domain"], "#888")
    is_m = progress.get(nid) == "mastered"

    nx_pos = x - NW / 2
    ny_pos = y - NH / 2

    stroke = "#2ecc71" if is_m else nc
    sw = 2 if is_m else 1

    svg.append(f'<rect x="{nx_pos}" y="{ny_pos}" width="{NW}" height="{NH}" rx="6" fill="#0d1117" stroke="{stroke}" stroke-width="{sw}"/>')

    if is_m:
        svg.append(f'<circle cx="{nx_pos + NW - 10}" cy="{ny_pos + 10}" r="4.5" fill="#2ecc71"/>')
        svg.append(f'<text x="{nx_pos + NW - 10}" y="{ny_pos + 13.5}" text-anchor="middle" fill="#0d1117" font-size="7" font-family="system-ui" font-weight="bold">✓</text>')

    # Domain badge
    bw = 36
    svg.append(f'<rect x="{nx_pos + 4}" y="{ny_pos + 3}" width="{bw}" height="11" rx="2.5" fill="{nc}" opacity="0.35"/>')
    svg.append(f'<text x="{nx_pos + 4 + bw/2}" y="{ny_pos + 11.5}" text-anchor="middle" fill="{nc}" font-size="6" font-family="system-ui">{n["domain"]}</text>')

    # Title
    title = n["title"]
    if len(title) <= 15:
        svg.append(f'<text x="{x}" y="{y+3.5}" text-anchor="middle" fill="#e6edf3" font-size="9.5" font-family="system-ui, sans-serif">{title}</text>')
    else:
        # Simple wrap
        words = title.split()
        line1 = ""
        for w in words:
            if len(line1 + " " + w) <= 15:
                line1 = (line1 + " " + w).strip()
            else:
                break
        rest = title[len(line1):].strip()
        if rest:
            svg.append(f'<text x="{x}" y="{y-1}" text-anchor="middle" fill="#e6edf3" font-size="9" font-family="system-ui, sans-serif">{line1}</text>')
            svg.append(f'<text x="{x}" y="{y+10}" text-anchor="middle" fill="#e6edf3" font-size="9" font-family="system-ui, sans-serif">{rest}</text>')
        else:
            svg.append(f'<text x="{x}" y="{y+3.5}" text-anchor="middle" fill="#e6edf3" font-size="9.5" font-family="system-ui, sans-serif">{title}</text>')

# Domain labels
svg.append(f'<text x="12" y="250" fill="#484f58" font-size="10" font-family="system-ui" font-weight="bold" letter-spacing="1">ENGINEERING</text>')
svg.append(f'<text x="12" y="445" fill="#484f58" font-size="10" font-family="system-ui" font-weight="bold" letter-spacing="1">AGENT</text>')
svg.append(f'<text x="12" y="575" fill="#484f58" font-size="10" font-family="system-ui" font-weight="bold" letter-spacing="1">AI_CREATION</text>')
svg.append(f'<text x="12" y="690" fill="#484f58" font-size="10" font-family="system-ui" font-weight="bold" letter-spacing="1">AUTOMATION (n8n)</text>')

# Stats
svg.append(f'<text x="{W/2}" y="{H-14}" text-anchor="middle" fill="#8b949e" font-size="10" font-family="system-ui">共 {len(nodes)} 个知识点 · 已掌握 {mastered_count}/{len(nodes)} · 待学习 {len(nodes)-mastered_count} 个</text>')

svg.append('</svg>')

with open("/tmp/kg_graph.svg", "w") as f:
    f.write("\n".join(svg))

print(f"Done: {len(nodes)} nodes, {len(edges)} edges")
