"""pytest 全局配置 — 使用临时数据库，避免 CI 缺少 data/ 目录"""
import os
import tempfile


def pytest_configure(config):
    """在导入任何应用模块前，用临时路径覆盖数据库路径"""
    tmp_dir = tempfile.mkdtemp(prefix="congxi_test_")
    db_path = os.path.join(tmp_dir, "test.db")
    os.environ["CONGXI_DATABASE_PATH"] = db_path
    # 确保目录存在
    os.makedirs(tmp_dir, exist_ok=True)
    # 存储临时目录以便清理
    config._congxi_test_dir = tmp_dir

    # 一次性建表（而不是在每个测试的 setup_method 中重复调用）
    from app.database import init_db
    init_db()


def pytest_unconfigure(config):
    """测试结束后清理临时数据库"""
    tmp_dir = getattr(config, "_congxi_test_dir", None)
    if tmp_dir and os.path.exists(tmp_dir):
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
