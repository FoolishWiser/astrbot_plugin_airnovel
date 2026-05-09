"""
server.py - AirNovel WebUI 服务器
基于 FastAPI + uvicorn，作为 asyncio task 运行在 AstrBot 事件循环上。
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from astrbot import logger


class WebUIServer:
    """AirNovel Web 管理界面服务器。"""

    def __init__(
        self,
        data_dir: str,
        book_callback: Callable,
        write_callback: Callable,
        outline_callback: Callable,
        config: dict,
    ):
        """
        Args:
            data_dir: 数据目录路径
            book_callback: async (action, **kw) -> dict 书籍操作回调
            write_callback: async (book_id) -> (bool, str) 续写回调
            outline_callback: async (title, desc, tags, prompt, sys_p) -> str 大纲生成回调
            config: 插件配置
        """
        self.data_dir = Path(data_dir)
        self.book_callback = book_callback
        self.write_callback = write_callback
        self.outline_callback = outline_callback
        self.config = config
        self.port = int(config.get("flask_port", 14514))
        self.auth_enabled = bool(config.get("auth_enabled", False))
        self.auth_username = str(config.get("auth_username", "airnovel"))
        self.auth_password = str(config.get("auth_password", "airnovel"))
        self._auth_token = None
        self._load_auth_config()

    def _load_auth_config(self):
        """从持久化文件加载自定义账号设置。"""
        import json as _json
        acf = self.data_dir / ".auth_config"
        if acf.exists():
            try:
                ac = _json.loads(acf.read_text("utf-8"))
                if ac.get("username"):
                    self.auth_username = ac["username"]
                if ac.get("password"):
                    self.auth_password = ac["password"]
            except Exception:
                pass

        self._app = FastAPI(title="AirNovel")
        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._setup_routes()

        self._server: Optional[uvicorn.Server] = None
        self._server_task: Optional[asyncio.Task] = None
        self._server_error: Optional[Exception] = None

    # ═══════════════════════════════════════════════════════════
    # 路由注册
    # ═══════════════════════════════════════════════════════════

    def _setup_routes(self):
        app = self._app

        # ── Auth 中间件 ──
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            if not self.auth_enabled:
                return await call_next(request)
            # 免鉴权路径
            public_paths = ["/login", "/api/", "/logout"]
            if any(request.url.path.startswith(p) for p in public_paths):
                return await call_next(request)
            # 检查 token
            token = request.cookies.get("airnovel_token")
            if token and token == self._auth_token:
                return await call_next(request)
            from starlette.responses import RedirectResponse
            return RedirectResponse(url="/login")

        # ── 登录页 ──
        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            if not self.auth_enabled:
                from starlette.responses import RedirectResponse
                return RedirectResponse(url="/")
            body = '''
            <div style="max-width:400px;margin:80px auto;">
              <div class="card" style="text-align:center;">
                <div style="font-size:48px;margin-bottom:12px;">🔐</div>
                <h2 style="border:none;margin-bottom:20px;">AirNovel 登录</h2>
                <form method="POST" action="/login">
                  <input name="username" placeholder="用户名" required style="margin-bottom:12px;">
                  <input name="password" type="password" placeholder="密码" required style="margin-bottom:16px;">
                  <button type="submit" class="btn btn-primary" style="width:100%;">登录</button>
                </form>
              </div>
            </div>'''
            return self._page_raw(body)

        @app.post("/login")
        async def login_submit(request: Request):
            import secrets
            form = await request.form()
            if form.get("username") == self.auth_username and form.get("password") == self.auth_password:
                self._auth_token = secrets.token_hex(16)
                from starlette.responses import RedirectResponse
                resp = RedirectResponse(url="/")
                resp.set_cookie(key="airnovel_token", value=self._auth_token, max_age=86400*7)
                return resp
            body = '<div style="max-width:400px;margin:80px auto;"><div class="card" style="text-align:center;"><p style="color:var(--danger);margin-bottom:12px;">用户名或密码错误</p><a href="/login" class="btn btn-primary">重新登录</a></div></div>'
            return self._page_raw(body)

        @app.get("/logout")
        async def logout():
            self._auth_token = None
            from starlette.responses import RedirectResponse
            resp = RedirectResponse(url="/login")
            resp.delete_cookie("airnovel_token")
            return resp

        # ── 设置页 ──
        @app.get("/settings", response_class=HTMLResponse)
        async def settings_page(request: Request):
            if self.auth_enabled:
                token = request.cookies.get("airnovel_token")
                if not token or token != self._auth_token:
                    from starlette.responses import RedirectResponse
                    return RedirectResponse(url="/login")
            body = f'''
            <h2 class="page-title">⚙️ 设置</h2>
            <div class="card">
              <h2>🔐 账号设置</h2>
              <form method="POST" action="/settings" data-loading="⏳ 保存中...">
                <label>用户名</label><input name="username" value="{self.auth_username}" required>
                <label>新密码</label><input name="password" type="password" placeholder="留空则不修改" value="">
                <label>确认密码</label><input name="confirm_password" type="password" placeholder="再次输入新密码">
                <button type="submit" class="btn btn-primary">💾 保存</button>
              </form>
            </div>
            <div class="card">
              <h2>📋 当前配置</h2>
              <p style="color:var(--neutral-400);font-size:13px;line-height:1.8;">
                登录认证: {"🟢 已开启" if self.auth_enabled else "🔴 已关闭"}<br>
                可在 AstrBot 插件配置中修改 <code>auth_enabled</code> 开关。
              </p>
            </div>'''
            return self._page(body)

        @app.post("/settings", response_class=HTMLResponse)
        async def settings_save(request: Request):
            form = await request.form()
            new_user = form.get("username", "").strip()
            new_pw = form.get("password", "").strip()
            confirm = form.get("confirm_password", "").strip()
            if not new_user:
                return self._page("", flash="用户名不能为空", ft="error")
            if new_pw and new_pw != confirm:
                return self._page("", flash="两次密码不一致", ft="error")
            # 保存到 config（通过写文件 + 标记让插件下次加载时读取）
            self.auth_username = new_user
            if new_pw:
                self.auth_password = new_pw
            # 同时写入 config 文件，使插件重载后能恢复
            import json as _json
            cf = self.data_dir / ".auth_config"
            cf.write_text(_json.dumps({"username": self.auth_username, "password": self.auth_password}))
            return self._page("", flash="✅ 账号设置已保存，将在下次插件重载后持久生效", ft="success")

        @app.get("/", response_class=HTMLResponse)
        async def index():
            return self._page(self._books_html())

        @app.get("/create", response_class=HTMLResponse)
        async def create_page():
            return self._page("""
            <h2 class="page-title">✍️ 新建书籍</h2>
            <div class="card">
              <form method="POST" action="/create" data-loading="⏳ 正在请求 AI 生成大纲...">
                <label>📖 书名 *</label><input name="title" required placeholder="输入书名">
                <label>📝 简介</label><textarea name="desc" rows="3" placeholder="小说的简要介绍（可选）"></textarea>
                <label>🏷️ 标签</label><input name="tags" placeholder="奇幻, 冒险, 热血（逗号分隔）">
                <label>🎨 创作提示词 *</label><textarea name="prompt" required style="min-height:120px;" placeholder="描述你想要的创作方向、风格、核心设定..."></textarea>
                <button type="submit" class="btn btn-primary" style="width:auto;">📝 创建并生成大纲</button>
              </form>
            </div>""")

        @app.post("/create", response_class=HTMLResponse)
        async def create_submit(request: Request):
            form = await request.form()
            title = form.get("title", "").strip()
            desc = form.get("desc", "").strip()
            tags = [t.strip() for t in form.get("tags", "").split(",") if t.strip()]
            prompt = form.get("prompt", "").strip()
            if not title or not prompt:
                return self._page("<p>书名和提示词不能为空</p><a href='/create' class='btn'>返回</a>",
                                  flash="书名和提示词不能为空", ft="error")

            # 检测同名书籍
            for existing in self._all_books():
                if existing.get("title") == title:
                    return self._page(
                        f'<div style="text-align:center;padding:40px;"><div style="font-size:64px;margin-bottom:16px;">📌</div>'
                        f'<h2 style="border:none;font-size:20px;margin-bottom:8px;">《{title}》已存在</h2>'
                        f'<p style="color:var(--neutral-400);margin-bottom:20px;">请使用不同的书名。</p>'
                        f'<div style="display:flex;gap:8px;justify-content:center;"><a href="/create" class="btn btn-primary">返回修改</a>'
                        f'<a href="/book/{existing.get("book_id")}" class="btn btn-ghost">查看已有</a></div></div>',
                        flash=f"同名书籍已存在", ft="error")

            # 先创建书籍（大纲待生成）
            safe = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", title)[:50]
            bid = f"{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            sys_p = self.config.get("system_prompt", "")
            meta = {
                "book_id": bid, "title": title, "description": desc, "tags": tags,
                "prompt": prompt, "outline": "", "system_prompt": sys_p,
                "created_at": datetime.now().isoformat(), "updated_at": datetime.now().isoformat(),
                "chapter_count": 0, "chapters": [], "activated": True,
            }
            (self.data_dir / "books" / bid).mkdir(parents=True, exist_ok=True)
            (self.data_dir / "books" / bid / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")

            # 异步生成大纲
            outline_ok = False
            try:
                outline = await self.outline_callback(title, desc, tags, prompt, sys_p)
                if outline:
                    meta["outline"] = outline
                    (self.data_dir / "books" / bid / "meta.json").write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
                    outline_ok = True
            except Exception as e:
                logger.error(f"生成大纲失败: {e}")

            if outline_ok:
                return self._page(
                    f'<div style="text-align:center;padding:40px;"><div style="font-size:64px;margin-bottom:16px;">🎉</div>'
                    f'<h2 style="border:none;font-size:22px;margin-bottom:8px;">《{title}》创建成功！</h2>'
                    f'<p style="color:var(--neutral-400);margin-bottom:20px;">大纲已自动生成，可随时在书籍页面修改。</p>'
                    f'<a href="/book/{bid}" class="btn btn-primary">📖 查看书籍</a></div>',
                    flash=f"✅ 《{title}》创建成功", ft="success")
            else:
                return self._page(
                    f'<div style="text-align:center;padding:40px;"><div style="font-size:64px;margin-bottom:16px;">⚠️</div>'
                    f'<h2 style="border:none;font-size:22px;margin-bottom:8px;">《{title}》已创建</h2>'
                    f'<p style="color:var(--neutral-400);margin-bottom:8px;">但 AI 生成大纲时遇到问题。</p>'
                    f'<div class="card" style="text-align:left;background:var(--warning-light, #fffbeb);border-color:rgba(245,158,11,0.2);"><p style="font-size:13px;color:var(--neutral-600);">可能原因：模型 ID 未配置、模型暂时不可用、或网络超时。<br>修复后可在书籍页面点击「大纲」→「AI 重新生成」。</p></div>'
                    f'<div style="margin-top:16px;"><a href="/book/{bid}" class="btn btn-primary">📖 查看书籍</a></div></div>',
                    flash="⚠️ 大纲生成失败", ft="error")

        @app.get("/book/{bid}", response_class=HTMLResponse)
        async def book_page(bid: str):
            meta = self._get_book(bid)
            if not meta:
                return self._page("<p>书籍不存在</p>", flash="书籍不存在", ft="error")
            chapters = meta.get("chapters", [])
            ch_list = "".join(
                f'<li><span>{ch.get("title","")} <span style="color:var(--neutral-400);font-size:12px;">{(ch.get("updated_at") or "")[:16]}</span></span>'
                f'<a href="/book/{bid}/chapter/{ch["id"]}" class="btn btn-primary btn-sm">阅读</a></li>'
                for ch in chapters
            ) or '<div style="text-align:center;padding:40px;color:var(--neutral-400);">还没有章节，点击「续写」开始创作</div>'
            outline = ""
            if meta.get("outline"):
                outline = f'<div class="card"><h2>📋 故事大纲</h2><div class="outline-box">{meta["outline"]}</div></div>'
            tags = "".join(f'<span style="display:inline-block;padding:2px 10px;background:var(--primary-light);color:var(--primary);border-radius:999px;font-size:12px;margin:2px 4px 2px 0;">{t}</span>' for t in meta.get("tags",[]))
            body = f'''
            <div class="card">
              <div style="display:flex;justify-content:space-between;align-items:start;flex-wrap:wrap;gap:12px;">
                <div>
                  <h2 style="border:none;margin-bottom:4px;">📖 {meta["title"]}</h2>
                  <p style="color:var(--neutral-400);font-size:14px;margin-bottom:8px;">{meta.get("description","")}</p>
                  {tags}
                  <p style="color:var(--neutral-400);font-size:13px;margin-top:8px;">共 {meta.get("chapter_count",0)} 章 · 创建于 {(meta.get("created_at") or "")[:10]}</p>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                  <a href="/book/{bid}/write" class="btn btn-success btn-sm" onclick="return confirm(\'立即续写下一章？\')">✍️ 续写</a>
                  <a href="/book/{bid}/outline" class="btn btn-warning btn-sm">📝 大纲</a>
                  <a href="/" class="btn btn-ghost btn-sm">← 返回</a>
                </div>
              </div>
            </div>
            {outline}
            <div class="card"><h2>📑 章节 ({len(chapters)})</h2><ul class="chapter-list">{ch_list}</ul></div>'''
            return self._page(body)

        @app.get("/book/{bid}/chapter/{cid}", response_class=HTMLResponse)
        async def chapter_page(bid: str, cid: int):
            meta = self._get_book(bid)
            ch = self._get_chapter(bid, cid)
            if not meta or not ch:
                return self._page("<p>章节不存在</p>", flash="章节不存在", ft="error")
            body = f'''
            <div style="max-width:800px;margin:0 auto;">
              <div class="card">
                <div style="text-align:center;margin-bottom:20px;">
                  <h2 style="border:none;font-size:20px;">{ch.get("title","")}</h2>
                  <p style="color:var(--neutral-400);font-size:13px;">
                    《{meta["title"]}》 · {(ch.get("updated_at") or "")[:10]}
                    <a href="/book/{bid}" style="color:var(--primary);text-decoration:none;margin-left:12px;">← 返回书籍</a>
                  </p>
                </div>
                <div class="content-area">{ch.get("content","")}</div>
              </div>
            </div>'''
            return self._page(body)

        @app.get("/book/{bid}/write")
        async def write_now(bid: str):
            meta = self._get_book(bid)
            if not meta:
                return JSONResponse({"code": 1, "msg": "书籍不存在"})
            # 后台异步执行续写
            async def _delayed():
                try:
                    ok, msg = await self.write_callback(bid)
                    return ok, msg
                except Exception as e:
                    return False, str(e)
            if not hasattr(self, '_write_results'):
                self._write_results = {}
            self._write_results[bid] = asyncio.create_task(_delayed())
            # 返回带 meta refresh 的加载页，5秒后自动跳回书籍页
            body = (
                '<div style="text-align:center;padding:60px 20px;">'
                '<div style="width:48px;height:48px;border:4px solid var(--neutral-200);border-top-color:var(--primary);'
                'border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 20px;"></div>'
                '<h2 style="border:none;font-size:20px;margin-bottom:8px;">✍️ 正在续写中...</h2>'
                '<p style="color:var(--neutral-400);">AI 正在生成下一章内容，请稍候...</p>'
                '<p style="color:var(--neutral-400);font-size:13px;margin-top:8px;">页面将在完成后自动跳转</p>'
                '<meta http-equiv="refresh" content="3;url=/book/' + bid + '">'
                '<style>@keyframes spin{to{transform:rotate(360deg)}}</style>'
                '</div>'
            )
            return self._page(body)

        @app.get("/book/{bid}/write/check")
        async def write_check(bid: str):
            if not hasattr(self, '_write_results') or bid not in self._write_results:
                return {"done": False}
            task = self._write_results[bid]
            if task.done():
                try:
                    ok, msg = task.result()
                    return {"done": True, "ok": ok, "msg": msg}
                except Exception as e:
                    return {"done": True, "ok": False, "msg": str(e)}
            return {"done": False}

        @app.get("/book/{bid}/outline", response_class=HTMLResponse)
        async def outline_page(bid: str):
            meta = self._get_book(bid)
            if not meta:
                return self._page("<p>书籍不存在</p>", flash="书籍不存在", ft="error")
            body = f'''
            <h2 class="page-title">📋 编辑大纲 - 《{meta["title"]}》</h2>
            <div class="card">
              <form method="POST" action="/book/{bid}/outline" data-loading="⏳ 保存中...">
                <textarea name="outline" style="min-height:350px;font-size:14px;line-height:1.7;">{meta.get("outline","")}</textarea>
                <div style="display:flex;gap:8px;margin-top:12px;">
                  <button type="submit" class="btn btn-success">💾 保存</button>
                  <button type="submit" formaction="/book/{bid}/outline/regenerate" class="btn btn-warning">🔄 AI 重新生成</button>
                  <a href="/book/{bid}" class="btn btn-ghost">取消</a>
                </div>
              </form>
            </div>'''
            return self._page(body)

        @app.post("/book/{bid}/outline", response_class=HTMLResponse)
        async def outline_save(bid: str, request: Request):
            form = await request.form()
            outline = form.get("outline", "").strip()
            self._update_book(bid, outline=outline)
            return self._page(
                f'<div style="text-align:center;padding:40px;"><div style="font-size:64px;margin-bottom:16px;">✅</div>'
                f'<p style="font-size:16px;margin-bottom:20px;">大纲已保存</p>'
                f'<a href="/book/{bid}" class="btn btn-primary">📖 返回书籍</a></div>',
                flash="大纲已保存", ft="success")

        @app.post("/book/{bid}/outline/regenerate", response_class=HTMLResponse)
        async def outline_regenerate(bid: str):
            meta = self._get_book(bid)
            if not meta:
                return self._page("<p>书籍不存在</p>", flash="书籍不存在", ft="error")
            try:
                sys_p = self.config.get("system_prompt", "")
                outline = await self.outline_callback(
                    meta["title"], meta.get("description", ""),
                    meta.get("tags", []), meta.get("prompt", ""), sys_p)
                if outline:
                    self._update_book(bid, outline=outline)
                return self._page(
                    f'<div style="text-align:center;padding:40px;"><div style="font-size:64px;margin-bottom:16px;">🔄</div>'
                    f'<p style="font-size:16px;margin-bottom:20px;">大纲已重新生成</p>'
                    f'<a href="/book/{bid}/outline" class="btn btn-primary">📝 查看大纲</a></div>',
                    flash="大纲已重新生成", ft="success")
            except Exception as e:
                return self._page(
                    f'<div style="text-align:center;padding:40px;"><div style="font-size:64px;margin-bottom:16px;">❌</div>'
                    f'<p style="font-size:16px;margin-bottom:20px;">重新生成失败: {e}</p>'
                    f'<a href="/book/{bid}/outline" class="btn btn-primary">返回重试</a></div>',
                    flash=f"生成失败", ft="error")

        # ── API ──
        @app.get("/api/books")
        async def api_books():
            data = [{
                "book_id": b.get("book_id"), "title": b.get("title"),
                "description": b.get("description"), "chapter_count": b.get("chapter_count", 0),
                "created_at": b.get("created_at"), "activated": b.get("activated", True),
            } for b in self._all_books()]
            return {"code": 0, "data": data}

        @app.get("/api/books/{bid}/chapters")
        async def api_chapters(bid: str):
            meta = self._get_book(bid)
            if not meta:
                return {"code": 1, "msg": "书籍不存在"}
            return {"code": 0, "data": meta.get("chapters", [])}

        @app.get("/api/books/{bid}/chapters/{cid}")
        async def api_chapter(bid: str, cid: int):
            ch = self._get_chapter(bid, cid)
            if not ch:
                return {"code": 1, "msg": "章节不存在"}
            return {"code": 0, "data": ch}

        @app.get("/api/latest")
        async def api_latest(limit: int = 10):
            return {"code": 0, "data": self._latest(limit)}

    # ═══════════════════════════════════════════════════════════
    # 数据访问
    # ═══════════════════════════════════════════════════════════

    def _all_books(self) -> list[dict]:
        result = []
        seen = set()
        bd = self.data_dir / "books"
        if bd.exists():
            for d in bd.iterdir():
                if d.is_dir():
                    mf = d / "meta.json"
                    if mf.exists():
                        try:
                            meta = json.loads(mf.read_text("utf-8"))
                            bid = meta.get("book_id")
                            if bid and bid not in seen:
                                seen.add(bid)
                                result.append(meta)
                        except Exception:
                            pass
        result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return result

    def _get_book(self, bid: str) -> Optional[dict]:
        mf = self.data_dir / "books" / bid / "meta.json"
        if mf.exists():
            try:
                return json.loads(mf.read_text("utf-8"))
            except Exception:
                return None
        return None

    def _update_book(self, bid: str, **kw):
        meta = self._get_book(bid)
        if meta:
            meta.update(kw)
            meta["updated_at"] = datetime.now().isoformat()
            (self.data_dir / "books" / bid).mkdir(parents=True, exist_ok=True)
            (self.data_dir / "books" / bid / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")

    def _get_chapter(self, bid: str, cid: int) -> Optional[dict]:
        meta = self._get_book(bid)
        if not meta:
            return None
        for ch in meta.get("chapters", []):
            if ch["id"] == cid:
                fp = self.data_dir / "books" / bid / "chapters" / ch["filename"]
                content = fp.read_text("utf-8") if fp.exists() else ""
                return {**ch, "content": content}
        return None

    def _latest(self, limit: int = 10) -> list[dict]:
        items = []
        for meta in self._all_books():
            for ch in meta.get("chapters", []):
                fp = self.data_dir / "books" / meta["book_id"] / "chapters" / ch["filename"]
                preview = fp.read_text("utf-8")[:200] if fp.exists() else ""
                items.append({
                    "book_id": meta["book_id"], "book_title": meta["title"],
                    "chapter_id": ch["id"], "chapter_title": ch["title"],
                    "preview": preview, "updated_at": ch.get("updated_at", ""),
                })
        items.sort(key=lambda x: x["updated_at"], reverse=True)
        return items[:limit]

    # ═══════════════════════════════════════════════════════════
    # 页面渲染
    # ═══════════════════════════════════════════════════════════

    CSS = """
    <style>
    :root {
      --primary: #6366f1; --primary-hover: #4f46e5; --primary-light: rgba(99,102,241,0.1);
      --success: #10b981; --warning: #f59e0b; --danger: #ef4444;
      --neutral-50: #f8fafc; --neutral-100: #f1f5f9; --neutral-200: #e2e8f0;
      --neutral-300: #cbd5e1; --neutral-400: #94a3b8; --neutral-500: #64748b;
      --neutral-600: #475569; --neutral-700: #334155; --neutral-800: #1e293b;
      --bg-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.08), 0 2px 4px -2px rgb(0 0 0 / 0.05);
      --shadow-md: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.08);
      --radius: 0.75rem; --radius-sm: 0.5rem;
      --transition: 250ms cubic-bezier(0.4, 0, 0.2, 1);
    }
    *{margin:0;padding:0;box-sizing:border-box;}
    body{font-family:-apple-system,'Microsoft YaHei','PingFang SC',sans-serif;background:linear-gradient(135deg,#f5f7fa 0%,#e4e9f2 100%);color:var(--neutral-800);min-height:100vh;}
    .nav{background:rgba(255,255,255,0.8);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid rgba(226,232,240,0.8);padding:0 24px;display:flex;align-items:center;height:64px;position:sticky;top:0;z-index:100;}
    .nav .title{font-size:20px;font-weight:700;color:var(--neutral-800);margin-right:32px;display:flex;align-items:center;gap:8px;}
    .nav .title span{background:var(--bg-gradient);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
    .nav a{color:var(--neutral-500);text-decoration:none;font-size:14px;font-weight:500;padding:8px 16px;border-radius:var(--radius-sm);transition:var(--transition);}
    .nav a:hover,.nav a.active{color:var(--primary);background:var(--primary-light);}
    .container{max-width:1200px;margin:0 auto;padding:24px 20px;}
    .card{background:#fff;border-radius:var(--radius);padding:24px;margin-bottom:16px;box-shadow:var(--shadow);transition:var(--transition);border:1px solid var(--neutral-200);}
    .card:hover{box-shadow:var(--shadow-md);}
    .card h2{font-size:18px;font-weight:600;color:var(--neutral-800);margin-bottom:16px;display:flex;align-items:center;gap:8px;}
    .book-card{display:flex;justify-content:space-between;align-items:center;padding:20px 24px;background:#fff;border-radius:var(--radius);margin-bottom:12px;box-shadow:var(--shadow);border:1px solid var(--neutral-200);transition:var(--transition);cursor:default;}
    .book-card:hover{box-shadow:var(--shadow-md);transform:translateY(-1px);}
    .book-card .info{flex:1;}
    .book-card .title{font-size:16px;font-weight:600;color:var(--neutral-800);}
    .book-card .meta{font-size:13px;color:var(--neutral-400);margin-top:4px;}
    .book-card .actions{display:flex;gap:8px;flex-shrink:0;}
    .badge{display:inline-flex;align-items:center;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:500;}
    .badge-active{background:rgba(16,185,129,0.1);color:var(--success);}
    .badge-inactive{background:var(--neutral-100);color:var(--neutral-400);}
    .btn{display:inline-flex;align-items:center;gap:6px;padding:8px 20px;border-radius:var(--radius-sm);border:none;cursor:pointer;font-size:14px;font-weight:500;text-decoration:none;transition:var(--transition);}
    .btn-primary{background:var(--primary);color:#fff;}
    .btn-primary:hover{background:var(--primary-hover);transform:translateY(-1px);box-shadow:0 4px 12px rgba(99,102,241,0.3);}
    .btn-success{background:var(--success);color:#fff;}
    .btn-success:hover{background:#059669;transform:translateY(-1px);box-shadow:0 4px 12px rgba(16,185,129,0.3);}
    .btn-warning{background:var(--warning);color:#fff;}
    .btn-warning:hover{background:#d97706;transform:translateY(-1px);}
    .btn-ghost{background:var(--neutral-100);color:var(--neutral-600);}
    .btn-ghost:hover{background:var(--neutral-200);}
    .btn-sm{padding:6px 14px;font-size:13px;}
    input,textarea,select{width:100%;padding:10px 14px;border:1px solid var(--neutral-200);border-radius:var(--radius-sm);font-size:14px;transition:var(--transition);background:var(--neutral-50);}
    input:focus,textarea:focus{outline:none;border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-light);background:#fff;}
    textarea{min-height:100px;resize:vertical;font-family:inherit;}
    label{display:block;font-weight:500;margin-bottom:6px;color:var(--neutral-600);font-size:14px;}
    .chapter-list{list-style:none;}
    .chapter-list li{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:1px solid var(--neutral-100);transition:var(--transition);border-radius:var(--radius-sm);}
    .chapter-list li:last-child{border-bottom:none;}
    .chapter-list li:hover{background:var(--neutral-50);}
    .content-area{line-height:1.9;font-size:15px;white-space:pre-wrap;padding:20px;background:var(--neutral-50);border-radius:var(--radius);border:1px solid var(--neutral-200);color:var(--neutral-700);}
    .outline-box{background:linear-gradient(135deg,#fef9e7 0%,#fdf6e3 100%);border:1px solid #f9e79f;border-radius:var(--radius-sm);padding:16px;margin:12px 0;max-height:500px;overflow-y:auto;white-space:pre-wrap;font-size:13px;line-height:1.7;}
    .flash{padding:12px 20px;border-radius:var(--radius-sm);margin-bottom:16px;font-size:14px;font-weight:500;display:flex;align-items:center;gap:8px;}
    .flash.success{background:rgba(16,185,129,0.1);color:#065f46;border:1px solid rgba(16,185,129,0.2);}
    .flash.error{background:rgba(239,68,68,0.1);color:#991b1b;border:1px solid rgba(239,68,68,0.2);}
    .flash.info{background:rgba(99,102,241,0.1);color:#4338ca;border:1px solid rgba(99,102,241,0.2);}
    .page-title{font-size:24px;font-weight:700;color:var(--neutral-800);margin-bottom:20px;display:flex;align-items:center;gap:10px;}
    @media(max-width:768px){.book-card{flex-direction:column;align-items:flex-start;gap:12px;}.book-card .actions{align-self:flex-end;}}
    </style>"""

    def _page_raw(self, body: str) -> str:
        """纯页面（无导航栏，用于登录页）。"""
        return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
        <title>📖 AirNovel</title>{self.CSS}</head><body><div class="container">{body}</div></body></html>'''

    def _page(self, body: str, flash: str = None, ft: str = "info") -> str:
        f = f'<div class="flash {ft}">{flash}</div>' if flash else ""
        spinner = '''<div id="loading" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(255,255,255,.85);z-index:9999;justify-content:center;align-items:center;flex-direction:column;">
  <div style="width:48px;height:48px;border:4px solid #ddd;border-top-color:#3498db;border-radius:50%;animation:spin .8s linear infinite;"></div>
  <div style="margin-top:16px;color:#555;font-size:14px;" id="loadingMsg">⏳ 正在请求 AI 生成大纲...</div>
</div>
<style>@keyframes spin{to{transform:rotate(360deg)}}</style>
<script>
document.querySelectorAll('form[data-loading]').forEach(function(f){f.addEventListener('submit',function(){
  document.getElementById('loading').style.display='flex';
  document.getElementById('loadingMsg').textContent=f.getAttribute('data-loading')||'⏳ 处理中...';
  // 禁用提交按钮防止重复点击
  var btn=f.querySelector('button[type=submit]');
  if(btn){btn.disabled=true;btn.style.opacity=0.5;}
})});
</script>'''
        return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
        <title>📖 AirNovel</title>{self.CSS}</head><body>
        {spinner}
        <nav class="nav"><span class="title">📖 AirNovel</span><a href="/">书籍概览</a><a href="/create">新建书籍</a><a href="/settings">设置</a>{'<a href="/logout" style="margin-left:auto;color:var(--neutral-400);">退出</a>' if self.auth_enabled else ''}</nav>
        <div class="container">{f}{body}</div></body></html>'''

    def _books_html(self) -> str:
        books = self._all_books()
        rows = ""
        for b in books:
            act = "🟢" if b.get("activated", True) else "🔴"
            desc = (b.get("description") or "")[:80]
            rows += f'''
            <div class="book-card">
              <div class="info">
                <div class="title">{act} {b["title"]}</div>
                <div class="meta">{b.get("chapter_count",0)} 章 · {(b.get("created_at") or "")[:10]}</div>
                <div style="color:var(--neutral-400);font-size:13px;margin-top:2px;">{desc}</div>
              </div>
              <div class="actions">
                <a href="/book/{b["book_id"]}" class="btn btn-primary btn-sm">查看</a>
                <a href="/book/{b["book_id"]}/write" class="btn btn-success btn-sm" onclick="return confirm(\'立即续写下一章？\')">续写</a>
              </div>
            </div>'''
        if not rows:
            rows = '<div style="text-align:center;padding:60px 20px;color:var(--neutral-400);"><div style="font-size:48px;margin-bottom:16px;">📖</div><p style="font-size:16px;margin-bottom:12px;">还没有创建任何书籍</p><a href="/create" class="btn btn-primary">创建第一本书</a></div>'
        return f'<h2 class="page-title">📚 书籍概览</h2>{rows}'

    # ═══════════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════════

    async def start(self):
        """启动 FastAPI 服务器（作为 asyncio task）。"""
        if self._server_task and not self._server_task.done():
            logger.warning("AirNovel WebUI 已在运行")
            return

        self._server_error = None
        config = uvicorn.Config(
            app=self._app, host="0.0.0.0", port=self.port,
            log_level="info", loop="asyncio", lifespan="on",
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._serve_safely())

        try:
            for _ in range(50):
                if getattr(self._server, "started", False):
                    logger.info(f"AirNovel WebUI 已启动: http://0.0.0.0:{self.port}")
                    logger.info("⚠️  仅限局域网使用，无鉴权")
                    return
                if self._server_error is not None:
                    raise RuntimeError(f"启动失败: {self._server_error}")
                if self._server_task.done():
                    raise RuntimeError("启动失败，任务已结束")
                await asyncio.sleep(0.1)
            logger.warning("WebUI 启动耗时较长，仍在后台启动中")
        except asyncio.CancelledError:
            await self._stop_locked()
            raise
        except Exception:
            await self._stop_locked()
            raise

    async def stop(self):
        """停止 FastAPI 服务器。"""
        await self._stop_locked()

    async def _serve_safely(self):
        try:
            if self._server:
                await self._server.serve()
        except asyncio.CancelledError:
            raise
        except SystemExit as e:
            self._server_error = e
            logger.error(f"AirNovel WebUI 端口 {self.port} 被占用，请更换端口或释放后重试。")
        except Exception as e:
            self._server_error = e
            logger.error(f"AirNovel WebUI 异常: {e}", exc_info=True)

    async def _stop_locked(self):
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        self._server = None
        self._server_task = None
        logger.info("AirNovel WebUI 已停止")
