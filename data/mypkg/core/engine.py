# engine.py - 从 utils 导入函数

# 方式 1: 绝对导入 - 从 mypkg 包导入
from mypkg.utils import greet

def run(name):
    """使用导入的函数"""
    message = greet(name)
    return f"[engine] {message}"
