import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram import Client as TgClient

from config import CFG
import database as db
import exporter
import keyboards as kb
import texts
from parser_engine import ParserEngine, ParseTask

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

STATE: dict[int, str] = {}

bot = Bot(token=CFG.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

if CFG.user_session_string:
    userbot = TgClient("dewflow_userbot", api_id=CFG.api_id, api_hash=CFG.api_hash,
                       session_string=CFG.user_session_string)
else:
    userbot = TgClient(CFG.user_session_name, api_id=CFG.api_id, api_hash=CFG.api_hash)


def allowed(uid: int) -> bool:
    return not CFG.admin_ids or uid in CFG.admin_ids


@dp.update.outer_middleware()
async def gate(handler, event, data):
    uid = None
    if getattr(event, "message", None) and event.message.from_user:
        uid = event.message.from_user.id
    elif getattr(event, "callback_query", None) and event.callback_query.from_user:
        uid = event.callback_query.from_user.id
    if uid is not None and not allowed(uid):
        try:
            if getattr(event, "message", None):
                await event.message.answer("⛔ Нет доступа. Попроси админа добавить твой ID в ADMIN_IDS.")
            else:
                await event.callback_query.answer("⛔ Нет доступа", show_alert=True)
        except Exception:
            pass
        return
    return await handler(event, data)


class BotSender:
    def __init__(self, bot: Bot):
        self._b = bot

    async def send_message(self, chat_id: int, text: str, **kw):
        return await self._b.send_message(chat_id, text, **kw)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kw):
        try:
            return await self._b.edit_message_text(text, chat_id, message_id, **kw)
        except Exception:
            return None

    async def send_document(self, chat_id: int, path: str, caption: str | None = None, **kw):
        return await self._b.send_document(chat_id, FSInputFile(path), caption=caption, **kw)

    async def send_animation(self, chat_id: int, url: str, caption: str | None = None, **kw):
        return await self._b.send_animation(chat_id, url, caption=caption, **kw)

    async def send_photo(self, chat_id: int, photo, caption: str | None = None, **kw):
        return await self._b.send_photo(chat_id, photo, caption=caption, **kw)


sender = BotSender(bot)
engine = ParserEngine(userbot, sender, CFG.db_path, workers=CFG.workers_count)


MENU: dict[int, int] = {}
PICK: dict[int, list] = {}
PICKSEL: dict[int, dict] = {}
WPICK: dict[int, list] = {}


def welcome_media():
    if os.path.exists(CFG.welcome_file):
        return FSInputFile(CFG.welcome_file)
    return CFG.welcome_gif


async def show_home(chat_id: int):
    try:
        media = welcome_media()
        if isinstance(media, FSInputFile):
            logging.info(f"Sending local file: {CFG.welcome_file}")
            return await sender.send_photo(chat_id, media, caption=texts.WELCOME,
                                           reply_markup=kb.main_menu())
        else:
            logging.info(f"Sending animation from URL: {media}")
            return await sender.send_animation(chat_id, media, caption=texts.WELCOME,
                                               reply_markup=kb.main_menu())
    except Exception as e:
        logging.warning(f"Media failed, fallback to text: {e}")
        return await sender.send_message(chat_id, texts.WELCOME, reply_markup=kb.main_menu())


async def forget_menu(uid: int, chat_id: int, except_id: int | None = None):
    old = MENU.pop(uid, None)
    if old and old != except_id:
        try:
            await bot.delete_message(chat_id, old)
        except Exception:
            pass


@dp.message(CommandStart(), F.chat.type == "private")
async def cmd_start(m: Message):
    logging.info(f"/start from {m.from_user.id} in private")
    STATE.pop(m.from_user.id, None)
    PICKSEL.pop(m.from_user.id, None)
    await forget_menu(m.from_user.id, m.chat.id)
    sent = await show_home(m.chat.id)
    if sent:
        MENU[m.from_user.id] = sent.message_id


@dp.message(CommandStart(), F.chat.type != "private")
async def cmd_start_group(m: Message):
    try:
        await m.reply("Привет! Напиши мне в личку /start — меню и парсинг там 👇")
    except Exception:
        pass


@dp.message(Command("cancel"))
async def cmd_cancel(m: Message):
    STATE.pop(m.from_user.id, None)
    PICKSEL.pop(m.from_user.id, None)
    await m.answer("Отменено. Возврат в меню 👇", reply_markup=kb.main_menu())


DASH: dict[int, dict] = {}


def _bar(pct: float, w: int = 12) -> str:
    f = max(0, min(w, round(pct / 100 * w)))
    return "█" * f + "░" * (w - f)


def _short(t, n: int = 26) -> str:
    t = str(t)
    return t if len(t) <= n else t[:n - 1] + "…"


