"""数据库初始化脚本"""
import os
import sys

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db


def main():
    """初始化数据库"""
    print("正在初始化数据库...")
    init_db()
    print("数据库初始化完成!")


if __name__ == "__main__":
    main()
