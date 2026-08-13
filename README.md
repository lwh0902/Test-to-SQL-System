# DataPilot

**面向企业业务团队的可信自然语言数据分析工作台**

DataPilot 让业务、运营和产品人员直接用自然语言理解数据、追问指标、拆解趋势和定位异常；同时把“能回答”建立在可控语义、可验证 SQL 和严格数据源权限之上。它不是把问题直接交给模型写 SQL，而是一条可审计、可回归、可上线的分析链路。

---

## 为什么选择 DataPilot

| 优势 | 价值 |
|------|------|
| **业务语义接地** | 以 Catalog、业务语义模型和实体字典约束模型理解，降低“看似合理、实际查错”的风险。 |
| **端到端可控** | 自然语言 → 结构化 AnalysisSpec → 参数化 SQL → 只读执行 → 结果解释；每一段都有明确输入输出。 |
| **可信追问体验** | 支持趋势、拆维、筛选、实体名纠正和缺失时间确认，不用要求业务人员懂数据库。 |
| **生产级数据源边界** | 公共空间可查不可改；私有空间、会话和数据库连接均按用户所有权实时校验。 |
| **默认安全执行** | 模型不直接执行 SQL；只允许编译器生成的单条只读查询，绑定参数、超时和危险能力拦截全程生效。 |
| **可观测与可回归** | JSON/SSE 同一业务主链，统一 QueryOutcome、Trace 与自动化测试，便于持续评测准确率。 |

> 当前版本为专业内测主线。仓库内置完整安全边界和自动化回归，但真实生产上线仍应完成数据库迁移、只读账号授予、网络出站白名单和部署环境验收。

---

## 核心架构

```
客户端 (React)
    │  HTTPS / SSE
    ▼
FastAPI 传输层（鉴权 · 限流 · 路由适配）
    │
    ▼
AnalysisApplicationService          ← 唯一业务入口
    │
    ├─ 空间授权 / 连接解析 / Catalog
    ├─ Supervisor（功能策略 + LLM 语义理解）
    └─ Dispatch（Hub-and-Spoke，禁止 Agent 互调）
           │
           ├─ Query Kernel      AnalysisSpec → 参数化 SQL → Guarded MySQL
           ├─ Schema Inventory  Catalog 驱动业务导览 / data_map
           ├─ Diagnosis         Insight → Report → Review（A2A）
           └─ Chat / Help / Clarify / Summary Cite
```

**设计原则**

- **Hub-and-Spoke**：仅 Supervisor 决策与分派；Agent 之间不自由组网
- **最小唤醒**：普通查数不启动诊断 / 报告链路
- **QueryOutcome 为事实**：错误不伪装成「0 行」；无有效数据不出报告
- **Catalog 接地**：规划与过滤以真实库表与字段语义为准
- **Fail Closed**：私有空间没有有效、归属匹配的数据源时拒绝执行，不走缓存或默认连接兜底

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.10+ · FastAPI · Uvicorn · SQLAlchemy 2 · PyMySQL |
| 分析内核 | AnalysisSpec · SemanticCatalog · QueryHarness · Supervisor |
| 编排 / 诊断 | A2A Dispatcher · 受控 Procedure（非自由 ReAct） |
| LLM | Anthropic 兼容 API（默认 DeepSeek；可配置） |
| SQL 安全 | AnalysisSpec 编译 · 参数绑定 · sqlglot AST · 只读策略 · 数据库侧超时 |
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
mysql -u root -p datacheck < config/migrations/007_semantic_models.sql
mysql -u root -p datacheck < config/migrations/008_managed_space_connections.sql
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
| `DATAPILOT_DB_ALLOWED_HOSTS` | 生产必填 | 私有 MySQL 可连接的精确域名或 CIDR 白名单，例如 `db.company.internal,10.20.0.0/16` |

**安全约定**：仓库只包含 `.env.example`；真实 `.env`、密钥、连接串不得入库。业务数据源必须使用最小权限的只读账号。

---

## 核心能力说明（V1.5）

### 查询主链

1. 用户提问进入 `AnalysisApplicationService`
2. Supervisor 判定功能意图（查数 / 追问 / 库表理解 / 诊断等）
3. 语义解析将问题落为受约束的 `AnalysisSpec`，缺少必要时间范围时主动确认
4. 查询路径：受权数据源 → 参数化 SQL 编译 → 守卫执行 → 结果解释
5. 支持相对时间、趋势、拆维、实体名称精确匹配与空间本地示例引导

### 库表导览

- 「这个库有什么表」「每个表是干嘛的」走 Catalog 人话导览
- 返回 `data_map` 结构化载荷，前端展示表职责卡片
- 推荐问题按当前 Space 生成，避免跨库错误示例

### 会话持久化

- 消息按 `user_id × space_id × session_id` 隔离写入
- 刷新后可恢复历史问答与关键元数据

### 数据源与权限

- **公共空间**：所有已登录用户可查询；只有管理员可以变更连接或重新建档。
- **私有空间**：空间、会话与关联连接必须属于同一用户；每次运行再次核验。
- **连接防护**：不提供任意主机/端口探测接口；私有数据源必须命中部署方出站白名单。
- **执行防护**：拒绝写操作、多语句、文件读写、锁与延迟函数；查询参数不拼接进 SQL，并设置数据库侧执行上限。

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
| GET/POST | `/api/connections` | 私有数据源连接管理（所有者范围） |
| GET | `/api/health` | 健康检查 |

鉴权：`Authorization: Bearer <access_token>`

---

## 开发约定

- 业务逻辑放在 `application/` 与 `agents/`，路由层只做传输适配
- 新增能力不得绕过 Supervisor 主链或运行期空间授权
- 查询类能力默认不唤醒诊断 Agent
- 提交前确认：无 `.env`、无密钥、无本地 mock、无测评产物和本地导出物

---

## 版本

| 版本 | 说明 |
|------|------|
| **专业内测主线** | 语义查询主链、LLM Supervisor、时间确认、实体字典、统一 JSON/SSE、私有数据源所有权校验、参数化只读 SQL 执行 |

---

## License

Private / Internal use. All rights reserved.
