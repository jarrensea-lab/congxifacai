# N8N AI 自动化大师课 — 学习笔记

> 课程来源：《N8N AI 自动化大师课：从零构建企业级工作流》
> 共 6 模块，29 个视频，30+ 个工作流模板

---

## 模块一：工作流基础知识（3 课）

### 1. AI 自动化的分级与应用

**核心内容：**
- **Level 0 — 规则自动化**：IFTTT 式 IF-THEN，无 AI 参与，确定性流程
- **Level 1 — 流程自动化 (RPA)**：屏幕点击 + 表单填充，模拟人工操作
- **Level 2 — AI 辅助自动化**：在关键节点嵌入 LLM，处理不确定输入
- **Level 3 — AI 原生工作流**：LLM 驱动决策，Agent 自主编排，动态执行路径

**关键概念：**
- 自动化的 ROI 随着 AI 渗透率提升呈指数增长
- n8n 定位：AI 原生工作流引擎，覆盖 Level 2-3
- 适合自动化的场景：重复性内容处理、多平台分发、数据聚合、定时报告

### 2. 为什么选择 N8N

**各大平台对比：**

| 维度 | n8n | Zapier | Make | Airflow |
|------|-----|--------|------|---------|
| 许可 | MIT（开源） | 付费 | 付费 | Apache 2.0 |
| 自托管 | ✅ | ❌ | ❌ | ✅ |
| AI Agent | ✅ 原生 | ✅ 付费 | ✅ | ❌ |
| 可视化 | ✅ | ✅ | ✅ | ❌ |
| 社区节点 | 150+ | 6000+ | 600+ | 插件 |
| 定价 | 免费/自托管 | $20+/月 | $9+/月 | 免费 |

**n8n 的核心优势：**
- 代码即工作流：每个节点都可以用 JavaScript 自定义
- LangChain 集成：原生支持 AI Agent、向量存储、Embedding
- 自托管数据主权：所有数据在自己服务器
- 社区节点系统：可安装第三方扩展

### 3. AI 工作流全景图

**n8n 架构核心原理：**
- **触发器 (Trigger)**：工作流的入口，分为定时触发 (Schedule)、手动触发、Webhook 触发、Chat 触发
- **节点 (Node)**：工作流的执行单元，每个节点完成一个原子操作
- **数据流 (Data Flow)**：节点间通过 JSON 数据传递，前一个节点的输出自动成为下一个节点的输入
- **工作流 (Workflow)**：节点的有向无环图 (DAG) 编排

**关键设计原则：**
- 无状态执行：每次执行独立，通过 Memory 节点维持状态
- 错误隔离：每个节点独立 try-catch，单点失败不影响全局
- 数据管道：数据在节点间流动，每个节点只做一件事

---

## 模块二：安装 N8N（3 课）

### 1. 本地部署 N8N 及避坑指南

**Docker 部署命令（Mac）：**
```bash
docker run -d --name n8n \
  -p 5678:5678 \
  -e GENERIC_TIMEZONE="Asia/Shanghai" \
  -e TZ="Asia/Shanghai" \
  -v ~/n8n:/home/node/.n8n \
  -v ~/n8ndata:/home/node/n8ndata \
  docker.n8n.io/n8nio/n8n
```

**Docker 部署命令（Windows PowerShell）：**
```powershell
docker run -d --name n8n -p 5678:5678 `
  -e GENERIC_TIMEZONE="Asia/Shanghai" `
  -e TZ="Asia/Shanghai" `
  -v D:/works/n8n:/home/node/.n8n `
  -v D:/works/n8ndata:/home/node/n8ndata `
  docker.n8n.io/n8nio/n8n
