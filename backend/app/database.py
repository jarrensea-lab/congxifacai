"""数据库连接和管理"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

# 支持 CONGXI_DATABASE_PATH 环境变量覆盖（CI/测试环境使用临时数据库）
import os
_db_path = os.environ.get("CONGXI_DATABASE_PATH") or settings.DATABASE_PATH

# 创建数据库引擎
engine = create_engine(
    f"sqlite:///{_db_path}",
    connect_args={"check_same_thread": False, "timeout": 30}
)

# 启用 WAL 模式 (支持并发读写) + 外键约束
from sqlalchemy import event
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基类
Base = declarative_base()


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库"""
    # 先导入所有模型，确保 Base.metadata 完成注册再建表
    from app.models import (SimAccount, Position, TradingOrder, TradeLog,
                            TradingSignal, RiskAlert, AIStrategy, StrategyInstance,
                            ReviewLog, DebateResult, UserPreference, PushRecord)

    Base.metadata.create_all(bind=engine)
    # 自动初始化模拟账户（仅当不存在时）
    from sqlalchemy.orm import Session
    with Session(engine) as session:
        if not session.query(SimAccount).first():
            session.add(SimAccount())
            session.commit()
