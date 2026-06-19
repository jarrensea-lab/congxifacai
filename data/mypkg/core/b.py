# b.py - 延迟导入，避免循环导入
def hello_b():
    from mypkg.core.a import hello_a
    return "hello from b"