```

**常见问题与避坑：**
- **网络问题**：需要全局代理才能拉取镜像，MAC 设置 `export http_proxy="http://127.0.0.1:端口号"`
- **时区问题**：必须设置 `GENERIC_TIMEZONE` 和 `TZ`，否则 Schedule 触发器时间不准
- **数据持久化**：两个 volume 挂载缺一不可（`.n8n` 存配置，`n8ndata` 存文件）
- **端口冲突**：5678 端口可能被占用，可替换为其他端口映射

### 2. 升级 N8N 版本及还原工作流数据

**升级步骤：**
```bash
# 1. 拉取最新镜像
docker pull docker.n8n.io/n8nio/n8n:版本号

# 2. 清理旧容器
docker stop n8n && docker rm n8n

# 3. 运行新容器（volume 挂载路径必须一致！）
docker run -d --name n8n -p 5678:5678 \
  -e GENERIC_TIMEZONE="Asia/Shanghai" \
  -e TZ="Asia/Shanghai" \
  -v /本地路径/n8n:/home/node/.n8n \
  -v /本地路径/n8ndata:/home/node/n8ndata \
  docker.n8n.io/n8nio/n8n:版本号

# 4. 验证
docker exec n8n n8n --version
```

**注意事项：**
- 务必保持 volume 挂载路径一致，否则工作流数据丢失
- 建议每月更新一次，避免版本太旧
- 更新到最新版可不指定版本号（用 latest）

### 3. 云服务器安装及安全配置

**生产环境架构：**
- 面板管理：1Panel（国产 Docker 面板）
- 数据库：PostgreSQL 15（替代 SQLite，支持高并发）
- N8N 容器：挂载数据卷 + 环境变量配置

**PostgreSQL 部署：**
```bash
docker run -d --name n8n_postgres \
  -e POSTGRES_USER=用户名 \
  -e POSTGRES_PASSWORD=密码 \
  -e POSTGRES_DB=n8n \
  -p 5432:5432 \
  -v /opt/1panel/apps/postgres-data:/var/lib/postgresql/data \
  --restart unless-stopped \
  postgres:15
