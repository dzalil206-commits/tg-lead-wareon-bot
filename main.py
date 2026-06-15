# Главный бот TG Lead Wareon (@TGLeadWareonBot)
# aiogram 3.7+  |  aiosqlite  |  python-dotenv
# Полностью автономный — без зависимости от сайта

import asyncio
import logging
import os
import secrets
import string
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# ===== НАСТРОЙКИ =====
BOT_TOKEN   = os.environ.get('BOT_TOKEN',   '8340651502:AAFur2gI4vgHzmAUPb348F5EF9iD1EgMSEg')
ADMIN_ID    = int(os.environ.get('ADMIN_ID', '5062414502'))
SUPPORT_URL = os.environ.get('SUPPORT_URL', 'https://t.me/TGLeadSupportBot')
CHANNEL_URL = os.environ.get('CHANNEL_URL', 'https://t.me/TGLeadWareon')
CHANNEL_ID  = os.environ.get('CHANNEL_ID',  '@TGLeadWareon')
DB_PATH     = os.environ.get('DB_PATH',     'bot_data.db')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp  = Dispatcher(storage=MemoryStorage())

# ===== ДИЗАЙН =====
DIVIDER = '━━━━━━━━━━━━━━━━━━━━━━'
LOGO    = f'{DIVIDER}\n    🔥 <b>TG LEAD WAREON</b>\n{DIVIDER}'

PLAN_ICONS = {'Miner': '⛏️', 'Sender': '📨', 'Start': '🚀', 'Pro': '⚡', 'Scale': '👑'}
PLAN_DESC  = {
    'Miner':  'парсинг аудитории',
    'Sender': 'массовые рассылки',
    'Start':  'Miner + Sender',
    'Pro':    'Start + лимиты ×3',
    'Scale':  'всё включено',
}
PLAN_PRICE = {'Miner': '490', 'Sender': '990', 'Start': '990', 'Pro': '2 490', 'Scale': '6 990'}


def status_badge(days: int) -> str:
    if days <= 0:
        return '🔴 Истекла'
    if days <= 3:
        return f'🟡 Истекает через {days} дн.'
    return f'🟢 Активна · {days} дн.'


def progress_bar(fraction: float, length: int = 10) -> str:
    """Визуальный бар: ▰▰▰▰▱▱▱▱▱▱"""
    fraction = max(0.0, min(1.0, fraction))
    filled   = round(fraction * length)
    return '▰' * filled + '▱' * (length - filled)


def license_progress(lic: dict) -> str:
    """Бар оставшегося срока лицензии."""
    try:
        created = datetime.fromisoformat(lic['created_at'][:10])
        expires = datetime.fromisoformat(lic['expires_at'][:10])
        total   = (expires - created).days or 1
        left    = lic['days_left']
        return progress_bar(left / total)
    except Exception:
        return progress_bar(1.0 if lic.get('days_left', 0) > 0 else 0.0)


