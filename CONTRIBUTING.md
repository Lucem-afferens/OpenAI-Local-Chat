# Contributing

Спасибо за интерес к **openai-local-chat**! Ниже — как подготовить изменения и отправить pull request.

---

## Перед началом

1. Оформите [issue](https://github.com/Lucem-afferens/OpenAI-Local-Chat/issues) для крупных изменений — обсудим подход.
2. Убедитесь, что изменение вписывается в scope: локальный UI-прокси к OpenAI без тяжёлой инфраструктуры.

---

## Окружение разработки

```bash
git clone https://github.com/Lucem-afferens/OpenAI-Local-Chat.git
cd OpenAI-Local-Chat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# добавьте OPENAI_API_KEY

uvicorn app:app --reload --host 127.0.0.1 --port 8765
```

UI авто-обновляется при правках `static/index.html` и `app.py` (см. `/api/client-revision`).

---

## Стиль кода

- **Python 3.10+**, type hints где уже принято в файле
- Сообщения пользователю и ошибки API — **на русском** (как в существующем коде)
- Минимальный diff: не рефакторить несвязанный код
- Без новых зависимостей без веской причины
- Комментарии — только для неочевидной логики

---

## Структура изменений

| Область | Файлы |
|---------|-------|
| API / OpenAI | `app.py` |
| БД / сессии | `store.py` |
| UI | `static/index.html` |
| Документация | `README.md`, `docs/*` |
| Конфиг | `.env.example`, `docs/CONFIGURATION.md` |

При добавлении API-эндпоинта обновите `docs/API.md` и при необходимости `docs/ARCHITECTURE.md`.

---

## Pull request

1. Fork → feature branch от `main`
2. Коммиты с понятными сообщениями (русский или английский)
3. Проверьте вручную:
   - отправка сообщения в чат
   - авто-роутинг (если затронут)
   - архив / новый чат
   - (если затронуто) изображения, биллинг, отмена
4. **Не включайте** `.env`, `data/`, ключи
5. Откройте PR с описанием: что, зачем, как проверить

---

## Что мы не принимаем

- Коммиты с реальными API keys
- Breaking changes без обсуждения
- Тяжёлые фреймворки для UI (React/Vue build) без согласования
- Функции, требующие хранения паролей пользователей, без security review

---

## Лицензия

Внося код, вы соглашаетесь на распространение под [MIT License](LICENSE).