```

**安全配置：**
- 设置 `docker update --restart unless-stopped n8n` 自动重启
- 配置 N8N_HOST 和 WEBHOOK_URL
- 备份数据库：`tar -czvf n8n_postgres_backup.tar.gz .`
- 端口防火墙：仅开放 5678 和面板端口

---

## 模块三：快速上手 N8N（8 课）

### 1. N8N 操作界面及功能入口

**界面布局：**
- **左侧面板**：节点搜索与分类（核心节点、AI 节点、社区节点）
- **画布区域**：拖拽式工作流编排，支持缩放和搜索
- **右侧面板**：选中节点的参数配置
- **顶部工具栏**：保存、测试、执行、版本历史、分享

**功能入口：**
- Credentials：管理所有外部服务的认证（API Key、OAuth）
- Template Hub：官方工作流模板库
- Settings：用户设置、密码修改、插件管理

### 2. N8N 中文界面配置

**配置方法：**
- Settings → User → Language → 选择简体中文
- 部分节点名称是英文的（因为底层是英文包），但 UI 菜单全部中文

### 3. N8N 核心节点功能详解

**节点分类与核心类型：**

| 类别 | 节点类型 | 作用 |
|------|---------|------|
| 触发器 | Schedule Trigger | 定时触发（cron 表达式） |
| 触发器 | Manual Trigger | 手动测试触发 |
| 触发器 | HTTP Trigger / Webhook | HTTP 请求触发 |
| 触发器 | Chat Trigger | 聊天消息触发（AI 场景） |
| 数据操作 | Set (Edit Fields) | 设置/编辑字段值 |
| 数据操作 | Filter | 条件过滤 |
| 数据操作 | Switch | 多路分支路由 |
| 数据操作 | Merge | 合并多条数据流 |
| 数据操作 | Split In Batches | 分批处理 |
| 数据操作 | Split Out | 展平数组 |
| 数据操作 | Aggregate | 聚合多条数据 |
| 数据处理 | Code | 执行 JavaScript 代码 |
| 数据处理 | HTTP Request | HTTP 请求 |
| 数据处理 | Markdown | Markdown 格式化 |
| 数据处理 | HTML | HTML 解析 |
| 数据输出 | Notion | Notion API 操作 |
| 数据输出 | Telegram | Telegram Bot 消息 |
| 数据输出 | Gmail | 邮件发送 |
| 数据输出 | AWS S3 | 文件存储 |

**关键节点详解：**
- **Merge 节点**：支持"只匹配"(AND)、"添加附加"(APPEND)、"按索引"三种模式
- **Split In Batches**：将数组拆分成批次，逐个循环处理（类似 for-each）
- **Split Out**：将嵌套数组展开为独立数据流
- **Aggregate**：与 Split In Batches 配对，在循环结束后聚合所有结果

### 4. 将需求转换为 N8N 节点

**方法论：**
1. 明确触发条件 → 选择 Trigger 节点
2. 拆解处理步骤 → 选择数据处理节点
3. 确定数据源 → 选择输入节点（HTTP/API/RSS）
4. 确定目标应用 → 选择输出节点
5. 处理异常分支 → 添加 Filter/Switch/Error Trigger

**示例：从 RSS 获取文章并写入 Notion**
- RSS Feed Read（获取文章） → Markdown（格式化） → Split In Batches（分批） → Notion（写入）

### 5. 常用变量功能

**n8n 的数据引用语法：**
- `{{ $json.fieldName }}` — 引用上一个节点的字段
- `{{ $node["NodeName"].json.fieldName }}` — 引用指定节点的字段
- `{{ $item.index }}` — 当前数据项索引
- `{{ $binary.data.url }}` — 引用二进制文件 URL

**Set 节点的用法：**
- 创建/修改变量
- 拼接字符串
- 设置固定值作为后续节点输入
- 数据格式转换

### 6. 授权复杂节点实操

**Credentials 管理：**
- 在节点配置中点击"Add Credential"
- 支持的认证方式：API Key、Bearer Token、OAuth 1.0/2.0、Basic Auth
- 凭证可复用：同一凭证可在多个节点中引用

**第三方 API 授权流程：**
- Notion：创建 Integration → 获取 API Key → 在 N8N 添加凭证
- Telegram：BotFather 创建 Bot → 获取 Token → 在 N8N 添加凭证
- AWS S3：Access Key + Secret Key → 在 N8N 添加凭证

### 7. N8N 工作流必备工具

**企业级工作流工具箱：**
- **Error Trigger**：捕获工作流中所有未处理的错误
- **Wait 节点**：暂停执行，等待条件满足后继续
- **Execute Workflow**：子工作流调用，模块化编排
- **Form Trigger**：表单触发，收集用户输入
- **Sticky Note**：画布注释，标注说明

### 8. 内网穿透（ngrok/tailscale）

**原理：** 将本地 N8N 服务暴露到公网，使外部 Webhook 能触发本地工作流

**使用场景：**
- 接收外部服务（如微信、飞书）的 Webhook 回调
- 本地开发调试 AI Agent
- 演示工作流给远程用户

---

## 模块四：N8N 工作流实战（6 课）

### 1. 构建第一个 N8N 工作流

**入门示例：定时获取 AI 资讯并同步到 Notion**
- 触发器：Schedule Trigger（每天 9:00）
- 数据源：RSS Feed Read（机器之心、新智元、量子位等 RSS）
- 处理：Markdown 格式化 + Filter 筛选
- 输出：Notion 写入数据库

**核心概念实践：**
- Cron 表达式设置
- 多 RSS 源 + Merge 节点合并
- 去重逻辑（Merge 节点筛选 A 减 B）

### 2. AI 节点详解与实战

**AI 节点类型体系：**

| 节点 | 用途 |
|------|------|
| AI Agent | LangChain Agent，可调用工具和 Memory |
| LLM Chain | 简单 LLM 链（Prompt→LLM→输出） |
| Chat Model | 指定聊天模型（OpenAI/DeepSeek） |
| Structured Output Parser | 解析 LLM 输出为 JSON Schema |
| Tool | 工具节点（SerpAPI 等） |
| Memory | 对话记忆（Window Buffer / Postgres Chat） |
| Vector Store | 向量数据库（Pinecone/Supabase） |

**实战：AI Agent + SerpAPI 搜索**
- Chat Trigger（接收聊天消息） → AI Agent → OpenAI Chat Model → SerpAPI Tool
- Agent 自动决定是否需要搜索，调用工具后整合回答

### 3. 强化 AI Agent：Memory 数据库

**Memory 类型对比：**

| 类型 | 特点 | 适用场景 |
|------|------|---------|
| Window Buffer | 仅保留最近 N 条对话 | 轻量级、短期记忆 |
| Postgres Chat Memory | 持久化存储全部对话 | 生产级、长期记忆 |
| Simple Buffer | 内存缓存，重启丢失 | 临时测试 |

**配置步骤：**
- Window Buffer：设置 window size（默认 10 条）
- Postgres Chat Memory：创建 PostgreSQL 数据库 → 创建表 → 配置连接参数
- Memory 使 Agent 记住历史对话和上下文

### 4. 构建知识库问答工作流（RAG）

**RAG 架构：**
```
文档 → Text Splitter → Embedding → Vector Store → 检索 → LLM 回答
```

**技术栈：**
- 文档加载：Google Drive / Default Data Loader
- 文本切分：Recursive Character Text Splitter
- Embedding：OpenAI Embeddings
- 向量存储：Supabase Vector Store / Pinecone
- 问答触发：Chat Trigger + AI Agent + Vector Store 检索

**数据流：**
1. 手动触发 → 读取 Google Drive 文档
2. 文本切分 → 生成 Embedding
3. 存入 Supabase Vector Store
4. 用户聊天 → 检索相关文档片段
5. LLM 基于检索内容生成回答

### 5. Code 节点实战

**Code 节点 = JavaScript 万能节点：**
- 数据格式转换（JSON ↔ CSV ↔ 自定义格式）
- 字符串处理（正则匹配、拆分、拼接）
- 条件逻辑（复杂 if-else 分支）
- 调用外部 API（fetch 请求）
- 数据处理（排序、过滤、聚合）

**实战示例：**
- RSS 内容清洗 → Code 节点提取正文
- 数据过滤 → Code 节点按规则筛选
- 格式转换 → Code 节点将 Markdown 转 HTML

### 6. 社区节点探索

**安装方式：**
- npm 安装：`npm install @n8n/n8n-nodes-langchain`（LangChain 节点）
- 社区节点安装文档：https://docs.n8n.io/integrations/community-nodes/
- 手动安装：下载节点包 → 复制到 n8n 的 nodes 目录

**重要社区节点：**
- LangChain 节点包：AI Agent、Vector Store、Embedding、Memory
- 飞书节点：`@n8n/n8n-nodes-feishu-lite`
- Notion MD：Notion Markdown 节点

---

## 模块五：企业级工作流开发（5 课）

### 1. AI 爬虫工作流

**架构：**
```
Schedule Trigger → HTTP Request（爬取接口） → Code（数据解析）
→ Split In Batches → HTTP Request（逐页获取）
→ Notion（存入数据库）
```

**技术栈：**
- Firecrawl：网页爬取 API（`firecrawl_DEMO.json`）
- 本地爬虫接口：自定义 HTTP 爬取服务
- 多 RSS 源聚合 + 去重

**关键点：**
- Code 节点处理 HTML → Markdown 转换
- Split In Batches 分批处理大量数据
- Merge 节点实现去重（已有 vs 新增）

### 2. 全自动日报工作流（34 个节点）

**完整工作流：**
```
Schedule Trigger (定时)
  → Notion（拉取当日待处理文章）
  → Code（数据清洗）
  → AI Agent（内容筛选 + DeepSeek + Structured Output）
  → Split Out（展开结果数组）
  → Loop Over Items（逐条处理）
    → Notion（写入日报文档）
    → Code（URL 处理）
    → HTTP Request（获取截图）
    → Edit Image（图片处理）
    → AWS S3（存储图片）
    → HTTP Request（TTS 合成音频）
    → AWS S3（存储音频）
    → Telegram（发送文字 + 图片 + 音频）
    → HTTP Request（微信群推送）
