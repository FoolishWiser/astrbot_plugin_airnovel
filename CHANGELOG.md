# 📋 AirNovel 更新日志

## [1.1.0] - 2026-05-09

### 新增
- 🔐 **账号认证系统**：可在插件配置中开关。开启后 Web 界面需要登录才能访问
- ⚙️ **WebUI 设置页面**：支持在线修改用户名和密码
- 📝 **CHANGELOG**：新增本文件

### 优化
- 导航栏增加「设置」入口和「退出」按钮（开启认证时显示）
- API 接口不受登录限制，安卓客户端可继续无鉴权调用

### 修复
- 定时任务未触发的问题（`_register_cron` 缺少 `await` 导致协程未执行）
- `_conf_schema.json` 时区默认值 typo：`Asia/Shanhai` → `Asia/Shanghai`
- cron job 未持久化到数据库（`persistent=True`）

## [1.0.0] - 2026-05-08

### 新增
- 初始版本发布
- AI 自动连载小说：Web 管理界面 + 定时续写 + 局域网 API
- FastAPI + uvicorn 架构（与 LivingMemory 一致）
- 每日续写次数限制
- 每本书独立定时任务
