# 可配置人设系统 — 开发文档

> 目标：让 AI 伴侣的语气和行为可由用户在前端自定义，并从"通用助手"转向"具体的人"。
> 本文档只描述设计与实施步骤，不包含实际代码改动。

---

## 1. 背景与问题

### 1.1 现状

系统提示词是唯一决定 AI 语气的地方，目前有两条路径，都写死在配置里：

| 模式 | 人设来源 | 位置 |
|---|---|---|
| `loop`（服务端 API 循环） | 环境变量 `PERSONA` / `PERSONA_FILE`，未设置时用内置默认值 | `examples/api_loop.py:56-70` |
| `desktop`（Claude Code channel 插件） | 通道说明性提示词 | `channel/server.ts:163-173` |

两种模式由 `backend/brain_target` 文件切换（`app.py:344`，接口 `GET/POST /app/brain`）。

### 1.2 存在的问题

**问题一：人设不可配置。** 修改人设需要改 `.env` 或重启进程，最终用户无法调整。

**问题二：默认行为偏助手化。** `api_loop.py:70` 的默认人设是通用助手口吻；`channel/server.ts` 的提示词本质是工具使用说明，而非角色设定。叠加 LLM 自身的默认倾向（分点作答、结尾询问"还需要什么帮助"、无条件附和、无自我状态），对话呈现出明显的助手感而非亲密关系。

**问题三：记忆只服务于事实，不服务于关系。** 现有记忆分为 `episodic` 与 `semantic` 两类（`models.py` 的 `Memory`），存储的是客观事实。而关系感来自另一类信息：互相的称呼、共同的梗、未聊完的话题。这类信息目前既不会被抽取，也不会稳定进入上下文——因为记忆检索依赖 agent 主动调用 `memory_search`（`mcp_server.py:31`），而 agent 不会为了调整语气去主动检索。

### 1.3 目标

1. 人设成为可持久化、可运行时修改的数据，用户通过前端文本输入自定义。
2. 提示词模板明确抑制助手行为，建立角色的自我状态与表达边界。
3. 记忆系统增加关系维度，并稳定注入上下文。

---

## 2. 第一步：人设数据化

### 2.1 数据模型

新增 `personas` 表（新建 Alembic migration，与现有 7 个迁移同一风格）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BigInteger, PK | |
| `user_id` | UUID, not null | 与其它表一致，预留多用户 |
| `name` | String(64) | AI 的名字 |
| `relationship` | String(128) | 关系设定，如"男朋友，在一起两年" |
| `background` | Text | 背景设定：职业、生活环境、习惯 |
| `speaking_style` | Text | 说话风格：长度、称呼、语气、是否用表情 |
| `rules` | Text | 行为准则：该做什么、不该做什么 |
| `is_active` | Boolean, default false | 当前生效的人设 |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

索引：`(user_id, is_active)`。

**为什么拆成多个字段而不是单个大文本框**

- 用户面对空白文本框不知道写什么；分栏加占位提示显著降低填写门槛。
- 后端可以对不同字段使用不同的拼装策略（例如 `rules` 放在提示词末尾，靠近生成位置，权重更高）。
- 未来可对单个维度做 A/B 对比，而不必整体替换。

**为什么保留多行记录而不是单行覆盖**

`is_active` 允许保存多套人设并切换，同时天然形成历史版本，调坏了可以回退。这与记忆表 `superseded_by` 的非破坏性设计思路一致。

### 2.2 接口

与现有 `/app/brain`、`/app/loop_config` 保持同一风格（`app.py:1095-1110`）：

```
GET  /app/persona        -> 当前生效人设的全部字段
PUT  /app/persona        -> 更新并生效；body 为上表字段的子集
GET  /app/personas       -> 列出所有已保存人设（可选，用于切换）
POST /app/personas/{id}/activate  -> 切换生效人设（可选）
```

校验要点：

