# 恭喜发财 - 快速开始指南

## 系统要求

- Python 3.10+
- Node.js 18+
- Ollama (用于运行 Qwen3.5 模型)

## 第一步：安装和启动 Ollama

```bash
# macOS
brew install ollama

# 拉取模型
ollama pull qwen3.5:35b-a3b-q4_K_M

# 启动服务
ollama serve
```

## 第二步：配置环境

```bash
# 复制环境配置
cp .env.example .env.local

# 编辑.env.local，填写你的飞书 Webhook URL
# FEISHU_WEBHOOK_URL=你的飞书机器人 Webhook
```

## 第三步：安装依赖

```bash
# 后端依赖
cd backend
pip install -r requirements.txt

# 前端依赖
cd ../frontend
npm install
```

## 第四步：初始化数据库

```bash
cd ../scripts
python init_data.py
```

## 第五步：启动应用

### 方法一：使用启动脚本 (推荐)
```bash
cd ../scripts
./start_all.sh
```

### 方法二：分别启动

**后端:**
```bash
cd ../backend
python app/main.py
```

**前端:**
```bash
cd ../frontend
npm run dev
```

## 第六步：访问应用

打开浏览器访问：http://localhost:8000

## 常用操作

### 添加持仓
1. 进入"持仓管理"
2. 填写股票代码、名称、持仓数量、成本价
3. 点击"添加持仓"

### 查看 AI 分析
1. 进入"AI 分析"
2. 点击"刷新"按钮生成盘前策略

### 查看风险提醒
- 实时风险监控在"仪表盘"页面自动刷新

## 问题排查

### Ollama 无法启动
```bash
# 检查 Ollama 状态
ollama list

# 重新启动
ollama serve
```

### 前端无法访问
```bash
# 检查后端是否运行
curl http://localhost:8000/api/health

# 确认前端构建
cd frontend
npm run build
```

### 数据库问题
```bash
# 重新初始化数据库
cd ../scripts
python init_data.py
```

## 下一步

详细文档请查看：[docs/projects/cong-xi-fa-cai-design.md](../docs/projects/cong-xi-fa-cai-design.md)
