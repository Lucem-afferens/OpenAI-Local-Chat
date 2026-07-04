# Деплой на Vercel

Публичный веб-интерфейс: каждый посетитель вводит **свой** OpenAI API key. Ключ хранится в **localStorage браузера** и передаётся в заголовке `Authorization` — **не сохраняется** на сервере Vercel.

## Что меняется по сравнению с локальным запуском

| | Локально (`uvicorn`) | Vercel |
|---|---------------------|--------|
| API key | `.env` и/или браузер | **только браузер** |
| История чатов | SQLite на диске | **IndexedDB** в браузере |
| Биллинг в UI | да (с admin key в `.env`) | **нет** |
| Фоновые задачи | да | **нет** — синхронный `/api/chat` |
| Pro-модели (до 2 ч) | да | **ограничено** таймаутом Vercel (≈60 с на Pro-плане) |

## Быстрый деплой

1. Форк / clone репозитория [Lucem-afferens/OpenAI-Local-Chat](https://github.com/Lucem-afferens/OpenAI-Local-Chat)
2. [vercel.com](https://vercel.com) → **Add New Project** → Import репозитория
   - Если его нет в списке → **Adjust GitHub App Permissions** и дайте Vercel доступ к репо.
3. **Framework Preset:** **Other** (не Next.js, не Vite, **не FastAPI / Python**)
4. **Build & Output** (Settings → General → Build & Development Settings):

| Поле | Значение |
|------|----------|
| Framework Preset | **Other** |
| Root Directory | `./` |
| Build Command | `npm run vercel-build` |
| Output Directory | **пусто** (не `public`) |
| Install Command | `npm install` |

5. **Environment Variables:** `OPENAI_API_KEY` **не добавлять**
6. Deploy

### Ошибка «Application startup failed. Exiting.» на `/`

Vercel пытается запустить **`app.py` (Python/FastAPI)** вместо статики + Node `/api`.  
Исправление: preset **Other**, Output Directory **пустой**, redeploy.  
В репозитории `.vercelignore` исключает Python-файлы из деплоя Vercel.

После деплоя откройте сайт → введите свой ключ в модальном окне или в **⚙ Настройки**.

## Безопасность (публичный репозиторий)

- Исходный код не содержит секретов
- Serverless-функции **не пишут** ключ в файлы и **не должны** логировать заголовок `Authorization`
- Ключ в localStorage виден только вам в DevTools — не используйте на чужих компьютерах
- Любой может открыть ваш Vercel-URL и работать **со своим** ключом (это ожидаемо)
- Не используйте org admin key в браузере — только personal/chat key

## Локальная разработка UI для Vercel

```bash
npm install
npm run vercel-build
npx vercel dev
```

`vercel dev` поднимает и статику, и `/api/*` как на production.

## Локальный FastAPI (как раньше)

```bash
cp .env.example .env   # OPENAI_API_KEY — опционально
uvicorn app:app --reload --host 127.0.0.1 --port 8765
```

Если `OPENAI_API_KEY` в `.env` **не задан**, локальный сервер тоже попросит ключ в браузере (как на Vercel), но история останется в SQLite.

## Структура Vercel

```
api/                  # Serverless proxy (Node.js)
  _lib/               # общий код (префикс _ — не маршрут)
  config.js           # GET /api/config
  ...
public/               # генерируется vercel-build (не в git)
  index.html
  assets/runtime.js
vercel.json
package.json
```

## Ограничения

- **Таймаут функций** — длинные Pro-ответы могут обрываться; на Vercel используйте обычные модели
- **Нет серверной истории** — очистка данных браузера удалит чаты
- **Billing widget** отключён — смотрите баланс на [platform.openai.com](https://platform.openai.com)
