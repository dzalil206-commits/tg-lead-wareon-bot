# TG Lead Wareon Bot — Стратегия и ТЗ

> Автономный бот (@TGLeadWareonBot), без зависимости от сайта.
> Цель: функциональнее конкурентов («Punk of Leads» и аналоги). Полный цикл лидогенерации
> в Telegram внутри одного бота: прогрев → аккаунты → сбор аудитории → рассылка → автоответ → лиды.

---

## 0. Архитектура

Два процесса + общая БД:

| Слой | Роль | Стек |
|---|---|---|
| **Бот** (control plane) | UI, FSM, запись задач, статусы | aiogram 3.7+ (polling) |
| **Воркер** (execution plane) | Берёт jobs из очереди, выполняет на сессиях, пишет прогресс в БД, шлёт пуши через bot-токен | Telethon + asyncio-очередь |
| **БД** | состояние всего | SQLite (`aiosqlite`) → позже Postgres |
| **Сессии** | зашифрованные `.session` строки на аккаунт | Fernet/AES |
| **Прокси-пул** | привязка к аккаунту, health-check | — |

**Переиспользование:** Telethon-логику (отправка, Miner-парсинг, правка профиля) вынести из сайта
(`lead-ecosystem/sender_routes.py`, Miner) в `worker/` бот-репозитория, Flask выкинуть.

Очередь задач (таблица `jobs`): бот пишет задачу со `status='queued'`, воркер опрашивает,
ставит `running`, по ходу обновляет `progress`/`log`, в конце `done`/`failed`. Бот показывает
живой статус, читая ту же строку (или принимает пуш от воркера).

---

## 1. Модель данных (целевая)

```
users(
  tg_id PK, first_name, username, referred_by,
  onboarded INTEGER DEFAULT 0,        -- подписку проверяли? (гейт показываем только при 0)
  sub_verified_at TEXT,
  trial_used INTEGER DEFAULT 0,       -- триал уже активировали?
  notify_enabled INTEGER DEFAULT 1,
  joined_at
)

licenses(id PK, tg_id, product, license_key, expires_at, created_at)  -- product: Trial|Miner|Sender|Start|Pro|Scale

proxies(
  id PK, tg_id, scheme, host, port, login, password,
  status TEXT DEFAULT 'unknown',      -- ok|dead|unknown
  last_check, created_at
)

accounts(
  id PK, tg_id, phone, session_enc,   -- зашифрованная Telethon-сессия
  proxy_id,                            -- обязательная привязка
  status TEXT DEFAULT 'new',           -- new|alive|limited|needs_login|banned
  warmth INTEGER DEFAULT 0,            -- индекс прогрева 0..100
  first_name, last_name, username, about, personal_channel,
  device_model, app_version, lang,    -- антидетект-профиль (постоянный на сессию)
  spamblock TEXT,                      -- none|active|checking
  added_at, last_seen
)

audiences(id PK, tg_id, name, source, count, created_at)
audience_items(id PK, audience_id, user_id, username, first_name, flags)

campaigns(
  id PK, tg_id, mode TEXT,            -- basic|multiphase|postbot
  audience_id, account_ids TEXT,      -- JSON список
  text TEXT, prompt TEXT,             -- text для basic, prompt для multiphase/autoreply
  rotation TEXT,                       -- JSON [{text,weight}]
  daily_limit, delay_min, delay_max, quiet_from, quiet_to,
  status TEXT DEFAULT 'draft',        -- draft|running|paused|done
  sent, delivered, replied, created_at
)

autoreplies(
  id PK, tg_id, account_id, mode TEXT, -- ai|fixed
  prompt TEXT, fixed_text TEXT,
  work_from, work_to, enabled INTEGER DEFAULT 1
)

leads(
  id PK, tg_id, campaign_id, account_id, peer_id, username,
  status TEXT DEFAULT 'new',          -- new|dialog|qualified|client|declined
  last_message, updated_at
)

warmups(
  id PK, account_id, preset TEXT,     -- new3d|fast24|deep7d
  status TEXT, started_at, finished_at, log TEXT
)

jobs(
  id PK, tg_id, kind TEXT,            -- add_account|warmup|parse|send|autoreply|spamblock|profile|validity
  payload TEXT,                        -- JSON параметры
  status TEXT DEFAULT 'queued',       -- queued|running|done|failed
  progress INTEGER DEFAULT 0, log TEXT, result TEXT,
  created_at, updated_at
)

reviews(id PK, tg_id, username, rating, body, created_at)
```

