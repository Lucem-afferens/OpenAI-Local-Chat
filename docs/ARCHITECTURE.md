# Архитектура

Документ описывает устройство **openai-local-chat**: компоненты, потоки данных и ключевые решения.

## Обзор

```
┌─────────────┐     HTTP      ┌──────────────┐     HTTPS      ┌─────────────┐
│   Browser   │ ◄──────────► │  FastAPI     │ ◄───────────► │  OpenAI API │
│ index.html  │   /api/*     │  app.py      │               │             │
└─────────────┘              └──────┬───────┘               └─────────────┘
                                    │
                                    │ SQL
                                    ▼
                             ┌──────────────┐
                             │   SQLite     │
                             │  store.py    │
                             │ data/*.sqlite│
                             └──────────────┘
```

| Слой | Файл | Роль |
|------|------|------|
| UI | `static/index.html` | SPA без фреймворка: polling сообщений, localStorage для настроек |
| API | `app.py` | Маршруты, бизнес-логика, вызовы OpenAI |
| Persistence | `store.py` | Сессии, сообщения, `billing.json` |
| Secrets | `.env` | `OPENAI_API_KEY`, опционально admin key |

---

## Жизненный цикл приложения

При старте (`lifespan` в FastAPI):

1. `store.init_db()` — создание/миграция SQLite.
2. `store.get_resumable_jobs()` — поиск assistant-сообщений в статусе `pending` / `running`.
3. Для каждой незавершённой задачи — повторная постановка в `ThreadPoolExecutor`.

При остановке — graceful shutdown пула потоков (без отмены уже запущенных futures).

---

## Модель данных (SQLite)

### Таблица `sessions`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | TEXT PK | UUID |
| `title` | TEXT | Заголовок (авто из первого сообщения) |
| `system` | TEXT | System prompt сессии |
| `model` | TEXT | Последняя выбранная модель |
| `created_at`, `updated_at` | REAL | Unix timestamp |
| `archived_at` | REAL NULL | Если задан — чат в архиве |

### Таблица `messages`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | TEXT PK | UUID |
| `session_id` | TEXT FK | Ссылка на сессию |
| `role` | TEXT | `user` / `assistant` |
| `content` | TEXT | Текст сообщения |
| `status` | TEXT | `completed`, `pending`, `running`, `failed`, `cancelled` |
| `model` | TEXT | Модель запроса |
| `attachment_name` | TEXT | Имя вложения (user) |
| `request_json` | TEXT | Полный payload для assistant (JSON) |
| `result_json` | TEXT | Ответ API (JSON) |
| `error_json` | TEXT | Ошибка (JSON) |
| `created_at`, `completed_at` | REAL | Unix timestamp |

### Файл `data/billing.json`

Локальная конфигурация виджета баланса: `credit_usd`, `set_at_unix`, `anchor_day_unix`, `baseline_spent_usd`.

---

## Отправка сообщения в чате

Основной путь UI — **асинхронный** (фоновая задача):

```
UI  POST /api/sessions/{id}/messages
         │
         ├─► store.enqueue_chat()  → user msg + assistant msg (pending)
         │
         └─► _schedule_assistant_job() → ThreadPoolExecutor
                    │
                    └─► _run_assistant_job()
                           ├─ routing (если auto)
                           ├─ OpenAI API
                           └─ store.complete_* / fail_* / cancel_*
```

UI опрашивает `GET /api/messages/{id}` до статуса `completed` / `failed` / `cancelled`.

Синхронный путь `POST /api/chat` сохранён для отладки и интеграций; UI его не использует.

---

## Авто-роутинг моделей

При `routing_mode: "auto"`:

1. **Эвристика:** вложение > 48 KB → tier `complex`.
2. **Классификатор:** один запрос к `CLASSIFIER_MODEL` (по умолчанию `gpt-4o-mini`) с `max_tokens=16`, ответ: `simple` | `complex` | `reasoning`.
3. **Выбор модели** из `TIER_MODEL_PRIORITY` — первая модель в списке для tier (не проверяется доступность в аккаунте; при 404 возможен fallback).
4. Если tier `reasoning`, но `allow_pro: false` → downgrade до `complex`.

При ошибке классификатора — tier `complex`.

### Fallback после ответа

Если модель вернула пустой ответ или ошибку «model not found», сервер может повторить запрос на модели tier `complex` (см. `_execute_chat_with_routing`).

---

## Выбор OpenAI API: Chat vs Responses

| Условие | API |
|---------|-----|
| Модель с `-pro` (не `-instruct`) | `client.responses.create` / stream |
| Остальные chat-модели | `client.chat.completions.create` |

Pro-модели используют **streaming** (`responses.stream`), чтобы длинные ответы не обрывались по idle timeout.

### Таймауты

| Тип | connect | read | write |
|-----|---------|------|-------|
| Обычные модели | 30 s | 1800 s (30 min) | 90 s |
| Pro | 60 s | 7200 s (2 h) | 180 s |

`max_retries=0` — ошибки возвращаются сразу, без скрытых повторов SDK.

---

## Отмена запросов

Механизм кооперативной отмены:

1. `POST /api/messages/{id}/cancel` → `threading.Event` + `store.cancel_assistant_message`.
2. Перед и после вызова OpenAI — `_job_cancel_check`.
3. После `_mark_job_openai_started` отмена **не прерывает** уже отправленный запрос — UI получает `already_processing: true`.

Завершённые сообщения (`completed`, `failed`, `cancelled`) защищены от перезаписи в SQL (`WHERE status IN ('pending', 'running')`).

---

## Биллинг

```
GET /api/billing
    │
    ├─► Organization Costs API (Admin key)
    │       spent_today_usd, spent_month_usd
    │
    └─► Локальный credit_usd + baseline_spent_usd
            remaining_usd = credit - (cumulative - baseline)
```

Кэш ответа: 60 секунд (`BILLING_CACHE_SEC`).

При сохранении баланса через UI (`POST /api/billing/config`) фиксируется `baseline_spent_usd` на текущий момент — расход «до сохранения» не вычитается из остатка.

---

## Изображения

- **Generate:** JSON body → `client.images.generate`.
- **Edit:** `multipart/form-data` → `client.images.edit` (image, optional mask, prompt).

Параметры `size`, `quality`, `output_format` нормализуются под семейство модели (DALL·E 2/3 vs GPT Image).

---

## Локализация ошибок

`_localize_error_text` и `_extract_token_limit_info` переводят типичные ошибки OpenAI на русский и извлекают лимит/фактический размер в токенах для UI.

---

## Клиентское состояние (localStorage)

| Ключ | Назначение |
|------|------------|
| `openai_local_chat_model` | Выбранная модель |
| `openai_local_chat_session` | ID активной сессии |
| `openai_local_chat_reply_language` | `ru` / `en` |
| `openai_local_chat_routing_mode` | `manual` / `auto` |
| `openai_local_chat_allow_pro` | Разрешить Pro в авто-режиме |

История сообщений хранится **на сервере**, не в браузере.

---

## Авто-обновление UI

`GET /api/client-revision` возвращает `max(mtime)` файлов `index.html`, `app.py`, `store.py`.  
Клиент периодически сравнивает revision и перезагружает страницу при изменении — удобно при `--reload` в разработке.

---

## Ограничения и компромиссы

- **Нет multi-user auth** — одна инсталляция = один владелец API key.
- **Thread pool** для фоновых задач — не Celery; при высокой нагрузке возможна очередь.
- **SQLite** — достаточно для локального использования; не рассчитан на тысячи параллельных клиентов.
- **Авто-роутинг** выбирает модель из статического списка, не из live `/v1/models`.
