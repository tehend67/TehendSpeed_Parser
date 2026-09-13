import asyncio
import time
from dataclasses import dataclass
from pyrogram import Client
from pyrogram.errors import FloodWait, ChannelPrivate, ChatAdminRequired
import database as db

MAX_RESUME = 2


@dataclass
class ParseTask:
    chat_ref: str
    owner_id: int
    status_msg_id: int
    status_chat_id: int
    only_phone: bool
    attempt: int = 0
    run: int = 0
    auto: bool = False


class ParserEngine:
    def __init__(self, userbot: Client, bot, db_path: str, workers: int = 3):
        self.userbot = userbot
        self.bot = bot
        self.db_path = db_path
        self.queue: asyncio.Queue[ParseTask | None] = asyncio.Queue()
        self.workers_count = workers
        self.progress: dict[str, dict] = {}
        self._started = False
        self._gen = 0

    async def start(self):
        if self._started:
            return
        self._started = True
        for i in range(self.workers_count):
            asyncio.create_task(self._worker(i), name=f"parser-worker-{i}")

    async def enqueue(self, task: ParseTask):
        task.run = self._gen
        self.progress[task.chat_ref] = {
            "title": task.chat_ref, "done": 0, "saved": 0, "total": "?",
            "status": "queued", "pass": 1, "error": None,
            "started_at": None, "ended_at": None, "speed": 0,
        }
        await self.queue.put(task)

    async def cancel_pending(self) -> int:
        self._gen += 1
        n = 0
        while True:
            try:
                t = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            p = self.progress.get(t.chat_ref)
            if p and p.get("status") in ("queued", "flood"):
                p.update(status="cancelled", ended_at=time.time())
            self.queue.task_done()
            n += 1
        return n

    def snapshot(self) -> dict:
        by_run, by_q, by_done, by_err, by_flood, by_cancel = [], [], [], [], [], []
        run_saved = 0
        for ref, p in self.progress.items():
            run_saved += p.get("saved", 0)
            st = p.get("status", "queued")
            (by_run if st == "run" else
             by_q if st == "queued" else
             by_done if st == "done" else
             by_err if st == "error" else
             by_flood if st == "flood" else
             by_cancel).append((ref, p))
        active = []
        for ref, p in by_run:
            active.append({"title": p.get("title", ref), "done": p.get("done", 0),
                           "total": p.get("total", "?"), "speed": p.get("speed", 0),
                           "eta": self._eta(p), "pass": p.get("pass", 1)})
        total = len(self.progress)
        pending = len(by_run) + len(by_q) + len(by_flood)
        return {
            "total": total, "run": len(by_run), "queued": len(by_q),
            "done": len(by_done), "error": len(by_err),
            "cancelled": len(by_cancel), "flood": len(by_flood),
            "pending": pending, "run_saved": run_saved,
            "active": active,
            "queue_titles": [p.get("title", r) for r, p in by_q],
            "done_titles": [p.get("title", r) for r, p in by_done],
            "error_items": [(p.get("title", r), p.get("error") or "?") for r, p in by_err],
        }

    @staticmethod
    def _eta(p: dict) -> str | None:
        tot, done, spd = p.get("total"), p.get("done", 0), p.get("speed", 0)
        if not isinstance(tot, int) or not spd or tot <= done:
            return None
        s = int((tot - done) / spd)
        return f"{s // 60:02d}:{s % 60:02d}"

    async def _worker(self, wid: int):
        while True:
            task: ParseTask | None = await self.queue.get()
            try:
                if task is None:
                    break
                if task.run != self._gen:
                    p = self.progress.get(task.chat_ref)
                    if p and p.get("status") in ("queued", "flood"):
                        p.update(status="cancelled", ended_at=time.time())
                    continue
                await self._parse_one(task, wid)
            except Exception as e:
                try:
                    await self.bot.send_message(task.status_chat_id, f"⚠️ Ошибка <code>{task.chat_ref}</code>: {e}")
                except Exception:
                    pass
            finally:
                self.queue.task_done()

    async def _live_updater(self, task: ParseTask, chat_title: str):
        frames = ["⏳", "⌛", "🔄", "⚡️"]
        i = 0
        ref = task.chat_ref
        while self.progress.get(ref, {}).get("status") == "run":
            p = self.progress[ref]
            try:
                tot = p["total"] if isinstance(p["total"], int) else "?"
                await self.bot.edit_message_text(
                    task.status_chat_id, task.status_msg_id,
                    f"{frames[i % len(frames)]} <b>Парсинг:</b> <code>{chat_title}</code>\n"
                    f"Спарсено: <b>{p['done']}</b> / {tot} • {p.get('speed', 0)}/с\n"
                    f"Сохранено: <b>{p['saved']}</b> (дедуп по ID, проход {p.get('pass', 1)})"
                )
            except Exception:
                pass
            i += 1
            await asyncio.sleep(3)

    async def _resolve(self, ref: str):
        last = None
        for _ in range(3):
            try:
                return await self.userbot.get_chat(ref)
            except FloodWait as e:
                last = e
                await asyncio.sleep(min(e.value + 1, 60))
            except Exception as e:
                raise e
        raise last or RuntimeError("no access")

    async def _parse_one(self, task: ParseTask, wid: int):
        ref = task.chat_ref
        p = self.progress.get(ref) or {}
        p.update(status="run", error=None, started_at=p.get("started_at") or time.time(),
                 ended_at=None, done=0, **({"pass": task.attempt + 1} if task.attempt else {}))
        self.progress[ref] = p
        saved = p.get("saved", 0)

        try:
            chat = await self._resolve(ref)
        except FloodWait:
            p.update(status="error", error="FloodWait на входе", ended_at=time.time())
            await self.bot.edit_message_text(task.status_chat_id, task.status_msg_id,
                f"⏳ <code>{ref}</code>: лимит Telegram, попробуй позже (пришли ссылку заново).")
            return
        except Exception as ex:
            p.update(status="error", error=str(ex)[:90], ended_at=time.time())
            await self.bot.edit_message_text(task.status_chat_id, task.status_msg_id,
                f"❌ Не могу открыть <code>{ref}</code>: {ex}\n(приват? юзербот должен состоять в чате)")
            return

        title = getattr(chat, "title", None) or getattr(chat, "username", None) or ref
        chat_key = str(title)[:120]
        p["title"] = chat_key
        total = getattr(chat, "members_count", None)
        p["total"] = total if isinstance(total, int) else "?"

        t0 = time.time()
        is_first = not await db.chat_has_history(self.db_path, chat_key)

        async def flush_mem(ids: list[int]):
            if ids:
                await db.record_membership_many(self.db_path, chat_key, ids, t0)

        updater = asyncio.create_task(self._live_updater(task, str(title)))
        done, batch, mem_ids = 0, [], []
        try:
            async for m in self.userbot.get_chat_members(chat.id):
                u = m.user
                if u is None or u.is_bot or u.is_deleted:
                    done += 1
                    continue
                mem_ids.append(u.id)
                phone = getattr(u, "phone_number", None)
                if task.only_phone and not phone:
                    done += 1
                    p["done"] = done
                    if len(mem_ids) >= 1000:
                        await flush_mem(mem_ids)
                        mem_ids.clear()
                    continue
                full_name = f"{u.first_name or ''} {u.last_name or ''}".strip() or "—"
                batch.append((
                    u.id, full_name[:120], u.username,
                    int(bool(getattr(u, "is_premium", False))),
                    phone, str(title)[:200],
                    int(bool(getattr(u, "photo", None))),
                ))
                done += 1
                if len(batch) >= 200:
                    await db.upsert_many(self.db_path, batch)
                    await flush_mem(mem_ids)
                    saved += len(batch)
                    batch.clear()
                    mem_ids.clear()
                p["done"] = done
                el = max(1, int(time.time() - (p["started_at"] or time.time())))
                p["speed"] = round(done / el, 1)
        except FloodWait as e:
            if batch:
                await db.upsert_many(self.db_path, batch)
                saved += len(batch)
                batch.clear()
            await flush_mem(mem_ids)
            mem_ids.clear()
            p["saved"] = saved
            updater.cancel()
            if task.attempt < MAX_RESUME and task.run == self._gen:
                task.attempt += 1
                wait = min(int(e.value) + 2, 120)
                p.update(status="flood", error=f"FloodWait, пауза {wait}с (проход {task.attempt + 1})")
                try:
                    await self.bot.edit_message_text(
                        task.status_chat_id, task.status_msg_id,
                        f"⏳ <code>{p['title']}</code>: лимит Telegram, жду {wait}с и продолжаю "
                        f"(сохранено {saved}, дубли не страшны)...")
                except Exception:
                    pass
                await asyncio.sleep(wait)
                if task.run == self._gen:
                    p["status"] = "queued"
                    await self.queue.put(task)
                else:
                    p.update(status="cancelled", ended_at=time.time())
                return
            p.update(status="error", error="FloodWait ×3, частично", ended_at=time.time())
            try:
                await self.bot.edit_message_text(task.status_chat_id, task.status_msg_id,
                    f"⚠️ <code>{p['title']}</code>: упёрся в лимит Telegram. Сохранено: {saved}. "
                    f"Пришли ссылку позже — дедуп доберёт остальных.")
            except Exception:
                pass
            return
        except (ChannelPrivate, ChatAdminRequired) as e:
            p.update(status="error", error="нет доступа", ended_at=time.time())
            updater.cancel()
            await self.bot.edit_message_text(task.status_chat_id, task.status_msg_id,
                f"🔒 Нет доступа к <code>{p['title']}</code>: {e}\nДобавь юзербот-аккаунт в группу.")
            return
        except Exception as e:
            p.update(status="error", error=str(e)[:90], ended_at=time.time())
            updater.cancel()
            await self.bot.edit_message_text(task.status_chat_id, task.status_msg_id,
                f"⚠️ Парсинг <code>{p['title']}</code> прерван: {e}\nСохранено: {saved}")
            return

        if batch:
            await db.upsert_many(self.db_path, batch)
            saved += len(batch)
            batch.clear()
        await flush_mem(mem_ids)
        mem_ids.clear()

        await db.record_scan(self.db_path, chat_key, t0, done, is_first)
        diff_block = await self._diff_block(chat_key, t0, is_first)

        p.update(done=done, saved=saved, status="done", ended_at=time.time())
        updater.cancel()
        try:
            await self.bot.edit_message_text(
                task.status_chat_id, task.status_msg_id,
                f"✅ <b>Готово:</b> <code>{p['title']}</code>\n"
                f"Просмотрено: <b>{done}</b>\n"
                f"Сохранено/обновлено: <b>{saved}</b>\n"
                f"Дедупликация по ID активна — дубли не создаются."
                f"{diff_block}"
            )
        except Exception:
            pass
        try:
            cross = await db.cross_joiners(self.db_path, chat_key, t0, 8)
            if cross and not is_first:
                lines = []
                for c in cross:
                    nm = f"@{c['username']}" if c.get("username") else (c.get("name") or c["id"])
                    lines.append(f"• {nm} — был(а) в: {(c.get('other_chats') or '?')[:80]}")
                await self.bot.send_message(
                    task.status_chat_id,
                    f"🚨 <b>Пересечения:</b> в <code>{p['title']}</code> вступили люди из твоих других чатов:\n"
                    + "\n".join(lines))
        except Exception:
            pass

    @staticmethod
    def _fmt_user(u: dict) -> str:
        return f"@{u['username']}" if u.get("username") else (u.get("name") or str(u["id"]))

    async def _diff_block(self, chat_key: str, t0: float, is_first: bool) -> str:
        if is_first:
            return "\n📌 Первый снимок — дальше будет сравнение new/left."
        try:
            n_total, n_sample = await db.new_members(self.db_path, chat_key, t0, 5)
            l_total, l_sample = await db.left_members(self.db_path, chat_key, t0, 5)
            block = f"\n🆕 Новых: <b>{n_total}</b> • 👋 Ушло: <b>{l_total}</b>"
            if n_sample:
                block += "\nНовые: " + ", ".join(self._fmt_user(u) for u in n_sample)
            if l_sample:
                block += "\nУшли: " + ", ".join(self._fmt_user(u) for u in l_sample)
            return block
        except Exception:
            return ""