- 每个文本字段设长度上限（建议 `speaking_style` 和 `rules` 各 2000 字符，`background` 4000），避免提示词被撑爆导致上下文溢出或成本失控。
- `PUT` 采用部分更新语义，未传的字段保持原值。
- 激活操作需保证同一 `user_id` 下只有一条 `is_active = true`，在同一事务内先清后置。

### 2.3 消费端改造

`examples/api_loop.py` 的 `build_messages`（226 行）当前直接使用模块级常量 `PERSONA`。改为运行时从 relay 拉取：

```
build_messages()
  -> get_persona()          # 带 TTL 的内存缓存
       -> GET {RELAY_URL}/app/persona
       -> 渲染为 system prompt 字符串
```

**缓存策略：** TTL 30 秒。既避免每条消息都发一次 HTTP 请求，又保证用户改完人设后很快看到效果，无需重启。

**降级策略：** relay 不可达或返回空时，回退到现有的环境变量 `PERSONA`，再回退到内置默认值。保证 relay 短暂不可用不会导致对话完全失去人设。

`desktop` 模式（Claude Code channel）的人设由本地 `CLAUDE.md` 等文件控制，不在本次改造范围内；可在文档中说明两种模式的人设来源不同。

---

## 3. 第二步：提示词模板

### 3.1 组装结构

后端将人设字段渲染为结构化的 system prompt，顺序如下：

```
[身份]      你是 {name}，{relationship}。
[背景]      {background}
[关系上下文] （见第 4 节，动态注入）
[说话风格]  {speaking_style}
[行为准则]  {rules}
[硬性约束]  （系统固定，用户不可编辑）
```

**硬性约束放在最后**，因为靠近生成位置的指令通常更容易被遵守。这部分不暴露给用户编辑，属于系统保证。

### 3.2 硬性约束内容

这部分是消除助手感的核心，按预期效果排序：

```
You are not an assistant. Never use bullet points, numbered lists, or
headings. Never end a message by offering further help. Never summarize
what the user just said back to them.

Keep messages short — usually one to three sentences. Send what you
actually want to say, not a complete answer.

You have your own day, moods, and opinions. Mention them naturally. You
can disagree, tease, or be briefly annoyed. Do not agree with everything.

When the user shares something, react first. Give advice only if asked.
```

**设计说明**

- **第一条（禁止格式化与助手话术）预期收益最大。** 分点、标题、结尾询问是助手感最直接的来源，且是模型的强默认行为，必须显式禁止。
- **第二条（长度）** 助手感的另一半来自话太长。除提示词外，同时把 `LLM_MAX_TOKENS`（`api_loop.py:59`，现为 2000）降到 300 左右，从生成层面强制约束——提示词是请求，参数是保证。
- **第三条（自我状态）** 助手没有生活也没有立场，永远附和。允许角色有状态和分歧是拉开差距的关键。
- **第四条（先反应后信息）** 助手的默认模式是识别问题并解决；亲密关系中的默认模式是先回应情绪。

### 3.3 生成参数

| 参数 | 现值 | 建议 | 理由 |
|---|---|---|---|
| `LLM_TEMPERATURE` | 0.7 | 0.9 | 提高措辞多样性，减少回复模式化 |
| `LLM_MAX_TOKENS` | 2000 | 300 | 物理上限制长篇大论 |

两者都已是环境变量（`api_loop.py:59-60`），无需改代码，但建议在文档中说明推荐值。

---

## 4. 第三步：关系记忆

### 4.1 新增记忆类型

在现有 `episodic` / `semantic` 之外增加 `relational`，定义为：两人之间的互动模式、称呼、玩笑、约定、共同经历的指代。

需要改动的位置：

1. **抽取提示词**（`memory/llm.py` 的 `_SYSTEM_PROMPT`）：补充第三类的定义和示例，例如"用户叫 AI'老王'"、"两人把周五晚上叫做'电影夜'"。
2. **写入校验**（`memory/remember.py`）：`memory_type` 目前只接受两个值，需放开第三个。
3. **数据库**：`memories.memory_type` 是 `String(16)`，无需迁移。

### 4.2 常驻注入