Ленивые миграции через `ALTER TABLE ... ADD COLUMN` в `db_init` (try/except).

---

## 2. Онбординг и подписка «один раз»

`/start`:
1. `user = db_get_user(tg_id)`; `db_ensure_user(...)`.
2. Разбор deep-link `ref_<tg_id>` (реф-бонус).
3. **Если `user.onboarded == 0`** → показать гейт подписки (приветствие + `📢 Подписаться` / `✅ Проверить подписку`). Иначе сразу `_enter_app`.
4. Кнопка `✅ Проверить подписку` → если подписан: `onboarded=1`, `sub_verified_at=now`, `_enter_app`. **Повторно гейт не показываем никогда** (даже при отписке).

Экран гейта = текущий продающий текст «Автопоиск клиентов» + строка «Чтобы открыть бота — подпишись».

---

## 3. Меню и триал

**Новичок** (`trial_used=0` и нет активной лицензии):
- Сетка разделов видна, но по тапу — мягкий замок (см. `_require_license`).
- По центру широкая кнопка **`🎁 Попробовать бесплатно`** (`callback=trial_start`).

FSM/флоу триала:
- `trial_start` → экран «Использовать бесплатно 2 дня» + кнопка `✅ Использовать бесплатно 2 дня` (`callback=trial_activate`).
- `trial_activate` → если `trial_used==0`: `db_add_license(tg_id,'Trial',2)`, `trial_used=1` →
  «✅ Пробный период активирован, вернитесь в меню и начните работу» + `⬅️ Назад` (`callback=menu`).
  Если уже использован → alert «Триал уже активирован».

**Активный** — полная сетка:
```
🔥 AI-Прогрев        👤 Менеджер аккаунтов
📨 Умные рассылки    🎯 Автосбор аудитории
🤖 Автоответ         📊 Лиды / Аналитика
──────────────────────────────────────
👤 Профиль  🛡 Лицензии  👥 Рефералы  💬 Поддержка
```

Гейт-хелпер `_require_license(call)` — пропускает только при активной лицензии (триал считается).

---

## 4. Модули (ТЗ)

### 4.1 🔥 AI-Прогрев  (`warmup`)
**Назначение:** человекоподобная отлёжка/активность, чтобы аккаунт не банили.
**Действия (library, выполняет воркер на сессии):** краткий заход, установка username/аватара/bio,
отлёжка, вступление в каналы (безопасный публичный список), чтение каналов, просмотр историй,
диалог с контактом (прогрев-пары), включение 2FA, сброс чужих сессий.
**Пресеты:** `new3d` (новый, 3 дня), `fast24` (24ч), `deep7d` (глубокий, 7 дней).
**Экраны:**
- Список аккаунтов → выбор → выбор пресета → подтверждение → `job kind=warmup`.
- Живой статус: фазы + ✅/⏳, временные окна (зеркало лога пользователя), `warmth` растёт.
**Напоминание (необязательное):** перед `send`/`parse`, если `warmth < порога`:
«⚠️ Аккаунт не прогрет, риск бана. `[🔥 Прогреть]` `[Пропустить]`». Выбор за пользователем.
**Улучшения:** авто-прогрев при добавлении; пауза при флагах Telegram; рандомизация темпа.

### 4.2 📨 Умные авторассылки  (`sending`)
Три режима:
- **basic** — AI-уникализация каждого сообщения (Claude через прокси aiprimetech), обход спам-фильтров.
- **multiphase** ⭐ — LLM ведёт естественный диалог, читает ответы, в нужный момент органично питчит.
  Требует обработку входящих на user-сессии + LLM-цикл состояния диалога. Конверсия ×3–5.
- **postbot** — массовый охват через @PostBot.
**Визард (FSM):** режим → аккаунты → база (audience) → текст/промт → лимиты (daily_limit,
delay_min/max, quiet hours) → запуск (`job kind=send`). Живая статистика sent/delivered/replied.
**Улучшения:** гейт по прогреву, ramp-up по возрасту аккаунта, A/B-варианты (rotation по весам),
детект ответов → авто-перенос в `leads`, авто-стоп при спамблоке, чёрный список/стоп-слова, follow-up.

