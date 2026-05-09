"""
main.py - AirNovel 插件主文件
对标 LivingMemory 架构：FastAPI/uvicorn 以 asyncio task 运行在 AstrBot 事件循环上。
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, StarTools, register, StarTools

from .webui import WebUIServer


@register("AirNovel", "AstrBot User", "AI 自动连载小说工具", "1.1.0")
class AirNovelPlugin(Star):
    """AI 自动连载小说插件。"""

    # 每日续写次数追踪（类级别，所有实例共享）
    _daily_counts: dict[str, int] = {}  # book_id -> count
    _daily_date: str = ""               # 当前记录的日期

    def __init__(self, context: Context, config: dict[str, Any]):
        super().__init__(context)
        self.context = context
        self.config = config

        self.data_dir = str(StarTools.get_data_dir())
        self.webui_server = None
        self._initialized = False
        self._background_tasks: set[asyncio.Task] = set()

        # 异步初始化（对标 LivingMemory）
        self._create_tracked_task(self._initialize())

    def _create_tracked_task(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    # ═════════════════════════════════════════════════════════════
    # 初始化
    # ═════════════════════════════════════════════════════════════

    async def _initialize(self):
        try:
            # 确保数据目录存在
            Path(self.data_dir, "books").mkdir(parents=True, exist_ok=True)
            logger.info(f"AirNovel 数据目录: {self.data_dir}")

            # 自动恢复 WebUI
            await self._start_webui()

            # 自动注册定时任务
            self._register_cron()

            self._initialized = True
            logger.info("AirNovel 初始化完成")
        except Exception as e:
            logger.error(f"AirNovel 初始化失败: {e}", exc_info=True)

    async def _start_webui(self):
        """启动 Web 管理界面（对标 LivingMemory）。"""
        try:
            self.webui_server = WebUIServer(
                data_dir=self.data_dir,
                book_callback=self._book_op,
                write_callback=self._write_chapter,
                outline_callback=self._gen_outline,
                config=self.config,
            )
            await self.webui_server.start()
        except Exception as e:
            logger.error(f"启动 WebUI 失败: {e}", exc_info=True)
            self.webui_server = None

    # ═════════════════════════════════════════════════════════════
    # AI 回调
    # ═════════════════════════════════════════════════════════════

    async def _book_op(self, action: str, **kw) -> dict:
        """书籍操作回调（供 WebUI 使用）。"""
        return {}

    async def _gen_outline(self, title: str, desc: str, tags: list[str],
                           prompt: str, sys_p: str) -> str:
        """生成大纲回调。"""
        pid = self.config.get("model_id") or None
        provider = self.context.get_provider_by_id(pid) if pid else self.context.get_using_provider()
        if not provider:
            raise RuntimeError("未配置模型 ID")
        up = (
            f"请为以下小说创作一份详细的故事大纲。\n\n"
            f"【书名】{title}\n【简介】{desc}\n【标签】{', '.join(tags)}\n"
            f"【创作方向】{prompt}\n\n"
            f"请分点输出：1.故事背景 2.主要人物 3.主线剧情 4.核心冲突 5.分卷建议。"
        )
        resp: LLMResponse = await provider.text_chat(prompt=up, system_prompt=sys_p, contexts=None)
        if resp and resp.role == "assistant" and resp.completion_text:
            return resp.completion_text
        raise RuntimeError(f"模型返回异常")

    # ═════════════════════════════════════════════════════════════
    # 数据读写
    # ═════════════════════════════════════════════════════════════

    def _books_dir(self) -> Path:
        return Path(self.data_dir, "books")

    def _load_meta(self, bid: str) -> dict | None:
        import json
        mf = self._books_dir() / bid / "meta.json"
        if mf.exists():
            try:
                return json.loads(mf.read_text("utf-8"))
            except Exception:
                return None
        return None

    def _save_meta(self, bid: str, meta: dict):
        import json
        (self._books_dir() / bid).mkdir(parents=True, exist_ok=True)
        (self._books_dir() / bid / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")

    def _save_chapter(self, bid: str, title: str, content: str) -> dict | None:
        import re, json
        meta = self._load_meta(bid)
        if not meta:
            return None
        num = meta.get("chapter_count", 0) + 1
        safe = re.sub(r'[\\/:*?"<>|]', "", title)[:60]
        fname = f"第{num}章_{safe}.txt" if safe else f"第{num}章.txt"
        ch_dir = self._books_dir() / bid / "chapters"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / fname).write_text(content, "utf-8")
        ch = {"id": num, "title": title or f"第{num}章", "filename": fname,
              "updated_at": datetime.now().isoformat()}
        meta.setdefault("chapters", []).append(ch)
        meta["chapter_count"] = num
        meta["updated_at"] = datetime.now().isoformat()
        self._save_meta(bid, meta)
        return ch

    def _recent_chapters(self, bid: str, n: int = 3) -> list[dict]:
        meta = self._load_meta(bid)
        if not meta:
            return []
        chapters = meta.get("chapters", [])
        recent = chapters[-n:] if len(chapters) >= n else chapters
        result = []
        for ch in recent:
            fp = self._books_dir() / bid / "chapters" / ch["filename"]
            content = fp.read_text("utf-8")[:500] if fp.exists() else ""
            result.append({**ch, "content": content})
        return result

    def _all_books(self) -> list[dict]:
        import json
        result = []
        seen = set()
        bd = self._books_dir()
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

    # ═════════════════════════════════════════════════════════════
    # 定时任务（每本书独立注册）
    # ═════════════════════════════════════════════════════════════

    def _register_cron(self):
        """为每本激活的书籍注册独立的定时任务。"""
        try:
            import asyncio
            default_cron = self.config.get("cron_expression", "0 8 * * *")
            tz = self.config.get("timezone", "Asia/Shanghai")
            books = self._all_books()
            registered = 0
            for b in books:
                if not b.get("activated", True):
                    continue
                bid = b["book_id"]
                book_cron = b.get("cron", default_cron)
                job_name = f"airnovel_write_{bid}"
                # 使用 asyncio.ensure_future 确保协程被执行
                asyncio.ensure_future(
                    self.context.cron_manager.add_basic_job(
                        name=job_name,
                        cron_expression=book_cron,
                        handler=lambda bid=bid: asyncio.create_task(self._cron_write_one(bid)),
                        description=f"AirNovel 续写《{b['title']}》",
                        timezone=tz,
                        persistent=True,
                    )
                )
                registered += 1
            logger.info(f"AirNovel 定时续写已注册: {registered} 本书")
        except Exception as e:
            logger.error(f"注册定时任务失败: {e}")

    async def _cron_write_one(self, bid: str):
        """定时任务：续写单本书（不受每日次数限制）。"""
        try:
            s, m = await self._write_chapter_cron(bid)
            logger.info(f"定时续写 {m}")
        except Exception as e:
            logger.error(f"定时续写失败: {e}")

    async def _write_chapter_cron(self, bid: str) -> tuple[bool, str]:
        """续写（定时任务用，不受每日次数限制）。"""
        return await self._do_write(bid)

    async def _write_chapter(self, bid: str) -> tuple[bool, str]:
        """续写（手动触发，受每日次数限制）。"""
        # ── 每日次数检查 ──
        from datetime import date
        today = date.today().isoformat()
        if AirNovelPlugin._daily_date != today:
            AirNovelPlugin._daily_date = today
            AirNovelPlugin._daily_counts.clear()
        limit = self.config.get("daily_write_limit", 1)
        current = AirNovelPlugin._daily_counts.get(bid, 0)
        if current >= limit:
            msg = "一天多看一章就够了哦～" if limit <= 1 else f"今天已经续写 {limit} 次了，明天再来吧～"
            return False, msg
        ok, msg = await self._do_write(bid)
        if ok:
            AirNovelPlugin._daily_counts[bid] = current + 1
        return ok, msg

    async def _do_write(self, bid: str) -> tuple[bool, str]:
        """执行实际的 AI 续写逻辑。"""
        meta = self._load_meta(bid)
        if not meta:
            return False, "书籍不存在"
        pid = self.config.get("model_id") or None

    # ═════════════════════════════════════════════════════════════
    # AstrBot 命令
    # ═════════════════════════════════════════════════════════════

    @filter.command("airnovel")
    async def cmd(self, event: AstrMessageEvent):
        if not self._initialized:
            yield event.plain_result("⏳ AirNovel 正在初始化...")
            return
        text = event.get_message_str().strip()
        parts = text.split()
        sub = parts[1].lower() if len(parts) > 1 else ""

        if sub == "web":
            port = self.config.get("flask_port", 14514)
            if self.webui_server:
                yield event.plain_result(f"✅ WebUI 运行中: http://<IP>:{port}")
            else:
                yield event.plain_result("WebUI 未启动，查看日志")

        elif sub == "write":
            yield event.plain_result("🔄 正在续写...")
            await self._cron_write_all()
            yield event.plain_result("✅ 续写完成")

        elif sub == "list":
            books = self._all_books()
            lines = ["📚 书籍列表:"] if books else ["📚 暂无书籍"]
            for b in books:
                act = "🟢" if b.get("activated", True) else "🔴"
                lines.append(f"  {act} {b['title']} ({b.get('chapter_count',0)}章)")
            yield event.plain_result("\n".join(lines))

        elif sub == "status":
            web = "🟢 运行中" if (self.webui_server and getattr(self.webui_server, '_server', None) and getattr(self.webui_server._server, 'started', False)) else "🔴 未启动"
            books = self._all_books()
            yield event.plain_result(
                f"📖 AirNovel\n"
                f"├ WebUI: {web} (端口 {self.config.get('flask_port',14514)})\n"
                f"├ 书籍: {len(books)} 本\n"
                f"└ 数据: {self.data_dir}"
            )

        else:
            yield event.plain_result(
                "📖 AirNovel 命令:\n"
                "├ /airnovel web    - Web 面板状态\n"
                "├ /airnovel write  - 续写所有书\n"
                "├ /airnovel list   - 书籍列表\n"
                "├ /airnovel status - 状态\n"
                "└ 面板: http://<IP>:14514"
            )

    # ═════════════════════════════════════════════════════════════
    # 生命周期
    # ═════════════════════════════════════════════════════════════

    async def terminate(self):
        logger.info("AirNovel 正在卸载...")
        if self.webui_server:
            await self.webui_server.stop()
        # 取消后台任务
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        logger.info("AirNovel 已卸载")