def render_dashboard(snap: dict, db_total) -> str:
    total = snap["total"]
    fin = snap["done"] + snap["error"] + snap["cancelled"]
    pct = (fin / total * 100) if total else 100.0
    L = [f"📊 <b>Парсинг</b> — ✅{snap['done']}/{total} • 🔄{snap['run']} • "
         f"⏳{snap['queued'] + snap['flood']} • ❌{snap['error']}"]
    L.append(f"{_bar(pct)} {pct:.0f}%")
    L.append(f"💾 В базе: <b>{db_total}</b> (+{snap['run_saved']} за запуск)")
    if snap["active"]:
        L.append("🔄 <b>Сейчас обрабатываются:</b>")
        for a in snap["active"][:3]:
            tot = a["total"] if isinstance(a["total"], int) else "?"
            spd = f"{a['speed']}/с" if a["speed"] else "старт…"
            eta = f" • ⏱{a['eta']}" if a.get("eta") else ""
            L.append(f"• {_short(a['title'])} — {a['done']}/{tot} • {spd}{eta}")
    if snap["queue_titles"]:
        rest = len(snap["queue_titles"]) - 2
        q = ", ".join(_short(t, 18) for t in snap["queue_titles"][:2])
        L.append(f"⏳ <b>В очереди ({len(snap['queue_titles'])}):</b> {q}" + (f" +{rest}" if rest > 0 else ""))
    if snap["error_items"]:
        L.append(f"❌ <b>Ошибки ({len(snap['error_items'])}):</b>")
        for t, e in snap["error_items"][:3]:
            L.append(f"• {_short(t, 22)} — {_short(e, 45)}")
    if snap["cancelled"]:
        L.append(f"🚫 Отменено: {snap['cancelled']}")
    return "\n".join(L)


def dash_kb():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="dash_refresh"),
         InlineKeyboardButton(text="🛑 Очистить очередь", callback_data="dash_clear")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="home")],
    ])


async def dash_loop(owner_id: int):
    while True:
        d = DASH.get(owner_id)
        if not d:
            return
        snap = engine.snapshot()
        try:
            db_total = (await db.get_stats(CFG.db_path))["total"]
        except Exception:
            db_total = "?"
        try:
            await bot.edit_message_text(render_dashboard(snap, db_total),
                                        d["chat"], d["msg"], reply_markup=dash_kb())
        except Exception as e:
            if "not found" in str(e).lower():
                break
        if snap["pending"] == 0:
            break
        await asyncio.sleep(3)
    DASH.pop(owner_id, None)


@dp.message(Command("status"))
async def cmd_status(m: Message):
    snap = engine.snapshot()
    if snap["total"] == 0:
        await m.answer("Очередь пуста. Запусти парсинг кнопкой 🔍.", reply_markup=kb.main_menu())
        return
    db_total = (await db.get_stats(CFG.db_path))["total"]
    txt = render_dashboard(snap, db_total)
    old = DASH.get(m.from_user.id)
    if old:
        try:
            await bot.edit_message_text(txt, old["chat"], old["msg"], reply_markup=dash_kb())
            await m.answer("Дашборд обновлён 👆")
            return
        except Exception:
            pass
    msg = await m.answer(txt, reply_markup=dash_kb())
    DASH[m.from_user.id] = {"chat": msg.chat.id, "msg": msg.message_id}
    asyncio.create_task(dash_loop(m.from_user.id))


@dp.message(Command("clear"))
async def cmd_clear(m: Message):
    n = await engine.cancel_pending()
    await m.answer(f"🛑 Из очереди убрано: <b>{n}</b>.\n"
                   f"Чаты в работе допарсятся до конца, их статусы прилетят отдельно.")


async def show_panel(q: CallbackQuery, text: str, reply_markup=None, **kw):
    m, uid = q.message, q.from_user.id
    dwp = kw.pop("disable_web_page_preview", None)
    is_media = (not m.text) and (m.caption is not None)
    try:
        if m.text:
            kw2 = dict(kw)
            if dwp is not None:
                kw2["disable_web_page_preview"] = dwp
            await m.edit_text(text, reply_markup=reply_markup, **kw2)
        elif is_media and len(text) <= 1000:
            await m.edit_caption(caption=text, reply_markup=reply_markup, **kw)
        else:
            raise ValueError("needs-new-message")
        MENU[uid] = m.message_id
    except Exception as e:
        if "message is not modified" in str(e):
            MENU[uid] = m.message_id
            return
        try:
            await m.delete()
        except Exception:
            pass
        kw3 = dict(kw)
        if dwp is not None:
            kw3["disable_web_page_preview"] = dwp
        sent = await bot.send_message(m.chat.id, text, reply_markup=reply_markup, **kw3)
        MENU[uid] = sent.message_id


@dp.callback_query(F.data == "home")
async def cb_home(q: CallbackQuery):
    uid = q.from_user.id
    STATE.pop(uid, None)
    PICKSEL.pop(uid, None)
    DASH.pop(uid, None)
    try:
        await q.message.delete()
    except Exception:
        pass
    await forget_menu(uid, q.message.chat.id, except_id=q.message.message_id)
    sent = await show_home(q.message.chat.id)
    if sent:
        MENU[uid] = sent.message_id
    await q.answer()


@dp.callback_query(F.data == "parse")
async def cb_parse(q: CallbackQuery):
    STATE[q.from_user.id] = "await_chats"
    await show_panel(q, texts.ASK_CHATS)
    await q.answer()


