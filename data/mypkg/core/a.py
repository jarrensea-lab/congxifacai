# a.py - 导入 b，但 b 也要导入 a，形成循环
from mypkg.core.b import hello_b

def hello_a():
    return "hello from a"
