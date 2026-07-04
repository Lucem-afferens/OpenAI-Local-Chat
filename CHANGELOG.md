# Changelog

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## [Unreleased]

### Added

- Генерация и редактирование изображений (GPT Image, DALL·E 2/3)
- Виджет биллинга: расход за день/месяц, остаток по сохранённому балансу
- Organization Costs API через `OPENAI_ADMIN_API_KEY`
- Отмена запросов до отправки в OpenAI
- Парсинг лимитов context/TPM в ошибках с русскими сообщениями
- `/api/client-revision` — авто-перезагрузка UI при изменении кода
- `/api/jobs/pending` — счётчик фоновых задач
- Полная документация: `docs/`, `SECURITY.md`, `CONTRIBUTING.md`

### Changed

- `.env.example` — standalone конфигурация без привязки к внешнему монорепо
- `.gitignore` — явное игнорирование `data/*`

---

## [1.0.0] — 2026-06-09

### Added

- Локальный веб-чат на FastAPI + SQLite
- Русский UI без сборки фронтенда
- Chat Completions и Responses API (Pro-модели)
- Авто-роутинг моделей по сложности запроса
- История сессий, архив, удаление из архива
- Вложения `.md` / `.txt` (до 512 KB)
- Фоновые задачи с восстановлением после перезапуска
- Экспорт: копирование, TXT, Markdown
- Локализация ошибок OpenAI на русский

[Unreleased]: https://github.com/Lucem-afferens/OpenAI-Local-Chat/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Lucem-afferens/OpenAI-Local-Chat/releases/tag/v1.0.0