@dp.callback_query(F.data == "filters")
async def cb_filters(q: CallbackQuery):
    only = await db.get_only_phone(CFG.db_path, q.from_user.id)
    mode = "только с номером" if only else "все пользователи"
    await show_panel(q, f"⚙️ <b>Настройки фильтров</b>\nТекущий режим: {mode}",
                     reply_markup=kb.filters_menu(only))
    await q.answer()


@dp.callback_query(F.data == "toggle_phone")
async def cb_toggle(q: CallbackQuery):
    new_val = await db.toggle_only_phone(CFG.db_path, q.from_user.id)
    try:
        await q.message.edit_reply_markup(reply_markup=kb.filters_menu(new_val))
    except Exception:
        pass
    await q.answer(f"Режим: {'ТОЛЬКО С НОМЕРОМ 🟢' if new_val else 'ВСЕ 🔴'}")


@dp.callback_query(F.data == "db")
async def cb_db(q: CallbackQuery):
    await show_panel(q, "🗄 <b>База данных</b>\nВыбери: статистика или экспорт глобальной базы.",
                     reply_markup=kb.db_menu())
    await q.answer()


@dp.callback_query(F.data == "stats")
async def cb_stats(q: CallbackQuery):
    s = await db.get_stats(CFG.db_path)
    top = "\n".join([f"• {t[0]} — {t[1]}" for t in s.get("top_sources", [])]) or "— пусто —"
    txt = (f"📊 <b>Статистика глобальной БД</b>\n\n"
           f"👥 Всего уникальных: <b>{s['total']}</b>\n"
           f"🔗 С username: <b>{s['with_username']}</b>\n"
           f"📱 С номером: <b>{s['with_phone']}</b>\n"
           f"⭐ Premium: <b>{s['premium']}</b>\n\n"
           f"🏆 Топ источников:\n{top}")
    await show_panel(q, txt, reply_markup=kb.db_menu())
    await q.answer()


@dp.callback_query(F.data == "portfolio")
async def cb_portfolio(q: CallbackQuery):
    await show_panel(q, texts.PORTFOLIO, reply_markup=kb.portfolio_menu(),
                     disable_web_page_preview=True)
    await q.answer()


@dp.callback_query(F.data.startswith("exp_"))
async def cb_export(q: CallbackQuery):
    fmt = q.data.split("_", 1)[1]
    await q.answer("Готовлю файл...")
    uid = q.from_user.id
    only = await db.get_only_phone(CFG.db_path, uid)
    rows = await db.fetch_all(CFG.db_path, only_phone=only)
    if not rows:
        await bot.send_message(q.message.chat.id, "База пока пуста. Сначала запусти парсинг 🔍")
        return
    if fmt == "xlsx":
        path = await asyncio.to_thread(exporter.export_xlsx, rows)
    elif fmt == "csv":
        path = await asyncio.to_thread(exporter.export_csv, rows)
    elif fmt == "json":
        path = await asyncio.to_thread(exporter.export_json, rows)
    else:
        path = await asyncio.to_thread(exporter.export_txt, rows)
    cap = f"📦 Экспорт {fmt.upper()} — {len(rows)} уникальных {'(только с номером)' if only else ''}"
    await bot.send_document(q.message.chat.id, FSInputFile(path), caption=cap)


def _u(u: dict) -> str:
    if u.get("username"):
        return f"@{u['username']}"
    return f"{u.get('name') or '—'} (<code>{u['id']}</code>)"


HELP = """📖 <b>Команды аналитики</b>
/search <code>запрос</code> — поиск по ID / @username / имени
/overlap <code>чат1 | чат2</code> — общие участники двух чатов
/anchors <code>[мин=3]</code> — якорные (сидят во многих чатах)
/warm <code>[мин=2] [all]</code> — тёплые под outreach (XLSX)
/growth <code>чат</code> — динамика роста + график
/diff <code>чат</code> — новые/ушедшие по последнему снимку
/segments — сегментация базы + пирог
/status — дашборд очереди
/clear — убрать очередь
/watch <code>add чат часы</code> | <code>del чат</code> | <code>list</code> — автоповтор по расписанию
/help — эта справка"""


@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HELP)


@dp.message(Command("search"), F.chat.type == "private")
async def cmd_search(m: Message):
    q = (m.text.split(maxsplit=1) + [""])[1].strip()
    if not q:
        await m.answer("Пример: <code>/search durov</code> или <code>/search 123456</code>")
        return
    rows = await db.search_users(CFG.db_path, q, 10)
    if not rows:
        await m.answer("Ничего не найдено.")
        return
    out = [f"🔎 <b>Найдено: {len(rows)}</b>"]
    for r in rows:
        chats = (r.get("chats") or "—")[:90]
        out.append(f"• {_u(r)} | id <code>{r['id']}</code>\n  чаты: {chats}")
    await m.answer("\n".join(out))


