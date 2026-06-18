#!/bin/bash
# 恭喜发财 - 启动脚本

echo "================================"
echo "  恭喜发财 - A 股智能监控系统"
echo "================================"
echo ""

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "错误：未找到 Python3，请先安装 Python3"
    exit 1
fi

# 检查 pip
if ! command -v pip3 &> /dev/null; then
    echo "错误：未找到 pip3"
    exit 1
fi

# 检查 Ollama
if ! command -v ollama &> /dev/null; then
    echo "警告：未找到 Ollama，请先安装并启动 Ollama 服务"
    echo "安装：brew install ollama"
    echo "启动：ollama serve"
    echo ""
    read -p "按回车键继续..."
fi

# 检查模型
echo "检查 Ollama 模型..."
ollama list | grep -q "qwen3.5" || {
    echo "警告：未找到 Qwen3.5 模型，正在拉取..."
    ollama pull qwen3.5:35b-a3b-q4_K_M
}

# 启动 Ollama（如未运行）
echo ""
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "Ollama 已在运行"
else
    echo "启动 Ollama 服务..."
    ollama serve &
    OLLAMA_PID=$!
    echo "Ollama PID: $OLLAMA_PID"
    sleep 3
fi

# 安装 Python 依赖
echo ""
echo "安装 Python 依赖..."
pip3 install -r requirements.txt

# 初始化数据库
echo ""
echo "初始化数据库..."
cd backend
python3 app/init_db.py

# 启动应用
echo ""
echo "启动应用服务..."
echo "访问地址：http://localhost:8000"
python3 app/main.py

# 清理
cleanup() {
    echo ""
    echo "正在停止 Ollama 服务..."
    kill $OLLAMA_PID 2>/dev/null
    exit
}

trap cleanup SIGINT SIGTERM
