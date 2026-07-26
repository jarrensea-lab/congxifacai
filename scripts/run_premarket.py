#!/usr/bin/env python3
"""V6 盘前任务 — DeepSeek辩论 → 双通道飞书推送"""
import sys
import os
import asyncio
import json
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
os.environ["DOTENV_PATH"] = os.path.join(PROJECT_ROOT, ".env.local")

from app.services.market_data_health import fetch_market_index_snapshot
from app.services.sentinel_input_gate import (
    inject_active_sentinel_evidence,
    sentinel_audit_only_message,
)

LARK_CLI = "/Users/zhuchenyuan/.npm-global/bin/lark-cli"
CHAT_ID = "oc_c51ef6103f2e0b5b9ed9c40ab86b3e45"


async def build_premarket_market_data(
    holdings_data: dict,
    *,
    market_source=None,
) -> dict:
    """Build legacy premarket input from validated three-index truth."""
    snapshot = await fetch_market_index_snapshot(market_source)
    available_cash = holdings_data.get("available_cash", 0)
    return {
        **snapshot,
        "sectors": [],
        "holdings": holdings_data.get("holdings", []),
        "holdings_str": holdings_data.get("holdings_str", "空仓"),
        "news": [],
        "available_cash": available_cash,
        "total_assets": holdings_data.get("total_assets", available_cash),
    }


def apply_premarket_sentinel_input(
    package: dict | None,
    report_date: str,
    market_data: dict,
) -> dict[str, object]:
    """Apply the shared Sentinel gate at the legacy premarket entry."""
    return inject_active_sentinel_evidence(
        package,
        report_date,
        market_data,
    )


def lark_send(text: str) -> bool:
    """lark-cli IM 文本推送"""
    try:
        r = subprocess.run([LARK_CLI, "im", "+messages-send", "--chat-id", CHAT_ID,
                           "--text", text[:8000], "--as", "bot"],
                          capture_output=True, text=True, timeout=15)
        # lark-cli outputs JSON to stdout, warnings to stderr
        output = r.stdout.strip()
        if output:
            try:
                data = json.loads(output.split("\n")[-1])
                return data.get("ok", False)
            except: pass
        # Check both stdout and stderr for ok
        combined = r.stdout + r.stderr
        return '"ok":true' in combined or '"ok": true' in combined
    except Exception as e:
        print(f"lark send error: {e}", flush=True)
        return False