@dp.message(Command("overlap"), F.chat.type == "private")
async def cmd_overlap(m: Message):
    args = (m.text.split(maxsplit=1) + [""])[1]
    parts = [p.strip() for p in args.replace(",", "|").split("|") if p.strip()]
    if len(parts) < 2:
        await m.answer("Пример: <code>/overlap baraholkaUA_1 | x_escort</code>\n"
                       "Названия чатов — как в /stats или дашборде.")
        known = await db.known_chats(CFG.db_path, 15)
        if known:
            await m.answer("Известные чаты:\n" + "\n".join(f"• <code>{c}</code> ({n})" for c, n in known))
        return
    total, rows = await db.overlap(CFG.db_path, parts[0], parts[1], 20)
    out = [f"🔗 <b>Пересечение:</b> <code>{parts[0]}</code> × <code>{parts[1]}</code> = <b>{total}</b>"]
    out += [f"• {_u(r)}" for r in rows]
    if total > len(rows):
        out.append(f"<i>…и ещё {total - len(rows)}</i>")
    await m.answer("\n".join(out))


@dp.message(Command("anchors"), F.chat.type == "private")
async def cmd_anchors(m: Message):
    args = (m.text.split(maxsplit=1) + [""])[1].split()
    min_c = int(args[0]) if args and args[0].isdigit() else 3
    rows = await db.anchors(CFG.db_path, min_c, 25)
    if not rows:
        await m.answer(f"Якорных (в {min_c}+ чатах) пока нет — спарси больше чатов.")
        return
    out = [f"⚓ <b>Якорные (в {min_c}+ чатах):</b>"]
    for r in rows:
        out.append(f"• {_u(r)} — <b>{r['n']}</b> чатов\n  {(r.get('chats') or '')[:90]}")
    await m.answer("\n".join(out))


@dp.message(Command("warm"), F.chat.type == "private")
async def cmd_warm(m: Message):
    args = (m.text.split(maxsplit=1) + [""])[1].split()
    min_c = int(args[0]) if args and args[0].isdigit() else 2
    need_un = not (len(args) > 1 and args[1].lower() == "all")
    rows = await db.warm_segment(CFG.db_path, min_c, need_un, 5000)
    if not rows:
        await m.answer("Тёплых нет под эти фильтры. Пример: <code>/warm 3</code> или <code>/warm 2 all</code>")
        return
    adapted = [{"id": r["id"], "name": r.get("name") or "", "username": r.get("username"),
                "is_premium": r.get("is_premium", 0), "phone": r.get("phone"),
                "source_chat": (r.get("chats") or "")[:200], "updated_at": f"{r['n']} chats"}
               for r in rows]
    path = await asyncio.to_thread(exporter.export_xlsx, adapted)
    await bot.send_document(m.chat.id, FSInputFile(path),
                            caption=f"🔥 Тёплые: <b>{len(rows)}</b> (в {min_c}+ чатах"
                                    f"{', с username' if need_un else ''})")


@dp.message(Command("growth"), F.chat.type == "private")
async def cmd_growth(m: Message):
    chat = (m.text.split(maxsplit=1) + [""])[1].strip()
    if not chat:
        await m.answer("Пример: <code>/growth baraholkaUA_1</code>")
        return
    hist = await db.scan_history(CFG.db_path, chat, 30)
    if not hist:
        await m.answer("Снимков этого чата нет — спарси его хотя бы раз.")
        return
    import charts
    path = await asyncio.to_thread(charts.growth_chart, hist, chat)
    last, first = hist[-1]["seen"], hist[0]["seen"]
    await bot.send_photo(m.chat.id, FSInputFile(path),
                         caption=f"📈 <b>{chat}</b>: снимков <b>{len(hist)}</b>, "
                                 f"было {first} → стало {last} (<b>{last - first:+}</b>)")
    if len(hist) == 1:
        await m.answer("Пока 1 снимок — повтори парсинг позже, и появится динамика.")


@dp.message(Command("diff"), F.chat.type == "private")
async def cmd_diff(m: Message):
    chat = (m.text.split(maxsplit=1) + [""])[1].strip()
    if not chat:
        await m.answer("Пример: <code>/diff baraholkaUA_1</code>")
        return
    sc = await db.last_scan(CFG.db_path, chat)
    if not sc:
        await m.answer("Снимков этого чата нет.")
        return
    t0 = sc["started_at"]
    n_total, n_sample = await db.new_members(CFG.db_path, chat, t0, 15)
    l_total, l_sample = await db.left_members(CFG.db_path, chat, t0, 15)
    out = [f"🔄 <b>{chat}</b> — последний снимок: seen <b>{sc['seen']}</b>",
           f"🆕 Новых: <b>{n_total}</b>"]
    out += [f"• {_u(u)}" for u in n_sample]
    out.append(f"👋 Ушло: <b>{l_total}</b>")
    out += [f"• {_u(u)}" for u in l_sample]
    await m.answer("\n".join(out))


@dp.message(Command("segments"), F.chat.type == "private")
async def cmd_segments(m: Message):
    seg = await db.segments(CFG.db_path)
    import charts
    path = await asyncio.to_thread(charts.segments_pie, seg)
    await bot.send_photo(m.chat.id, FSInputFile(path),
                         caption=f"🧩 <b>Сегментация</b> (всего {seg['total']}):\n"
                                 f"🔗 с username: <b>{seg['username']}</b>\n"
                                 f"📷 с фото: <b>{seg['photo']}</b>\n"
                                 f"⭐ Premium: <b>{seg['premium']}</b>\n"
                                 f"📱 с номером: <b>{seg['phone']}</b>\n"
                                 f"🔀 в 2+ чатах: <b>{seg['multi']}</b>")


