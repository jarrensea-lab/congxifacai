"""数据库初始化脚本 — v3.0 持仓由交易引擎管理"""
import os
import sys

# 添加后端目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from database import init_db, engine
from models import SimAccount


def init_sim_account():
    """初始化模拟账户 (如果不存在则创建)"""
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        existing = session.query(SimAccount).first()
        if existing:
            print(f"模拟账户已存在: 资金¥{existing.cash/100:,.2f}")
            return

        # 创建初始账户 (100万)
        account = SimAccount(
            cash=10000000,
            frozen=0,
            total_value=10000000,
            initial_capital=10000000,
        )
        session.add(account)
        session.commit()
        print(f"模拟账户初始化完成: 初始资金¥{account.initial_capital/100:,.2f}")


if __name__ == "__main__":
    print("初始化数据库...")
    init_db()
    init_sim_account()
    print("初始化完成!")