```

**关键节点：**
- AI Agent + DeepSeek：智能筛选文章内容
- Structured Output Parser：确保输出为 JSON Schema
- Split Out + Split In Batches：处理批量数据
- AWS S3：媒体文件存储
- Edit Image：截图处理
- TTS（火山引擎）：音频生成
- Telegram Bot：多渠道分发

### 3. 自动生成短视频工作流（35 个节点）

**完整流程：**
```
Form Trigger（用户输入主题）
  → LLM Chain（生成视频脚本）
  → AI Agent（扩展脚本 + Structured Output）
  → Split Out（拆分为多场景）
  → Loop Over Items（逐场景处理）
    → HTTP Request（生成图片/视频）
    → Wait（等待异步任务完成）
    → HTTP Request（查询任务状态）
    → If（判断是否完成）
    → HTTP Request（下载视频）
    → Read/Write File（写入磁盘）
  → Execute Command（ffmpeg 拼接）
  → Read/Write File（输出最终视频）
```

**技术栈：**
- DeepSeek Chat Model：脚本生成
- Replicate API：AI 图片/视频生成
- ffmpeg：视频拼接和转码
- Execute Command 节点：执行系统命令

### 4. 微信群聊摘要工作流

**架构：**
```
Manual Trigger → MCP Client（调用外部工具获取群消息）
→ Split Out → Loop Over Items
→ AI Agent（OpenAI Chat Model）
→ Notion（存档摘要）
```

**核心能力：**
- MCP Client：Model Context Protocol，调用外部 AI 工具
- AI Agent：自动总结群聊内容，提取关键信息
- 无封号风险：模拟正常用户频率

### 5. 抖音运营神器

**功能：**
- 监控对标账号数据
- 下载无水印视频
- 提取视频文案
- 数据存入 Notion

**技术栈：**
- TikHub API / Douyin TikTok Download API
- 父/子工作流架构：父工作流分发，子工作流并行处理多账号
- Form Trigger：用户输入账号列表
- Execute Workflow：并行触发子工作流

---

## 模块六：自媒体工作流开发（3 课）

### 1. 低粉爆款视频采集

**工作流（41 个节点）：**
```
Form Trigger → Edit Fields（设置参数）
  → Filter（筛选粉丝数）
  → Filter（筛选点赞/评论/收藏）
  → Code（数据格式化）
  → Notion（创建记录）
  → Remove Duplicates（去重）
  → Merge → 飞书通知（结果推送）
  → AI 转录（OpenAI Whisper）
  → 多线路 HTTP 请求（数据采集）
