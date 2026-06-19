"""测试脚本 — 逐步测试报告生成的每个环节耗时"""
import asyncio
import time
import json

# Data source tests
async def test_data_sources():
    from app.data_sources.tencent_client import TencentDataSource
    from app.data_sources.akshare_market import AKShareMarketClient
    from app.data_sources.akshare_news import AKShareNewsClient

    tencent = TencentDataSource()
    market = AKShareMarketClient()
    news = AKShareNewsClient()

    results = {}

    # Test 1: Tencent batch fetch
    t0 = time.time()
    indices = await tencent.fetch_batch(["000001", "399001", "399006"])
    results["tencent_batch"] = time.time() - t0

    # Test 2: Tencent single fetch
    t0 = time.time()
    stock = await tencent.fetch("000725")
    results["tencent_single"] = time.time() - t0

    # Test 3: Industry fund flow
    t0 = time.time()
    industry = await market.fetch_fund_flow_industry()
    results["industry_flow"] = time.time() - t0

    # Test 4: Market indices
    t0 = time.time()
    mkt = await market.fetch_market_indices()
    results["market_indices"] = time.time() - t0

    # Test 5: News
    t0 = time.time()
    news_ctx = await news.fetch_all_news(["000725", "601899", "603993"])
    results["news"] = time.time() - t0

    # Test 6: Full market context
    t0 = time.time()
    mkt_ctx = await market.fetch_all_market_data(["000725", "601899", "603993"])
    results["market_context"] = time.time() - t0

    return results, indices, stock, industry, mkt, news_ctx, mkt_ctx


async def main():
    print("=" * 60)
    print("报告生成流程测试")
    print("=" * 60)

    # Step 1: Data sources
    print("\n--- 数据源耗时测试 ---")
    try:
        results, indices, stock, industry, mkt, news_ctx, mkt_ctx = await test_data_sources()
        for name, elapsed in results.items():
            print(f"  {name:20s}: {elapsed:.2f}s")
        total_data = sum(results.values())
        print(f"  {'TOTAL DATA':20s}: {total_data:.2f}s")
    except Exception as e:
        print(f"  数据源测试失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # Step 2: Ollama with small prompt
    print("\n--- Ollama 调用测试 ---")
    for prompt_size in [200, 500, 1000, 2000]:
        try:
            success = result.get("success")
            content_len = len(result.get("content", ""))
            print(f"  Prompt {prompt_size:5d} chars: {elapsed:.2f}s  success={success}  output_len={content_len}")
            if not success:
                print(f"    Error: {result.get('error', 'unknown')[:100]}")
        except Exception as e:
            print(f"  Prompt {prompt_size:5d} chars: FAILED - {e}")

    # Step 3: Full realistic prompt
    print("\n--- 完整流程模拟 ---")
    try:
        market_data = f"""## 盘中实时市场概况
- 上证指数: {'3412.35' if indices.get('000001') else 'N/A'} ({indices.get('000001',{}).get('change_pct',0):+.2f}%)
- 深证成指: {'10923.5' if indices.get('399001') else 'N/A'} ({indices.get('399001',{}).get('change_pct',0):+.2f}%)
- 创业板指: {'2234.5' if indices.get('399006') else 'N/A'} ({indices.get('399006',{}).get('change_pct',0):+.2f}%)

## 行业资金流向 Top10
"""
        if industry:
            for s in industry[:5]:
                market_data += f"- {s['name']}: 净额{s['net']} 涨跌{s['change_pct']}%\n"

        holdings_data = "- 京东方A(000725): 4200股, 成本¥5.41, 现价¥5.43, 日内盈亏+0.37%\n"
        holdings_data += "- 紫金矿业(601899): 1200股, 成本¥32.46, 现价¥31.89, 日内盈亏-1.76%\n"
        holdings_data += "- 洛阳钼业(603993): 1000股, 成本¥20.09, 现价¥19.77, 日内盈亏-1.59%"

        # News + market context
        context = (news_ctx or "无新闻")[:1000] + "\n\n" + (mkt_ctx or "无市场数据")[:2000]

        full_prompt = f"""你是A股盘中交易策略师。请基于实时数据，给出简洁可执行的操作策略。面向新手。

【今日要闻】
{context[:1500]}

【市场数据】
{market_data[:1000]}

【持仓情况】
{holdings_data}

请精简输出JSON:
{{{{"market_snapshot":"一句话","overall_action":"做多/观望/减仓","confidence":1-10,"holdings_advice":[{{{{"code":"代码","name":"名称","action":"操作","reason":"理由"}}}}],"key_risks":["风险"]}}}}
"""
        print(f"  Full prompt size: {len(full_prompt)} chars")
        t0 = time.time()
        from app.ai.client import OllamaClient
        client = OllamaClient()
        result = await client.generate(full_prompt, format_json=True, model="qwen3.5:2b", retries=0)
        elapsed = time.time() - t0
        success = result.get("success")
        print(f"  2B model: {elapsed:.2f}s  success={success}")
        if success:
            print(f"  Output length: {len(result.get('content',''))} chars")
            try:
                parsed = json.loads(result.get('content', '{}'))
                print(f"  Action: {parsed.get('overall_action', 'N/A')}")
            except:
                print(f"  Raw: {result.get('content','')[:200]}")
        else:
            print(f"  Error: {result.get('error', 'unknown')}")

    except Exception as e:
        print(f"  完整流程测试失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("测试完成")

if __name__ == "__main__":
    asyncio.run(main())
