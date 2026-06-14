# Главный бот TG Lead Wareon (@TGLeadWareonBot)
# aiogram 3.7+  |  aiohttp  |  python-dotenv

import asyncio
import logging
import os

import aiohttp
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
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# ===== НАСТРОЙКИ =====
BOT_TOKEN        = "8340651502:AAFur2gI4vgHzmAUPb348F5EF9iD1EgMSEg"
ADMIN_ID         = 5062414502
BOT_SECRET       = "tglw_bot_secret_2026"
REVIEW_BOT_TOKEN = os.environ.get('REVIEW_BOT_TOKEN', '')
API_BASE         = 'https://tgleadwareon.ru'
SITE_URL         = 'https://tgleadwareon.ru'
SUPPORT_URL      = 'https://t.me/TGLeadSupportBot'
CHANNEL_URL      = os.environ.get('CHANNEL_URL', 'https://t.me/TGLeadWareon')
# Канал обязательной подписки. Бот ДОЛЖЕН быть администратором этого канала,
# иначе проверка подписки работать не будет (тогда гейт пропускает всех).
CHANNEL_ID       = os.environ.get('CHANNEL_ID', '@TGLeadWareon')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp  = Dispatcher(storage=MemoryStorage())

# ===== ДИЗАЙН =====
DIVIDER  = '━━━━━━━━━━━━━━━━━━━━━━'
LOGO     = f'{DIVIDER}\n    🔥 <b>TG LEAD WAREON</b>\n{DIVIDER}'

PLAN_ICONS = {
    'Miner':  '⛏️',
    'Sender': '📨',
    'Start':  '🚀',
    'Pro':    '⚡',
    'Scale':  '👑',
}

PLAN_DESC = {
    'Miner':  'парсинг аудитории',
    'Sender': 'массовые рассылки',
    'Start':  'Miner + Sender',
    'Pro':    'Start + лимиты ×3',
    'Scale':  'всё включено',
}

PLAN_PRICE = {
    'Miner':  '490',
    'Sender': '990',
    'Start':  '990',
    'Pro':    '2 490',
    'Scale':  '6 990',
}


def status_badge(days: int) -> str:
    """Возвращает цветной индикатор статуса лицензии."""
    if days <= 0:
        return '🔴 Истекла'
    elif days <= 3:
        return f'🟡 Истекает через {days} дн.'
    else:
        return f'🟢 Активна · {days} дн.'


def format_date(iso: str) -> str:
    """2026-06-15 → 15 июня 2026"""
    months = ['', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    try:
        parts = iso[:10].split('-')
        return f'{int(parts[2])} {months[int(parts[1])]} {parts[0]}'
    except Exception:
        return iso[:10]


# ─────────────────────────── FSM состояния ───────────────────────
class States(StatesGroup):
    waiting_email  = State()
    waiting_review = State()
    review_rating  = State()


# ─────────────────────────── API helper ──────────────────────────
async def api(method: str, path: str, **kwargs) -> dict:
    kwargs.setdefault('headers', {})['X-Bot-Secret'] = BOT_SECRET
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with getattr(s, method)(f'{API_BASE}{path}', **kwargs) as r:
                return await r.json()
    except Exception as e:
        logging.error(f'API {method.upper()} {path} — {e}')
        return {}


# ─────────────────────────── Клавиатуры ──────────────────────────
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='🌐 Прокси',   callback_data='proxies'),
            InlineKeyboardButton(text='👤 Аккаунты', callback_data='accounts'),
        ],
        [
            InlineKeyboardButton(text='🎯 Аудитория', callback_data='audience'),
            InlineKeyboardButton(text='📨 Рассылка',  callback_data='sending'),
        ],
        [InlineKeyboardButton(text='🚀 Как начать (4 шага)',     callback_data='howto')],
        [InlineKeyboardButton(text='🛡 Мои лицензии',            callback_data='licenses')],
        [
            InlineKeyboardButton(text='💳 Купить / Продлить', callback_data='buy'),
            InlineKeyboardButton(text='🔑 Ключ',              callback_data='get_key'),
        ],
        [InlineKeyboardButton(text='👥 Реферальная программа',    callback_data='referral')],
        [
            InlineKeyboardButton(text='⭐️ Отзыв  +2 дня', callback_data='review'),
            InlineKeyboardButton(text='💬 Поддержка',      url=SUPPORT_URL),
        ],
    ])


