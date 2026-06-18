#!/usr/bin/env python3
"""🎯 5支标的 AI 辩论 — 调用恭喜发财辩论引擎"""
import sys
import os
import json
import asyncio

os.chdir(os.path.dirname(os.path.abspath(__file__)))
os.environ['DOTENV_PATH'] = os.path.join(os.path.dirname(__file__), '..', '.env.local')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUTPUT_FILE = "/Users/zhuchenyuan/工作流/cong-xi-fa-cai/辩论报告-5支低价股.json"

async def main():
    print("=" * 60, flush=True)
    print("  恭喜发财 AI 辩论 — 5支低价股专场", flush=True)
    print("=" * 60, flush=True)

    from app.data_sources.tencent_client import TencentDataSource
    from app.data_sources.akshare_news import AKShareNewsClient

    # 目标标的
    POOL = ["sz002303", "sz002774", "sh600615", "sz000034", "sh600016"]
    POOL_INFO = {
        "sz002303": {"code": "002303", "name": "美盈森"},
        "sz002774": {"code": "002774", "name": "快意电梯"},
        "sh600615": {"code": "600615", "name": "鑫源智造"},
        "sz000034": {"code": "000034", "name": "神州数码"},
        "sh600016": {"code": "600016", "name": "民生银行"},
    }

    # 1. 行情数据
    tc = TencentDataSource()
    quotes = await tc.fetch_batch(POOL)
    print(f"📊 行情: {len(quotes)}支", flush=True)

    # 格式化行情
    quote_lines = []
    for k, q in quotes.items():
        info = POOL_INFO.get(k, {})
        line = (f"{info.get('name','?')}({info.get('code','?')}): "
                f"¥{q.get('price','?')} | 涨幅{q.get('change_pct','?')}% | "
                f"换手{q.get('turnover_pct','?')}% | 量比{q.get('vol_ratio','?')} | "
                f"振幅{q.get('amplitude_pct','?')}% | PE={q.get('pe_ttm','?')} | "
                f"PB={q.get('pb','?')} | 流通市值{q.get('float_mcap_yi','?')}亿")
        quote_lines.append(line)
    market_data_str = "\n\n".join(quote_lines)

    # 2. 指数
    try:
        idx = await tc.fetch_batch(["sh000001", "sz399001"])
        sh = idx.get("sh000001", {}).get("price", 3350)
        sz = idx.get("sz399001", {}).get("price", 10800)
    except:
        sh, sz = 3350, 10800
    market_data_str += f"\n\n【大盘指数】\n上证: {sh} | 深证: {sz}"

    # 3. 新闻
    news_lines = []
    try:
        nc = AKShareNewsClient()
        cjzc = await nc.fetch_cjzc()
        if cjzc:
            for n in cjzc[:8]:
                news_lines.append(f"- {n.get('title','')}")
    except Exception as e:
        print(f"⚠️ 新闻获取异常: {e}", flush=True)

    # 4. 个股新闻
    for code in ["002303", "002774", "600615", "000034", "600016"]:
        try:
            sn = await nc.fetch_stock_news(code, limit=3)
            if sn:
                name = POOL_INFO.get(f"sz{code}", POOL_INFO.get(f"sh{code}", {}))
                news_lines.append(f"\n【{name.get('name',code)}】")
                for n in sn[:2]:
                    news_lines.append(f"- {n.get('title','')}")
        except:
            pass

    news_str = "\n".join(news_lines[:30]) if news_lines else "无今日要闻数据"

    # 5. 持仓（空仓）
    holdings_data = json.dumps({
        "status": "当前空仓",
        "available_cash": 3165.72,
        "total_assets": 3165.72,
        "holdings": []
    }, ensure_ascii=False)

    print(f"📰 新闻: {len(news_lines)}条", flush=True)
    print("💵 现金: ¥3,165.72", flush=True)

    # 6. 调用辩论引擎
    print("\n🧠 启动 AI 辩论 (DeepSeek)...", flush=True)
    from app.engine.workshop import run_debate

    analysis_report = {
        "market": {"indices": {"shanghai": sh, "shenzhen": sz}},
        "indices": {"sh000001": {"price": sh}},
        "holdings_str": "当前空仓，可用现金¥3,165.72",
        "available_cash": 3165.72,
        "news": [{"title": n} for n in news_lines[:10]],
        "custom_stocks": POOL_INFO,
        "stock_type_filter": "低价股",
        "budget_constraint": "账户总资产¥3,165.72，单票预算不超过¥2,700",
    }

    result = await run_debate(analysis_report, strategy_type="pool_debate")
    decision = result.get("decision", {})
    debate_roles = result.get("roles", {})
    risk_level = result.get("recommended_risk_level", 3)

    # 7. 组装报告
    report = {
        "meta": {
            "title": "🎯 恭喜发财 AI 辩论 — 5支低价股可行性报告",
            "date": "2026-06-16",
            "model": "DeepSeek + Qwen (辩论场)",
            "participants": ["猎手(短线)", "账房(中低频)", "守夜人(风控)", "研究员(产业链)", "裁判(综合)"],
            "budget": "¥3,165.72",
        },
        "debate_roles": {
            "hunter": debate_roles.get("hunter", {}),
            "accountant": debate_roles.get("accountant", {}),
            "guardian": debate_roles.get("guardian", {}),
            "researcher": debate_roles.get("researcher", {}),
        },
        "judge": decision,
        "risk_level": risk_level,
        "market_data": quotes,
        "news": news_lines[:20],
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 报告已保存: {OUTPUT_FILE}", flush=True)
    print(f"   - 风险等级: R{risk_level}", flush=True)
    print(f"   - 裁判结论: {decision.get('final_view','N/A')[:80]}", flush=True)
    print(f"   - 推荐标的: {len(decision.get('stock_pool',[]))}支", flush=True)
    print("=" * 60, flush=True)

if __name__ == "__main__":
    asyncio.run(main())
