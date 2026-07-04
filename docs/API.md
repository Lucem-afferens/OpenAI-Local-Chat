# HTTP API

Базовый URL: `http://127.0.0.1:8765`

Интерактивная схема: `/docs` (Swagger), `/redoc` (ReDoc).

Общие замечания:

- Все тела запросов — JSON (`Content-Type: application/json`), кроме `POST /api/images/edit` (multipart).
- Ошибки OpenAI возвращаются как JSON с полем `message` на русском и опционально `kind`, `tokens_limit`, `tokens_actual`.
- HTTP-коды: `400` (валидация), `404` (не найдено), `409` (конфликт), `429` (rate limit), `502`/`504` (OpenAI / timeout).

---

## Статические страницы

### `GET /`

Главная страница — `static/index.html` с заголовками `Cache-Control: no-store`.

### `GET /assets/*`

Статические файлы из `static/`.

---

## Модели

### `GET /api/models`

Список chat-моделей для UI.

**Ответ 200:**

```json
{
  "models": ["gpt-4o-mini", "gpt-4o", "..."],
  "source": "openai"
}
```

| `source` | Значение |
|----------|----------|
| `openai` | Список с API |
| `fallback_empty` | API вернул пустой список |
| `fallback_error` | Ошибка API + поле `warning` |

---

### `GET /api/image-models`

Аналогично для image-моделей (`gpt-image-*`, `dall-e-*`).

---

## Сессии

### `POST /api/sessions`

Создать чат.

**Тело (все поля опциональны):**

```json
{
  "title": "Новый чат",
  "system": "Ты помощник…",
  "model": "gpt-4o-mini"
}
```

**Ответ 200:**

```json
{
  "session": { "id": "…", "title": "…", "created_at": 0, "updated_at": 0, "archived_at": null },
  "messages": []
}
```

---

### `GET /api/sessions`

Активные сессии (не в архиве), до 40 шт., `updated_at DESC`.

Query: `?archived=false` (по умолчанию).

---

### `GET /api/sessions/archived/list`

Архивные сессии.

---

### `GET /api/sessions/{session_id}`

Сессия + все сообщения.

**404** — сессия не найдена.

---

### `POST /api/sessions/{session_id}/archive`

Переместить чат в архив. В архиве отправка сообщений запрещена.

---

### `DELETE /api/sessions/{session_id}`

Удалить **только архивный** чат навсегда.

**400** — чат не в архиве.

---

## Сообщения и чат

### `POST /api/sessions/{session_id}/messages`

Поставить сообщение в очередь (основной путь UI).

**Тело (`ChatBody`):**

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `model` | string | да | ID модели (или placeholder при auto) |
| `message` | string | да | Текст пользователя |
| `system` | string | нет | System prompt |
| `reply_language` | string | нет | `ru` (default) или `en` |
| `routing_mode` | string | нет | `manual` (default) или `auto` |
| `allow_pro` | bool | нет | Pro в авто-режиме (default: false) |
| `attachment_name` | string | нет | Имя файла |
| `attachment_text` | string | нет | Содержимое `.md`/`.txt` |

**Ответ 200:**

```json
{
  "session_id": "…",
  "user_message_id": "…",
  "assistant_message_id": "…",
  "status": "pending",
  "background": true
}
```

---

### `GET /api/messages/{message_id}`

Статус и содержимое сообщения. UI polling до `completed` / `failed` / `cancelled`.

**Поля сообщения:**

| Поле | Описание |
|------|----------|
| `status` | `pending`, `running`, `completed`, `failed`, `cancelled` |
| `content` | Текст ответа или ошибки |
| `result` | JSON метаданных (model, api, routing_*, usage…) |
| `error` | JSON ошибки при `failed` |

---

### `POST /api/messages/{message_id}/cancel`

Отменить pending/running assistant message.

**409** — уже завершено.

**200 с `already_processing: true`** — запрос уже у OpenAI.

---

### `POST /api/chat`

Синхронный чат (без SQLite-очереди). Тело — `ChatBody`.

**Ответ 200** — объект с `reply`, `model`, `api`, опционально routing-поля.

---

## Фоновые задачи и revision

### `GET /api/jobs/pending`

```json
{ "count": 2 }
```

Количество assistant-сообщений в `pending` / `running`.

---

### `GET /api/client-revision`

```json
{ "revision": 1735689600123 }
```

Timestamp для авто-reload UI.

---

## Биллинг

### `GET /api/billing`

Виджет расхода и остатка. Кэш 60 с.

**Пример ответа:**

```json
{
  "usage_api": true,
  "currency": "usd",
  "overview_url": "https://platform.openai.com/settings/organization/billing/overview",
  "updated_at": 1735689600,
  "spent_month_usd": 12.34,
  "spent_today_usd": 1.23,
  "credit_usd": 50.0,
  "credit_set_at": 1735600000,
  "remaining_usd": 42.5,
  "configured": true,
  "hint": null
}
```

---

### `POST /api/billing/config`

Сохранить стартовый баланс.

**Тело:**

```json
{ "credit_usd": 50.0 }
```

**Ответ:**

```json
{
  "ok": true,
  "config": { "credit_usd": 50.0, "set_at_unix": 1735689600 },
  "warning": "…"
}
```

`warning` — если не удалось зафиксировать baseline через Costs API.

---

## Изображения

### `POST /api/images/generate`

**Тело (`ImageGenerateBody`):**

| Поле | Default | Описание |
|------|---------|----------|
| `prompt` | — | Текст промпта |
| `model` | `gpt-image-1.5` | Image model |
| `size` | `1024x1024` | Зависит от модели |
| `quality` | `auto` | `standard`, `hd`, `high`, … |
| `n` | `1` | 1–10 |
| `output_format` | null | `png`, `jpeg`, `webp` (GPT Image) |

**Ответ 200:**

```json
{
  "images": [{ "b64_json": "…", "revised_prompt": "…" }],
  "model": "gpt-image-1.5",
  "api": "images.generate",
  "usage": {}
}
```

---

### `POST /api/images/edit`

`multipart/form-data`:

| Поле | Обязательно | Описание |
|------|-------------|----------|
| `prompt` | да | Инструкция редактирования |
| `image` | да | Исходное изображение |
| `mask` | нет | Маска (прозрачные области = редактировать) |
| `model` | нет | default `gpt-image-1.5` |
| `size` | нет | `auto`, `1024x1024`, … |
| `quality` | нет | `auto`, `high`, … |
| `input_fidelity` | нет | `low` / `high` (GPT Image, не mini) |
| `n` | нет | 1–10 |
| `output_format` | нет | `png`, `jpeg`, `webp` |

**Лимиты размера файла:**

- GPT Image: 50 MB
- DALL·E 2 edit: 4 MB

Формат ответа — как у generate.

---

## Коды ошибок (типичные)

| HTTP | `kind` | Ситуация |
|------|--------|----------|
| 400 | — | Невалидное вложение, слишком большой файл |
| 404 | — | Сессия / сообщение не найдены |
| 409 | — | Отмена завершённого сообщения |
| 429 | `rate_limit` | OpenAI 429 |
| 502 | `connection`, `stream_error`, `http` | Ошибка API |
| 504 | `timeout` | Read timeout |