def kb_section(*rows) -> InlineKeyboardMarkup:
    kb = [list(r) for r in rows]
    kb.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def _require_account(call: CallbackQuery):
    """Гейтинг продуктовых разделов: нужна привязка аккаунта + активная лицензия."""
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})
    if not data.get('found'):
        await call.message.edit_text(
            f'🔒 <b>Сначала привяжите аккаунт</b>\n{DIVIDER}\n\n'
            f'Нажмите /start и введите email сайта.\n\n'
            f'<i>Нет аккаунта? → <a href="{SITE_URL}/register">Регистрация — 3 дня бесплатно</a></i>',
            reply_markup=kb_back()
        )
        return None
    active = [l for l in data.get('licenses', []) if not l['is_expired']]
    if not active:
        await call.message.edit_text(
            f'⏳ <b>Нет активной лицензии</b>\n{DIVIDER}\n\n'
            f'Инструменты доступны на платном тарифе или в пробном периоде.\n'
            f'Активируйте доступ — и возвращайтесь 👇',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='💳 Выбрать тариф',            callback_data='buy')],
                [InlineKeyboardButton(text='👥 Получить дни (рефералы)',  callback_data='referral')],
                [InlineKeyboardButton(text='🏠 Главное меню',             callback_data='menu')],
            ])
        )
        return None
    return data


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='menu')]
    ])


def kb_buy() -> InlineKeyboardMarkup:
    rows = []
    for key in ['Miner', 'Sender', 'Start', 'Pro', 'Scale']:
        icon  = PLAN_ICONS[key]
        price = PLAN_PRICE[key]
        desc  = PLAN_DESC[key]
        label = f'{icon} {key}  ·  {price} ₽/мес'
        rows.append([InlineKeyboardButton(text=label, url=f'{SITE_URL}/buy/{key.lower()}')])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


# ─────────────────────── Обязательная подписка ───────────────────
def kb_subscribe() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📢 Подписаться на канал', url=CHANNEL_URL)],
        [InlineKeyboardButton(text='✅ Проверить подписку',   callback_data='check_sub')],
    ])