@dp.message(Command("watch"), F.chat.type == "private")
async def cmd_watch(m: Message):
    args = (m.text.split(maxsplit=1) + [""])[1].split()
    if not args:
        await m.answer("Использование:\n<code>/watch add ссылка_чата часы</code>\n"
                       "<code>/watch del ссылка_чата</code>\n<code>/watch list</code>")
        return
    if args[0] == "list":
        lst = await db.watch_list(CFG.db_path)
        if not lst:
            await m.answer("Расписание пусто.")
            return
        import datetime as _dt
        out = ["⏰ <b>Автопарсинг:</b>"]
        for w in lst:
            nxt = _dt.datetime.fromtimestamp(w["next_run"]).strftime("%d.%m %H:%M")
            out.append(f"• <code>{w['chat']}</code> — каждые {w['interval_h']}ч, след: {nxt}")
        await m.answer("\n".join(out))
        return
    if args[0] == "add" and len(args) >= 3:
        try:
            hours = float(args[2].replace(",", "."))
            assert hours > 0
        except (ValueError, AssertionError):
            await m.answer("Часы — положительное число. Пример: <code>/watch add @channel 24</code>")
            return
        await db.watch_add(CFG.db_path, args[1], hours, m.from_user.id)
        await m.answer(f"✅ <code>{args[1]}</code> будет обновляться каждые <b>{hours}ч</b> автоматически.")
        return
    if args[0] == "del" and len(args) >= 2:
        ok = await db.watch_del(CFG.db_path, args[1])
        await m.answer("Убрано из расписания." if ok else "Такого чата в расписании нет.")
        return
    await m.answer("Не понял. Пример: <code>/watch add @channel 24</code>")


async def watch_loop():
    while True:
        try:
            due = await db.watch_due(CFG.db_path)
            for w in due:
                if not w["owner_id"]:
                    continue
                only = await db.get_only_phone(CFG.db_path, w["owner_id"])
                try:
                    msg = await bot.send_message(
                        w["owner_id"],
                        f"⏰ <b>Автопарсинг по расписанию:</b> <code>{w['chat']}</code>...")
                    await engine.enqueue(ParseTask(chat_ref=w["chat"], owner_id=w["owner_id"],
                                                   status_msg_id=msg.message_id, status_chat_id=w["owner_id"],
                                                   only_phone=only, auto=True))
                except Exception as e:
                    logging.warning(f"watch enqueue {w['chat']}: {e}")
        except Exception as e:
            logging.warning(f"watch loop: {e}")
        await asyncio.sleep(60)


def _nav_back(parent: str = "analytics") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data=parent)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
    ])


def analytics_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Поиск по базе", callback_data="feat:search")],
        [InlineKeyboardButton(text="🔗 Пересечения чатов", callback_data="feat:overlap")],
        [InlineKeyboardButton(text="⚓ Якоря 2+", callback_data="anchors:2"),
         InlineKeyboardButton(text="3+", callback_data="anchors:3"),
         InlineKeyboardButton(text="5+", callback_data="anchors:5")],
        [InlineKeyboardButton(text="🔥 Тёплые 2+", callback_data="warm:2:1"),
         InlineKeyboardButton(text="3+", callback_data="warm:3:1")],
        [InlineKeyboardButton(text="🌡️ Все 2+", callback_data="warm:2:0"),
         InlineKeyboardButton(text="3+", callback_data="warm:3:0")],
        [InlineKeyboardButton(text="📈 Рост чата", callback_data="feat:growth"),
         InlineKeyboardButton(text="🔄 Diff new/left", callback_data="feat:diff")],
        [InlineKeyboardButton(text="🧩 Сегментация", callback_data="feat:segments")],
        [InlineKeyboardButton(text="⏰ Автопарсинг", callback_data="feat:watch")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="home")],
    ])


async def build_overlap(a: str, b: str) -> str:
    total, rows = await db.overlap(CFG.db_path, a, b, 12)
    out = [f"🔗 <b>Пересечение:</b> <code>{a}</code> × <code>{b}</code> = <b>{total}</b>"]
    out += [f"• {_u(r)}" for r in rows]
    if total > len(rows):
        out.append(f"<i>…и ещё {total - len(rows)} (полный список — экспортом)</i>")
    return "\n".join(out)


async def build_anchors(min_c: int) -> str:
    rows = await db.anchors(CFG.db_path, min_c, 15)
    if not rows:
        return f"Якорных (в {min_c}+ чатах) пока нет — спарси больше чатов."
    out = [f"⚓ <b>Якорные (в {min_c}+ чатах):</b>"]
    for r in rows:
        out.append(f"• {_u(r)} — <b>{r['n']}</b>\n  {(_short(r.get('chats') or '', 80))}")
    return "\n".join(out)


async def build_diff(chat: str) -> str:
    sc = await db.last_scan(CFG.db_path, chat)
    if not sc:
        return "Снимков этого чата нет — спарси его хотя бы раз."
    t0 = sc["started_at"]
    n_total, n_sample = await db.new_members(CFG.db_path, chat, t0, 10)
    l_total, l_sample = await db.left_members(CFG.db_path, chat, t0, 10)
    out = [f"🔄 <b>{chat}</b> — снимок: seen <b>{sc['seen']}</b>", f"🆕 Новых: <b>{n_total}</b>"]
    out += [f"• {_u(u)}" for u in n_sample]
    out.append(f"👋 Ушло: <b>{l_total}</b>")
    out += [f"• {_u(u)}" for u in l_sample]
    return "\n".join(out)