def format_date(iso: str) -> str:
    months = ['', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    try:
        parts = iso[:10].split('-')
        return f'{int(parts[2])} {months[int(parts[1])]} {parts[0]}'
    except Exception:
        return iso[:10]


def _gen_key() -> str:
    chars = string.ascii_uppercase + string.digits
    return '-'.join(''.join(secrets.choice(chars) for _ in range(5)) for _ in range(4))


# ==================== БАЗА ДАННЫХ ====================

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                tg_id          TEXT PRIMARY KEY,
                first_name     TEXT    DEFAULT '',
                username       TEXT    DEFAULT '',
                referred_by    TEXT,
                notify_enabled INTEGER DEFAULT 1,
                joined_at      TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS licenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id       TEXT NOT NULL,
                product     TEXT NOT NULL,
                license_key TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id      TEXT NOT NULL,
                username   TEXT DEFAULT '',
                rating     INTEGER,
                body       TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_lic_tg ON licenses(tg_id);
        ''')
        # Ленивая миграция для старых баз
        try:
            await db.execute('ALTER TABLE users ADD COLUMN notify_enabled INTEGER DEFAULT 1')
        except Exception:
            pass
        await db.commit()


async def db_ensure_user(tg_id: str, first_name: str = '', username: str = '', referred_by: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR IGNORE INTO users (tg_id, first_name, username, referred_by) VALUES (?,?,?,?)',
            (tg_id, first_name, username, referred_by)
        )
        await db.commit()


async def db_get_user(tg_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM users WHERE tg_id=?', (tg_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def db_get_licenses(tg_id: str) -> list[dict]:
    today = datetime.utcnow().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT *, CAST((julianday(expires_at) - julianday(?)) AS INTEGER) AS days_left '
            'FROM licenses WHERE tg_id=? ORDER BY expires_at',
            (today, tg_id)
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['days_left'] = max(0, d['days_left'] or 0)
        d['is_expired'] = d['days_left'] <= 0
        result.append(d)
    return result


async def db_add_license(tg_id: str, product: str, days: int) -> str:
    key     = _gen_key()
    expires = (datetime.utcnow() + timedelta(days=days)).date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO licenses (tg_id, product, license_key, expires_at) VALUES (?,?,?,?)',
            (tg_id, product, key, expires)
        )
        await db.commit()
    return key


async def db_add_days(tg_id: str, product: str, days: int):
    """Продлевает действующую лицензию на N дней или создаёт новую."""
    today = datetime.utcnow().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT id, expires_at FROM licenses WHERE tg_id=? AND product=? AND expires_at>=? '
            'ORDER BY expires_at DESC LIMIT 1',
            (tg_id, product, today)
        ) as cur:
            row = await cur.fetchone()
        if row:
            lic_id, exp = row
            new_exp = (datetime.fromisoformat(exp) + timedelta(days=days)).date().isoformat()
            await db.execute('UPDATE licenses SET expires_at=? WHERE id=?', (new_exp, lic_id))
        else:
            key     = _gen_key()
            new_exp = (datetime.utcnow() + timedelta(days=days)).date().isoformat()
            await db.execute(
                'INSERT INTO licenses (tg_id, product, license_key, expires_at) VALUES (?,?,?,?)',
                (tg_id, product, key, new_exp)
            )
        await db.commit()


async def db_referral_count(tg_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM users WHERE referred_by=?', (tg_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def db_review_count(tg_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM reviews WHERE tg_id=?', (tg_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def db_get_notify(tg_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT notify_enabled FROM users WHERE tg_id=?', (tg_id,)) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row and row[0] is not None else True


async def db_toggle_notify(tg_id: str) -> bool:
    """Инвертирует флаг уведомлений, возвращает новое значение."""
    current = await db_get_notify(tg_id)
    new_val = 0 if current else 1
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE users SET notify_enabled=? WHERE tg_id=?', (new_val, tg_id))
        await db.commit()
    return bool(new_val)


async def db_expiring_today(days_ahead: int) -> list[dict]:
    target = (datetime.utcnow() + timedelta(days=days_ahead)).date().isoformat()
    today  = datetime.utcnow().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT tg_id, product, expires_at FROM licenses '
            'WHERE date(expires_at)=? AND expires_at>=?',
            (target, today)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ==================== FSM ====================

class States(StatesGroup):
    waiting_review = State()
    review_rating  = State()


# ==================== КЛАВИАТУРЫ ====================

def kb_subscribe() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📢 Подписаться на канал', url=CHANNEL_URL)],
        [InlineKeyboardButton(text='✅ Проверить подписку',   callback_data='check_sub')],
    ])


def kb_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🚀 Начать работу', callback_data='menu')],
        [InlineKeyboardButton(text='📚 Как начать (4 шага)', callback_data='howto')],
        [
            InlineKeyboardButton(text='👤 Профиль',   callback_data='profile'),
            InlineKeyboardButton(text='👥 Рефералка', callback_data='referral'),
        ],
        [
            InlineKeyboardButton(text='⭐️ Отзывы',    callback_data='review'),
            InlineKeyboardButton(text='💬 Поддержка', url=SUPPORT_URL),
        ],
    ])


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='🌐 Прокси',    callback_data='proxies'),
            InlineKeyboardButton(text='👤 Аккаунты',  callback_data='accounts'),
        ],
        [
            InlineKeyboardButton(text='🎯 Аудитория', callback_data='audience'),
            InlineKeyboardButton(text='📨 Рассылка',  callback_data='sending'),
        ],
        [InlineKeyboardButton(text='🚀 Как начать (4 шага)', callback_data='howto')],
        [InlineKeyboardButton(text='🛡 Мои лицензии',         callback_data='licenses')],
        [
            InlineKeyboardButton(text='💳 Купить / Продлить', callback_data='buy'),
            InlineKeyboardButton(text='🔑 Ключ',              callback_data='get_key'),
        ],
        [InlineKeyboardButton(text='👥 Реферальная программа', callback_data='referral')],
        [
            InlineKeyboardButton(text='⭐️ Отзыв  +2 дня', callback_data='review'),
            InlineKeyboardButton(text='💬 Поддержка',      url=SUPPORT_URL),
        ],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='menu')]
    ])


def kb_section(*rows) -> InlineKeyboardMarkup:
    kb = [list(r) for r in rows]
    kb.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def kb_stars() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='1 ★',     callback_data='star_1'),
            InlineKeyboardButton(text='2 ★★',    callback_data='star_2'),
            InlineKeyboardButton(text='3 ★★★',   callback_data='star_3'),
        ],
        [
            InlineKeyboardButton(text='4 ★★★★',  callback_data='star_4'),
            InlineKeyboardButton(text='5 ★★★★★', callback_data='star_5'),
        ],
        [InlineKeyboardButton(text='✖️ Отмена', callback_data='menu')],
    ])


# ==================== GATES ====================

async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ('member', 'administrator', 'creator')
    except Exception as e:
        logging.warning(f'Проверка подписки {user_id}: {e} (fail-open)')
        return True


def _sub_gate_text() -> str:
    return (
        f'{LOGO}\n\n'
        f'🚀 <b>Твой центр роста в Telegram</b>\n\n'
        f'🌐 Прокси · 👤 Аккаунты · 🎯 Сбор аудитории · 📨 Умные рассылки — '
        f'всё в одном боте, без рутины.\n'
        f'⚡ Запусти поток целевого трафика быстро и просто.\n\n'
        f'{DIVIDER}\n\n'
        f'🔒 <b>Чтобы начать — подпишитесь на канал</b>\n\n'
        f'<i>Там обновления, кейсы и фишки сервиса.</i>'
    )


async def _require_license(call: CallbackQuery) -> list[dict] | None:
    """Пропускает только пользователей с активной лицензией."""
    tg_id    = str(call.from_user.id)
    licenses = await db_get_licenses(tg_id)
    active   = [l for l in licenses if not l['is_expired']]
    if not active:
        await call.message.edit_text(
            f'⏳ <b>Нет активной лицензии</b>\n{DIVIDER}\n\n'
            f'Инструменты доступны на платном тарифе.\n'
            f'Обратитесь в поддержку для оформления доступа 👇',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='💳 Купить доступ', callback_data='buy')],
                [InlineKeyboardButton(text='💬 Поддержка',     url=SUPPORT_URL)],
                [InlineKeyboardButton(text='🏠 Главное меню',  callback_data='menu')],
            ])
        )
        return None
    return active


# ==================== /start ====================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    if not await is_subscribed(message.from_user.id):
        await message.answer(_sub_gate_text(), reply_markup=kb_subscribe(),
                             disable_web_page_preview=True)
        return

    tg_id = str(message.from_user.id)
    args  = message.text.split(maxsplit=1)

    # Реферальная ссылка: /start ref_<tg_id>
    ref_by = None
    if len(args) > 1 and args[1].startswith('ref_'):
        ref_id = args[1][4:]
        if ref_id != tg_id:
            ref_by = ref_id

    is_new = await db_get_user(tg_id) is None
    await db_ensure_user(tg_id, message.from_user.first_name or '',
                         message.from_user.username or '', ref_by)

    # Начислить реферальный бонус пригласившему
    if is_new and ref_by:
        lics    = await db_get_licenses(ref_by)
        active  = [l for l in lics if not l['is_expired']]
        product = active[0]['product'] if active else 'Start'
        await db_add_days(ref_by, product, 1)
        try:
            await bot.send_message(int(ref_by), f'🎉 <b>+1 день</b> к лицензии — к вам присоединился реферал!')
        except Exception:
            pass

    await _enter_app(message.from_user, answer_msg=message)


async def _enter_app(from_user, answer_msg=None, edit_call=None):
    tg_id    = str(from_user.id)
    name     = from_user.first_name or 'друг'
    licenses = await db_get_licenses(tg_id)
    active   = [l for l in licenses if not l['is_expired']]

    if active:
        soonest   = min(active, key=lambda l: l['days_left'])
        plans     = ' · '.join(sorted({l['product'] for l in active}))
        status_ln = (
            f'💎 <b>Доступ открыт</b> · {plans}\n'
            f'📅 Активен до {format_date(soonest["expires_at"])}'
        )
    else:
        status_ln = (
            f'🆓 <b>Бесплатный режим</b>\n'
            f'🔓 Оформите доступ, чтобы запустить инструменты'
        )

    text = (
        f'{LOGO}\n\n'
        f'👋 <b>{name}</b>, добро пожаловать!\n\n'
        f'Это твой центр привлечения клиентов в Telegram — '
        f'<b>сбор целевой аудитории и умные рассылки</b> прямо в боте, '
        f'без программ на компьютере.\n\n'
        f'{DIVIDER}\n'
        f'✨ <b>Что умеет бот</b>\n\n'
        f'🌐 <b>Прокси</b> — стабильная и безопасная работа аккаунтов\n'
        f'👤 <b>Аккаунты</b> — подключение по номеру или импортом сессии\n'
        f'🎯 <b>Аудитория</b> — сбор базы из открытых чатов и каналов\n'
        f'📨 <b>Рассылка</b> — массовые сообщения с AI-текстами и имитацией человека\n\n'
        f'{DIVIDER}\n'
        f'💡 <b>Как это работает</b>\n'
        f'1️⃣ Подключи аккаунт  →  2️⃣ Собери базу  →  '
        f'3️⃣ Запусти рассылку  →  4️⃣ Получай заявки\n\n'
        f'{DIVIDER}\n'
        f'{status_ln}\n\n'
        f'👇 Жми <b>«Начать работу»</b> — и поехали'
    )
    if edit_call:
        await edit_call.message.edit_text(text, reply_markup=kb_home(),
                                          disable_web_page_preview=True)
    else:
        await answer_msg.answer(text, reply_markup=kb_home(),
                                disable_web_page_preview=True)


# ==================== ПОДПИСКА ====================

@dp.callback_query(F.data == 'check_sub')
async def cb_check_sub(call: CallbackQuery, state: FSMContext):
    if await is_subscribed(call.from_user.id):
        await call.answer('Спасибо за подписку! 🎉')
        tg_id = str(call.from_user.id)
        await db_ensure_user(tg_id, call.from_user.first_name or '', call.from_user.username or '')
        await _enter_app(call.from_user, edit_call=call)
    else:
        await call.answer('Вы ещё не подписались на канал 😔', show_alert=True)


# ==================== ГЛАВНОЕ МЕНЮ ====================

@dp.callback_query(F.data == 'menu')
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    tg_id    = str(call.from_user.id)
    name     = call.from_user.first_name or 'друг'
    licenses = await db_get_licenses(tg_id)
    active   = [l for l in licenses if not l['is_expired']]

    if active:
        soonest  = min(active, key=lambda l: l['days_left'])
        lic_line = f'🟢 Доступ активен  ·  {len(active)} лиц.'
        exp_line = f'📅 До {format_date(soonest["expires_at"])}'
    else:
        lic_line = '🔴 Нет активной подписки'
        exp_line = '💡 Раздел «Купить / Продлить» откроет инструменты'

    await call.message.edit_text(
        f'{LOGO}\n\n'
        f'👤 <b>{name}</b>  ·  {lic_line}\n'
        f'{exp_line}\n\n'
        f'{DIVIDER}\n'
        f'<b>🛠 Инструменты</b>\n'
        f'🌐 Прокси — безопасность аккаунтов\n'
        f'👤 Аккаунты — подключение TG-аккаунтов\n'
        f'🎯 Аудитория — сбор целевой базы\n'
        f'📨 Рассылка — массовые сообщения\n\n'
        f'<b>⚙️ Аккаунт</b>\n'
        f'🛡 Лицензии · 🔑 Ключи · 👥 Рефералы · ⭐️ Отзыв\n\n'
        f'👇 Выберите раздел',
        reply_markup=kb_main()
    )


# ==================== ПРОФИЛЬ ====================

async def _profile_view(tg_id: str, from_user) -> tuple[str, InlineKeyboardMarkup]:
    user     = await db_get_user(tg_id)
    licenses = await db_get_licenses(tg_id)
    active   = [l for l in licenses if not l['is_expired']]
    ref_cnt  = await db_referral_count(tg_id)
    rev_cnt  = await db_review_count(tg_id)
    notify   = await db_get_notify(tg_id)

    name     = from_user.first_name or '—'
    username = f'@{from_user.username}' if from_user.username else '—'
    joined   = format_date(user['joined_at']) if user else '—'

    # Статус-плашка
    if active:
        plan_names = ' · '.join(sorted({l['product'] for l in active}))
        status     = f'💎 <b>PREMIUM</b> · {plan_names}'
    else:
        status     = '🆓 <b>FREE</b> · без активной подписки'

    lines = [
        f'👤 <b>Личный кабинет</b>',
        DIVIDER,
        '',
        status,
        '',
        f'<b>Имя:</b> {name}',
        f'<b>Username:</b> {username}',
        f'<b>ID:</b> <code>{tg_id}</code>',
        f'<b>В боте с:</b> {joined}',
        '',
        DIVIDER,
        '🛡 <b>Подписки</b>',
    ]

    if active:
        for l in active:
            icon = PLAN_ICONS.get(l['product'], '📦')
            bar  = license_progress(l)
            lines.append(
                f'\n{icon} <b>{l["product"]}</b> · {status_badge(l["days_left"])}\n'
                f'   {bar}\n'
                f'   📅 до {format_date(l["expires_at"])}'
            )
    else:
        lines.append('\n🔴 Нет активных подписок\n   Оформите доступ, чтобы открыть инструменты.')

    lines += [
        '',
        DIVIDER,
        '📈 <b>Активность</b>',
        f'👥 Рефералов: <b>{ref_cnt}</b>  ·  🎁 заработано <b>+{ref_cnt} дн.</b>',
        f'⭐️ Отзывов: <b>{rev_cnt}</b>',
    ]

    text = '\n'.join(lines)
    notify_label = '🔔 Уведомления: ВКЛ' if notify else '🔕 Уведомления: ВЫКЛ'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='🛡 Мои лицензии', callback_data='licenses'),
            InlineKeyboardButton(text='🔑 Ключи',        callback_data='get_key'),
        ],
        [
            InlineKeyboardButton(text='👥 Рефералы',  callback_data='referral'),
            InlineKeyboardButton(text='⭐️ Отзыв',     callback_data='review'),
        ],
        [InlineKeyboardButton(text=notify_label, callback_data='profile_notify')],
        [InlineKeyboardButton(text='💳 Купить / Продлить', callback_data='buy')],
        [InlineKeyboardButton(text='🏠 Главное меню',      callback_data='menu')],
    ])
    return text, kb


@dp.callback_query(F.data == 'profile')
async def cb_profile(call: CallbackQuery):
    text, kb = await _profile_view(str(call.from_user.id), call.from_user)
    await call.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)


@dp.callback_query(F.data == 'profile_notify')
async def cb_profile_notify(call: CallbackQuery):
    new_val = await db_toggle_notify(str(call.from_user.id))
    await call.answer('🔔 Напоминания включены' if new_val else '🔕 Напоминания выключены')
    text, kb = await _profile_view(str(call.from_user.id), call.from_user)
    await call.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)


# ==================== ОНБОРДИНГ ====================

@dp.callback_query(F.data == 'howto')
async def cb_howto(call: CallbackQuery):
    await call.message.edit_text(
        f'🚀 <b>Как начать? Проще, чем кажется</b>\n{DIVIDER}\n\n'
        f'<b>{{ 1 }}</b> 🌐 Добавьте прокси\n'
        f'<i>чтобы аккаунты работали стабильно и безопасно</i>\n\n'
        f'<b>{{ 2 }}</b> 👤 Подключите аккаунты\n'
        f'<i>по номеру телефона или импортом сессии</i>\n\n'
        f'<b>{{ 3 }}</b> 🎯 Соберите базу юзеров/чатов\n'
        f'<i>автоматически из открытых чатов и каналов</i>\n\n'
        f'<b>{{ 4 }}</b> 📨 Запустите рассылку по базам\n'
        f'<i>с имитацией человека и AI-вариантами текста</i>\n\n'
        f'{DIVIDER}\n'
        f'📚 В каждом разделе есть подробные инструкции.',
        reply_markup=kb_section(
            [
                InlineKeyboardButton(text='🌐 Прокси',   callback_data='proxies'),
                InlineKeyboardButton(text='👤 Аккаунты', callback_data='accounts'),
            ],
            [
                InlineKeyboardButton(text='🎯 Аудитория', callback_data='audience'),
                InlineKeyboardButton(text='📨 Рассылка',  callback_data='sending'),
            ],
        )
    )


# ==================== РАЗДЕЛЫ ПРОДУКТА ====================

@dp.callback_query(F.data == 'proxies')
async def cb_proxies(call: CallbackQuery):
    if not await _require_license(call):
        return
    await call.message.edit_text(
        f'🌐 <b>Прокси</b>\n{DIVIDER}\n\n'
        f'Прокси нужны, чтобы аккаунты работали стабильно и не попадали под ограничения.\n\n'
        f'<b>Форматы:</b>\n'
        f'<code>socks5://user:pass@host:port</code>\n'
        f'<code>host:port:login:password</code>\n\n'
        f'📖 <b>Инструкция:</b> добавьте прокси списком, проверьте на валидность и '
        f'распределите по аккаунтам — последовательно или случайно.\n\n'
        f'<i>🔧 Полное управление прокси через бота — скоро.</i>',
        reply_markup=kb_section(
            [InlineKeyboardButton(text='💬 Поддержка', url=SUPPORT_URL)],
        )
    )


@dp.callback_query(F.data == 'accounts')
async def cb_accounts(call: CallbackQuery):
    if not await _require_license(call):
        return
    await call.message.edit_text(
        f'👤 <b>Аккаунты</b>\n{DIVIDER}\n\n'
        f'Подключите Telegram-аккаунты для сбора аудитории и рассылок.\n\n'
        f'<b>Способы подключения:</b>\n'
        f'• по номеру телефона (код + 2FA)\n'
        f'• импорт .session / TData\n\n'
        f'📖 <b>Инструкция:</b> привяжите прокси к аккаунту, проверьте статус '
        f'(жив / ограничен / требует входа), настройте профиль и прогрев.\n\n'
        f'<i>🔧 Подключение аккаунтов через бота — скоро.</i>',
        reply_markup=kb_section(
            [InlineKeyboardButton(text='💬 Поддержка', url=SUPPORT_URL)],
        )
    )


@dp.callback_query(F.data == 'audience')
async def cb_audience(call: CallbackQuery):
    if not await _require_license(call):
        return
    await call.message.edit_text(
        f'🎯 <b>Аудитория</b>\n{DIVIDER}\n\n'
        f'Соберите целевую базу из открытых чатов и каналов автоматически.\n\n'
        f'<b>Что собирается:</b> username, ID, имя, активность.\n'
        f'<b>Фильтры:</b> онлайн-статус, язык, активность.\n\n'
        f'📖 <b>Инструкция:</b> укажите чаты/каналы → запустите сбор → получите готовую '
        f'базу для рассылки.\n\n'
        f'⚖️ Сбор — только из <b>открытых</b> источников.\n\n'
        f'<i>🔧 Запуск сбора через бота — скоро.</i>',
        reply_markup=kb_section(
            [InlineKeyboardButton(text='💬 Поддержка', url=SUPPORT_URL)],
        )
    )


@dp.callback_query(F.data == 'sending')
async def cb_sending(call: CallbackQuery):
    if not await _require_license(call):
        return
    await call.message.edit_text(
        f'📨 <b>Рассылка</b>\n{DIVIDER}\n\n'
        f'Запустите умную рассылку по собранным базам.\n\n'
        f'<b>Возможности:</b>\n'
        f'• персонализация, spintax, AI-варианты текста\n'
        f'• имитация человека: «печатает», задержки, паузы\n'
        f'• чередование вариантов по весам, антидубли\n'
        f'• статистика доставки и логи\n\n'
        f'📖 <b>Инструкция:</b> выберите аккаунт → базу → текст → лимиты → запуск.\n\n'
        f'⚖️ Рассылки — только тем, кто дал согласие (38-ФЗ). Спам запрещён.\n\n'
        f'<i>🔧 Запуск рассылок через бота — скоро.</i>',
        reply_markup=kb_section(
            [InlineKeyboardButton(text='💬 Поддержка', url=SUPPORT_URL)],
        )
    )


# ==================== ЛИЦЕНЗИИ ====================

@dp.callback_query(F.data == 'licenses')
async def cb_licenses(call: CallbackQuery):
    tg_id    = str(call.from_user.id)
    licenses = await db_get_licenses(tg_id)

    if not licenses:
        await call.message.edit_text(
            f'🛡 <b>Мои лицензии</b>\n{DIVIDER}\n\n'
            f'🔴 Активных лицензий нет\n\n'
            f'Приобретите подписку, чтобы начать работу.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='💳 Выбрать тариф', callback_data='buy')],
                [InlineKeyboardButton(text='🏠 Главное меню',  callback_data='menu')],
            ])
        )
        return

    lines = [f'🛡 <b>Мои лицензии</b>\n{DIVIDER}']
    for lic in licenses:
        icon  = PLAN_ICONS.get(lic['product'], '📦')
        badge = status_badge(lic['days_left'])
        date  = format_date(lic['expires_at'])
        lines.append(
            f'\n{icon} <b>{lic["product"].upper()}</b>\n'
            f'   {badge}\n'
            f'   📅 До {date}'
        )

    await call.message.edit_text(
        '\n'.join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='💳 Продлить',     callback_data='buy')],
            [InlineKeyboardButton(text='🏠 Главное меню', callback_data='menu')],
        ])
    )


# ==================== КЛЮЧ ====================

@dp.callback_query(F.data == 'get_key')
async def cb_get_key(call: CallbackQuery):
    tg_id    = str(call.from_user.id)
    licenses = await db_get_licenses(tg_id)
    active   = [l for l in licenses if not l['is_expired']]

    if not active:
        await call.message.edit_text(
            f'🔑 <b>Ключ активации</b>\n{DIVIDER}\n\n'
            f'🔴 Нет активных лицензий\n\n'
            f'Приобретите подписку, чтобы получить ключ.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='💳 Выбрать тариф', callback_data='buy')],
                [InlineKeyboardButton(text='🏠 Главное меню',  callback_data='menu')],
            ])
        )
        return

    lines = [f'🔑 <b>Ключи активации</b>\n{DIVIDER}\n<i>Нажмите на ключ, чтобы скопировать</i>']
    for lic in active:
        icon  = PLAN_ICONS.get(lic['product'], '📦')
        badge = status_badge(lic['days_left'])
        lines.append(
            f'\n{icon} <b>{lic["product"]}</b>  ·  {badge}\n'
            f'<code>{lic["license_key"]}</code>'
        )

    await call.message.edit_text('\n'.join(lines), reply_markup=kb_back())


# ==================== КУПИТЬ ====================

@dp.callback_query(F.data == 'buy')
async def cb_buy(call: CallbackQuery):
    lines = [f'💳 <b>Тарифы</b>\n{DIVIDER}\n']
    for key in ['Miner', 'Sender', 'Start', 'Pro', 'Scale']:
        icon  = PLAN_ICONS[key]
        price = PLAN_PRICE[key]
        desc  = PLAN_DESC[key]
        lines.append(f'{icon} <b>{key}</b> — {desc}\n    └ {price} ₽ / месяц')
    lines += [
        f'\n{DIVIDER}',
        '💬 Напишите менеджеру — оформим доступ и пришлём ключ прямо в бот.',
    ]
    await call.message.edit_text(
        '\n'.join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='💬 Написать менеджеру', url=SUPPORT_URL)],
            [InlineKeyboardButton(text='🏠 Главное меню',        callback_data='menu')],
        ])
    )


# ==================== РЕФЕРАЛЫ ====================

@dp.callback_query(F.data == 'referral')
async def cb_referral(call: CallbackQuery):
    tg_id    = str(call.from_user.id)
    count    = await db_referral_count(tg_id)
    bot_info = await bot.get_me()
    ref_link = f'https://t.me/{bot_info.username}?start=ref_{tg_id}'
    promo    = (
        f'Попробуй TG Lead Wareon — лучший инструмент для '
        f'парсинга и рассылок в Telegram. 👉 {ref_link}'
    )
    await call.message.edit_text(
        f'👥 <b>Реферальная программа</b>\n{DIVIDER}\n\n'
        f'🎯 За каждого приглашённого — <b>+1 день</b> к лицензии\n\n'
        f'📊 Приглашено: <b>{count} чел.</b>\n'
        f'🎁 Заработано: <b>+{count} дней</b>\n\n'
        f'{DIVIDER}\n\n'
        f'🔗 <b>Ваша ссылка:</b>\n<code>{ref_link}</code>\n\n'
        f'📢 <b>Готовый текст:</b>\n<i>{promo}</i>',
        reply_markup=kb_back()
    )


# ==================== ОТЗЫВ ====================

@dp.callback_query(F.data == 'review')
async def cb_review(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        f'⭐️ <b>Оставить отзыв</b>\n{DIVIDER}\n\n'
        f'🎁 За отзыв вы получите <b>+2 дня</b> к лицензии!\n\n'
        f'Напишите несколько предложений о сервисе:\n'
        f'<i>что понравилось, что помогло, результаты...</i>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='✖️ Отмена', callback_data='menu')]
        ])
    )
    await state.set_state(States.waiting_review)


@dp.message(States.waiting_review)
async def process_review_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if len(text) < 20:
        await message.answer('❌ <b>Отзыв слишком короткий</b>\n\nНапишите хотя бы пару предложений:')
        return
    await state.update_data(review_text=text)
    await message.answer('👍 <b>Отлично!</b>\n\nТеперь поставьте оценку сервису:', reply_markup=kb_stars())
    await state.set_state(States.review_rating)


@dp.callback_query(States.review_rating, F.data.startswith('star_'))
async def process_review_rating(call: CallbackQuery, state: FSMContext):
    rating   = int(call.data.split('_')[1])
    fsm_data = await state.get_data()
    text     = fsm_data.get('review_text', '')
    tg_id    = str(call.from_user.id)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO reviews (tg_id, username, rating, body) VALUES (?,?,?,?)',
            (tg_id, call.from_user.username or '', rating, text)
        )
        await db.commit()

    # Добавить +2 дня к первой активной лицензии (или создать Start)
    lics    = await db_get_licenses(tg_id)
    active  = [l for l in lics if not l['is_expired']]
    product = active[0]['product'] if active else 'Start'
    await db_add_days(tg_id, product, 2)

    stars = '★' * rating + '☆' * (5 - rating)
    try:
        await bot.send_message(
            ADMIN_ID,
            f'⭐️ <b>Новый отзыв</b>\n'
            f'от @{call.from_user.username or tg_id} (ID: {tg_id})\n'
            f'Оценка: {stars}\n\n'
            f'<i>{text}</i>'
        )
    except Exception:
        pass

    await state.clear()
    await call.message.edit_text(
        f'✅ <b>Отзыв отправлен! Спасибо!</b>\n{DIVIDER}\n\n'
        f'<b>{stars}</b>\n\n'
        f'<i>«{text}»</i>\n\n'
        f'{DIVIDER}\n\n'
        f'🎁 <b>+2 дня</b> добавлено к вашей лицензии!',
        reply_markup=kb_back()
    )


# ==================== ТЕКСТОВЫЕ КОМАНДЫ ====================

@dp.message(Command('help'))
async def cmd_help(message: Message):
    await message.answer(
        f'{LOGO}\n\n'
        f'📖 <b>Команды бота</b>\n\n'
        f'▸ /start — главное меню\n'
        f'▸ /key — ваши лицензионные ключи\n'
        f'▸ /ref — реферальная ссылка\n'
        f'▸ /help — эта справка\n\n'
        f'{DIVIDER}\n\n'
        f'💬 <a href="{SUPPORT_URL}">Поддержка</a>'
    )


@dp.message(Command('key'))
async def cmd_key(message: Message):
    tg_id    = str(message.from_user.id)
    licenses = await db_get_licenses(tg_id)
    active   = [l for l in licenses if not l['is_expired']]
    if not active:
        await message.answer('🔴 <b>Нет активных лицензий</b>\n\nНапишите в поддержку для покупки доступа.')
        return
    lines = [f'🔑 <b>Ваши ключи</b>\n{DIVIDER}']
    for lic in active:
        icon = PLAN_ICONS.get(lic['product'], '📦')
        lines.append(f'\n{icon} <b>{lic["product"]}</b>\n<code>{lic["license_key"]}</code>')
    await message.answer('\n'.join(lines))


@dp.message(Command('ref'))
async def cmd_ref(message: Message):
    tg_id    = str(message.from_user.id)
    count    = await db_referral_count(tg_id)
    bot_info = await bot.get_me()
    ref_link = f'https://t.me/{bot_info.username}?start=ref_{tg_id}'
    await message.answer(
        f'👥 <b>Рефералы</b>\n{DIVIDER}\n\n'
        f'📊 Приглашено: <b>{count} чел.</b>  ·  Бонус: <b>+{count} дн.</b>\n\n'
        f'🔗 Ваша ссылка:\n<code>{ref_link}</code>'
    )


# ==================== АДМИН-КОМАНДЫ ====================
# Доступны только ADMIN_ID

@dp.message(Command('addlicense'))
async def cmd_addlicense(message: Message):
    """Выдать лицензию: /addlicense <tg_id> <product> <days>"""
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 4:
        await message.answer(
            'Использование: <code>/addlicense &lt;tg_id&gt; &lt;product&gt; &lt;days&gt;</code>\n'
            f'Продукты: {", ".join(PLAN_ICONS)}'
        )
        return
    _, tg_id, product, days_str = parts
    if product not in PLAN_ICONS:
        await message.answer(f'Неверный продукт. Доступные: {", ".join(PLAN_ICONS)}')
        return
    try:
        days = int(days_str)
        assert days > 0
    except (ValueError, AssertionError):
        await message.answer('Дней должно быть положительным числом')
        return

    await db_ensure_user(tg_id, '', '')
    key     = await db_add_license(tg_id, product, days)
    expires = (datetime.utcnow() + timedelta(days=days)).date().isoformat()

    await message.answer(
        f'✅ <b>Лицензия выдана</b>\n\n'
        f'ID: <code>{tg_id}</code>\n'
        f'Продукт: {PLAN_ICONS.get(product)} {product}\n'
        f'Дней: {days}  ·  До {format_date(expires)}\n'
        f'Ключ: <code>{key}</code>'
    )
    try:
        await bot.send_message(
            int(tg_id),
            f'{LOGO}\n\n'
            f'🎉 <b>Лицензия активирована!</b>\n\n'
            f'{PLAN_ICONS.get(product)} <b>{product}</b>\n'
            f'📅 Действует до {format_date(expires)}\n\n'
            f'🔑 Ключ: <code>{key}</code>\n\n'
            f'Нажмите /start чтобы начать работу!'
        )
    except Exception as e:
        await message.answer(f'⚠️ Не удалось уведомить пользователя: {e}')


@dp.message(Command('adddays'))
async def cmd_adddays(message: Message):
    """Продлить лицензию: /adddays <tg_id> <product> <days>"""
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 4:
        await message.answer('Использование: <code>/adddays &lt;tg_id&gt; &lt;product&gt; &lt;days&gt;</code>')
        return
    _, tg_id, product, days_str = parts
    try:
        days = int(days_str)
    except ValueError:
        await message.answer('Дней должно быть числом')
        return

    await db_add_days(tg_id, product, days)
    await message.answer(f'✅ Добавлено {days} дней к {product} для <code>{tg_id}</code>')
    try:
        await bot.send_message(int(tg_id), f'🎁 <b>+{days} дней</b> к лицензии <b>{product}</b>!')
    except Exception:
        pass


@dp.message(Command('rmlicense'))
async def cmd_rmlicense(message: Message):
    """Удалить лицензии: /rmlicense <tg_id> [product]"""
    if message.from_user.id != ADMIN_ID:
        return
    parts   = message.text.split()
    tg_id   = parts[1] if len(parts) > 1 else None
    product = parts[2] if len(parts) > 2 else None
    if not tg_id:
        await message.answer('Использование: <code>/rmlicense &lt;tg_id&gt; [product]</code>')
        return
    async with aiosqlite.connect(DB_PATH) as db:
        if product:
            await db.execute('DELETE FROM licenses WHERE tg_id=? AND product=?', (tg_id, product))
        else:
            await db.execute('DELETE FROM licenses WHERE tg_id=?', (tg_id,))
        await db.commit()
    suffix = f' ({product})' if product else ' (все продукты)'
    await message.answer(f'✅ Лицензии удалены для <code>{tg_id}</code>{suffix}')


@dp.message(Command('userinfo'))
async def cmd_userinfo(message: Message):
    """Инфо о пользователе: /userinfo <tg_id>"""
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer('Использование: <code>/userinfo &lt;tg_id&gt;</code>')
        return
    tg_id = parts[1]
    user  = await db_get_user(tg_id)
    lics  = await db_get_licenses(tg_id)

    if not user:
        await message.answer(f'Пользователь <code>{tg_id}</code> не найден в базе')
        return

    lines = [
        f'👤 <b>Пользователь {tg_id}</b>\n{DIVIDER}',
        f'Имя: {user["first_name"] or "—"}',
        f'Username: @{user["username"]}' if user['username'] else 'Username: —',
        f'Реферал от: {user["referred_by"] or "—"}',
        f'В боте с: {user["joined_at"][:10]}',
        '',
        '<b>Лицензии:</b>',
    ]
    if lics:
        for l in lics:
            badge = status_badge(l['days_left'])
            lines.append(
                f'  {PLAN_ICONS.get(l["product"], "📦")} {l["product"]} '
                f'— {badge} — до {l["expires_at"][:10]}\n'
                f'  Ключ: <code>{l["license_key"]}</code>'
            )
    else:
        lines.append('  Нет лицензий')

    await message.answer('\n'.join(lines))


@dp.message(Command('stats'))
async def cmd_stats(message: Message):
    """Общая статистика: /stats"""
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM users') as cur:
            total_users = (await cur.fetchone())[0]
        async with db.execute(
            'SELECT COUNT(DISTINCT tg_id) FROM licenses WHERE date(expires_at) >= date("now")'
        ) as cur:
            active_users = (await cur.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM licenses') as cur:
            total_lics = (await cur.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM reviews') as cur:
            total_reviews = (await cur.fetchone())[0]
    await message.answer(
        f'📊 <b>Статистика бота</b>\n{DIVIDER}\n\n'
        f'👤 Пользователей: <b>{total_users}</b>\n'
        f'🟢 С активной лицензией: <b>{active_users}</b>\n'
        f'🛡 Лицензий всего: <b>{total_lics}</b>\n'
        f'⭐️ Отзывов: <b>{total_reviews}</b>'
    )


# ==================== ПЛАНИРОВЩИК ====================

async def _notify_expiring():
    await asyncio.sleep(60)
    while True:
        for days_ahead in [3, 1]:
            try:
                users = await db_expiring_today(days_ahead)
                for u in users:
                    tg_id = u.get('tg_id')
                    if not tg_id:
                        continue
                    if not await db_get_notify(tg_id):
                        continue
                    icon = PLAN_ICONS.get(u.get('product', ''), '📦')
                    date = format_date(u.get('expires_at', ''))
                    if days_ahead == 3:
                        text = (
                            f'⚠️ <b>Лицензия истекает через 3 дня</b>\n{DIVIDER}\n\n'
                            f'{icon} <b>{u["product"]}</b>\n'
                            f'📅 Дата окончания: {date}\n\n'
                            f'Продлите сейчас, чтобы не прерывать работу 👇'
                        )
                    else:
                        text = (
                            f'🔴 <b>Лицензия истекает СЕГОДНЯ!</b>\n{DIVIDER}\n\n'
                            f'{icon} <b>{u["product"]}</b>\n\n'
                            f'Продлите прямо сейчас 👇'
                        )
                    try:
                        await bot.send_message(
                            int(tg_id), text,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text='💳 Продлить сейчас', callback_data='buy')]
                            ])
                        )
                        logging.info(f'Уведомление: tg={tg_id} product={u["product"]} days={days_ahead}')
                    except Exception as e:
                        logging.warning(f'Не удалось уведомить {tg_id}: {e}')
            except Exception as e:
                logging.error(f'Scheduler error (days={days_ahead}): {e}')
        await asyncio.sleep(12 * 3600)


# ==================== ЗАПУСК ====================

async def main():
    if not BOT_TOKEN:
        raise ValueError('BOT_TOKEN не задан')
    await db_init()
    logging.info('🤖 TG Lead Wareon Bot запускается (standalone, без сайта)...')
    asyncio.create_task(_notify_expiring())
    await dp.start_polling(bot, allowed_updates=['message', 'callback_query'])


if __name__ == '__main__':
    asyncio.run(main())