### 4.3 🎯 Автосбор аудитории  (`audience`)
**Источники:** участники чатов, комментаторы каналов, поиск по ключевым словам, аудитория
конкурентов, гео. **Фильтры:** онлайн, активность, язык, есть username, premium.
**Экраны:** указать источник(и) → фильтры → запуск (`job kind=parse`) → именованная база, дедуп, счётчик, экспорт.
**Улучшения:** AI-расширение ключевиков, lookalike, скоринг лида, кнопка «сразу в рассылку».

### 4.4 👤 Менеджер аккаунтов  (`accounts`)
**Добавление — порядок строгий:**
1. Проверить наличие рабочего прокси у пользователя. **Нет прокси → экран «Сначала подключите прокси»**
   + `[➕ Добавить прокси]` / `[🛒 Купить прокси]`. Без прокси аккаунт не добавляем.
2. Способ: по номеру (FSM: phone → code → 2FA) или импорт `.session`/TData.
**Карточка аккаунта:** статус (жив/ограничен/нужен вход/бан), warmth, привязанный прокси.
**Функции:** снять спамблок (@SpamBot), проверка валидности, оценка живучести (health),
правка профиля (аватар/имя/юз/описание/личный канал) в боте, сброс сессий, включение 2FA.
**Улучшения:** массовые операции, авто-ротация прокси, монитор здоровья + алерты при бане,
бэкап/экспорт сессий, группы/теги, антидетект-профиль (device/app/lang постоянные на сессию).

### 4.5 🤖 Автоответ  (`autoreply`)
**Режимы:** AI-промт или фиксированное сообщение. Выбор аккаунта(ов) для подключения.
**Экраны:** выбрать аккаунт → режим → промт/текст → рабочие часы → включить (`autoreplies.enabled=1`).
Воркер слушает входящие на сессии и отвечает.
**Улучшения:** маршрутизация по ключевым словам, передача «живому» оператору с пушем, захват лида
в CRM, AI-квалификация, связка с multiphase.

### 4.6 📊 Лиды / Аналитика  (новый модуль — наше преимущество)
- **Лиды:** ответившие → статусы (new→dialog→qualified→client→declined), карточка диалога.
- **Аналитика:** sent/delivered/replied/conversion/**cost-per-lead**, по кампаниям и аккаунтам.

---

## 5. Доп. улучшения (бэклог)
- Биллинг в боте (Telegram Stars / ЮKassa / крипта) — продление без менеджера.
- Событийные пуши: бан аккаунта, ответ лида, конец кампании, спамблок.
- Авто-лимиты по возрасту аккаунта.
- Библиотека готовых сценариев по нишам (прогрев+база+тексты).
- Команда/мультиоператор + роли (тариф для агентств).

---

## 6. Дорожная карта
- **Фаза 0 — done:** автономная БД, онбординг, профиль, продающий home, стиль (blockquote/bullets).
- **Фаза 1 — done:** подписка-один-раз (`users.onboarded`) + триал «2 дня» (`trial_start`/`trial_activate`, `users.trial_used`) + новое меню из 6 модулей с CTA-кнопкой триала для новичков + оболочки разделов (warmup/accounts/sending/audience/autoreply/leads) + необязательное напоминание о прогреве перед sending/audience (`_maybe_warmup_hint` + `skipwarm_*`). *(чистый бот, без воркера)*
- **Фаза 2:** Менеджер аккаунтов + прокси + добавление по номеру (воркер + Telethon). *(фундамент)*
- **Фаза 3:** Автосбор (парсер).
- **Фаза 4:** Базовая рассылка + движок прогрева.
- **Фаза 5:** Многофазная рассылка + Автоответ (LLM-диалог).
- **Фаза 6:** Лиды/CRM + аналитика + биллинг в боте.

---

## 7. Юридический контур
Дисклеймеры спам/согласие/38-ФЗ; лицензия на ПО + ст.1253.1 ГК (информационный посредник);
ч.3 ст.6 152-ФЗ (обработка по поручению). Прогрев/антидетект — dual-use, легитимны как защита аккаунта.