async def build_search(query: str) -> str:
    rows = await db.search_users(CFG.db_path, query, 10)
    if not rows:
        return "Ничего не найдено."
    out = [f"🔎 <b>Найдено: {len(rows)}</b>"]
    for r in rows:
        out.append(f"• {_u(r)} | id <code>{r['id']}</code>\n  чаты: {_short(r.get('chats') or '—', 90)}")
    return "\n".join(out)


async def do_warm(min_c: int, need_un: bool) -> tuple[str, str]:
    rows = await db.warm_segment(CFG.db_path, min_c, need_un, 5000)
    if not rows:
        raise ValueError("empty")
    adapted = [{"id": r["id"], "name": r.get("name") or "", "username": r.get("username"),
                "is_premium": r.get("is_premium", 0), "phone": r.get("phone"),
                "source_chat": (r.get("chats") or "")[:200], "updated_at": f"{r['n']} chats"}
               for r in rows]
    path = await asyncio.to_thread(exporter.export_xlsx, adapted)
    cap = (f"🔥 Тёплые: <b>{len(rows)}</b> (в {min_c}+ чатах"
           f"{', с username' if need_un else ''})")
    return path, cap


async def show_picker(q: CallbackQuery, feat: str, title: str, second: bool = False):
    known = await db.known_chats(CFG.db_path, 8)
    if not known:
        STATE[q.from_user.id] = f"await_{feat}_manual"
        await show_panel(q, "База чатов пуста — спарси хотя бы 1 чат.\n"
                            "Или введи название вручную 👇", reply_markup=_nav_back())
        await q.answer()
        return
    PICK[q.from_user.id] = [c for c, _ in known]
    rows = []
    for i, (c, n) in enumerate(known):
        rows.append([InlineKeyboardButton(text=f"{_short(c, 26)} ({n})",
                                          callback_data=f"{'pk2' if second else 'pk'}:{feat}:{i}")])
    rows.append([InlineKeyboardButton(text="✏️ Ввести вручную", callback_data=f"pkmanual:{feat}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="analytics")])
    await show_panel(q, title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await q.answer()


@dp.callback_query(F.data == "analytics")
async def cb_analytics(q: CallbackQuery):
    STATE.pop(q.from_user.id, None)
    await show_panel(q, "📈 <b>Аналитика базы</b>\nВсё кнопками — выбери инструмент 👇",
                     reply_markup=analytics_kb())
    await q.answer()


@dp.callback_query(F.data == "feat:search")
async def cb_feat_search(q: CallbackQuery):
    STATE[q.from_user.id] = "await_search"
    await show_panel(q, "🔎 <b>Поиск</b>\nПришли ID, @username или часть имени 👇",
                     reply_markup=_nav_back())
    await q.answer()


@dp.callback_query(F.data == "feat:overlap")
async def cb_feat_overlap(q: CallbackQuery):
    await show_picker(q, "overlap", "🔗 Выбери <b>первый</b> чат 👇")


@dp.callback_query(F.data.startswith("pk:"))
async def cb_pick(q: CallbackQuery):
    _, feat, idx = q.data.split(":")
    chats = PICK.get(q.from_user.id, [])
    try:
        chat = chats[int(idx)]
    except (IndexError, ValueError):
        await q.answer("Список устарел — открой панель заново")
        return
    if feat == "overlap":
        PICKSEL[q.from_user.id] = {"first": chat}
        await show_picker(q, "overlap", f"Первый: <code>{chat}</code>\nВыбери <b>второй</b> чат 👇", second=True)
        return
    if feat == "growth":
        await run_growth_panel(q, chat)
    elif feat == "diff":
        await show_panel(q, await build_diff(chat), reply_markup=_nav_back())
    await q.answer()


@dp.callback_query(F.data.startswith("pk2:"))
async def cb_pick2(q: CallbackQuery):
    _, feat, idx = q.data.split(":")
    chats = PICK.get(q.from_user.id, [])
    first = PICKSEL.get(q.from_user.id, {}).get("first")
    PICKSEL.pop(q.from_user.id, None)
    if not first:
        await q.answer("Начни сначала")
        return
    try:
        second = chats[int(idx)]
    except (IndexError, ValueError):
        await q.answer("Список устарел — открой заново")
        return
    if first == second:
        await q.answer("Это один и тот же чат — выбери другой")
        return
    await show_panel(q, await build_overlap(first, second), reply_markup=_nav_back())
    await q.answer()


@dp.callback_query(F.data.startswith("pkmanual:"))
async def cb_pick_manual(q: CallbackQuery):
    feat = q.data.split(":")[1]
    uid = q.from_user.id
    prompts = {
        "overlap": ("await_overlap_manual", "Пришли два чата через <code>|</code>:\n<code>чат1 | чат2</code>"),
        "growth": ("await_growth_manual", "Пришли название чата для графика роста 👇"),
        "diff": ("await_diff_manual", "Пришли название чата для diff 👇"),
    }
    st, txt = prompts.get(feat, ("await_chats", "Пришли чаты 👇"))
    STATE[uid] = st
    await show_panel(q, txt, reply_markup=_nav_back())
    await q.answer()


@dp.callback_query(F.data.startswith("anchors:"))
async def cb_anchors_btn(q: CallbackQuery):
    n = int(q.data.split(":")[1])
    await show_panel(q, await build_anchors(n), reply_markup=_nav_back())
    await q.answer()


@dp.callback_query(F.data.startswith("warm:"))
async def cb_warm_btn(q: CallbackQuery):
    _, mn, u = q.data.split(":")
    try:
        path, cap = await do_warm(int(mn), u == "1")
    except ValueError:
        await q.answer("Тёплых нет под эти фильтры", show_alert=True)
        return
    await bot.send_document(q.message.chat.id, FSInputFile(path), caption=cap)
    await q.answer("Готово")


@dp.callback_query(F.data == "feat:growth")
async def cb_feat_growth(q: CallbackQuery):
    await show_picker(q, "growth", "📈 Выбери чат для графика роста 👇")


@dp.callback_query(F.data == "feat:diff")
async def cb_feat_diff(q: CallbackQuery):
    await show_picker(q, "diff", "🔄 Выбери чат для new/left 👇")


async def run_growth_panel(q: CallbackQuery, chat: str):
    hist = await db.scan_history(CFG.db_path, chat, 30)
    if not hist:
        await show_panel(q, "Снимков этого чата нет — спарси его хотя бы раз.",
                         reply_markup=_nav_back())
        return
    import charts
    path = await asyncio.to_thread(charts.growth_chart, hist, chat)
    last, first = hist[-1]["seen"], hist[0]["seen"]
    await bot.send_photo(q.message.chat.id, FSInputFile(path),
                         caption=f"📈 <b>{chat}</b>: снимков <b>{len(hist)}</b>, "
                                 f"было {first} → стало {last} (<b>{last - first:+}</b>)")
    if len(hist) == 1:
        await q.answer("Пока 1 снимок — повтори парсинг позже")
    else:
        await q.answer("Готово")


async def run_growth_manual(m: Message, chat: str):
    hist = await db.scan_history(CFG.db_path, chat, 30)
    if not hist:
        await m.answer("Снимков этого чата нет — спарси его хотя бы раз.",
                       reply_markup=_nav_back())
        return
    import charts
    path = await asyncio.to_thread(charts.growth_chart, hist, chat)
    last, first = hist[-1]["seen"], hist[0]["seen"]
    await bot.send_photo(m.chat.id, FSInputFile(path),
                         caption=f"📈 <b>{chat}</b>: снимков <b>{len(hist)}</b>, "
                                 f"было {first} → стало {last} (<b>{last - first:+}</b>)",
                         reply_markup=_nav_back())


@dp.callback_query(F.data == "feat:segments")
async def cb_feat_segments(q: CallbackQuery):
    seg = await db.segments(CFG.db_path)
    import charts
    path = await asyncio.to_thread(charts.segments_pie, seg)
    await bot.send_photo(q.message.chat.id, FSInputFile(path),
                         caption=f"🧩 <b>Сегментация</b> (всего {seg['total']}):\n"
                                 f"🔗 с username: <b>{seg['username']}</b>\n"
                                 f"📷 с фото: <b>{seg['photo']}</b>\n"
                                 f"⭐ Premium: <b>{seg['premium']}</b>\n"
                                 f"📱 с номером: <b>{seg['phone']}</b>\n"
                                 f"🔀 в 2+ чатах: <b>{seg['multi']}</b>")
    await q.answer("Готово")


@dp.callback_query(F.data == "feat:watch")
async def cb_feat_watch(q: CallbackQuery):
    await show_watch_panel(q)
    await q.answer()


async def show_watch_panel(q: CallbackQuery):
    lst = await db.watch_list(CFG.db_path)
    import datetime as _dt
    if not lst:
        txt = "⏰ <b>Автопарсинг пуст.</b>\nНажми ➕ и пришли <code>чат часы</code>."
        rows = []
    else:
        WPICK[q.from_user.id] = [w["chat"] for w in lst]
        lines = []
        rows = []
        for i, w in enumerate(lst):
            nxt = _dt.datetime.fromtimestamp(w["next_run"]).strftime("%d.%m %H:%M")
            lines.append(f"• <code>{w['chat']}</code> — {w['interval_h']}ч → {nxt}")
            rows.append([InlineKeyboardButton(text=f"❌ {_short(w['chat'], 24)}",
                                              callback_data=f"watchdel:{i}")])
        txt = "⏰ <b>Автопарсинг:</b>\n" + "\n".join(lines)
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="watchadd")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="analytics")])
    await show_panel(q, txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "watchadd")
