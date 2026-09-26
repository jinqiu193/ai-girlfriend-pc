# AI Girlfriend

本地跑的 AI 女友聊天程序,基于 [memU](../) 的 embedding-only 记忆 + 任意 OpenAI 兼容 LLM(GPT-4 / Claude / DeepSeek 等),以及本地 sentence-transformers 跑 embedding。

UI 是薄荷奶绿主题,响应式布局,支持桌面 / 平板 / 手机三档,跟随系统切换深浅色。

---

## 目录

- [快速开始](#快速开始)
- [界面速览](#界面速览)
- [角色卡](#角色卡)
- [长期记忆机制](#长期记忆机制)
- [对话上下文](#对话上下文)
- [图片](#图片)
- [配置项](#配置项)
- [常见问题](#常见问题)
- [目录结构](#目录结构)
- [已知限制](#已知限制)

---

## 快速开始

### 前置

- **Python 3.11+**
- **uv**(包管理器):`irm https://astral.sh/install.ps1 | iex` 或 `winget install --id Astral.UV`
- 一个能用的 OpenAI 兼容 LLM endpoint(DeepSeek / OpenAI / 通过 Anthropic 协议的网关都行)
- 一个 OpenAI 兼容 embedding endpoint(本地 bge-m3 也已内置)

### 启动(Windows,最简单)

双击 **`start.bat`**:

- 第一次会自动复制 `.env.example` → `.env` 并用记事本打开让你填密码 / API key
- 关掉记事本保存后,**再次双击** `start.bat` 即可启动
- 浏览器自动打开 `http://127.0.0.1:8765`,登录即可

### 启动(macOS / Linux / 手动)

```bash
cd ai-girlfriend

# 1. 复制环境变量并填写
cp .env.example .env
# 编辑 .env:改 ADMIN_PASSWORD / SESSION_SECRET / LLM_API_KEY / EMBEDDING_API_KEY

# 2. 安装依赖(memU 通过 path 自动装上,本地 bge-m3 也会一并装)
uv sync

# 3. 启动
uv run uvicorn app:app --host 127.0.0.1 --port 8765
```

启动后:

1. 浏览器访问 http://127.0.0.1:8765
2. 用 `.env` 里的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录(默认 `admin` / `changeme`,**生产请改**)
3. 点侧栏 **＋** 导入角色卡 → 开始聊天

> **重要**:`uv` 已经把父目录的 `memu` 当 workspace 依赖装好,**不要**用裸 `python` 启动,否则会 `ModuleNotFoundError: No module named 'memu'`。

---

## 界面速览

| 区域 | 说明 |
|---|---|
| 侧栏(rails) | 左 6px 宽,4 个图标:聊天、联系人、记忆库、设置 |
| 会话列表面板 | 选中"聊天"时显示,列出所有对话 |
| 对话主区 | 右侧气泡聊天窗口,底部 composer(支持发图) |
| 联系人面板 | 选中"联系人"时显示,网格列出所有角色 |
| 角色详情面板 | 点联系人后右侧滑出,基本信息 / 长期记忆 / 设定三个 tab |

### 响应式断点

| 宽度 | 表现 |
|---|---|
| ≥881px | 三列:rail + 会话列表 + 主区 |
| 641–880px | rail 变窄,会话列表稍窄,主区不变 |
| ≤640px | rail 隐藏,联系人模式单列切换,底部 composer 贴底安全区 |
| ≤420px | composer 内边距收紧,头像缩小,基本信息表单单列堆叠 |

### 主题

- 配色:薄荷奶绿(`#6FB89E`)+ 暖象牙底(`#FAF7F2`),点缀烤瓷粉 / 香槟金
- 圆角:主体 12px,气泡 18px(iMessage 风)
- 暗色:跟随系统(`@media (prefers-color-scheme: dark)`),`<meta name="theme-color">` 随系统切换同步

---

## 角色卡

支持 **SillyTavern V1 / V2 / V3 JSON** 和 **PNG 内嵌 JSON** 角色卡。

导入方式:

1. 左侧栏点 **＋** → 选 `.json` 或 `.png` 文件
2. 头像自动从 PNG chunk 或 JSON 字段提取
3. 在"联系人"里点开可看基本信息 / 长期记忆 / 原始设定

### 聊天记录导入(生成新角色)

侧栏底部 **⬇ 导入聊天记录** 按钮 → 选聊天导出文件 → 解析后看到段列表 →
选一段 → LLM 从对方话语里派生出 V2 角色卡 → 把这段聊天按 30 条一组写入 memU 长期记忆 → 新建角色并切到它。

支持的格式:`telegram` (Telegram Desktop 导出) / `qq_chat_exporter` / `wechat` / `chatgpt` / `claude` / `generic` (任意 `{"messages": [...]}` 数组或对象)。

### 导出当前角色(对话 + 角色卡)

聊天的右侧 header 上多了 **⬇** 按钮,点一下下载当前角色全量对话 + V2 角色卡的 JSON 文件(`ai-girlfriend-<角色名>-<日期>.json`)。导出文件可以被本系统或其他实例用上面的"聊天记录导入"重新导入,完成角色迁移。

### 卡片字段(按 Spec 注入)

| 字段 | 注入位置 |
|---|---|
| `description` | system · 设定 |
| `personality` | system · 性格 |
| `scenario` | system · 场景 |
| `mes_example` | system · 示例对话 |
| `system_prompt` | system · 系统补充 |
| `post_history_instructions` | system · 行为锚(每次都追加) |
| `first_mes` | 第一条 assistant 消息,自动写入历史 |
| `alternate_greetings` | 第一次聊天时随机选一条作为开场 |
| `lorebook` / `character_book` | 关键词触发的隐藏设定 |

**不会注入**的字段:`creator`,`creator_notes`,`tags`,`extensions`(元数据)。

### 占位符

卡片文本里可以写 `{{char}}` / `{{user}}`,启动时会被替换成角色名 / 用户名。

---

## 长期记忆机制

> 记忆是 **embedding-only** 的——LLM **完全不参与**记忆管理。所有"她记得你说过什么"都是 memU 通过向量相似度召回来的。

### 数据流

```
每轮对话结束
    │
    ▼
routes/chat_routes.py
    │  把这一轮的 user + assistant 拼成一段 summary
    │
    ▼
memU.commit_results([
    RecallFilePayload(
        content=summary,
        track="memory",
        where={user_id, character_id},   # 双隔离
    )
])
    │
    ▼
memU 自动:切段 → embed(bge-m3 本地 / 或远程) → 写 sqlite / postgres
```

下次开聊时:

```
用户发消息
    │
    ▼
routes/memory_routes.py → /api/memory/recall
    │  memu.progressive_retrieve(
    │      query=user_msg,
    │      where={user_id, character_id},
    │      top_segments=5, top_files=3,
    │  )
    │
    ▼
拼进 system prompt 的 [Recall] 段:
  ## [Recall] 你隐约记得关于用户和你们之间的事
  - 她养了一只猫叫年糕
  - 上周她加班到凌晨
  - ...
```

### 隔离

每个用户的每个角色有独立记忆空间:

- `where={"user_id": "admin", "character_id": "char_xxx"}` 是 memU scope 的扩展字段
- 三七(角色 A)不会记得林晚(角色 B)和你聊过的内容
- 换账号登录也互不干扰

### 查看

联系人面板 → 选角色 → 切到 **长期记忆** tab 即可看到该角色召回到的所有记忆片段。

---

## 对话上下文

每次 LLM 调用拼出来的 messages 大致是:

```
[
  {system: 角色卡 + 行为守则 + [Recall] + [Lorebook 触发]},
  {user: 最近第1条},
  {assistant: ...},
  ...
  {user: 最近第N条},
  {user: 当前消息},      ← --history-window 控制 N,默认 20
]
```

`HISTORY_WINDOW` 调小 → 更省 token 但丢上下文;调大 → 更连贯但慢 / 贵。

---

## 图片

| 方向 | 实现 |
|---|---|
| 用户发图 | 上传到 `data/uploads/`,消息里存 URL,LLM 通过 OpenAI 视觉 API 识别 |
| AI 主动发图 | LLM 在回复里写 `[IMAGE: 一句话说明]`,前端按占位符拆段触发图片生成,**目前仅从角色卡自带的 assets 选**;不接 SD / DALL-E 真生成 |

单张图片上限 `MAX_UPLOAD_MB`(默认 8 MB)。

---

## 配置项

`.env` 里所有键:

| 键 | 默认 | 说明 |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | 登录用户名(单 admin) |
| `ADMIN_PASSWORD` | `changeme` | 登录密码,**必改** |
| `SESSION_SECRET` | `dev-secret-change-me` | Cookie 签名密钥,**必改**(用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成) |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容 endpoint |
| `LLM_API_KEY` | `sk-replace-me` | LLM 密钥 |
| `LLM_MODEL` | `gpt-4o-mini` | 任意支持的模型名 |
| `EMBEDDING_BASE_URL` | 同 LLM | 留空则用本地 bge-m3 |
| `EMBEDDING_API_KEY` | 同 LLM | |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `EMBED_LOCAL_ENABLED` | `true` | 是否启用本地 bge-m3(端口 11435) |
| `EMBED_MODEL_NAME` | `BAAI/bge-m3` | 本地 embedding 模型 |
| `DATA_DIR` | `./data` | 上传图片 + sqlite 都在这里 |
| `DB_PATH` | `./data/app.sqlite3` | 业务表(角色、消息) |
| `MEMU_DB_PATH` | `./data/memu.db` | 记忆向量库 |
| `APP_HOST` | `127.0.0.1` | 监听地址,想暴露给局域网改成 `0.0.0.0` |
| `APP_PORT` | `8765` | 监听端口 |
| `HISTORY_WINDOW` | `20` | 每轮带进 prompt 的最近消息数 |
| `MAX_UPLOAD_MB` | `8` | 单张上传图片大小上限 |

---

## 常见问题

**Q: 启动报 `ModuleNotFoundError: No module named 'memu'`?**
A: 必须用 `uv run ...` 或先 `uv sync` 让父目录的 memU 装进 venv。裸 `python` 找不到。

**Q: 启动报 `Connection refused` 给 embedding?**
A: 第一次启动会下载 bge-m3 模型(~2 GB),看终端进度。如果网络不行,把 `EMBED_LOCAL_ENABLED=false` 然后填远程 embedding 配置。

**Q: AI 同一个话题聊几句就不连贯了?**
A: 适当调大 `HISTORY_WINDOW`(试试 30–40)。更深层的修复需要"话题级"合并记忆,目前还没做。

**Q: 她忘了之前说过什么?**
A: 看 `.env` 里 `MEMU_DB_PATH` 的 sqlite 在不在;`/api/memory/recall` 是看 top-5,主题太分散时早期记忆会排在后面。

**Q: 长期记忆 tab 里只显示一行?**
A: 当前 UI 只渲染 `seg.text`,后续会补全文件名 / 时间 / 上下文摘要。

**Q: 怎么彻底重置记忆?**
A: 关闭服务,删 `data/memu.db`,重启。

**Q: 局域网让别人也能访问?**
A: `.env` 里 `APP_HOST=0.0.0.0`,然后开放 `APP_PORT` 防火墙。同时**必须**改 `ADMIN_PASSWORD` 和 `SESSION_SECRET`。

---

## 目录结构

```
ai-girlfriend/
├── start.bat              # Windows 一键启动
├── README.md
├── pyproject.toml         # 依赖 + workspace 指向 ../memu
├── app.py                 # FastAPI 入口 + lifespan
├── config.py              # .env → Settings
├── auth.py                # 登录/session
├── db.py                  # 业务表 CRUD(SQLite)
├── schemas.sql            # 业务表 DDL
├── character_card.py      # SillyTavern V1/V2/V3 + PNG 解析
├── lorebook.py            # 世界书条目触发
├── prompts.py             # 角色卡 + 召回 → LLM messages
├── memu_setup.py          # MemoryService 单例 + Scope 模型
├── llm.py                 # OpenAI / Anthropic 流式生成
├── local_embed_server.py  # 内嵌 bge-m3 HTTP 服务
├── upload.py              # 图片保存 + 缩略图
├── chat_import.py         # 历史聊天导入(角色卡派生)
├── routes/
│   ├── auth_routes.py
│   ├── character_routes.py
│   ├── chat_routes.py
│   ├── chat_import_routes.py
│   ├── memory_routes.py
│   └── upload_routes.py
├── templates/             # Jinja2
├── static/                # CSS / JS
└── data/                  # 运行时数据(sqlite / uploads / 缩略图)
```

---

## 与 memU 的关系

- **memU 不修改**:`memu_setup.py` 只通过 `UserConfig(model=ChatScope)` 扩展 scope 字段,memU 后端零改动即支持 `character_id` 双隔离
- **记忆粒度**:每轮对话 = 一条 `recall_file`(`track="memory"`),`content` 是用户消息 + 助手回复摘要
- **检索**:对话开始时 `progressive_retrieve(query, where={user_id, character_id})` 拿 top 5 segments,作为 `[Recall]` 段注入 system prompt
- **角色卡本身不进 memU**:角色设定是模板,不是记忆;只在我们自己的 `characters` 表里

---

## 已知限制

- 角色"主动发图"目前只能从角色卡自带的 assets 中选;不接 SD / DALL-E 真生成
- 单 admin,无注册流程
- 记忆不做自动过期;库大了需要手动清理 `data/memu.db`
- 浏览器响应式但不是原生 App
- "话题级"长期记忆合并(同一话题聊完多轮后再合并入一条 recall_file)尚未实现,目前是每轮一条
- 长期记忆 UI 只展示文本,没显示来源 / 时间 / 上下文摘要
- 没有 streaming 中途取消按钮
