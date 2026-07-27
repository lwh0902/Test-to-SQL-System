# DataPilot

**Version V1.5**

自然语言驱动的只读数据分析工作台。面向业务与运营同学：用中文提问即可完成库表理解、指标查询、结果可视化与可选深度诊断，无需手写 SQL。

---

## 产品定位

| 能力 | 说明 |
|------|------|
| 对话式查数 | 指标、趋势、拆维、筛选、相对时间（如「最近七天」） |
| 业务数据地图 | 基于 Catalog 的表职责导览，中文标题 + 用途说明 |
| 会话与空间 | 多空间（Space）隔离；会话消息持久化 |
| 只读安全 | L0 写意图拒绝、SQL AST 校验、查询结果结构化 Outcome |
| 深度诊断 | 显式请求时触发；依赖有效查数结果，不做空报告 |
| 流式体验 | JSON / SSE 双通道，前端玻璃拟态工作台 |

> 本版本为 **V1.5 专业内测基线**。生产级多日试点与完整验收仍按内部规范推进，请勿将本仓库表述为已正式上线交付。

---

## 架构（V1.5）

```
客户端 (React)
    │  HTTPS / SSE
    ▼
FastAPI 传输层（鉴权 · 限流 · 路由适配）
    │
    ▼
AnalysisApplicationService          ← 唯一业务入口
    │
    ├─ L0 写保护 / 数据面预检 / Catalog
    ├─ Supervisor（L1 Flash 主路径，L2 规则回退）
    └─ Dispatch（Hub-and-Spoke，禁止 Agent 互调）
           │
           ├─ Query Kernel      AnalysisSpec → SQL Compiler → Guarded MySQL
           ├─ Schema Inventory  Catalog 驱动业务导览 / data_map
           ├─ Diagnosis         Insight → Report → Review（A2A）
           └─ Chat / Help / Clarify / Summary Cite
```

**设计原则**

- **Hub-and-Spoke**：仅 Supervisor 决策与分派；Agent 之间不自由组网
- **最小唤醒**：普通查数不启动诊断 / 报告链路
- **QueryOutcome 为事实**：错误不伪装成「0 行」；无有效数据不出报告
- **Catalog 接地**：规划与过滤以真实库表与字段语义为准

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.10+ · FastAPI · Uvicorn · SQLAlchemy 2 · PyMySQL |
| 分析内核 | AnalysisSpec · SemanticCatalog · QueryHarness · Supervisor |
| 编排 / 诊断 | A2A Dispatcher · 受控 Procedure（非自由 ReAct） |
| LLM | Anthropic 兼容 API（默认 DeepSeek；可配置） |
| SQL 安全 | sqlglot AST · 只读策略 · 行预览上限 |
| 前端 | React 19 · TypeScript · Vite · Ant Design · ECharts |
| 存储 | MySQL（系统库 + 业务只读源） |

---

## 仓库结构

```
datacheck/
├── backend/                 # API 与分析内核
│   ├── app/
│   │   ├── api/             # HTTP / SSE 适配
│   │   ├── application/     # 应用服务入口
│   │   ├── agents/          # 分析 / 诊断 / Catalog / SQL
│   │   ├── a2a/             # 异步任务分发
│   │   ├── guards/          # SQL 与安全
│   │   ├── models/          # 对外 Schema
│   │   ├── pilot/           # 试点开关与准入
│   │   └── services/        # 会话、持久化、兼容图
│   ├── .env.example         # 环境变量模板（无密钥）
│   └── pyproject.toml
├── frontend/                # 工作台 UI
├── config/                  # 系统表与迁移 SQL
└── README.md
```

---

## 快速开始

### 1. 环境要求

- Python **3.10+**
- Node.js **20+**
- MySQL **8.x**
- 可用的 LLM API Key（Anthropic 兼容）

### 2. 数据库

```bash
# 初始化系统表（按实际路径调整）
mysql -u root -p < config/init_system_tables.sql
# 如有增量迁移
mysql -u root -p datacheck < config/migrations/005_agent_runtime.sql
mysql -u root -p datacheck < config/migrations/006_a2a_lease.sql
```

### 3. 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

cp .env.example .env
# 编辑 .env：填写 DB_*、LLM_API_KEY、JWT_SECRET、DB_ENCRYPTION_KEY
# 切勿将 .env 提交到 Git

uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：`GET http://127.0.0.1:8000/api/health`

### 4. 前端

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器打开：`http://127.0.0.1:5173`

### 5. 环境变量（摘要）

完整说明见 [`backend/.env.example`](backend/.env.example)。

| 变量 | 必填 | 说明 |
|------|------|------|
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | 是 | 系统库连接 |
| `LLM_API_KEY` | 是 | 模型密钥 |
| `LLM_BASE_URL` / `LLM_MODEL` | 否 | 默认 DeepSeek 兼容端点 |
| `JWT_SECRET` | 是 | 访问令牌签名 |
| `DB_ENCRYPTION_KEY` | 是 | AES-256-GCM，Base64 的 32 字节密钥 |
| `DATAPILOT_SUPERVISOR_L1` | 否 | 默认开启 L1；`0` 关闭 |
| `DATAPILOT_AUTO_MOCK` | 否 | 启动时自动挂载演示空间（默认开） |

**安全约定**：仓库只包含 `.env.example`；真实 `.env`、密钥、连接串不得入库。

---

## 核心能力说明（V1.5）

### 查询主链

1. 用户提问进入 `AnalysisApplicationService`
2. Supervisor 判定意图（查数 / 追问 / 库表理解 / 诊断等）
3. 查询路径：`AnalysisSpec` 规划 → 编译 SQL → 守卫执行 → 答案装配
4. 支持相对时间（最近 N 天 / 一周 / 本月等）与空间本地示例引导

### 库表导览

- 「这个库有什么表」「每个表是干嘛的」走 Catalog 人话导览
- 返回 `data_map` 结构化载荷，前端展示表职责卡片
- 推荐问题按当前 Space 生成，避免跨库错误示例

### 会话持久化

- 消息按 `user_id × space_id × session_id` 隔离写入
- 刷新后可恢复历史问答与关键元数据

### 深度诊断

- 需用户明确请求，且前置查询为有效数据结果
- 超时场景优先确定性证据回退，避免无意义 500

---

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录 |
| GET | `/api/spaces` | 空间列表 |
| POST | `/api/sessions` | 创建会话 |
| GET | `/api/sessions/{id}` | 会话与消息 |
| POST | `/api/chat` | 对话（JSON） |
| POST | `/api/chat/stream` | 对话（SSE） |
| GET | `/api/health` | 健康检查 |

鉴权：`Authorization: Bearer <access_token>`

---

## 开发约定

- 业务逻辑放在 `application/` 与 `agents/`，路由层只做传输适配
- 新增能力不得绕过 Supervisor 主链（禁止平行关键字路由器）
- 查询类能力默认不唤醒诊断 Agent
- 提交前确认：无 `.env`、无密钥、无本地导出物

---

## 版本

| 版本 | 说明 |
|------|------|
| **V1.5** | 分析内核主链、Catalog 业务导览、相对时间、会话隔离持久化、空间本地引导、诊断受控链路 |

---

## License

Private / Internal use. All rights reserved.