async def main():
    from app.config import settings
    from app.database import SessionLocal, init_db
    from app.models import SimAccount, Position

    print("=" * 60, flush=True)
    print("  恭喜发财 V6 盘前任务", flush=True)
    print("=" * 60, flush=True)

    # 1. DB
    init_db()
    db = SessionLocal()
    account = db.query(SimAccount).first()
    positions = db.query(Position).all()
    hd = {"holdings": [], "holdings_str": "空仓", "available_cash": (account.cash/100) if account else 100000.0}
    db.close()
    print(f"持仓: {hd['holdings_str']}, 可用: ¥{hd['available_cash']:,.0f}", flush=True)

    # 2. 市场数据
    from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource

    tc = FastRealtimeMarketDataSource()
    market_data = await build_premarket_market_data(
        hd,
        market_source=tc,
    )
    sh = market_data["indices"].get("sh000001", {}).get("price")
    sz = market_data["indices"].get("sz399001", {}).get("price")
    market_status = market_data["market_source_status"]["status"]
    if market_status == "ok" and sh and sz:
        print(f"指数: 上证{sh} 深证{sz}", flush=True)
    else:
        print(
            f"⚠️ 指数数据不完整（{market_status}），不使用固定占位值",
            flush=True,
        )

    try:
        from datetime import date
        from app.ai.sentinel_research import load_research_package

        sentinel_root = os.path.join(os.path.dirname(__file__), "..", "data", "sentinel")
        sentinel_package = load_research_package(str(date.today()), output_root=sentinel_root)
        sentinel_decision = apply_premarket_sentinel_input(
            sentinel_package,
            str(date.today()),
            market_data,
        )
        if sentinel_decision["active"] is True:
            ingest_result = sentinel_decision.get("ingest_result") or {}
            print(
                "Sentinel evidence: "
                f"{ingest_result.get('evidence_count', 0)} 条证据, "
                f"{ingest_result.get('upserted_targets', 0)} 个标的入池",
                flush=True,
            )
        elif sentinel_package:
            print(
                "Sentinel evidence 仅归档: "
                f"{sentinel_audit_only_message(sentinel_decision)}",
                flush=True,
            )
    except Exception as e:
        print(f"⚠️ Sentinel evidence 接入失败，降级继续: {e}", flush=True)

    # 3. 分析 + 辩论
    from app.engine.analysis import run_analysis
    from app.engine.workshop import run_debate
    from app.services.quote_enrichment import enrich_decision_with_realtime_quotes
    from app.services.report_templates import strategy_report_md
    import httpx

    report = await run_analysis(market_data)
    print("分析完成", flush=True)

    print("启动 AI 辩论 (DeepSeek)...", flush=True)
    debate_result = await run_debate(report)
    decision = debate_result.get("decision", {})
    risk = debate_result.get("recommended_risk_level", 3)
    pos_plan = decision.get("position_plan", {})
    pool = decision.get("stock_pool", [])
    print(f"辩论: R{risk}, {len(pool)}支标的, {decision.get('final_view','?')}", flush=True)

    # 4. 策略报告
    decision = await enrich_decision_with_realtime_quotes(decision, tc)
    report_md = strategy_report_md(decision)

    # 4a. 飞书卡片 (OpenAPI 优先，Webhook 兜底)
    from app.services.feishu_pusher import send_feishu_card

    summary = report_md[:2800] + ("\n\n...\n\n*[完整报告已推送至群聊]*" if len(report_md) > 2800 else "")
    push_result = await send_feishu_card(
        title=f"🐕 旺财V6 盘前策略 [R{risk}]",
        content=summary,
        webhook_url=settings.FEISHU_WEBHOOK_URL,
        app_id=settings.FEISHU_APP_ID,
        app_secret=settings.FEISHU_APP_SECRET,
        chat_id=settings.FEISHU_CHAT_ID,
        api_base=settings.FEISHU_API_BASE,
    )
    print(f"📨 飞书卡片({push_result.get('channel') or 'none'}): {'✅' if push_result.get('feishu_api') or push_result.get('feishu_webhook') else '⚠️'}", flush=True)

    # 4b. lark-cli IM 全文推送
    full_text = f"**🐕 旺财V6 盘前策略 [R{risk}]**\n\n{report_md[:7500]}"
    if getattr(settings, "FEISHU_WEBHOOK_ONLY", False):
        print("💬 lark IM 全文: ⏭️ Webhook-only 模式已跳过", flush=True)
    else:
        im_ok = lark_send(full_text)
        print(f"💬 lark IM 全文: {'✅' if im_ok else '⚠️'}", flush=True)

    # 5. 策略图表
    print("生成图表...", flush=True)
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
        # Try Arial Unicode MS first (best CJK support on macOS), then others
        # Try Arial Unicode MS first (best CJK support on macOS), then others
        zh_font = None
        for fname in fm.findSystemFonts():
            fl = fname.lower()
            if any(k in fl for k in ["arial unicod", "pingfang", "heiti", "stheit", "songti"]):
                zh_font = fm.FontProperties(fname=fname)
                break
        if zh_font is None:
            zh_font = next((fm.FontProperties(fname=f) for f in fm.findSystemFonts()
                           if any(k in f.lower() for k in ["noto sans cjk","simhei","wqy"])), None)
        entries = pos_plan.get("entries", [])
        cash_pct = pos_plan.get("suggested_cash_pct", 20)

        if entries:
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
            labels = [e.get("name", e.get("code", "?")) for e in entries]
            sizes = [e.get("weight_pct", 0) for e in entries]
            colors = ['#4CAF50','#2196F3','#FF9800','#E91E63','#9C27B0','#BDBDBD'][:len(entries)+1]
            if cash_pct > 0: labels.append("现金"); sizes.append(cash_pct)
            ax1.pie(sizes, labels=None, autopct='%1.1f%%', colors=colors[:len(sizes)], startangle=90)
            ax1.set_title('仓位分配', fontproperties=zh_font, fontsize=14, pad=20)
            ax1.legend(labels, title="标的", loc="center left", bbox_to_anchor=(1,0.2,0.5,0.6), prop=zh_font)

            names = [e.get("name", e.get("code", "?"))[:4] for e in entries]
            buy_vals = []
            for e in entries:
                try: buy_vals.append(float(str(e.get("phases",[{}])[0].get("price",0)).replace(",","")))
                except: buy_vals.append(0)
            buy_nums = [float(v) if v else 0 for v in buy_vals]
            x = range(len(names))
            ax2.bar([i-0.25 for i in x], [max(0,v*0.95) for v in buy_nums], 0.25, label='止损(估)', color='#F44336')
            ax2.bar(x, buy_nums, 0.25, label='买入', color='#4CAF50')
            ax2.bar([i+0.25 for i in x], [v*1.1 for v in buy_nums], 0.25, label='目标(估)', color='#2196F3')
            ax2.set_xticks(x); ax2.set_xticklabels(names, fontproperties=zh_font)
            ax2.set_title('价格区间', fontproperties=zh_font, fontsize=14)
            ax2.legend(prop=zh_font); ax2.grid(axis='y', alpha=0.3)
            plt.tight_layout()
            os.makedirs("/tmp/congxi_v6_charts", exist_ok=True)
            tp = f"/tmp/congxi_v6_charts/strategy_{os.popen('date +%Y%m%d').read().strip()}.png"
            plt.savefig(tp, dpi=150, bbox_inches='tight'); plt.close()
            print(f"📊 图表: {tp} ({os.path.getsize(tp)//1024}KB)", flush=True)
        else:
            print("无建仓计划，跳过图表", flush=True)
    except Exception as e:
        print(f"⚠️ 图表生成失败: {e}", flush=True)

    print("=" * 60, flush=True)
    print("  盘前任务完成", flush=True)
    print("=" * 60, flush=True)

if __name__ == "__main__":
    asyncio.run(main())