async def cb_watch_add(q: CallbackQuery):
    STATE[q.from_user.id] = "await_watch_add"
    await show_panel(q, "Пришли <code>чат часы</code>:\n<code>@channel 24</code>",
                     reply_markup=_nav_back("feat:watch"))
    await q.answer()


@dp.callback_query(F.data.startswith("watchdel:"))
async def cb_watch_del(q: CallbackQuery):
    chats = WPICK.get(q.from_user.id, [])
    try:
        chat = chats[int(q.data.split(":")[1])]
        await db.watch_del(CFG.db_path, chat)
        await q.answer("Убрано")
    except (IndexError, ValueError):
        await q.answer("Список устарел")
    await show_watch_panel(q)


@dp.message(F.text)
async def on_chats(m: Message):
    uid = m.from_user.id
    st = STATE.get(uid)
    if m.text.startswith("/"):
        return
    if st == "await_search":
        STATE.pop(uid, None)
        await m.answer(await build_search(m.text.strip()), reply_markup=_nav_back())
        return
    if st == "await_overlap_manual":
        STATE.pop(uid, None)
        parts = [p.strip() for p in m.text.replace(",", "|").split("|") if p.strip()]
        if len(parts) < 2:
            await m.answer("Нужно два чата через <code>|</code>.", reply_markup=_nav_back())
            return
        await m.answer(await build_overlap(parts[0], parts[1]), reply_markup=_nav_back())
        return
    if st == "await_growth_manual":
        STATE.pop(uid, None)
        await run_growth_manual(m, m.text.strip())
        return
    if st == "await_diff_manual":
        STATE.pop(uid, None)
        await m.answer(await build_diff(m.text.strip()), reply_markup=_nav_back())
        return
    if st == "await_watch_add":
        STATE.pop(uid, None)
        args = m.text.split()
        if len(args) < 2:
            await m.answer("Формат: <code>чат часы</code>, напр. <code>@channel 24</code>",
                           reply_markup=_nav_back("feat:watch"))
            return
        try:
            hours = float(args[1].replace(",", "."))
            assert hours > 0
        except (ValueError, AssertionError):
            await m.answer("Часы — положительное число.", reply_markup=_nav_back("feat:watch"))
            return
        await db.watch_add(CFG.db_path, args[0], hours, uid)
        await m.answer(f"✅ <code>{args[0]}</code> — каждые <b>{hours}ч</b>.",
                       reply_markup=_nav_back("feat:watch"))
        return
    if st != "await_chats":
        return
    STATE.pop(uid, None)
    chats = [c.strip() for c in m.text.replace(",", "\n").split("\n") if c.strip()]
    if not chats:
        await m.answer("Пусто. Пришли хотя бы 1 чат.", reply_markup=kb.main_menu())
        return
    only = await db.get_only_phone(CFG.db_path, uid)
    await m.answer(f"🚀 Принято чатов: <b>{len(chats)}</b>. Ставлю в очередь ({engine.workers_count} воркера)...")
    for ch in chats:
        status = await m.answer(f"⏳ <b>В очереди:</b> <code>{ch}</code>...")
        await engine.enqueue(ParseTask(chat_ref=ch, owner_id=uid,
                                       status_msg_id=status.message_id, status_chat_id=m.chat.id,
                                       only_phone=only))
    dash = await m.answer("📊 Дашборд запускается...")
    DASH[uid] = {"chat": dash.chat.id, "msg": dash.message_id}
    asyncio.create_task(dash_loop(uid))
    await m.answer("Статусы live: дашборд выше + под каждым чатом ⚡️\n"
                   "Команды: /status — дашборд, /clear — убрать очередь",
                   reply_markup=kb.main_menu())


