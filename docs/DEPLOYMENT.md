# Развёртывание

Руководство по запуску **openai-local-chat** за пределами локальной разработки.

---

## Рекомендуемая модель

| Сценарий | Host | Auth |
|----------|------|------|
| Личное использование | `127.0.0.1` | Не требуется |
| Доступ с другого устройства в LAN | `0.0.0.0` + firewall | **Обязателен** reverse proxy + auth |
| Публичный интернет | VPS + HTTPS | **Обязателен** auth + rate limit |

⚠️ Без аутентификации любой посетитель тратит ваш OpenAI balance.

---

## systemd (Linux)

Файл `/etc/systemd/system/openai-local-chat.service`:

```ini
[Unit]
Description=OpenAI Local Chat
After=network.target

[Service]
Type=simple
User=openai-chat
Group=openai-chat
WorkingDirectory=/opt/openai-local-chat
EnvironmentFile=/opt/openai-local-chat/.env
ExecStart=/opt/openai-local-chat/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8765 --workers 1
Restart=on-failure
RestartSec=5

# Hardening (опционально)
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd --system --home /opt/openai-local-chat openai-chat
sudo systemctl daemon-reload
sudo systemctl enable --now openai-local-chat
```

Используйте **`--workers 1`**: SQLite и in-memory cancel state не рассчитаны на несколько процессов без доработок.

---

## Reverse proxy (Caddy)

Пример с базовой auth через Caddy (замените домен и хеш пароля):

```caddyfile
chat.example.com {
    basicauth /* {
        user $2a$14$HASH_FROM_caddy_hash_password
    }
    reverse_proxy 127.0.0.1:8765
}
```

Альтернативы: **nginx** + `auth_request`, **OAuth2 Proxy**, **Tailscale Serve**, **Cloudflare Access**.

---

## Reverse proxy (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name chat.example.com;

    ssl_certificate     /path/fullchain.pem;
    ssl_certificate_key /path/privkey.pem;

    auth_basic "OpenAI Chat";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Длинные ответы Pro-моделей
        proxy_read_timeout 7200s;
        proxy_send_timeout 7200s;
    }
}
```

Создание `.htpasswd`: `htpasswd -c /etc/nginx/.htpasswd user`

---

## Docker

Пример `Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py store.py ./
COPY static ./static

RUN useradd --create-home appuser
USER appuser

EXPOSE 8765
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
```

`docker-compose.yml`:

```yaml
services:
  chat:
    build: .
    ports:
      - "127.0.0.1:8765:8765"
    env_file: .env
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

Запуск:

```bash
docker compose up -d --build
```

Не публикуйте порт `8765` наружу без proxy и auth.

---

## SSH-туннель (простой удалённый доступ)

На сервере приложение слушает только localhost:

```bash
uvicorn app:app --host 127.0.0.1 --port 8765
```

На локальной машине:

```bash
ssh -L 8765:127.0.0.1:8765 user@your-server
```

Откройте http://127.0.0.1:8765 локально — трафик идёт через SSH.

---

## Бэкапы

Регулярно сохраняйте:

```bash
tar czf backup-$(date +%Y%m%d).tar.gz data/
```

В `data/`:

- `chat.sqlite` — вся история
- `billing.json` — настройки баланса

---

## Обновление

```bash
cd /opt/openai-local-chat
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart openai-local-chat
```

При `--reload` в dev UI перезагрузится сам (`/api/client-revision`). В production — restart сервиса.

---

## Мониторинг

Минимальный health check:

```bash
curl -sf http://127.0.0.1:8765/api/client-revision
```

Проверка pending jobs:

```bash
curl -s http://127.0.0.1:8765/api/jobs/pending
```

Логи Uvicorn/systemd — единственный встроенный observability; внешний APM не интегрирован.

---

## Ограничения production

- **SQLite** — один writer; не масштабируется горизонтально без смены БД.
- **ThreadPoolExecutor** — фоновые задачи в памяти процесса; при kill -9 pending jobs восстановятся при следующем старте.
- **Нет встроенного rate limiting** — добавляйте на уровне proxy или WAF.
