# OpenAI Local Chat

**Локальный веб-чат с OpenAI API** — FastAPI-бэкенд и одностраничный UI без сборки фронтенда.

**Как пользоваться:** склонируйте репозиторий на **свой компьютер**, добавьте свой `OPENAI_API_KEY` в `.env`, запустите — откройте в браузере. Никакого облачного деплоя не требуется: каждый запускает копию у себя и платит только за свой ключ OpenAI.

Ключ API хранится **только в локальном процессе** (Python) и не попадает в браузер.

> **English:** Clone, add your API key, run locally — personal OpenAI chat UI with history, auto routing, images, and Russian-first UX. Python + FastAPI + SQLite. No hosted service.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## Содержание

- [Возможности](#возможности)
- [Быстрый старт](#быстрый-старт)
- [Структура проекта](#структура-проекта)
- [Документация](#документация)
- [Безопасность](#безопасность)
- [Лицензия](#лицензия)

---

## Возможности

### Чат

| Функция | Описание |
|--------|----------|
| **Модели из API** | Список chat-моделей с [`GET /v1/models`](https://platform.openai.com/docs/api-reference/models/list); при ошибке — запасной список |
| **Chat Completions** | Обычные GPT / o-серии через `v1/chat/completions` |
| **Responses API** | Pro-модели (`gpt-*-pro`, `o*-pro`), для которых нет `chat/completions` |
| **Авто-роутинг** | Классификатор на mini выбирает tier: `simple` → mini, `complex` → GPT-5.5/4o, `reasoning` → Pro (если разрешено) |
| **История** | Сессии и сообщения в SQLite; фоновые задачи с восстановлением после перезапуска |
| **Архив** | Чаты можно архивировать (только чтение) или удалить навсегда из архива |
| **Вложения** | `.md`, `.txt` до 512 KB — текст добавляется в промпт |
| **Отмена запроса** | Остановка до отправки в OpenAI; после старта API — честное предупреждение |
| **Экспорт** | Копирование, TXT, Markdown, «Поделиться» |
| **Ошибки на русском** | Парсинг лимитов context/TPM с числами токенов |

### Изображения

| Функция | Описание |
|--------|----------|
| **Генерация** | GPT Image, DALL·E 2/3 через `images.generate` |
| **Редактирование** | Inpainting с маской, `input_fidelity` для GPT Image |
| **Лимиты файлов** | до 50 MB (GPT Image) / 4 MB (DALL·E 2 edit) |

### Биллинг (опционально)

| Функция | Описание |
|--------|----------|
| **Расход** | Сегодня / за месяц через [Organization Costs API](https://platform.openai.com/docs/api-reference/usage/costs) |
| **Остаток** | Сохранённый баланс минус расход после точки синхронизации |
| **Admin key** | Отдельный ключ с `api.usage.read` — не путать с chat key |

### UI

- Тёмная тема, адаптивная вёрстка (sidebar на мобильных)
- Режимы «Чат» и «Изображения»
- Авто-обновление страницы при изменении `app.py` / `index.html` (`/api/client-revision`)
- Настройки: system prompt, язык ответа, авто-роутинг, баланс OpenAI

---

## Быстрый старт

Три шага: **clone → `.env` → run**. Docker не нужен.

### Требования

- **Python 3.10+**
- Аккаунт OpenAI и [свой API key](https://platform.openai.com/api-keys)

### Установка

```bash
git clone https://github.com/Lucem-afferens/OpenAI-Local-Chat.git
cd OpenAI-Local-Chat

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Отредактируйте .env — вставьте OPENAI_API_KEY
```

### Запуск

```bash
uvicorn app:app --reload --host 127.0.0.1 --port 8765
```

Откройте в браузере: **http://127.0.0.1:8765**

История чатов сохраняется локально в `data/chat.sqlite` на вашем диске.

**Docker** — необязательная альтернатива тому же локальному запуску; см. [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (раздел Docker).

### Веб на Vercel

Import репозитория в [Vercel](https://vercel.com) — **`OPENAI_API_KEY` на сервере не нужен**. Каждый пользователь вводит свой ключ в UI; он хранится в **localStorage** браузера. Подробно: [docs/VERCEL.md](docs/VERCEL.md).

### Первые шаги в UI

1. Выберите **модель** в верхней панели или включите **«Авто»** в настройках.
2. Напишите сообщение и нажмите **↑** или **Ctrl/Cmd+Enter**.
3. При необходимости прикрепите **`.md` / `.txt`** (📎).
4. Перейдите в **🖼 Изображения** для генерации или редактирования картинок.
5. В **⚙ Настройки** задайте system prompt, язык ответа, API key (если нет `.env`) и (локально) баланс OpenAI.

---

## Структура проекта

```
openai-local-chat/
├── app.py              # FastAPI: API, OpenAI, роутинг, биллинг, изображения
├── store.py            # SQLite: сессии, сообщения, billing.json
├── api/                # Vercel serverless (прокси OpenAI, без хранения ключей)
├── static/
│   ├── index.html      # UI
│   └── runtime.js      # dual-mode: local / Vercel
├── data/               # Локальные данные (не в git)
│   ├── chat.sqlite
│   └── billing.json
├── docs/               # Подробная документация
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## Документация

| Документ | Содержание |
|----------|------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Архитектура, потоки данных, авто-роутинг, фоновые задачи |
| [docs/API.md](docs/API.md) | Справочник HTTP API |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Переменные окружения и настройки UI |
| [docs/VERCEL.md](docs/VERCEL.md) | Деплой на Vercel, ключ клиента в браузере |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker и прочее (опционально; для типичного использования не нужно) |
| [SECURITY.md](SECURITY.md) | Угрозы, рекомендации для публичного доступа |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Как внести вклад |
| [CHANGELOG.md](CHANGELOG.md) | История изменений |

Интерактивная документация API при запущенном сервере:

- Swagger UI: http://127.0.0.1:8765/docs
- ReDoc: http://127.0.0.1:8765/redoc

---

## Безопасность

Рассчитано на **личный локальный запуск** (`127.0.0.1`) или **Vercel** с ключом в браузере.

- **Никогда** не коммитьте `.env` и не публикуйте API key в issue/PR.
- **Локально:** ключ в `.env` или в настройках UI (localStorage).
- **Vercel:** только ключ в браузере; serverless-прокси не сохраняет его.
- Ключ в localStorage доступен скриптам на том же origin — не вводите на чужих ПК.
- Не открывайте порт `0.0.0.0` без auth на своём сервере. Подробнее: [SECURITY.md](SECURITY.md).

Сообщения об уязвимостях: см. [SECURITY.md](SECURITY.md).

---

## Зависимости

| Пакет | Назначение |
|-------|------------|
| [FastAPI](https://fastapi.tiangolo.com/) | HTTP API и раздача UI |
| [Uvicorn](https://www.uvicorn.org/) | ASGI-сервер |
| [OpenAI Python SDK](https://github.com/openai/openai-python) | Chat, Responses, Images |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | Загрузка `.env` |
| [httpx](https://www.python-httpx.org/) | HTTP-клиент (таймауты, Costs API) |
| [python-multipart](https://github.com/Kludex/python-multipart) | Загрузка файлов (image edit) |

---

## Лицензия

[MIT](LICENSE) © Nikolai Dudin