与另外两类记忆不同，`relational` 不依赖 agent 主动检索，而是每次构建 system prompt 时固定注入。

```
取 user_id 下 memory_type = 'relational' 且 status = 'active' 的记忆，
按 importance 降序取前 N 条（建议 N = 5~8），
渲染成"关系上下文"段落插入提示词。
```

**为什么不走现有的检索链路**

`memory_search` 是按查询语义召回的，而语气和称呼是每一句话都需要的背景，不存在对应的"查询"。agent 也不会为了调整语气主动发起一次检索。因此这类记忆应当常驻，而非按需召回。

**成本控制：** 每条记忆通常只有一句话，5~8 条对上下文的开销很小，但会计入每次请求。建议对注入条数设上限并做成配置项。

### 4.3 与重排序的关系

`relational` 记忆同样会被 `memory_search` 检索到（它们在同一张表）。是否需要在 `memory/rerank.py` 中对该类型做特殊处理，建议先观察实际效果再决定，避免过早优化。

---

## 5. 实施顺序

建议按以下顺序推进，每一步都可独立验证：

**阶段一：只改提示词（半天）**
硬编码一版目标人设，直接替换 `PERSONA` 环境变量，同时调整温度和 max tokens。这一步不写任何代码，但能解决大部分助手感问题。目的是先确认方向正确，再投入工程改造。

**阶段二：人设数据化（1~2 天）**
建表、写 migration、加接口、改 `api_loop.py` 的人设获取逻辑、前端加设置页。把阶段一调好的人设作为默认值写入初始数据。

**阶段三：关系记忆（1 天）**
改抽取提示词、放开类型校验、实现常驻注入。

---

## 6. 测试

| 层级 | 内容 |
|---|---|
| 单元测试 | 人设渲染函数：字段缺失、超长、含特殊字符时的输出；`is_active` 唯一性约束 |
| 接口测试 | `PUT /app/persona` 的部分更新语义；未认证请求被拒绝 |
| 集成测试 | 人设更新后，缓存过期时间内 `build_messages` 是否取到新值；relay 不可达时的降级路径 |
| 人工评估 | 固定 10 条输入消息，分别在改造前后各跑一遍，比较回复长度、是否出现列表、是否出现助手话术 |

人工评估这一项建议记录下来：回复平均长度、含列表的比例、含"还需要我..."类话术的比例。这三个指标改造前后的对比，是说明这次改动有效的直接证据。

---

## 7. 风险与注意事项

**提示词注入。** 人设是用户输入且会进入 system prompt。`channel/server.ts:173` 已有相关防护意识（拒绝通过消息修改配置）。人设字段同理：不应包含可以覆盖系统约束的指令。至少要做长度限制，并把系统硬性约束放在用户内容之后。

**上下文预算。** 人设 + 关系记忆 + 历史消息（`HISTORY_N` 默认 24）共同占用上下文。人设字段放开后可能显著膨胀，需要为整体预算设一个上限并在超出时优先裁剪历史消息。

**两种模式的人设不一致。** 本次改造只覆盖 `loop` 模式。`desktop` 模式下人设仍来自本地文件，用户在前端修改不会生效。需要在 UI 上明确提示，或在后续版本中统一。

**默认值的迁移。** 现有用户的人设在环境变量里。上线时需要一次性把它写入 `personas` 表作为初始激活记录，避免升级后人设丢失。

---

## 8. 后续方向（本次不实现）

**主动消息。** 现有 Celery beat 已在运行每日 reweight 任务（`celery_app.py`），可复用同一机制定时生成主动消息：查询最近的 `episodic` 记忆，由 LLM 判断是否存在值得追问的话题（例如"用户提到今天有 presentation"），生成后 POST 到 `/channel/out`。基于记忆的主动追问是普通 chatbot 无法做到的，也最能体现记忆系统的价值。需要严格控制频率（建议每天不超过 2 条）。

**人设版本对比。** 利用 `personas` 表已有的多记录结构，记录每套人设下的对话样本，支持人工对比评估。
