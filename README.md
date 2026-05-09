# 📖 AirNovel - AI 自动连载小说插件 (v1.1.0)

基于 AstrBot 的 AI 自动连载小说工具。提供 Web 管理界面、定时 AI 续写、局域网 API。

## 快速开始

1. 将插件目录复制到 AstrBot 的 `data/plugins/` 下
2. 重启 AstrBot 或通过 WebUI 重载插件
3. **浏览器打开** `http://<AstrBot的IP>:14514` 即可使用

> 无需额外命令，插件启动后 Web 界面自动可用。

## 配置

在 AstrBot WebUI → 插件管理 → AirNovel → 配置中设置：

| 配置项 | 说明 |
|--------|------|
| `model_id` | 续写使用的模型 ID（在 AstrBot LLM 配置中查看） |
| `system_prompt` | AI 写作的系统级要求，附加到每次续写请求 |
| `cron_expression` | 每日自动续写时间，默认 `0 8 * * *`（早 8 点） |
| `timezone` | 时区，默认 `Asia/Shanghai` |
| `max_chapter_context` | 续写时参考的最近章节数，默认 3 |
| `daily_write_limit` | 每日手动续写次数上限，默认 1 |
| `auth_enabled` | 是否启用 Web 界面登录认证，默认关闭 |
| `auth_username` | 登录用户名（默认 `airnovel`） |
| `auth_password` | 登录密码（默认 `airnovel`） |

> 开启认证后，也可以在 WebUI 的「设置」页面中修改用户名密码。

## 使用

### Web 管理界面

直接访问 `http://<IP>:14514`

- **书籍概览**：查看所有书籍、章节数、创建时间
- **新建书籍**：填写信息 → AI 自动生成大纲 → 创建完成
- **续写**：点击「续写」→ 显示加载动画 → 完成后自动跳转
- **大纲管理**：查看、编辑、让 AI 重新生成大纲

### 聊天命令

| 命令 | 功能 |
|------|------|
| `/airnovel` | 查看帮助 |
| `/airnovel list` | 列出所有书籍 |
| `/airnovel write` | 立即续写所有激活书籍 |
| `/airnovel status` | 查看插件状态 |

### 定时续写

配置好 `cron_expression` 后，每天在设定时间自动续写所有激活的书籍。

## API 接口（供安卓客户端调用）

所有接口无需鉴权，仅限局域网访问。

| 接口 | 说明 |
|------|------|
| `GET /api/books` | 所有书籍列表 |
| `GET /api/books/<id>/chapters` | 指定书的章节列表 |
| `GET /api/books/<id>/chapters/<n>` | 指定章节完整内容 |
| `GET /api/latest?limit=10` | 最新章节列表 |

## 数据存储

```
data/plugins_data/astrbot_plugin_airnovel/
└── books/
    ├── <book_id>/
    │   ├── meta.json          # 书籍元数据（大纲、章节索引等）
    │   └── chapters/
    │       ├── 第1章.txt
    │       └── ...
    └── ...
```

所有书籍数据为 JSON / 纯文本格式，可直接查看和备份。

## 架构说明

```
AstrBot 进程                    FastAPI (同进程 asyncio task)
────────────                    ─────────────────────────────
main.py (插件)                  webui/server.py
  ├── AstBot 命令处理             ├── Web 管理界面 (端口 14514)
  ├── AI 调用 (LLM)              ├── REST API (无鉴权)
  ├── 定时任务 (cron_manager)     └── 数据文件读写
  └── 数据管理
─────────────────────────────────────────────────────────
FastAPI 作为 asyncio task 运行在 AstrBot 事件循环上，
与 LivingMemory 插件采用相同架构。无需额外端口映射。
```

## 常见问题

**Web 页面打不开？**
检查防火墙是否放行 14514 端口。确认插件已加载（`/airnovel status`）。

**续写失败？**
检查插件配置中的 `model_id` 是否正确。在 AstrBot WebUI 的 LLM 配置中确认模型可用。

**AstrBot 重启后需要重新配置吗？**
不需要。书籍数据和定时任务会自动恢复。Web 界面会自动启动。

**为什么不用 Flask？**
AirNovel 采用 FastAPI + uvicorn，以 asyncio task 运行在 AstrBot 的事件循环上（与 LivingMemory 架构一致），避免了独立进程/线程带来的端口冲突和稳定性问题。

## 安全

- Web 界面监听 `0.0.0.0:14514`，**无鉴权**，仅限局域网使用
- API 接口同样无鉴权，请勿暴露到公网

本项目开发过程中使用了 DeepSeek V4 Flash 进行辅助编程。
Powered by DeepSeek V4 Flash.