```

**筛选逻辑：**
- 低粉（粉丝 < X 万）+ 高互动（赞/评/藏 > Y）
- 自动去重，避免重复采集
- 飞书通知推送新发现爆款

### 2. 一键"复刻"爆款短视频

**工作流（33 个节点）：**
```
Manual Trigger → Notion（获取爆款视频列表）
  → HTTP Request（下载无水印视频）
  → Read/Write File（存储视频/封面）
  → OpenAI Whisper（音频转录）
  → AI Agent（翻拍文案生成）
  → Structured Output Parser
  → Code（文案处理）
  → Loop Over Items（逐条处理）
  → HTTP Request（素材搜索）
  → DeepSeek Chat Model（素材匹配）
  → Execute Command（ffmpeg 处理）
  → 输出翻拍视频
```

**核心链路：**
1. 获取爆款视频 → 下载 → 转录文案
2. AI Agent 改写文案（保留核心结构，换表达）
3. Pexels 免费素材匹配
4. ffmpeg 视频合成

### 3. 小红书自动创作

**工作流（39 个节点）：**
```
Form Trigger（输入主题 + 风格）
  → AI Agent（文案生成）
  → DeepSeek / OpenAI（文案优化）
  → Structured Output Parser
  → Edit Image（封面生成，多风格支持）
  → Execute Command（字体渲染）
  → HTTP Request（AI 图片生成）
  → HTTP Request（AI 视频生成）
  → Wait → HTTP Request（查询进度）
  → Read/Write File（输出成品）
