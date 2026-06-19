"""回复格式化 — 将处理结果转换为飞书消息文本"""
from typing import Dict


def format_reply(result: Dict) -> str:
    """将操作结果格式化为友好的回复文本"""
    action = result.get("action", "")
    if action == "account_updated":
        return f"✅ 账户已更新: 总资产 ¥{result['total']:.2f} 现金 ¥{result['cash']:.2f}"
    elif action == "buy":
        return f"✅ 买入: {result['name']}({result['code']}) {result['qty']}股 @¥{result['cost']:.3f}"
    elif action == "sell":
        return f"✅ 卖出: {result['name']}({result['code']}) {result['qty']}股 @¥{result['price']:.2f} PnL=¥{result.get('pnl',0):.2f}"
    return "✅ 操作完成"