def kb_home() -> InlineKeyboardMarkup:
    """Лаконичный стартовый экран — одна крупная CTA, функции спрятаны за ней."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🚀 Начать работу сейчас', callback_data='menu')],
        [
            InlineKeyboardButton(text='👤 Профиль',  callback_data='profile'),
            InlineKeyboardButton(text='💬 Поддержка', url=SUPPORT_URL),
        ],
    ])


async def is_subscribed(user_id: int) -> bool:
    """Проверяет подписку на канал. Если бот не админ / ошибка — пропускает (fail-open)."""
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ('member', 'administrator', 'creator')
    except Exception as e:
        logging.warning(f'Проверка подписки {user_id}: {e} (бот должен быть админом канала)')
        return True


def _sub_gate_text() -> str:
    return (
        f'{LOGO}\n\n'
        f'🚀 <b>Твой центр роста в Telegram</b>\n\n'
        f'🌐 Прокси · 👤 Аккаунты · 🎯 Сбор аудитории · 📨 Умные рассылки — '
        f'всё в одном боте, без рутины.\n'
        f'⚡ Запусти поток целевого трафика быстро и просто.\n\n'
        f'{DIVIDER}\n\n'
        f'🔒 <b>Чтобы начать пользоваться ботом — подпишитесь на канал</b>\n\n'
        f'<i>Там обновления, кейсы и фишки сервиса.</i>'
    )


# ─────────────────────────── /start ──────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    # Гейт обязательной подписки
    if not await is_subscribed(message.from_user.id):
        await message.answer(_sub_gate_text(), reply_markup=kb_subscribe(),
                             disable_web_page_preview=True)
        return

    # Deep link для QR-привязки: /start link_XXXXXX
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith('link_'):
        await _handle_link_code(message, state, args[1][5:])
        return

    await _enter_app(message.from_user, state, answer_msg=message)


async def _enter_app(from_user, state: FSMContext, answer_msg=None, edit_call=None):
    """Показывает домашний экран после подтверждения подписки."""
    tg_id = str(from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if data.get('found'):
        name     = data['user']['name']
        active   = [l for l in data.get('licenses', []) if not l['is_expired']]
        if active:
            soonest   = min(active, key=lambda l: l['days_left'])
            lic_count = f'🟢 Активна  ·  {len(active)} {"лицензия" if len(active) == 1 else "лицензии" if len(active) < 5 else "лицензий"}'
            exp_line  = f'📅 До {format_date(soonest["expires_at"])}'
        else:
            lic_count = '🔴 Нет активных лицензий'
            exp_line  = '💡 Перейдите в «Купить / Продлить»'
        text = (
            f'{LOGO}\n\n'
            f'👋 Привет, <b>{name}</b>!\n\n'
            f'{lic_count}\n'
            f'{exp_line}\n\n'
            f'🌐 Прокси · 👤 Аккаунты · 🎯 Аудитория · 📨 Рассылка —\n'
            f'всё в одном боте, без рутины.\n\n'
            f'Нажми кнопку ниже, чтобы начать 👇'
        )
        if edit_call:
            await edit_call.message.edit_text(text, reply_markup=kb_home())
        else:
            await answer_msg.answer(text, reply_markup=kb_home())
    else:
        text = (
            f'{LOGO}\n\n'
            f'👋 Добро пожаловать!\n\n'
            f'Прокси · Аккаунты · Сбор аудитории · Умные рассылки —\n'
            f'всё в одном боте.\n\n'
            f'{DIVIDER}\n\n'
            f'📧 Введите <b>email</b>, с которым вы\n'
            f'зарегистрированы на сайте:\n\n'
            f'<i>Нет аккаунта? → <a href="{SITE_URL}/register">Регистрация</a></i>'
        )
        if edit_call:
            await edit_call.message.edit_text(text, disable_web_page_preview=True)
        else:
            await answer_msg.answer(text, disable_web_page_preview=True)
        await state.set_state(States.waiting_email)


# ─────────────────────────── Проверка подписки ───────────────────
@dp.callback_query(F.data == 'check_sub')
async def cb_check_sub(call: CallbackQuery, state: FSMContext):
    if await is_subscribed(call.from_user.id):
        await call.answer('Спасибо за подписку! 🎉')
        await _enter_app(call.from_user, state, edit_call=call)
    else:
        await call.answer('Вы ещё не подписались на канал 😔', show_alert=True)


async def _handle_link_code(message: Message, state: FSMContext, code: str):
    """Привязка через QR-код (deep link)."""
    tg_id  = str(message.from_user.id)
    result = await api('post', '/api/bot/link_code', json={'code': code, 'tg_id': tg_id})

    if result.get('ok'):
        data = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})
        name = data.get('user', {}).get('name', 'пользователь')
        await message.answer(
            f'{LOGO}\n\n'
            f'✅ <b>Аккаунт привязан!</b>\n\n'
            f'Привет, <b>{name}</b>! Добро пожаловать.\n\n'
            f'Выберите раздел 👇',
            reply_markup=kb_main()
        )
    else:
        err = result.get('error', 'Неизвестная ошибка')
        await message.answer(
            f'❌ <b>Не удалось привязать по QR-коду</b>\n\n'
            f'<i>{err}</i>\n\n'
            f'Введите email вручную:'
        )
        await state.set_state(States.waiting_email)


# ─────────────────────────── Привязка по email ───────────────────
@dp.message(States.waiting_email)
async def process_email(message: Message, state: FSMContext):
    email = message.text.strip().lower()

    if '@' not in email or '.' not in email:
        await message.answer(
            '❌ <b>Некорректный email</b>\n\n'
            'Введите адрес в формате <code>name@domain.ru</code>:'
        )
        return

    tg_id       = str(message.from_user.id)
    tg_username = message.from_user.username or ''

    result = await api('post', '/api/bot/link', json={
        'email':       email,
        'tg_id':       tg_id,
        'tg_username': tg_username,
    })

    if result.get('ok'):
        data = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})
        name = data.get('user', {}).get('name', email.split('@')[0])
        await state.clear()
        await message.answer(
            f'{LOGO}\n\n'
            f'✅ <b>Аккаунт успешно привязан!</b>\n\n'
            f'Привет, <b>{name}</b>!\n'
            f'Теперь управляйте подпиской прямо здесь.\n\n'
            f'Выберите раздел 👇',
            reply_markup=kb_main()
        )
    else:
        err = result.get('error', '')
        if 'не найден' in err:
            await message.answer(
                f'❌ <b>Аккаунт не найден</b>\n\n'
                f'Email <code>{email}</code> не зарегистрирован.\n\n'
                f'👉 <a href="{SITE_URL}/register">Создать аккаунт</a>\n\n'
                f'После регистрации введите email снова:'
            )
        else:
            await message.answer(f'⚠️ <b>Ошибка:</b> {err}\n\nПопробуйте ещё раз:')


# ─────────────────────────── Главное меню ────────────────────────
@dp.callback_query(F.data == 'menu')
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if data.get('found'):
        name     = data['user']['name']
        licenses = data.get('licenses', [])
        active   = [l for l in licenses if not l['is_expired']]

        if active:
            soonest  = min(active, key=lambda l: l['days_left'])
            lic_line = f'🟢 Активна  ·  {len(active)} лиц.'
            exp_line = f'📅 До {format_date(soonest["expires_at"])}'
        else:
            lic_line = '🔴 Нет активных лицензий'
            exp_line = f'💡 Перейдите в «Купить / Продлить»'

        text = (
            f'{LOGO}\n\n'
            f'👤 <b>{name}</b>\n\n'
            f'{lic_line}\n'
            f'{exp_line}\n\n'
            f'Выберите раздел 👇'
        )
    else:
        text = f'{LOGO}\n\nВыберите раздел 👇'

    await call.message.edit_text(text, reply_markup=kb_main())


# ─────────────────────────── Как начать (онбординг) ──────────────
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


# ─────────────────────────── Прокси ──────────────────────────────
@dp.callback_query(F.data == 'proxies')
async def cb_proxies(call: CallbackQuery):
    if not await _require_account(call):
        return
    await call.message.edit_text(
        f'🌐 <b>Прокси</b>\n{DIVIDER}\n\n'
        f'Прокси нужны, чтобы аккаунты работали стабильно и не попадали под ограничения.\n\n'
        f'<b>Форматы:</b>\n'
        f'<code>socks5://user:pass@host:port</code>\n'
        f'<code>host:port:login:password</code>\n\n'
        f'📖 <b>Инструкция:</b> добавьте прокси списком, проверьте на валидность и '
        f'распределите по аккаунтам — последовательно или случайно.\n\n'
        f'<i>Интерактивное управление прямо в боте подключается.</i>',
        reply_markup=kb_section(
            [InlineKeyboardButton(text='🛒 Купить прокси',        url=f'{SITE_URL}/buy_proxy')],
            [InlineKeyboardButton(text='🌐 Открыть в кабинете',   url=f'{SITE_URL}/dashboard')],
        )
    )


# ─────────────────────────── Аккаунты ────────────────────────────
@dp.callback_query(F.data == 'accounts')
async def cb_accounts(call: CallbackQuery):
    if not await _require_account(call):
        return
    await call.message.edit_text(
        f'👤 <b>Аккаунты</b>\n{DIVIDER}\n\n'
        f'Подключите Telegram-аккаунты для сбора аудитории и рассылок.\n\n'
        f'<b>Способы подключения:</b>\n'
        f'• по номеру телефона (код + 2FA)\n'
        f'• импорт .session / TData\n\n'
        f'📖 <b>Инструкция:</b> привяжите прокси к аккаунту, проверьте статус '
        f'(жив / ограничен / требует входа), настройте профиль и прогрев.\n\n'
        f'<i>Подключение аккаунтов прямо в боте подключается.</i>',
        reply_markup=kb_section(
            [InlineKeyboardButton(text='🌐 Открыть в кабинете', url=f'{SITE_URL}/dashboard')],
        )
    )


# ─────────────────────────── Аудитория ───────────────────────────
@dp.callback_query(F.data == 'audience')
async def cb_audience(call: CallbackQuery):
    if not await _require_account(call):
        return
    await call.message.edit_text(
        f'🎯 <b>Аудитория</b>\n{DIVIDER}\n\n'
        f'Соберите целевую базу из открытых чатов и каналов автоматически.\n\n'
        f'<b>Что собирается:</b> username, ID, имя, активность.\n'
        f'<b>Фильтры:</b> онлайн-статус, язык, активность.\n\n'
        f'📖 <b>Инструкция:</b> укажите чаты/каналы → запустите сбор → получите готовую '
        f'базу для рассылки.\n\n'
        f'⚖️ Сбор — только из <b>открытых</b> источников. Ответственность за дальнейшую '
        f'обработку данных — на вас.\n\n'
        f'<i>Запуск сбора прямо в боте подключается.</i>',
        reply_markup=kb_section(
            [InlineKeyboardButton(text='🌐 Открыть в кабинете', url=f'{SITE_URL}/dashboard')],
        )
    )


# ─────────────────────────── Рассылка ────────────────────────────
@dp.callback_query(F.data == 'sending')
async def cb_sending(call: CallbackQuery):
    if not await _require_account(call):
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
        f'<i>Запуск рассылок прямо в боте подключается.</i>',
        reply_markup=kb_section(
            [InlineKeyboardButton(text='🌐 Открыть в кабинете', url=f'{SITE_URL}/dashboard')],
        )
    )


# ─────────────────────────── Мои лицензии ────────────────────────
@dp.callback_query(F.data == 'licenses')
async def cb_licenses(call: CallbackQuery):
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await call.answer('Сначала привяжите аккаунт через /start', show_alert=True)
        return

    licenses = data.get('licenses', [])

    if not licenses:
        text = (
            f'🛡 <b>Мои лицензии</b>\n'
            f'{DIVIDER}\n\n'
            f'🔴 Активных лицензий нет\n\n'
            f'Приобретите подписку, чтобы начать работу.'
        )
        buttons = [
            [InlineKeyboardButton(text='💳 Выбрать тариф', callback_data='buy')],
            [InlineKeyboardButton(text='🏠 Главное меню',  callback_data='menu')],
        ]
    else:
        lines = [f'🛡 <b>Мои лицензии</b>\n{DIVIDER}']
        for lic in licenses:
            icon   = PLAN_ICONS.get(lic['product'], '📦')
            days   = lic['days_left']
            badge  = status_badge(days)
            date   = format_date(lic['expires_at'])
            lines.append(
                f'\n{icon} <b>{lic["product"].upper()}</b>\n'
                f'   {badge}\n'
                f'   📅 До {date}'
            )
        text = '\n'.join(lines)
        buttons = [
            [InlineKeyboardButton(text='💳 Продлить',     callback_data='buy')],
            [InlineKeyboardButton(text='🏠 Главное меню', callback_data='menu')],
        ]

    await call.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


# ─────────────────────────── Получить ключ ───────────────────────
@dp.callback_query(F.data == 'get_key')
async def cb_get_key(call: CallbackQuery):
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await call.answer('Сначала привяжите аккаунт через /start', show_alert=True)
        return

    active = [l for l in data.get('licenses', []) if not l['is_expired']]

    if not active:
        await call.message.edit_text(
            f'🔑 <b>Ключ активации</b>\n'
            f'{DIVIDER}\n\n'
            f'🔴 Нет активных лицензий\n\n'
            f'Приобретите подписку, чтобы получить ключ.',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='💳 Выбрать тариф', callback_data='buy')],
                [InlineKeyboardButton(text='🏠 Главное меню',  callback_data='menu')],
            ])
        )
        return

    lines = [
        f'🔑 <b>Ключи активации</b>\n'
        f'{DIVIDER}\n'
        f'<i>Нажмите на ключ, чтобы скопировать</i>'
    ]
    for lic in active:
        icon  = PLAN_ICONS.get(lic['product'], '📦')
        days  = lic['days_left']
        badge = status_badge(days)
        lines.append(
            f'\n{icon} <b>{lic["product"]}</b>  ·  {badge}\n'
            f'<code>{lic["license_key"]}</code>'
        )

    await call.message.edit_text('\n'.join(lines), reply_markup=kb_back())


# ─────────────────────────── Купить / продлить ───────────────────
@dp.callback_query(F.data == 'buy')
async def cb_buy(call: CallbackQuery):
    lines = [
        f'💳 <b>Тарифы</b>\n{DIVIDER}\n'
    ]
    for key in ['Miner', 'Sender', 'Start', 'Pro', 'Scale']:
        icon  = PLAN_ICONS[key]
        price = PLAN_PRICE[key]
        desc  = PLAN_DESC[key]
        lines.append(f'{icon} <b>{key}</b> — {desc}\n    └ {price} ₽ / месяц')

    lines.append(f'\n{DIVIDER}')
    lines.append('⬇️ Нажмите на тариф для оплаты\n💡 Лицензия активируется автоматически')

    await call.message.edit_text('\n'.join(lines), reply_markup=kb_buy())


# ─────────────────────────── Рефералы ────────────────────────────
@dp.callback_query(F.data == 'referral')
async def cb_referral(call: CallbackQuery):
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await call.answer('Сначала привяжите аккаунт через /start', show_alert=True)
        return

    ref_link = data.get('ref_link', '')
    count    = data.get('referral_count', 0)

    promo = (
        f'Попробуй TG Lead Wareon — лучший инструмент для '
        f'парсинга и рассылок в Telegram. Первые 3 дня бесплатно 👉 {ref_link}'
    )

    await call.message.edit_text(
        f'👥 <b>Реферальная программа</b>\n'
        f'{DIVIDER}\n\n'
        f'🎯 За каждого приглашённого — <b>+1 день</b> к лицензии\n\n'
        f'📊 Приглашено: <b>{count} чел.</b>\n'
        f'🎁 Заработано: <b>+{count} дней</b>\n\n'
        f'{DIVIDER}\n\n'
        f'🔗 <b>Ваша ссылка:</b>\n'
        f'<code>{ref_link}</code>\n\n'
        f'📢 <b>Готовый текст:</b>\n'
        f'<i>{promo}</i>',
        reply_markup=kb_back()
    )


# ─────────────────────────── Отзыв ───────────────────────────────
@dp.callback_query(F.data == 'review')
async def cb_review(call: CallbackQuery, state: FSMContext):
    tg_id = str(call.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})
    if not data.get('found'):
        await call.answer('Сначала привяжите аккаунт через /start', show_alert=True)
        return

    await call.message.edit_text(
        f'⭐️ <b>Оставить отзыв</b>\n'
        f'{DIVIDER}\n\n'
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
        await message.answer(
            '❌ <b>Отзыв слишком короткий</b>\n\n'
            'Напишите хотя бы пару предложений:'
        )
        return

    await state.update_data(review_text=text)
    await message.answer(
        '👍 <b>Отлично!</b>\n\n'
        'Теперь поставьте оценку сервису:',
        reply_markup=kb_stars()
    )
    await state.set_state(States.review_rating)


@dp.callback_query(States.review_rating, F.data.startswith('star_'))
async def process_review_rating(call: CallbackQuery, state: FSMContext):
    rating    = int(call.data.split('_')[1])
    fsm_data  = await state.get_data()
    text      = fsm_data.get('review_text', '')
    tg_id     = str(call.from_user.id)
    user_data = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})
    email     = user_data.get('user', {}).get('email', '')

    result = await api('post', '/api/review_bonus', json={
        'token':       REVIEW_BOT_TOKEN,
        'telegram_id': tg_id,
        'username':    call.from_user.username or '',
        'rating':      rating,
        'text':        text,
        'user_email':  email,
    })

    await state.clear()
    stars = '★' * rating + '☆' * (5 - rating)
    bonus = result.get('bonus_days', 2)

    await call.message.edit_text(
        f'✅ <b>Отзыв отправлен! Спасибо!</b>\n'
        f'{DIVIDER}\n\n'
        f'<b>{stars}</b>\n\n'
        f'<i>«{text}»</i>\n\n'
        f'{DIVIDER}\n\n'
        f'🎁 <b>+{bonus} дня</b> добавлено к вашей лицензии!',
        reply_markup=kb_back()
    )


# ─────────────────────────── /help, /key, /ref ───────────────────
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
        f'🌐 <a href="{SITE_URL}">tgleadwareon.ru</a>\n'
        f'💬 <a href="{SUPPORT_URL}">Поддержка</a>'
    )


@dp.message(Command('key'))
async def cmd_key(message: Message):
    tg_id = str(message.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await message.answer(
            '❌ <b>Аккаунт не привязан</b>\n\n'
            'Введите /start и следуйте инструкции.'
        )
        return

    active = [l for l in data.get('licenses', []) if not l['is_expired']]
    if not active:
        await message.answer(
            '🔴 <b>Нет активных лицензий</b>\n\n'
            'Перейдите в /start и купите подписку.'
        )
        return

    lines = [f'🔑 <b>Ваши ключи</b>\n{DIVIDER}']
    for lic in active:
        icon = PLAN_ICONS.get(lic['product'], '📦')
        lines.append(f'\n{icon} <b>{lic["product"]}</b>\n<code>{lic["license_key"]}</code>')
    await message.answer('\n'.join(lines))


@dp.message(Command('ref'))
async def cmd_ref(message: Message):
    tg_id = str(message.from_user.id)
    data  = await api('get', '/api/bot/user_info', params={'tg_id': tg_id})

    if not data.get('found'):
        await message.answer(
            '❌ <b>Аккаунт не привязан</b>\n\n'
            'Введите /start и следуйте инструкции.'
        )
        return

    ref_link = data.get('ref_link', '')
    count    = data.get('referral_count', 0)
    await message.answer(
        f'👥 <b>Рефералы</b>\n{DIVIDER}\n\n'
        f'📊 Приглашено: <b>{count} чел.</b>  ·  Бонус: <b>+{count} дн.</b>\n\n'
        f'🔗 Ваша ссылка:\n<code>{ref_link}</code>'
    )


# ─────────────────────── Scheduler: уведомления ──────────────────
async def _notify_expiring():
    """Каждые 12 часов проверяет истекающие лицензии и уведомляет пользователей."""
    await asyncio.sleep(60)
    while True:
        for days in [3, 1]:
            try:
                result = await api('get', '/api/bot/expiring_licenses', params={'days': days})
                for u in result.get('users', []):
                    tg_id = u.get('telegram_id')
                    if not tg_id:
                        continue
                    try:
                        icon = PLAN_ICONS.get(u.get('product', ''), '📦')
                        date = format_date(u.get('expires_at', ''))

                        if days == 3:
                            text = (
                                f'⚠️ <b>Лицензия истекает через 3 дня</b>\n'
                                f'{DIVIDER}\n\n'
                                f'{icon} <b>{u["product"]}</b>\n'
                                f'📅 Дата окончания: {date}\n\n'
                                f'Продлите сейчас, чтобы не прерывать работу 👇'
                            )
                        else:
                            text = (
                                f'🔴 <b>Лицензия истекает СЕГОДНЯ!</b>\n'
                                f'{DIVIDER}\n\n'
                                f'{icon} <b>{u["product"]}</b>\n\n'
                                f'Продлите прямо сейчас 👇'
                            )

                        await bot.send_message(
                            tg_id,
                            text,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text='💳 Продлить сейчас', callback_data='buy')]
                            ])
                        )
                        logging.info(f'Уведомление: tg={tg_id} product={u["product"]} days={days}')
                    except Exception as e:
                        logging.warning(f'Не удалось отправить уведомление {tg_id}: {e}')
            except Exception as e:
                logging.error(f'Scheduler error (days={days}): {e}')

        await asyncio.sleep(12 * 3600)


# ─────────────────────────── Запуск ──────────────────────────────
async def main():
    if not BOT_TOKEN:
        raise ValueError('BOT_TOKEN не задан')

    logging.info('🤖 TG Lead Wareon Bot запускается...')
    asyncio.create_task(_notify_expiring())
    await dp.start_polling(bot, allowed_updates=['message', 'callback_query'])


if __name__ == '__main__':
    asyncio.run(main())