```

**风格模板：**
- 3D 风格、手写风格、极简风格、赛博朋克、复古、霓虹、扁平、涂鸦、自然、像素、书法

---

## 附：全部工作流模板索引

| 模块 | 文件 | 节点数 | 核心节点 |
|------|------|--------|---------|
| 3 | RSS文章同步.json | 30 | RSS + Merge + Filter + Markdown + Notion |
| 3 | RSStoNotion.json | 11 | RSS Trigger + AI Agent + Notion |
| 3 | 合并和聚合节点的区别.json | 28 | Merge + Aggregate + SplitInBatches |
| 3 | 日报工作流.json | 34 | AI Agent + DeepSeek + Telegram + S3 |
| 3 | 判断节点.json | 4 | Gmail + If + HTTP Request |
| 3 | 变量Demo.json | 8 | Set + Filter + RSS |
| 4 | AI_agent_demo.json | 7 | Agent + SerpAPI + Memory |
| 4 | 知识库工作流直连supabase.json | 12 | Vector Store + Embedding + Agent |
| 4 | RSSTOFile.json | 12 | RSS + HTML + Markdown + File |
| 4 | Code节点实战DEMO.json | 15 | Code + Switch + Notion |
| 4 | firecrawl_DEMO.json | 11 | Firecrawl + HTTP + Notion |
| 5 | AI_agent_Memory.json | 6 | Agent + Postgres Memory |
| 5 | 家庭菜谱.json | 8 | Agent + DeepSeek + Telegram |
| 5 | 微信群聊总结工作流.json | 8 | MCP Client + Agent + Notion |
| 5 | 抖音多账户监控工作流-父.json | 13 | Form + ExecuteWorkflow + SplitOut |
| 5 | 抖音多账户监控工作流-子.json | 14 | ExecuteWorkflow + Code + HTTP |
| 5 | 错误通知工作流.json | 2 | ErrorTrigger + Gmail |
| 5 | 自动生成短视频工作流.json | 35 | Agent + Replicate + ffmpeg |
| 5 | Code节点实战DEMO.json | 15 | RSS + Code + Notion |
| 5 | 知识库工作流直连supabase.json | 12 | Supabase Vector + Agent |
| 6 | 小红书内容创作工作流.json | 39 | Agent + EditImage + HTTP(视频/图片) |
| 6 | 小红书自动创作.json | 18 | OpenAI + DeepSeek + 风格模板 |
| 6 | 抖音低粉爆款筛选工作流.json | 41 | Filter + TikHub + Notion + 飞书 |
| 6 | 一键"复刻"爆款短视频.json | 33 | Whisper + Agent + ffmpeg + Pexels |

---

## 学习路径建议

按以下顺序学习，每个模块完成对应的工作流模板实操：

1. **模块一**（理论）→ 理解 AI 自动化分级和 n8n 定位
2. **模块二**（环境）→ Docker 本地部署 + 升级
3. **模块三**（基础）→ 先做 `变量Demo.json`，再做 `RSS文章同步.json`
4. **模块四**（进阶）→ 做 `AI_agent_demo.json` → `知识库工作流直连supabase.json`
5. **模块五**（企业级）→ 做 `日报工作流.json` → `自动生成短视频工作流.json`
6. **模块六**（实战）→ 做 `抖音低粉爆款筛选工作流.json` → `小红书内容创作工作流.json`
