"""
Локальный UI для запросов к OpenAI: ключ только на сервере, не в браузере.
Загружает переменные из .env в корне проекта (и опционально из родительской папки).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import store

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent

load_dotenv(REPO_ROOT / ".env")
load_dotenv(BASE_DIR / ".env")

# Таймауты: Pro может считать очень долго; ошибки API — без лишних retry (честный ответ сразу).
TIMEOUT_DEFAULT = httpx.Timeout(connect=30.0, read=1800.0, write=90.0, pool=60.0)
TIMEOUT_PRO = httpx.Timeout(connect=60.0, read=7200.0, write=180.0, pool=120.0)
PRO_READ_TIMEOUT_SEC = 7200

_api_key_env = (os.getenv("OPENAI_API_KEY") or "").strip() or None
_request_api_key: ContextVar[str | None] = ContextVar("request_api_key", default=None)


def _api_key_from_headers(authorization: str | None, x_openai_key: str | None) -> str | None:
    auth = (authorization or "").strip()
    if auth.lower().startswith("bearer "):
        key = auth[7:].strip()
        if key:
            return key
    header = (x_openai_key or "").strip()
    return header or None


def _effective_api_key() -> str:
    key = _request_api_key.get() or _api_key_env
    if not key:
        raise HTTPException(
            status_code=401,
            detail={
                "kind": "missing_api_key",
                "message": (
                    "API-ключ не задан. Добавьте OPENAI_API_KEY в .env "
                    "или укажите ключ в настройках (хранится только в вашем браузере)."
                ),
            },
        )
    return key


def _get_client(*, timeout: httpx.Timeout | None = None) -> OpenAI:
    return OpenAI(
        api_key=_effective_api_key(),
        timeout=timeout or TIMEOUT_DEFAULT,
        max_retries=0,
    )


if not _api_key_env:
    logger.warning(
        "OPENAI_API_KEY не задан в .env — ожидается ключ клиента в заголовке Authorization."
    )

_billing_cache: dict = {"at": 0.0, "payload": None}
BILLING_CACHE_SEC = 60
BILLING_OVERVIEW_URL = "https://platform.openai.com/settings/organization/billing/overview"

# Если /v1/models недоступен — показать типичные chat-модели (можно дописать вручную)
DEFAULT_CHAT_MODEL = "gpt-4o-mini"
CLASSIFIER_MODEL = DEFAULT_CHAT_MODEL

# Авто-роутинг: приоритет моделей по ярусу (первая доступная в аккаунте).
TIER_MODEL_PRIORITY: dict[str, list[str]] = {
    "simple": [
        "gpt-4o-mini",
        "gpt-5.4-mini",
        "gpt-4.1-mini",
        "gpt-4o-mini-2024-07-18",
    ],
    "complex": [
        "gpt-5.5",
        "gpt-4o",
        "gpt-5.4",
        "gpt-4.1",
        "gpt-4.1-mini",
    ],
    "reasoning": [
        "gpt-5.5-pro",
        "gpt-5.4-pro",
        "o4-mini",
        "o3-mini",
        "gpt-5.5",
        "gpt-4o",
    ],
}

FALLBACK_CHAT_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-5.4-mini",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.5-pro",
    "gpt-5.4-pro",
    "gpt-5.4-nano",
    "gpt-4.1-mini",
    "gpt-4.1",
    "o4-mini",
    "o3-mini",
]

FALLBACK_IMAGE_MODELS = [
    "gpt-image-2",
    "gpt-image-1.5",
    "gpt-image-1",
    "gpt-image-1-mini",
    "dall-e-3",
    "dall-e-2",
]

MAX_IMAGE_BYTES_GPT = 50 * 1024 * 1024
MAX_IMAGE_BYTES_DALLE2_EDIT = 4 * 1024 * 1024
MAX_CONTEXT_FILE_BYTES = 512 * 1024
_ALLOWED_DOC_SUFFIXES = {".md", ".markdown", ".txt", ".text"}

# Исключаем не-chat эндпоинты по id.
# *-instruct → только v1/completions, не v1/chat/completions (иначе 404).
_EXCLUDE = re.compile(
    r"embed|whisper|tts|dall|moderation|realtime|transcribe|speech|audio|image|video|instruct-beta|-instruct",
    re.I,
)


def _is_likely_chat_model(model_id: str) -> bool:
    mid = model_id.lower()
    if _EXCLUDE.search(mid):
        return False
    if mid.startswith("gpt-") or mid.startswith("o1") or mid.startswith("o3") or mid.startswith("o4"):
        return True
    if mid.startswith("chatgpt-"):
        return True
    return False


def _is_image_model(model_id: str) -> bool:
    mid = model_id.lower()
    if "gpt-image" in mid or "chatgpt-image" in mid:
        return True
    return mid.startswith("dall-e")


def _validate_attachment(name: str | None, text: str | None) -> None:
    if not text or not text.strip():
        return
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="У приложенного файла нет имени.")
    suffix = Path(name.strip()).suffix.lower()
    if suffix not in _ALLOWED_DOC_SUFFIXES:
        allowed = ", ".join(sorted(_ALLOWED_DOC_SUFFIXES))
        raise HTTPException(
            status_code=400,
            detail=f"Формат «{suffix or '(без расширения)'}» не поддерживается. Допустимы: {allowed}.",
        )
    if len(text.encode("utf-8")) > MAX_CONTEXT_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Файл слишком большой (макс. {MAX_CONTEXT_FILE_BYTES // 1024} KB).",
        )


def _build_user_message(message: str, attachment_name: str | None, attachment_text: str | None) -> str:
    msg = message.strip()
    doc = (attachment_text or "").strip()
    if not doc:
        return msg
    name = (attachment_name or "document").strip()
    return (
        f"Ниже приложен файл «{name}» для изучения.\n\n"
        f"---\n{doc}\n---\n\n"
        f"Запрос пользователя:\n{msg}"
    )


def _chat_body_for_api(body: ChatBody) -> ChatBody:
    _validate_attachment(body.attachment_name, body.attachment_text)
    merged = _build_user_message(body.message, body.attachment_name, body.attachment_text)
    if merged == body.message.strip():
        return body
    return body.model_copy(
        update={
            "message": merged,
            "attachment_name": None,
            "attachment_text": None,
        }
    )


def _is_pro_model(model_id: str) -> bool:
    """gpt-*-pro, o1-pro, o3-pro — долгие запросы через Responses API."""
    return _prefer_responses_api(model_id)


def _timeout_for_model(model_id: str) -> httpx.Timeout:
    return TIMEOUT_PRO if _is_pro_model(model_id) else TIMEOUT_DEFAULT


def _openai_error_response(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, APITimeoutError):
        return HTTPException(
            status_code=504,
            detail={
                "kind": "timeout",
                "message": (
                    f"Истекло время ожидания OpenAI (лимит read: "
                    f"{PRO_READ_TIMEOUT_SEC // 3600} ч для Pro, "
                    f"{int(TIMEOUT_DEFAULT.read)} с для остальных). "
                    "Сократите запрос или повторите."
                ),
            },
        )
    if isinstance(exc, RateLimitError):
        return HTTPException(
            status_code=429,
            detail={
                "kind": "rate_limit",
                "message": "Лимит запросов OpenAI (429). Подождите и повторите.",
                "raw": exc.message,
            },
        )
    if isinstance(exc, APIConnectionError):
        return HTTPException(
            status_code=502,
            detail={
                "kind": "connection",
                "message": (
                    "Не удалось завершить соединение с api.openai.com. "
                    "Это не «нет интернета» — чаще обрыв на стороне OpenAI/прокси. "
                    "Подождите 1–2 минуты и повторите."
                ),
                "raw": exc.message,
            },
        )
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        msg = _localize_error_text(exc.message)
        kind = "api_error"
        if status == 502:
            msg = (
                "OpenAI вернул 502 (Bad Gateway). Сервер перегружен или недоступен. "
                "Подождите ~60 с и повторите."
            )
            kind = "bad_gateway"
        elif status == 503:
            msg = "OpenAI временно недоступен (503). Повторите через минуту."
            kind = "unavailable"
        elif status in (401, 403):
            kind = "forbidden"
        return HTTPException(
            status_code=status if 400 <= status < 600 else 502,
            detail={"kind": kind, "message": msg, "raw": exc.message},
        )
    raw = str(exc)
    localized = _localize_error_text(raw)
    return HTTPException(
        status_code=500,
        detail={"kind": "unknown", "message": localized, "raw": raw},
    )


def _looks_russian(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
    return cyr / len(letters) >= 0.35


def _parse_token_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(str(raw).replace(",", "").replace("_", "").strip())
    except ValueError:
        return None


def _fmt_token_count(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}".replace(",", " ") + " токенов"


def _extract_token_limit_info(text: str) -> dict | None:
    """Достаёт лимит и фактический размер из текста ошибки OpenAI."""
    raw = str(text or "")
    low = raw.lower()
    limit = actual = None

    for pat in (
        r"maximum context length (?:is|of) ([\d,_]+)",
        r"maximum context (?:window )?(?:is|of) ([\d,_]+)",
        r"context window (?:is|of) ([\d,_]+)",
        r"configured limit of ([\d,_]+)\s*tokens?",
        r"maximum (?:of )?([\d,_]+)\s*tokens?",
    ):
        m = re.search(pat, raw, re.I)
        if m:
            limit = _parse_token_int(m.group(1))
            break

    for pat in (
        r"your messages resulted in ([\d,_]+)\s*tokens?",
        r"messages resulted in ([\d,_]+)\s*tokens?",
        r"you requested ([\d,_]+)\s*tokens?",
        r"requested ([\d,_]+)\s*tokens?",
        r"resulted in ([\d,_]+)\s*tokens?",
        r"([\d,_]+)\s*tokens?\s+in your (?:messages|input|prompt)",
    ):
        m = re.search(pat, raw, re.I)
        if m:
            val = _parse_token_int(m.group(1))
            if val is not None:
                actual = val
                break

    tpm = re.search(
        r"(?:limit|tpm)\s*[:\s]*([\d,_]+)\s*[,;]?\s*(?:requested|used)\s*[:\s]*([\d,_]+)",
        raw,
        re.I,
    )
    if tpm:
        return {
            "kind": "tpm",
            "limit": _parse_token_int(tpm.group(1)),
            "actual": _parse_token_int(tpm.group(2)),
        }

    is_context = any(
        x in low
        for x in (
            "maximum context length",
            "context length exceeded",
            "context_length_exceeded",
            "context window",
            "context_length",
        )
    )
    is_tpm = "tpm" in low or "tokens per min" in low or (
        "request too large" in low and "token" in low
    )

    if is_context and (limit is not None or actual is not None):
        return {"kind": "context", "limit": limit, "actual": actual}
    if is_tpm and (limit is not None or actual is not None):
        return {"kind": "tpm", "limit": limit, "actual": actual}
    if (limit is not None or actual is not None) and any(
        x in low for x in ("too large", "too long", "exceeds", "maximum")
    ):
        return {"kind": "size", "limit": limit, "actual": actual}
    return None


def _token_limit_stats_text(info: dict) -> str:
    parts: list[str] = []
    if info.get("limit") is not None:
        parts.append(f"лимит: {_fmt_token_count(info['limit'])}")
    if info.get("actual") is not None:
        parts.append(f"получилось: {_fmt_token_count(info['actual'])}")
    return f" ({', '.join(parts)})" if parts else ""


def _message_for_token_limit(info: dict) -> str:
    stats = _token_limit_stats_text(info)
    if info.get("kind") == "tpm":
        return (
            "Запрос слишком большой для лимита токенов в минуту (TPM)"
            + stats
            + ". Сократите сообщение, уберите вложенный файл или подождите минуту."
        )
    if info.get("kind") == "size":
        return (
            "Файл или запрос слишком большой"
            + stats
            + ". Уменьшите размер и повторите."
        )
    return (
        "Сообщение не помещается в контекст выбранной модели"
        + stats
        + ". Сократите текст, уменьшите файл или начните новый чат."
    )


def _localize_error_text(text: str) -> str:
    """Переводит сообщения OpenAI и системные ошибки в понятный русский текст."""
    if not text or not str(text).strip():
        return "Произошла неизвестная ошибка. Повторите запрос."
    raw = str(text).strip()
    if _looks_russian(raw):
        return raw

    low = raw.lower()

    def has(*parts: str) -> bool:
        return all(p in low for p in parts)

    def any_of(*parts: str) -> bool:
        return any(p in low for p in parts)

    token_info = _extract_token_limit_info(raw)
    if token_info:
        return _message_for_token_limit(token_info)

    if has("request too large", "tpm") or has("tokens per min", "limit"):
        return (
            "Запрос слишком большой для лимита токенов в минуту (TPM) вашей организации. "
            "Сократите сообщение, уберите вложенный файл или выберите модель с меньшим расходом токенов."
        )
    if any_of("maximum context length", "context length exceeded", "context_length_exceeded"):
        return (
            "Сообщение не помещается в контекст выбранной модели. "
            "Сократите текст, уменьшите файл или начните новый чат."
        )
    if any_of("incorrect api key", "invalid api key", "invalid_api_key", "api key provided"):
        return (
            "Неверный API-ключ OpenAI. Проверьте OPENAI_API_KEY в .env и перезапустите сервер."
        )
    if any_of("insufficient_quota", "exceeded your current quota", "billing_hard_limit"):
        return (
            "Закончился баланс или квота OpenAI. Проверьте биллинг на platform.openai.com."
        )
    if any_of("must be verified", "organization must be verified", "verified to use the model"):
        return (
            "Для этой модели нужна верификация организации в OpenAI. "
            "Пройдите её в настройках аккаунта или выберите другую модель."
        )
    if any_of("does not have access to model", "model_not_found", "no such model") or (
        "does not exist" in low and "model" in low
    ):
        m = re.search(r"model[`'\"]? ([^`'\"]+)", raw, re.I)
        model_name = m.group(1) if m else "выбранная модель"
        return f"Модель «{model_name}» недоступна вашему API-ключу. Выберите другую модель из списка."
    if any_of("not a chat model", "not supported for chat", "v1/chat/completions"):
        return (
            "Эта модель не поддерживает обычный чат. Выберите другую модель (например gpt-4o-mini)."
        )
    if any_of("overloaded", "engine is currently overloaded", "server had an error"):
        return (
            "Серверы OpenAI перегружены. Подождите 30–60 секунд и отправьте запрос снова."
        )
    if any_of("content policy", "safety system", "flagged", "moderation"):
        return (
            "Запрос отклонён политикой безопасности OpenAI. Переформулируйте сообщение."
        )
    if any_of(
        "connection error",
        "failed to connect",
        "connection refused",
        "name or service not known",
        "connection reset",
        "remote end closed",
        "broken pipe",
    ):
        return (
            "Соединение с api.openai.com оборвалось до ответа. Это не «нет интернета» — чаще таймаут "
            "или сбой на стороне OpenAI/Cloudflare. Подождите 1–2 минуты и повторите; для Pro попробуйте "
            "gpt-5.5 без Pro или короче запрос."
        )
    if any_of("cloudflare", "origin_bad_gateway", "error 502: bad gateway"):
        return (
            "OpenAI временно недоступен (502 через Cloudflare). Подождите ~60 секунд и повторите запрос."
        )
    if any_of("unsupported_parameter", "max_completion_tokens", "max_tokens") and "not supported" in low:
        return (
            "Несовместимый параметр запроса для этой модели. Обновите страницу и повторите; "
            "если не помогло — выберите gpt-4o-mini или gpt-5.5 без Pro."
        )
    if any_of("request timed out", "timed out", "timeout"):
        return (
            "Истекло время ожидания ответа OpenAI. Для Pro-моделей ответ может идти очень долго — "
            "сократите запрос или повторите позже."
        )
    if any_of("rate limit", "429", "too many requests"):
        return (
            "Превышен лимит запросов OpenAI (слишком много обращений за короткое время). "
            "Подождите минуту и повторите."
        )
    if any_of("bad gateway", "502"):
        return (
            "OpenAI временно недоступен (ошибка 502). Подождите около минуты и повторите запрос."
        )
    if any_of("service unavailable", "503"):
        return "OpenAI временно недоступен (ошибка 503). Повторите через минуту."
    if any_of("invalid image", "image format", "unsupported image", "invalid file"):
        return (
            "Файл изображения не подходит. Используйте PNG, JPEG или WebP в допустимом размере."
        )
    if any_of("string too long", "too large", "file is too big", "exceeds the maximum"):
        return (
            "Файл или запрос слишком большой для этой операции. Уменьшите размер и повторите."
        )
    if any_of("permission", "access denied", "forbidden", "403"):
        return (
            "Доступ к этой операции запрещён вашим API-ключом или организацией. "
            "Проверьте права в аккаунте OpenAI."
        )
    if any_of("invalid_request", "invalid request"):
        return (
            "Некорректный запрос к OpenAI. Проверьте модель, вложения и параметры, затем повторите."
        )
    if any_of("internal server error", "500"):
        return (
            "Внутренняя ошибка на стороне OpenAI (500). Повторите запрос через минуту."
        )
    if any_of("ssl", "certificate"):
        return (
            "Ошибка защищённого соединения с OpenAI. Проверьте системное время и сетевые настройки."
        )
    if any_of("json", "decode", "parse"):
        return (
            "Сервер OpenAI вернул неожиданный ответ. Повторите запрос. "
            "Если ошибка повторяется — выберите другую модель."
        )

    return (
        "Не удалось выполнить запрос к OpenAI. "
        "Попробуйте другую модель, сократите сообщение или повторите через минуту."
    )


def _normalize_error_detail(detail) -> dict:
    """Приводит detail к словарю с русским message."""
    if isinstance(detail, dict):
        msg = detail.get("message")
        raw = detail.get("raw")
        token_info = _extract_token_limit_info(str(raw or msg or ""))
        if token_info:
            if token_info.get("limit") is not None:
                detail = {**detail, "tokens_limit": token_info["limit"]}
            if token_info.get("actual") is not None:
                detail = {**detail, "tokens_actual": token_info["actual"]}
        if msg is not None:
            detail = {**detail, "message": _localize_error_text(str(msg))}
        elif raw is not None and token_info:
            detail = {**detail, "message": _message_for_token_limit(token_info)}
        return detail
    if isinstance(detail, str):
        return {"kind": "http", "message": _localize_error_text(detail)}
    return {"kind": "unknown", "message": _localize_error_text(str(detail))}


def _raise_openai_error(exc: Exception) -> None:
    """Преобразует исключение в HTTPException с понятным русским текстом."""
    if isinstance(exc, HTTPException):
        raise HTTPException(status_code=exc.status_code, detail=_normalize_error_detail(exc.detail)) from exc
    raise _openai_error_response(exc)


def _prefer_responses_api(model_id: str) -> bool:
    """
    Часть моделей (gpt-5.*-pro, o*-pro и т.п.) не поддерживает v1/chat/completions —
    только Responses API. Не трогаем *-instruct (там completions).
    """
    mid = model_id.lower()
    if "-instruct" in mid:
        return False
    if "-pro" not in mid:
        return False
    if mid.startswith("gpt-") or mid.startswith("o1") or mid.startswith("o3"):
        return True
    return False


REPLY_LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "ru": (
        "Всегда отвечай на русском языке. "
        "Если пользователь явно просит другой язык — используй его."
    ),
    "en": (
        "Always respond in English. "
        "If the user explicitly asks for another language — use that language."
    ),
}


class ChatBody(BaseModel):
    model: str = Field(min_length=1)
    message: str = Field(min_length=1)
    system: str | None = None
    reply_language: str = Field(default="ru")
    routing_mode: str = Field(default="manual")
    allow_pro: bool = Field(default=False)
    attachment_name: str | None = None
    attachment_text: str | None = None


class ImageGenerateBody(BaseModel):
    prompt: str = Field(min_length=1)
    model: str = Field(default="gpt-image-1.5", min_length=1)
    size: str | None = "1024x1024"
    quality: str | None = "auto"
    n: int = Field(default=1, ge=1, le=10)
    output_format: str | None = None


def _effective_system(body: ChatBody) -> str:
    lang = (body.reply_language or "ru").strip().lower()
    lang_inst = REPLY_LANGUAGE_INSTRUCTIONS.get(lang, REPLY_LANGUAGE_INSTRUCTIONS["ru"])
    custom = (body.system or "").strip()
    if custom:
        return f"{lang_inst}\n\n{custom}"
    return lang_inst


def _body_for_openai(body: ChatBody) -> ChatBody:
    return body.model_copy(update={"system": _effective_system(body)})


def _text_from_responses(resp) -> str:
    """Собираем текст из output_text или из items output (reasoning + message)."""
    raw = (resp.output_text or "").strip()
    if raw:
        return raw
    chunks: list[str] = []
    for item in resp.output or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "output_text":
                chunks.append(getattr(part, "text", "") or "")
    return "".join(chunks).strip()


def _text_from_chat_message(msg) -> str:
    """Нормализуем content (строка, None или редко список блоков в JSON)."""
    c = msg.content
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, dict):
                if block.get("type") in ("text", "output_text"):
                    parts.append(str(block.get("text") or ""))
            else:
                t = getattr(block, "text", None)
                if t:
                    parts.append(str(t))
        return "".join(parts)
    return str(c)


def _responses_stream_api(
    kwargs: dict, timeout: httpx.Timeout, *, job_id: str | None = None
) -> dict:
    """Поток для Pro: соединение не «засыпает», ошибки приходят по событиям."""
    text_parts: list[str] = []
    _job_cancel_check(job_id)
    _mark_job_openai_started(job_id)
    with _get_client(timeout=timeout).responses.stream(**kwargs, timeout=timeout) as stream:
        for event in stream:
            etype = getattr(event, "type", None)
            if etype == "response.output_text.delta":
                text_parts.append(getattr(event, "delta", "") or "")
            elif etype == "error":
                raw_msg = getattr(event, "message", "") or "Ошибка потока OpenAI."
                raise HTTPException(
                    status_code=502,
                    detail={
                        "kind": "stream_error",
                        "message": _localize_error_text(str(raw_msg)),
                        "code": getattr(event, "code", None),
                    },
                )
            elif etype == "response.failed":
                resp = getattr(event, "response", None)
                err = getattr(resp, "error", None) if resp else None
                err_msg = getattr(err, "message", None) if err else None
                raise HTTPException(
                    status_code=502,
                    detail={
                        "kind": "response_failed",
                        "message": _localize_error_text(
                            str(err_msg or "OpenAI не смог завершить ответ.")
                        ),
                    },
                )
            elif etype == "response.incomplete":
                raise HTTPException(
                    status_code=504,
                    detail={
                        "kind": "incomplete",
                        "message": (
                            "Ответ оборвался до завершения — Pro-модель не успела закончить за отведённое "
                            "время или достигнут лимит. Сократите запрос и повторите."
                        ),
                    },
                )
        final = stream.get_final_response()

    text = _text_from_responses(final) or "".join(text_parts).strip()
    usage = final.usage.model_dump() if final.usage else None
    if not text and final.status == "completed":
        logger.warning(
            "responses(stream): пустой текст при status=completed model=%s output=%s",
            final.model,
            json.dumps([getattr(x, "type", type(x).__name__) for x in (final.output or [])]),
        )
    return {
        "reply": text,
        "model": final.model,
        "finish_reason": None,
        "usage": usage,
        "api": "responses",
        "response_status": final.status,
        "streamed": True,
    }


def _responses_api(body: ChatBody, *, job_id: str | None = None) -> dict:
    """Модели вроде gpt-5.5-pro — только /v1/responses."""
    _job_cancel_check(job_id)
    _mark_job_openai_started(job_id)
    model = body.model.strip()
    pro = _is_pro_model(model)
    timeout = _timeout_for_model(model)
    kwargs: dict = {
        "model": model,
        "input": body.message.strip(),
    }
    if body.system and body.system.strip():
        kwargs["instructions"] = body.system.strip()

    if pro:
        return _responses_stream_api(kwargs, timeout, job_id=job_id)

    resp = _get_client(timeout=timeout).responses.create(**kwargs, timeout=timeout)
    text = _text_from_responses(resp)
    usage = resp.usage.model_dump() if resp.usage else None
    if not text and resp.status == "completed":
        logger.warning(
            "responses: пустой текст при status=completed model=%s output=%s",
            resp.model,
            json.dumps([getattr(x, "type", type(x).__name__) for x in (resp.output or [])]),
        )
    return {
        "reply": text,
        "model": resp.model,
        "finish_reason": None,
        "usage": usage,
        "api": "responses",
        "response_status": resp.status,
        "streamed": False,
    }


def _completions_fallback(body: ChatBody, *, job_id: str | None = None) -> dict:
    """Модели *-instruct и часть legacy — только completions API."""
    _job_cancel_check(job_id)
    _mark_job_openai_started(job_id)
    if body.system and body.system.strip():
        prompt = f"{body.system.strip()}\n\n{body.message.strip()}"
    else:
        prompt = body.message.strip()
    resp = _get_client(timeout=_timeout_for_model(body.model.strip())).completions.create(
        model=body.model.strip(),
        prompt=prompt,
        max_tokens=4096,
        timeout=_timeout_for_model(body.model.strip()),
    )
    ch = resp.choices[0]
    text = (ch.text or "").strip()
    return {
        "reply": text,
        "model": resp.model,
        "finish_reason": ch.finish_reason,
        "usage": resp.usage.model_dump() if resp.usage else None,
        "api": "completions",
    }


def _error_dict_from_exception(exc: Exception) -> dict:
    if isinstance(exc, HTTPException):
        return _normalize_error_detail(exc.detail)
    http = _openai_error_response(exc)
    return _normalize_error_detail(http.detail)


def _pick_model_for_tier(tier: str, *, allow_pro: bool) -> str:
    if tier == "reasoning" and not allow_pro:
        tier = "complex"
    candidates = TIER_MODEL_PRIORITY.get(tier) or TIER_MODEL_PRIORITY["simple"]
    return candidates[0]


def _parse_classifier_tier(raw: str) -> str:
    low = (raw or "").strip().lower()
    if "reason" in low:
        return "reasoning"
    if "simple" in low or low == "s":
        return "simple"
    if "complex" in low:
        return "complex"
    return "complex"


def _classify_request_tier(body: ChatBody, *, job_id: str | None = None) -> dict:
    """Дешёвый классификатор на mini: simple | complex | reasoning."""
    _job_cancel_check(job_id)
    if body.attachment_text and len(body.attachment_text.strip()) > 48_000:
        return {"tier": "complex", "raw": "large_attachment", "source": "heuristic"}

    preview = body.message.strip()[:3000]
    attachment_note = ""
    if body.attachment_name:
        attachment_note = f"\n[Прикреплён файл: {body.attachment_name}]"

    system = (
        "Классифицируй сложность запроса. Ответь одним словом: simple, complex или reasoning.\n"
        "simple — короткий вопрос, перевод, черновик, факт\n"
        "complex — код, анализ, сравнение, структурированный ответ, разбор файла\n"
        "reasoning — математика, доказательства, глубокий дебаг, архитектура, многошаговая логика"
    )
    try:
        _mark_job_openai_started(job_id)
        resp = _get_client(timeout=TIMEOUT_DEFAULT).chat.completions.create(
            model=CLASSIFIER_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Запрос:\n{preview}{attachment_note}"},
            ],
            max_tokens=16,
            temperature=0,
            timeout=TIMEOUT_DEFAULT,
        )
        raw = (_text_from_chat_message(resp.choices[0].message) or "").strip()
        return {"tier": _parse_classifier_tier(raw), "raw": raw, "source": "classifier"}
    except JobCancelled:
        raise
    except Exception as exc:
        logger.warning("classifier failed: %s", exc)
        return {"tier": "complex", "raw": "classifier_failed", "source": "fallback"}


def _resolve_routed_body(body: ChatBody, *, job_id: str | None = None) -> tuple[ChatBody, dict]:
    """При routing_mode=auto подбирает модель по ярусу сложности."""
    mode = (body.routing_mode or "manual").strip().lower()
    meta: dict = {"routing_mode": mode}
    if mode != "auto":
        return body, meta

    classified = _classify_request_tier(body, job_id=job_id)
    tier = classified["tier"]
    tier_downgraded = False
    if tier == "reasoning" and not body.allow_pro:
        tier = "complex"
        tier_downgraded = True

    model = _pick_model_for_tier(tier, allow_pro=body.allow_pro)
    meta.update(
        {
            "routing_mode": "auto",
            "routing_tier": tier,
            "routing_classifier": classified.get("raw"),
            "routing_classifier_source": classified.get("source"),
            "routing_model_planned": model,
        }
    )
    if tier_downgraded:
        meta["routing_tier_downgraded"] = True

    return body.model_copy(update={"model": model}), meta


def _attach_routing_to_data(data: dict, routing_meta: dict, *, fallback: bool = False) -> None:
    if routing_meta.get("routing_mode") != "auto":
        return
    data["routing_mode"] = "auto"
    data["routing_tier"] = routing_meta.get("routing_tier")
    data["routing_classifier"] = routing_meta.get("routing_classifier")
    if routing_meta.get("routing_tier_downgraded"):
        data["routing_tier_downgraded"] = True
    if fallback:
        data["routing_fallback"] = True


def _execute_chat_with_routing(body: ChatBody, *, job_id: str | None = None) -> dict:
    """Ручной режим или авто-роутинг с одним fallback simple → complex."""
    exec_body, routing_meta = _resolve_routed_body(body, job_id=job_id)
    outcome = _execute_chat_sync(exec_body, job_id=job_id)

    if outcome.get("ok"):
        _attach_routing_to_data(outcome["data"], routing_meta)
        return outcome

    if routing_meta.get("routing_tier") != "simple":
        return outcome

    err = outcome.get("error") or {}
    kind = err.get("kind", "")
    if kind not in ("timeout", "api_error", "unknown", "stream_error", "response_failed", "incomplete"):
        return outcome

    complex_model = _pick_model_for_tier("complex", allow_pro=False)
    if complex_model == exec_body.model:
        return outcome

    _job_cancel_check(job_id)
    logger.info("routing fallback simple → complex (%s)", complex_model)
    retry = _execute_chat_sync(exec_body.model_copy(update={"model": complex_model}), job_id=job_id)
    if retry.get("ok"):
        routing_meta = {**routing_meta, "routing_tier": "complex", "routing_model_planned": complex_model}
        _attach_routing_to_data(retry["data"], routing_meta, fallback=True)
    return retry if retry.get("ok") else outcome


def _execute_chat_sync(body: ChatBody, *, job_id: str | None = None) -> dict:
    """Синхронный вызов OpenAI. Возвращает {ok, data?|error?}."""
    _job_cancel_check(job_id)
    model = body.model.strip()
    try:
        if _prefer_responses_api(model):
            return {"ok": True, "data": _responses_api(body, job_id=job_id)}

        messages: list[dict[str, str]] = []
        if body.system and body.system.strip():
            messages.append({"role": "system", "content": body.system.strip()})
        messages.append({"role": "user", "content": body.message})

        try:
            _job_cancel_check(job_id)
            _mark_job_openai_started(job_id)
            resp = _get_client(timeout=_timeout_for_model(model)).chat.completions.create(
                model=model,
                messages=messages,
                timeout=_timeout_for_model(model),
            )
        except Exception as e:
            err = str(e).lower()
            if "not a chat model" in err or "v1/completions" in err:
                mid = model.lower()
                if "-instruct" in mid:
                    return {"ok": True, "data": _completions_fallback(body, job_id=job_id)}
                try:
                    return {"ok": True, "data": _responses_api(body, job_id=job_id)}
                except Exception as e_resp:
                    try:
                        return {"ok": True, "data": _completions_fallback(body, job_id=job_id)}
                    except Exception as e2:
                        return {
                            "ok": False,
                            "error": {
                                "kind": "routing_failed",
                                "message": (
                                    "Модель не поддерживает обычный чат, и альтернативные способы "
                                    "запроса тоже не сработали. Выберите другую модель или сократите "
                                    "сообщение."
                                ),
                            },
                        }
            raise

        choice = resp.choices[0]
        content = _text_from_chat_message(choice.message)
        return {
            "ok": True,
            "data": {
                "reply": content,
                "model": resp.model,
                "finish_reason": choice.finish_reason,
                "usage": resp.usage.model_dump() if resp.usage else None,
                "api": "chat.completions",
            },
        }
    except JobCancelled:
        raise
    except HTTPException as exc:
        return {"ok": False, "error": _error_dict_from_exception(exc)}
    except Exception as exc:
        return {"ok": False, "error": _error_dict_from_exception(exc)}


_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="openai-job")
_job_cancel_events: dict[str, threading.Event] = {}
_job_openai_started: set[str] = set()
_job_cancel_lock = threading.Lock()


class JobCancelled(Exception):
    """Запрос остановлен до обращения к OpenAI — токены не тратятся."""


def _job_cancel_register(message_id: str) -> threading.Event:
    event = threading.Event()
    with _job_cancel_lock:
        _job_cancel_events[message_id] = event
    return event


def _job_cancel_signal(message_id: str) -> None:
    with _job_cancel_lock:
        event = _job_cancel_events.get(message_id)
    if event:
        event.set()


def _job_state_clear(message_id: str) -> None:
    with _job_cancel_lock:
        _job_cancel_events.pop(message_id, None)
        _job_openai_started.discard(message_id)


def _is_job_openai_started(message_id: str | None) -> bool:
    if not message_id:
        return False
    with _job_cancel_lock:
        return message_id in _job_openai_started


def _mark_job_openai_started(message_id: str | None) -> None:
    if not message_id:
        return
    with _job_cancel_lock:
        _job_openai_started.add(message_id)


def _job_cancel_check(message_id: str | None) -> None:
    if not message_id or _is_job_openai_started(message_id):
        return
    with _job_cancel_lock:
        event = _job_cancel_events.get(message_id)
    if event and event.is_set():
        raise JobCancelled()
    if store.get_message_status(message_id) == "cancelled":
        raise JobCancelled()


def _run_assistant_job(assistant_message_id: str, request_payload: dict) -> None:
    _job_cancel_register(assistant_message_id)
    try:
        _job_cancel_check(assistant_message_id)
        store.set_message_status(assistant_message_id, "running")
        _job_cancel_check(assistant_message_id)
        body = _chat_body_for_api(_body_for_openai(ChatBody(**request_payload)))
        outcome = _execute_chat_with_routing(body, job_id=assistant_message_id)
        if outcome.get("ok"):
            data = outcome["data"]
            reply = str(data.get("reply") or "")
            store.complete_assistant_message(assistant_message_id, reply=reply, result=data)
        else:
            store.fail_assistant_message(
                assistant_message_id,
                outcome.get("error") or {"kind": "unknown", "message": "Неизвестная ошибка."},
            )
    except JobCancelled:
        store.cancel_assistant_message(assistant_message_id)
        logger.info("assistant job %s cancelled", assistant_message_id)
    except HTTPException as exc:
        store.fail_assistant_message(assistant_message_id, _error_dict_from_exception(exc))
    except Exception as exc:
        logger.exception("assistant job %s failed", assistant_message_id)
        store.fail_assistant_message(assistant_message_id, _error_dict_from_exception(exc))
    finally:
        _job_state_clear(assistant_message_id)


def _schedule_assistant_job(assistant_message_id: str, request_payload: dict) -> None:
    _executor.submit(_run_assistant_job, assistant_message_id, request_payload)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    store.init_db()
    for job in store.get_resumable_jobs():
        _schedule_assistant_job(job["assistant_message_id"], job["request_payload"])
    yield
    _executor.shutdown(wait=False, cancel_futures=False)


app = FastAPI(title="OpenAI Local Chat", lifespan=_lifespan)


@app.middleware("http")
async def bind_request_api_key(request: Request, call_next):
    key = _api_key_from_headers(
        request.headers.get("authorization"),
        request.headers.get("x-openai-api-key"),
    )
    token = _request_api_key.set(key)
    try:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        _request_api_key.reset(token)


@app.get("/api/config")
def api_config():
    return {
        "deployment": "local",
        "server_key_configured": bool(_api_key_env),
        "require_client_key": not bool(_api_key_env),
        "server_sessions": True,
        "server_billing": True,
        "sync_chat": False,
        "max_duration_hint_sec": PRO_READ_TIMEOUT_SEC,
    }


class SessionCreateBody(BaseModel):
    title: str | None = None
    system: str | None = None
    model: str | None = None


@app.get("/api/models")
def api_models():
    try:
        listed = _get_client().models.list()
        ids = sorted({m.id for m in listed.data if _is_likely_chat_model(m.id)})
        if not ids:
            return {"models": FALLBACK_CHAT_MODELS, "source": "fallback_empty"}
        return {"models": ids, "source": "openai"}
    except HTTPException:
        raise
    except Exception as e:
        return {
            "models": FALLBACK_CHAT_MODELS,
            "source": "fallback_error",
            "warning": _localize_error_text(str(e)),
        }


@app.post("/api/sessions")
def api_create_session(body: SessionCreateBody = SessionCreateBody()):
    session = store.create_session(
        title=(body.title or "Новый чат").strip() or "Новый чат",
        system=body.system,
        model=body.model,
    )
    return {"session": session, "messages": []}


@app.get("/api/sessions")
def api_list_sessions(archived: bool = False):
    return {"sessions": store.list_sessions(archived=archived)}


@app.get("/api/sessions/archived/list")
def api_list_archived_sessions():
    return {"sessions": store.list_sessions(archived=True)}


@app.post("/api/sessions/{session_id}/archive")
def api_archive_session(session_id: str):
    session = store.archive_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return {"session": session}


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str):
    if not store.delete_session_permanently(session_id):
        raise HTTPException(
            status_code=400,
            detail="Удалить можно только чат из архива.",
        )
    return {"ok": True, "id": session_id}


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: str):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return {"session": session, "messages": store.get_messages(session_id)}


@app.get("/api/messages/{message_id}")
def api_get_message(message_id: str):
    message = store.get_message(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Сообщение не найдено.")
    return {"message": message}


@app.post("/api/sessions/{session_id}/messages")
def api_enqueue_message(session_id: str, body: ChatBody):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    if session.get("archived_at"):
        raise HTTPException(status_code=400, detail="Чат в архиве — отправка сообщений недоступна.")

    _validate_attachment(body.attachment_name, body.attachment_text)
    payload = body.model_dump()
    user_text = body.message.strip()

    if not session.get("title") or session["title"] == "Новый чат":
        title = user_text[:60] + ("…" if len(user_text) > 60 else "")
        store.touch_session(session_id, title=title)

    store.update_session_settings(session_id, system=body.system, model=body.model)

    queued = store.enqueue_chat(
        session_id,
        user_content=user_text,
        request_payload=payload,
        attachment_name=body.attachment_name,
    )
    _schedule_assistant_job(queued["assistant_message_id"], payload)

    return {
        "session_id": session_id,
        "user_message_id": queued["user_message_id"],
        "assistant_message_id": queued["assistant_message_id"],
        "status": queued["status"],
        "background": True,
    }


def _billing_usage_api_key() -> str | None:
    for name in ("OPENAI_ADMIN_API_KEY", "OPENAI_USAGE_API_KEY"):
        val = os.getenv(name)
        if val and val.strip():
            return val.strip()
    return None


def _unix_month_start() -> int:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    return int(start.timestamp())


def _unix_day_start() -> int:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(start.timestamp())


def _unix_day_start_at(unix_ts: int) -> int:
    dt = datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
    start = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    return int(start.timestamp())


def _sum_cost_buckets(payload: dict) -> float:
    total = 0.0
    for bucket in payload.get("data") or []:
        for result in bucket.get("results") or []:
            amount = result.get("amount") or {}
            try:
                total += float(amount.get("value") or 0)
            except (TypeError, ValueError):
                continue
    return round(total, 4)


def _fetch_costs_usd(start_unix: int, end_unix: int) -> tuple[float, str | None]:
    """Расход через Organization Costs API. Возвращает (usd, error_hint)."""
    key = _billing_usage_api_key()
    if not key:
        return 0.0, (
            "В .env нет OPENAI_ADMIN_API_KEY. Обычный OPENAI_API_KEY для чата не подходит — "
            "нужен отдельный Admin API key с правом api.usage.read (создаётся в профиле OpenAI)."
        )

    # Costs API с bucket_width=1d требует интервал не короче суток.
    if end_unix <= start_unix:
        end_unix = start_unix + 86400
    elif end_unix - start_unix < 86400:
        end_unix = start_unix + 86400

    total = 0.0
    params: dict = {
        "start_time": start_unix,
        "end_time": end_unix,
        "bucket_width": "1d",
        "limit": 180,
    }
    headers = {"Authorization": f"Bearer {key}"}
    url = "https://api.openai.com/v1/organization/costs"

    try:
        with httpx.Client(timeout=30.0) as http:
            for _ in range(20):
                resp = http.get(url, headers=headers, params=params)
                if resp.status_code in (401, 403):
                    detail = resp.text[:280]
                    return 0.0, (
                        "Ключ для биллинга не подходит (нужен scope api.usage.read). "
                        f"Ответ API: {detail}"
                    )
                resp.raise_for_status()
                data = resp.json()
                total += _sum_cost_buckets(data)
                if not data.get("has_more"):
                    break
                page = data.get("next_page")
                if not page:
                    break
                params = {"page": page}
        return round(total, 2), None
    except Exception as exc:
        logger.warning("billing costs fetch failed: %s", exc)
        return 0.0, _localize_error_text(str(exc))


def _billing_credit_base() -> dict:
    cfg = store.get_billing_config()
    if cfg.get("credit_usd") is not None:
        try:
            credit = float(cfg["credit_usd"])
            set_at = float(cfg.get("set_at_unix") or time.time())
            anchor = cfg.get("anchor_day_unix")
            baseline = cfg.get("baseline_spent_usd")
            return {
                "credit_usd": credit,
                "set_at_unix": set_at,
                "anchor_day_unix": int(anchor) if anchor is not None else _unix_day_start_at(int(set_at)),
                "baseline_spent_usd": float(baseline) if baseline is not None else None,
                "source": "local",
            }
        except (TypeError, ValueError):
            pass
    env_credit = os.getenv("OPENAI_BILLING_CREDIT_USD", "").strip()
    if env_credit:
        try:
            credit = float(env_credit)
            set_at_raw = os.getenv("OPENAI_BILLING_CREDIT_SET_AT", "").strip()
            set_at = float(set_at_raw) if set_at_raw else float(_unix_month_start())
            baseline_raw = os.getenv("OPENAI_BILLING_BASELINE_SPENT_USD", "").strip()
            baseline = float(baseline_raw) if baseline_raw else None
            anchor_raw = os.getenv("OPENAI_BILLING_ANCHOR_DAY_UNIX", "").strip()
            anchor = int(anchor_raw) if anchor_raw else _unix_day_start_at(int(set_at))
            return {
                "credit_usd": credit,
                "set_at_unix": set_at,
                "anchor_day_unix": anchor,
                "baseline_spent_usd": baseline,
                "source": "env",
            }
        except ValueError:
            pass
    return {}


def _ensure_billing_baseline(
    credit_cfg: dict,
    now_unix: int,
    *,
    anchor_cumulative: float | None = None,
    anchor_err: str | None = None,
) -> tuple[float | None, str | None]:
    """Базовый расход на момент синхронизации баланса (чтобы не вычитать траты «до сохранения»)."""
    baseline = credit_cfg.get("baseline_spent_usd")
    if baseline is not None:
        return float(baseline), None

    anchor = int(credit_cfg["anchor_day_unix"])
    if anchor_cumulative is not None and anchor_err is None:
        current = anchor_cumulative
        err = None
    else:
        current, err = _fetch_costs_usd(anchor, now_unix)
    if err:
        return None, err

    if credit_cfg.get("source") == "local":
        store.patch_billing_config({"baseline_spent_usd": current, "anchor_day_unix": anchor})
    return current, None


def _build_billing_payload() -> dict:
    now_unix = int(time.time())
    credit_cfg = _billing_credit_base()
    credit_usd = credit_cfg.get("credit_usd")
    credit_set_at = credit_cfg.get("set_at_unix")

    day_start = _unix_day_start()
    spent_month, month_err = _fetch_costs_usd(_unix_month_start(), now_unix)
    spent_today, today_err = _fetch_costs_usd(day_start, day_start + 86400)

    usage_key = _billing_usage_api_key()
    err = month_err or today_err

    payload: dict = {
        "usage_api": bool(usage_key) and not err,
        "currency": "usd",
        "overview_url": BILLING_OVERVIEW_URL,
        "updated_at": now_unix,
        "spent_month_usd": spent_month,
        "spent_today_usd": spent_today,
    }

    if credit_usd is not None and credit_set_at is not None:
        anchor_day = int(credit_cfg["anchor_day_unix"])
        payload["credit_usd"] = round(credit_usd, 2)
        payload["credit_set_at"] = int(credit_set_at)
        payload["anchor_day_unix"] = anchor_day
        payload["credit_saved"] = True

        if anchor_day == day_start and not today_err:
            current_cumulative, since_err = spent_today, None
        else:
            current_cumulative, since_err = _fetch_costs_usd(anchor_day, now_unix)
        baseline, baseline_err = _ensure_billing_baseline(
            credit_cfg,
            now_unix,
            anchor_cumulative=current_cumulative,
            anchor_err=since_err,
        )
        since_err = since_err or baseline_err

        if baseline is not None and not since_err:
            spent_since = round(max(0.0, current_cumulative - baseline), 2)
            payload["baseline_spent_usd"] = round(baseline, 2)
            payload["spent_since_credit_usd"] = spent_since
            payload["remaining_usd"] = round(max(0.0, credit_usd - spent_since), 2)
            payload["configured"] = True
        else:
            if not err:
                err = since_err or baseline_err
            payload["spent_since_credit_usd"] = 0.0
            payload["remaining_usd"] = round(credit_usd, 2)
            payload["configured"] = False
    else:
        payload["credit_saved"] = False
        payload["configured"] = False

    if err:
        payload["usage_api"] = False
        if payload.get("credit_saved"):
            payload["hint"] = err + " Баланс показан без вычета расхода."
        else:
            payload["hint"] = err
    elif not payload.get("configured"):
        payload["hint"] = (
            "Скопируйте текущий баланс с billing/overview в настройках (поле «Баланс OpenAI») — "
            "остаток = этот баланс минус расход после сохранения."
        )
    else:
        payload["hint"] = None

    return payload


@app.get("/api/billing")
def api_billing():
    global _billing_cache
    now = time.time()
    if _billing_cache["payload"] and now - _billing_cache["at"] < BILLING_CACHE_SEC:
        return _billing_cache["payload"]
    payload = _build_billing_payload()
    _billing_cache = {"at": now, "payload": payload}
    return payload


class BillingConfigBody(BaseModel):
    credit_usd: float = Field(ge=0)


@app.post("/api/billing/config")
def api_billing_config(body: BillingConfigBody):
    global _billing_cache
    now_unix = int(time.time())
    anchor_day = _unix_day_start_at(now_unix)
    baseline, baseline_err = _fetch_costs_usd(anchor_day, now_unix)
    cfg = store.set_billing_credit(
        body.credit_usd,
        baseline_spent_usd=None if baseline_err else baseline,
        anchor_day_unix=anchor_day,
    )
    _billing_cache = {"at": 0.0, "payload": None}
    out: dict = {"ok": True, "config": cfg}
    if baseline_err:
        out["warning"] = (
            "Баланс сохранён, но не удалось зафиксировать расход для отсчёта. "
            + baseline_err
        )
    return out


@app.post("/api/messages/{message_id}/cancel")
def api_cancel_message(message_id: str):
    status = store.get_message_status(message_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено.")
    if status not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail="Этот запрос уже завершён — остановить нельзя.",
        )
    if _is_job_openai_started(message_id):
        return {
            "ok": True,
            "message_id": message_id,
            "status": status,
            "already_processing": True,
            "message": "Запрос уже у OpenAI — ответ появится в чате.",
        }
    _job_cancel_signal(message_id)
    if not store.cancel_assistant_message(message_id):
        if _is_job_openai_started(message_id):
            return {
                "ok": True,
                "message_id": message_id,
                "status": "running",
                "already_processing": True,
                "message": "Запрос уже у OpenAI — ответ появится в чате.",
            }
        raise HTTPException(status_code=409, detail="Не удалось остановить запрос.")
    return {"ok": True, "message_id": message_id, "status": "cancelled"}


@app.get("/api/jobs/pending")
def api_jobs_pending():
    return {"count": store.count_pending_jobs()}


@app.get("/api/client-revision")
def api_client_revision():
    """Версия клиента для авто-обновления страницы при правках index.html / app.py."""
    rev = 0.0
    for path in (BASE_DIR / "static" / "index.html", BASE_DIR / "app.py", BASE_DIR / "store.py"):
        try:
            rev = max(rev, path.stat().st_mtime)
        except OSError:
            pass
    return {"revision": int(rev * 1000)}


@app.post("/api/chat")
def api_chat(body: ChatBody):
    """Синхронный путь (без фона). UI использует /api/sessions/.../messages."""
    body = _chat_body_for_api(_body_for_openai(body))
    outcome = _execute_chat_with_routing(body)
    if outcome.get("ok"):
        return outcome["data"]
    err = outcome.get("error") or {"kind": "unknown", "message": "Неизвестная ошибка."}
    kind = err.get("kind", "unknown")
    status = 504 if kind == "timeout" else 429 if kind == "rate_limit" else 502
    raise HTTPException(status_code=status, detail=err)


@app.get("/api/image-models")
def api_image_models():
    try:
        listed = _get_client().models.list()
        ids = sorted({m.id for m in listed.data if _is_image_model(m.id)})
        if not ids:
            return {"models": FALLBACK_IMAGE_MODELS, "source": "fallback_empty"}
        return {"models": ids, "source": "openai"}
    except Exception as e:
        return {
            "models": FALLBACK_IMAGE_MODELS,
            "source": "fallback_error",
            "warning": _localize_error_text(str(e)),
        }


def _pack_images_payload(resp, model: str, api_tag: str) -> dict:
    items: list[dict] = []
    for img in resp.data or []:
        entry: dict = {}
        if img.b64_json:
            entry["b64_json"] = img.b64_json
        if img.url:
            entry["url"] = img.url
        if img.revised_prompt:
            entry["revised_prompt"] = img.revised_prompt
        items.append(entry)
    out: dict = {
        "images": items,
        "model": model,
        "usage": resp.usage.model_dump() if resp.usage else None,
        "created": resp.created,
        "api": api_tag,
    }
    if resp.output_format:
        out["output_format"] = resp.output_format
    if resp.size:
        out["size"] = resp.size
    if resp.quality:
        out["quality"] = resp.quality
    return out


def _build_generate_kwargs(body: ImageGenerateBody) -> dict:
    mid = body.model.strip().lower()
    prompt = body.prompt.strip()
    n = body.n
    size = (body.size or "").strip() or None
    quality = (body.quality or "").strip() or None
    out_fmt = (body.output_format or "").strip().lower() or None
    kwargs: dict = {"model": body.model.strip(), "prompt": prompt}

    if mid.startswith("dall-e-3"):
        kwargs["n"] = 1
        kwargs["response_format"] = "b64_json"
        kwargs["size"] = size if size in ("1024x1024", "1792x1024", "1024x1792") else "1024x1024"
        if quality in ("hd", "standard"):
            kwargs["quality"] = quality
    elif mid.startswith("dall-e-2"):
        kwargs["n"] = min(max(n, 1), 10)
        kwargs["response_format"] = "b64_json"
        kwargs["size"] = size if size in ("256x256", "512x512", "1024x1024") else "1024x1024"
    else:
        kwargs["n"] = min(max(n, 1), 10)
        gpt_sizes = ("auto", "1024x1024", "1536x1024", "1024x1536")
        kwargs["size"] = size if size in gpt_sizes else "1024x1024"
        if quality in ("standard", "low", "medium", "high", "auto"):
            kwargs["quality"] = quality
        if out_fmt in ("png", "jpeg", "webp"):
            kwargs["output_format"] = out_fmt
    return kwargs


@app.post("/api/images/generate")
def api_images_generate(body: ImageGenerateBody):
    try:
        kwargs = _build_generate_kwargs(body)
        resp = _get_client().images.generate(**kwargs)
        return _pack_images_payload(resp, body.model.strip(), "images.generate")
    except Exception as e:
        _raise_openai_error(e)


async def _upload_tuple(upload: UploadFile, default_name: str, max_bytes: int) -> tuple[str, bytes, str]:
    raw = await upload.read()
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Файл «{upload.filename or default_name}» слишком большой (макс. {max_bytes // (1024 * 1024)} MB).",
        )
    name = upload.filename or default_name
    ct = upload.content_type or "application/octet-stream"
    return (name, raw, ct)


@app.post("/api/images/edit")
async def api_images_edit(
    prompt: str = Form(...),
    image: UploadFile = File(...),
    mask: UploadFile | None = File(None),
    model: str = Form("gpt-image-1.5"),
    size: str = Form("auto"),
    quality: str = Form("auto"),
    input_fidelity: str = Form("low"),
    n: int = Form(1),
    output_format: str | None = Form(None),
):
    mid = model.strip().lower()
    max_in = MAX_IMAGE_BYTES_DALLE2_EDIT if mid.startswith("dall-e-2") else MAX_IMAGE_BYTES_GPT
    try:
        img_tuple = await _upload_tuple(image, "input.png", max_in)
        kwargs: dict = {
            "image": img_tuple,
            "prompt": prompt.strip(),
            "model": model.strip(),
            "n": min(max(n, 1), 10),
        }
        if mid.startswith("dall-e-2"):
            kwargs["response_format"] = "b64_json"
            sz = size.strip() if size else "1024x1024"
            kwargs["size"] = sz if sz in ("256x256", "512x512", "1024x1024") else "1024x1024"
        else:
            sz = size.strip() if size else "auto"
            kwargs["size"] = sz if sz in ("auto", "1024x1024", "1536x1024", "1024x1536") else "auto"
            q = (quality or "auto").strip()
            if q in ("standard", "low", "medium", "high", "auto"):
                kwargs["quality"] = q
            of = (output_format or "").strip().lower()
            if of in ("png", "jpeg", "webp"):
                kwargs["output_format"] = of
            if "gpt-image" in mid and "mini" not in mid and input_fidelity.strip() in ("high", "low"):
                kwargs["input_fidelity"] = input_fidelity.strip()

        if mask is not None and mask.filename:
            mask_max = MAX_IMAGE_BYTES_DALLE2_EDIT if mid.startswith("dall-e-2") else MAX_IMAGE_BYTES_GPT
            kwargs["mask"] = await _upload_tuple(mask, "mask.png", mask_max)

        resp = _get_client().images.edit(**kwargs)
        return _pack_images_payload(resp, model.strip(), "images.edit")
    except HTTPException:
        raise
    except Exception as e:
        _raise_openai_error(e)


static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)

app.mount("/assets", StaticFiles(directory=static_dir), name="assets")


@app.get("/")
def index():
    return FileResponse(
        static_dir / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )
