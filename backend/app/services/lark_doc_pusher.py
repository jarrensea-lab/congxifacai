"""V7 飞书全通道推送 — lark-cli 驱动文档/画板/Base/任务"""
import subprocess
import json
import os
from datetime import datetime
from app.utils.logger import logger

# lark-cli 路径
LARK_CLI = "/Users/zhuchenyuan/.npm-global/bin/lark-cli"

# 恭喜发财群聊 chat_id (从 bridge 或 env 获取)
CONGXI_CHAT_ID = os.environ.get("CONGXI_CHAT_ID", "oc_c51ef6103f2e0b5b9ed9c40ab86b3e45")
# 飞书文档文件夹 token (可选，用于存放生成的报告)
REPORT_FOLDER = os.environ.get("FEISHU_REPORT_FOLDER", "")

def _run_lark(*args, timeout=30):
    """同步执行 lark-cli 命令"""
    try:
        result = subprocess.run(
            [LARK_CLI] + list(args),
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)

async def push_strategy_doc(decision: dict, debate_result: dict = None):
    """推送完整策略报告到飞书文档

    Args:
        decision: run_debate 产出的决策卡
        debate_result: 完整辩论结果（含角色观点）
    """
    from app.services.report_templates import strategy_report_md

    today = datetime.now().strftime("%Y-%m-%d")
    doc_title = f"恭喜发财V7 策略报告 {today}"

    # 生成 Markdown 报告
    report = strategy_report_md(decision)

    # 追加辩论角色摘要
    if debate_result:
        roles = debate_result.get("roles", {})
        if roles:
            report += "\n\n## 🗣️ AI 辩论角色观点\n"
            for role_name, role_data in roles.items():
                role_label = {"hunter": "🐺 猎手(短线)", "accountant": "🧮 账房(中低频)", "guardian": "🛡️ 守夜人(风控)"}.get(role_name, role_name)
                view = role_data.get("analysis", role_data.get("raw", ""))[:300] if isinstance(role_data, dict) else str(role_data)[:300]
                report += f"\n### {role_label}\n{view}\n"

    # 先写临时 md 文件
    md_path = f"/tmp/congxi_v7_report_{today.replace('-', '')}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)

    # 使用 lark-doc 创建文档
    ok, out, err = _run_lark("docs", "create", "--title", doc_title, "--content-file", md_path, timeout=60)
    if ok:
        logger.info(f"✅ 飞书文档已创建: {doc_title}")
        # 提取文档 URL
        doc_url = _extract_url(out)
        return doc_url
    else:
        logger.warning(f"飞书文档创建失败: {err[:200]}")
        return None


async def push_chart_image(image_path: str, title: str = "策略图表") -> bool:
    """推送图片到飞书消息群聊"""
    if not os.path.exists(image_path):
        logger.warning(f"图表文件不存在: {image_path}")
        return False

    # 使用 lark-im 发送图片
    ok, out, err = _run_lark("im", "send-image", "--chat-id", CONGXI_CHAT_ID, "--image", image_path, timeout=30)
    if ok:
        logger.info(f"✅ 图表已推送: {title}")
        return True
    else:
        logger.warning(f"图表推送失败: {err[:200]}")
        return False


async def push_daily_report(status: dict) -> bool:
    """推送每日系统状态报告到飞书"""
    from app.services.report_templates import daily_report_md

    date_str = datetime.now().strftime("%Y-%m-%d")
    report = daily_report_md(
        date=date_str,
        api_health=status.get("api_health", True),
        deepseek_health=status.get("deepseek_health", True),
        tasks_run=status.get("tasks_run", 5),
        tasks_fail=status.get("tasks_fail", 0),
        errors=status.get("errors", []),
        daily_pnl=status.get("daily_pnl", 0),
        positions_count=status.get("positions_count", 0),
    )

    # 发送到群聊
    ok, out, err = _run_lark("im", "send", "--chat-id", CONGXI_CHAT_ID, "--text", report[:3000], timeout=15)
    if ok:
        logger.info("✅ 每日报告已推送")
        return True
    logger.warning(f"每日报告推送失败: {err[:200]}")
    return False


async def sync_stock_pool_to_base(stock_pool: list) -> bool:
    """同步选股池到飞书多维表格"""
    if not stock_pool:
        return False

    # 检查是否已有选股池 Base
    base_id = _get_or_create_stock_base()

    if not base_id:
        logger.warning("无法获取飞书 Base，跳过同步")
        return False

    # 批量添加记录
    success_count = 0
    for stock in stock_pool[:20]:
        code = stock.get("code", "")
        name = stock.get("name", "")
        reason = stock.get("reason", "")[:200]
        buy_range = stock.get("buy_range", "")
        stop_loss = stock.get("stop_loss", "")
        target = stock.get("target", "")

        # 使用 lark-base 添加记录
        record_data = json.dumps({
            "股票代码": code,
            "股票名称": name,
            "推荐理由": reason,
            "买入区间": buy_range,
            "止损价": stop_loss,
            "目标价": target,
            "日期": datetime.now().strftime("%Y-%m-%d"),
        })
        ok, out, err = _run_lark("base", "record", "add", "--table-id", base_id, "--data", record_data, timeout=15)
        if ok:
            success_count += 1

    logger.info(f"选股池同步完成: {success_count}/{len(stock_pool)} 条")
    return success_count > 0


async def push_strategy_chart(decision: dict) -> bool:
    """生成策略收益曲线图并推送到飞书"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
    except ImportError:
        logger.warning("matplotlib 未安装，跳过图表生成")
        return False

    # 尝试找到中文字体
    zh_font = None
    for fname in fm.findSystemFonts():
        if any(k in fname.lower() for k in ['pingfang', 'heiti', 'simhei', 'songti', 'noto sans cjk', 'wqy']):
            try:
                zh_font = fm.FontProperties(fname=fname)
                break
            except:
                pass

    # 提取建仓计划和持仓分布数据
    pos_plan = decision.get("position_plan", {})
    entries = pos_plan.get("entries", [])
    suggested_cash = pos_plan.get("suggested_cash_pct", 20)

    if not entries:
        logger.info("无建仓计划数据，跳过图表生成")
        return False

    # 生成饼图：仓位分配
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    labels = []
    sizes = []
    colors = ['#4CAF50', '#2196F3', '#FF9800', '#E91E63', '#9C27B0']

    for i, entry in enumerate(entries):
        labels.append(f"{entry.get('name', entry.get('code', '?'))}")
        sizes.append(entry.get("weight_pct", 0))

    if suggested_cash > 0:
        labels.append("现金")
        sizes.append(suggested_cash)
        colors.append('#BDBDBD')

    # 饼图
    wedges, texts, autotexts = ax1.pie(sizes, labels=None, autopct='%1.1f%%',
                                        colors=colors[:len(sizes)],
                                        startangle=90, pctdistance=0.85)
    ax1.set_title('仓位分配建议', fontproperties=zh_font, fontsize=14, pad=20)
    ax1.legend(wedges, labels, title="标的", loc="center left",
              bbox_to_anchor=(1, 0, 0.5, 1), prop=zh_font)

    # 柱状图：止盈止损区间
    names = [e.get("name", e.get("code", "?"))[:4] for e in entries]
    buy_prices = [float(str(e.get("phases", [{}])[0].get("price", 0)).replace(",", "")) for e in entries]
    stop_prices = [float(str(e.get("stop_loss", {}).get("price", 0)).replace(",", "")) for e in entries]
    target_prices = [float(str(e.get("take_profit", [{}])[0].get("target", 0)).replace(",", "")) for e in entries]

    x = range(len(names))
    width = 0.25

    bars1 = ax2.bar([i - width for i in x], stop_prices, width, label='止损', color='#F44336')
    bars2 = ax2.bar(x, buy_prices, width, label='买入', color='#4CAF50')
    bars3 = ax2.bar([i + width for i in x], target_prices, width, label='目标', color='#2196F3')

    ax2.set_xticks(x)
    ax2.set_xticklabels(names, fontproperties=zh_font)
    ax2.set_title('止盈止损区间', fontproperties=zh_font, fontsize=14)
    ax2.set_ylabel('价格 (元)', fontproperties=zh_font)
    ax2.legend(prop=zh_font)
    ax2.grid(axis='y', alpha=0.3)

    plt.tight_layout()

    # 保存
    chart_dir = "/tmp/congxi_v7_charts"
    os.makedirs(chart_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    chart_path = f"{chart_dir}/strategy_{today}.png"
    plt.savefig(chart_path, dpi=150, bbox_inches='tight')
    plt.close()

    logger.info(f"策略图表已生成: {chart_path}")
    return await push_chart_image(chart_path, "仓位分配 & 止盈止损")
    return True


def _extract_url(output: str) -> str:
    """从 lark-cli 输出中提取文档 URL"""
    for line in output.split("\n"):
        if "http" in line and ("feishu" in line or "lark" in line):
            # 提取 URL
            import re
            urls = re.findall(r'https?://[^\s]+', line)
            if urls:
                return urls[0]
    return ""

def _get_or_create_stock_base() -> str:
    """获取或创建选股池多维表格"""
    # 先尝试查找已有 Base
    ok, out, err = _run_lark("base", "list", timeout=10)
    if ok:
        for line in out.split("\n"):
            if "选股池" in line or "stock_pool" in line.lower():
                # 尝试提取 token
                import re
                tokens = re.findall(r'[A-Za-z0-9_-]{20,}', line)
                if tokens:
                    return tokens[0]

    # 创建新的 Base
    ok, out, err = _run_lark("base", "create", "--title", "恭喜发财V7 选股池", "--fields",
                             "股票代码,股票名称,推荐理由,买入区间,止损价,目标价,日期", timeout=30)
    if ok:
        logger.info("飞书多维表格「选股池」已创建")
        import re
        tokens = re.findall(r'[A-Za-z0-9_-]{20,}', out)
        if tokens:
            return tokens[0]
    return ""
