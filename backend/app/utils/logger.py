"""日志工具"""
import logging
import sys


def setup_logger(name: str = "congxi", log_file: str = None) -> logging.Logger:
    """设置日志配置"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 避免重复添加处理器
    if logger.handlers:
        return logger

    # 格式化器
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件处理器
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


logger = setup_logger()
