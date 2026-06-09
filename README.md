# OpenAI — локальный веб-чат

Простой интерфейс на **FastAPI**: список моделей с [OpenAI API](https://platform.openai.com/docs/api-reference/models/list). Ответы идут через [Chat Completions](https://platform.openai.com/docs/api-reference/chat/create) или, для Pro-моделей вроде **`gpt-5.5-pro`**, через [Responses API](https://platform.openai.com/docs/api-reference/responses/create) — у них нет поддержки `v1/chat/completions`. Ключ **только на сервере** — в браузер не передаётся.

## Ключ API

Используется переменная **`OPENAI_API_KEY`**:

1. Корневой `.env` репозитория Méntoras (как у основного бота), **или**
2. `openai-local-chat/.env` (перекрывает корень, если задано в обоих).

Образец: `.env.example`.

## Запуск

```bash
cd openai-local-chat
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload --host 127.0.0.1 --port 8765
```

Открой в браузере: **http://127.0.0.1:8765**

- Внизу выбери **модель** (список подгружается с API; при ошибке — запасной список) или включи в настройках режим **«Авто»** — сервер сам подберёт mini / GPT-5.5 / 4o / Pro по сложности запроса.
- Напиши запрос и нажми **Отправить** (или **Ctrl/Cmd+Enter** в поле сообщения).
- При необходимости прикрепи **`.md` / `.txt`** (до 512 KB) — текст файла уйдёт в запрос вместе с сообщением.

## Примечания

- В метаданных смотри **API:** `responses` (Pro и часть frontier) или `chat.completions` (обычные чат-модели).
- Не коммить файлы с реальным ключом; держи их в `.env` (он в `.gitignore` у корня проекта).
