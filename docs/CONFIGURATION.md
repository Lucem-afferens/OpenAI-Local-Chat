# Конфигурация

Все настройки сервера задаются через **переменные окружения** (файл `.env`) и **UI** (localStorage + `data/billing.json`).

---

## Загрузка `.env`

Порядок (последний перекрывает предыдущий):

1. `{родительская_папка}/.env` — опционально, если проект лежит внутри монорепозитория
2. `{корень_проекта}/.env` — основной файл

Скопируйте `.env.example` → `.env` и отредактируйте.

---

## Обязательные переменные

### `OPENAI_API_KEY`

Ключ для Chat Completions, Responses API и Images API.

- Получить: https://platform.openai.com/api-keys
- **Не коммитить** в git
- Используется только на сервере; браузер его не видит

Без этого ключа приложение **не запустится** (`RuntimeError` при импорте `app.py`).

---

## Опциональные переменные (биллинг)

### `OPENAI_ADMIN_API_KEY` или `OPENAI_USAGE_API_KEY`

Admin API key с правом **`api.usage.read`** для [Organization Costs API](https://platform.openai.com/docs/api-reference/usage/costs).

| | Chat key | Admin key |
|---|----------|-----------|
| Назначение | Запросы к моделям | Расход USD в UI |
| Scope | Chat, Images | `api.usage.read` |
| Обязателен | да | нет |

Без admin key виджет покажет подсказку; расход и остаток не обновятся автоматически.

### `OPENAI_BILLING_CREDIT_USD`

Стартовый баланс в USD для расчёта «остатка». Альтернатива — поле **«Баланс OpenAI»** в настройках UI (предпочтительно).

### `OPENAI_BILLING_CREDIT_SET_AT`

Unix timestamp момента установки баланса (если задаёте через env).

### `OPENAI_BILLING_BASELINE_SPENT_USD`

Уже потраченная сумма на момент установки баланса (чтобы не вычитать старые траты).

### `OPENAI_BILLING_ANCHOR_DAY_UNIX`

Начало суток (UTC) для привязки baseline.

---

## Настройки UI (localStorage)

Сохраняются в браузере пользователя:

| Настройка | Ключ localStorage | Значения |
|-----------|-------------------|----------|
| Модель чата | `openai_local_chat_model` | ID модели |
| Активная сессия | `openai_local_chat_session` | UUID |
| Язык ответа | `openai_local_chat_reply_language` | `ru`, `en` |
| Режим модели | `openai_local_chat_routing_mode` | `manual`, `auto` |
| Разрешить Pro | `openai_local_chat_allow_pro` | `0`, `1` |

System prompt хранится **в SQLite** (поле `sessions.system`), не в localStorage.

---

## Файлы данных (`data/`)

| Файл | Описание |
|------|----------|
| `data/chat.sqlite` | История чатов |
| `data/billing.json` | Сохранённый баланс и baseline |

Директория в `.gitignore`. Для бэкапа скопируйте всю папку `data/`.

---

## Константы сервера (код)

Изменяются только правкой `app.py`:

| Константа | Значение | Описание |
|-----------|----------|----------|
| `DEFAULT_CHAT_MODEL` | `gpt-4o-mini` | Модель по умолчанию и классификатор |
| `MAX_CONTEXT_FILE_BYTES` | 512 KB | Лимит вложений `.md`/`.txt` |
| `MAX_IMAGE_BYTES_GPT` | 50 MB | Загрузка для GPT Image edit |
| `MAX_IMAGE_BYTES_DALLE2_EDIT` | 4 MB | DALL·E 2 edit |
| `BILLING_CACHE_SEC` | 60 | Кэш `/api/billing` |
| `TIMEOUT_DEFAULT.read` | 1800 s | Таймаут обычных моделей |
| `TIMEOUT_PRO.read` | 7200 s | Таймаут Pro |

### Приоритет моделей (авто-роутинг)

`TIER_MODEL_PRIORITY` в `app.py`:

```python
"simple":    ["gpt-4o-mini", "gpt-5.4-mini", …]
"complex":   ["gpt-5.5", "gpt-4o", …]
"reasoning": ["gpt-5.5-pro", "gpt-5.4-pro", …]
```

Первая модель в списке используется как целевая. Подстройте под модели, доступные в вашем аккаунте.

---

## Параметры Uvicorn

Примеры:

```bash
# Разработка
uvicorn app:app --reload --host 127.0.0.1 --port 8765

# Production (несколько workers — осторожно с SQLite и фоновыми задачами)
uvicorn app:app --host 127.0.0.1 --port 8765 --workers 1
```

Для production см. [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Язык ответов модели

Server добавляет system instruction в зависимости от `reply_language`:

- `ru` — «Всегда отвечай на русском…»
- `en` — «Always respond in English…»

Пользовательский `system` из настроек **дополняет**, а не заменяет эту инструкцию.