@dp.callback_query(F.data == "dash_refresh")
async def cb_dash_refresh(q: CallbackQuery):
    snap = engine.snapshot()
    try:
        db_total = (await db.get_stats(CFG.db_path))["total"]
    except Exception:
        db_total = "?"
    try:
        await q.message.edit_text(render_dashboard(snap, db_total), reply_markup=dash_kb())
    except Exception:
        pass
    await q.answer("Обновлено")


@dp.callback_query(F.data == "dash_clear")
async def cb_dash_clear(q: CallbackQuery):
    n = await engine.cancel_pending()
    await q.answer(f"Убрано из очереди: {n}")
    snap = engine.snapshot()
    try:
        db_total = (await db.get_stats(CFG.db_path))["total"]
    except Exception:
        db_total = "?"
    try:
        await q.message.edit_text(render_dashboard(snap, db_total), reply_markup=dash_kb())
    except Exception:
        pass


async def main():
    CFG.validate()
    await db.init_db(CFG.db_path)
    print("DB OK, start userbot...", flush=True)
    await userbot.start()
    ume = await userbot.get_me()
    print(f"USERBOT OK: @{getattr(ume, 'username', '?')} id={ume.id}", flush=True)
    me = await bot.get_me()
    print(f"BOT OK: @{me.username} id={me.id} (HTTP polling)", flush=True)
    await engine.start()
    asyncio.create_task(watch_loop(), name="watch-scheduler")
    print(f"ENGINE OK. Workers: {engine.workers_count}. DB: {CFG.db_path}", flush=True)
    print("Жду /start в ЛИЧКЕ бота...", flush=True)
    try:
        await dp.start_polling(bot)
    finally:
        try:
            await userbot.stop()
        except Exception:
            pass
        try:
            await bot.session.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
