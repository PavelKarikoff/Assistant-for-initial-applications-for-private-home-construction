"""Запись разобранной заявки в Битрикс24 через входящий вебхук.

Адрес вебхука читается из B24_URL и в браузер, журнал и текст ошибок не попадает.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
SOURCE_NOTE = "Помощник по заявкам на строительство домов"
_EMPTY_COMPANY = {"не указано", "не указан", "нет", "—", "-", ""}
_PATRONYMIC_ENDINGS = ("ович", "евич", "овна", "евна", "ична", "инична", "ич")


class BitrixError(Exception):
    """Ошибка Битрикс24, которую можно показать на странице."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class CrmPublishResult:
    """Куда легла заявка. Идентификатор нужен только для сообщения на странице."""

    entity_label: str
    entity_id: int
    warning: str | None = None


def publish_application(
    *,
    name: str,
    phone: str,
    email: str,
    request_text: str,
    analysis: dict[str, Any],
) -> CrmPublishResult:
    """Создаёт лид и кладёт карточку разбора в таймлайн."""
    base_url, user_id = _webhook()
    fields = _lead_fields(name, phone, email, analysis)
    created = _call(base_url, "crm.item.add", {"entityTypeId": 1, "fields": fields})
    lead_id = _item_id(created)
    stage = _stage(base_url, lead_id, created)

    entity_type = "lead"
    entity_id = lead_id
    entity_label = "лид"
    binding = f"L_{lead_id}"
    warnings: list[str] = []

    if stage == "CONVERTED":
        deal_id = _find_deal(base_url, lead_id)
        if deal_id is None:
            warnings.append(
                "Лид сразу конвертировался в сделку, но саму сделку найти не удалось. "
                "Карточка добавлена к лиду."
            )
        else:
            entity_type = "deal"
            entity_id = deal_id
            entity_label = "сделка"
            binding = f"D_{deal_id}"

    if not _step(
        warnings,
        "Карточка в таймлайне не сохранилась",
        lambda: _call(
            base_url,
            "crm.timeline.comment.add",
            {
                "fields": {
                    "ENTITY_ID": entity_id,
                    "ENTITY_TYPE": entity_type,
                    "COMMENT": _comment(request_text, analysis),
                }
            },
        ),
    ):
        pass

    if analysis.get("needs_human"):
        _step(
            warnings,
            "Задача сотруднику не создана",
            lambda: _call(
                base_url,
                "tasks.task.add",
                {
                    "fields": {
                        "TITLE": _clip(str(analysis.get("next_step") or "Разобрать заявку"), 240),
                        "DESCRIPTION": str(analysis.get("human_reason") or ""),
                        "RESPONSIBLE_ID": user_id,
                        "UF_CRM_TASK": [binding],
                    }
                },
            ),
        )

    title = fields["title"]
    lead_type = str(analysis.get("lead_type") or "неясно")
    _step(
        warnings,
        "Уведомление сотруднику не отправлено",
        lambda: _call(
            base_url,
            "im.notify.system.add",
            {
                "USER_ID": user_id,
                "MESSAGE": (
                    f"Новая заявка: {title}. Тип: {lead_type}. "
                    f"{entity_label[:1].upper()}{entity_label[1:]} №{entity_id}."
                ),
            },
        ),
    )

    warning = " ".join(warnings) if warnings else None
    return CrmPublishResult(entity_label=entity_label, entity_id=entity_id, warning=warning)


def _webhook() -> tuple[str, int]:
    load_dotenv(ENV_PATH, override=True)
    raw = os.getenv("B24_URL", "").strip().strip('"').strip("'")
    if not raw:
        raise BitrixError(
            "В файле .env не заполнен B24_URL. Карточка на странице сохранена, "
            "в Битрикс24 заявка не отправлена."
        )
    parsed = urlparse(raw)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or "rest" not in parts
        or len(parts) < parts.index("rest") + 3
    ):
        raise BitrixError(
            "B24_URL в файле .env не похож на входящий вебхук Битрикс24. "
            "Карточка на странице сохранена, в CRM заявка не отправлена."
        )
    user_index = parts.index("rest") + 1
    user_raw = parts[user_index]
    token = parts[user_index + 1]
    if not user_raw.isdigit() or not token:
        raise BitrixError(
            "B24_URL в файле .env не похож на входящий вебхук Битрикс24. "
            "Карточка на странице сохранена, в CRM заявка не отправлена."
        )
    base = f"https://{parsed.netloc}/rest/{user_raw}/{token}/"
    return base, int(user_raw)


def _lead_fields(name: str, phone: str, email: str, analysis: dict[str, Any]) -> dict[str, Any]:
    lead_type = str(analysis.get("lead_type") or "неясно")
    fields: dict[str, Any] = {
        "title": _clip(f"Заявка: {name.strip()} — {lead_type}", 240),
        "sourceDescription": SOURCE_NOTE,
        "fm": [
            {"typeId": "PHONE", "valueType": "WORK", "value": phone.strip()},
            {"typeId": "EMAIL", "valueType": "WORK", "value": email.strip()},
        ],
    }
    fields.update(_split_name(name))
    company = str(analysis.get("company") or "").strip()
    if company.casefold() not in _EMPTY_COMPANY:
        fields["companyTitle"] = _clip(company, 240)
    return fields


def _split_name(full_name: str) -> dict[str, str]:
    parts = full_name.split()
    if not parts:
        return {}
    if len(parts) == 1:
        return {"name": parts[0]}
    if len(parts) == 2:
        return {"name": parts[0], "lastName": parts[1]}
    if _is_patronymic(parts[2]) and not _is_patronymic(parts[1]):
        return {"lastName": parts[0], "name": parts[1], "secondName": " ".join(parts[2:])}
    return {"name": parts[0], "secondName": parts[1], "lastName": " ".join(parts[2:])}


def _is_patronymic(word: str) -> bool:
    folded = word.casefold()
    return len(folded) > 4 and folded.endswith(_PATRONYMIC_ENDINGS)


def _comment(request_text: str, analysis: dict[str, Any]) -> str:
    needs_human = "Да" if analysis.get("needs_human") else "Нет"
    lines = [
        f"Тип лида: {analysis.get('lead_type') or 'неясно'}",
        f"Нужен человек: {needs_human}",
        f"Компания: {analysis.get('company') or 'не указано'}",
        f"Суть запроса: {analysis.get('summary') or 'не указано'}",
        "Известные данные:",
        *_bullets(analysis.get("key_data")),
        "Чего не хватает:",
        *_bullets(analysis.get("missing_data")),
        f"Следующий шаг: {analysis.get('next_step') or 'не указано'}",
        f"Почему нужен человек: {analysis.get('human_reason') or 'не требуется'}",
        "Черновик ответа:",
        str(analysis.get("reply_draft") or "не указано"),
        "Текст заявки:",
        request_text.strip() or "не указано",
    ]
    return _clip("\n".join(lines), 12000)


def _bullets(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["• не указано"]
    return [f"• {item}" for item in value if str(item).strip()]


def _stage(base_url: str, lead_id: int, created: Any) -> str:
    stage = _stage_of(created)
    if stage:
        return stage
    fetched = _call(base_url, "crm.item.get", {"entityTypeId": 1, "id": lead_id})
    return _stage_of(fetched)


def _stage_of(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    item = payload.get("item")
    if not isinstance(item, dict):
        return ""
    return str(item.get("stageId") or item.get("statusId") or "")


def _find_deal(base_url: str, lead_id: int) -> int | None:
    try:
        listed = _call(
            base_url,
            "crm.item.list",
            {"entityTypeId": 2, "select": ["id"], "filter": {"leadId": lead_id}},
        )
    except BitrixError:
        listed = None
    deal_id = _first_id(listed, "items", "id")
    if deal_id is not None:
        return deal_id
    try:
        deals = _call(
            base_url,
            "crm.deal.list",
            {"filter": {"LEAD_ID": lead_id}, "select": ["ID"]},
        )
    except BitrixError:
        return None
    return _first_id(deals, None, "ID")


def _first_id(payload: Any, list_key: str | None, id_key: str) -> int | None:
    rows: Any
    if list_key is None:
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get(list_key)
    else:
        rows = None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    raw = rows[0].get(id_key) or rows[0].get(id_key.lower()) or rows[0].get("id")
    if raw in (None, ""):
        return None
    return int(raw)


def _item_id(payload: Any) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("item"), dict):
        raise BitrixError("Битрикс24 создал лид, но не вернул его номер.")
    raw = payload["item"].get("id")
    if raw in (None, ""):
        raise BitrixError("Битрикс24 создал лид, но не вернул его номер.")
    return int(raw)


def _step(warnings: list[str], label: str, action: Any) -> bool:
    try:
        action()
    except BitrixError as exc:
        warnings.append(f"{label}. {exc.message}")
        return False
    return True


def _call(base_url: str, method: str, payload: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        f"{base_url}{method}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return _parse(raw, base_url, http_failed=True)
    except urllib.error.URLError:
        raise BitrixError(
            "Не удалось связаться с Битрикс24. Проверьте интернет и повторите обработку."
        )
    except TimeoutError:
        raise BitrixError("Битрикс24 не ответил вовремя. Повторите обработку через минуту.")
    return _parse(raw, base_url, http_failed=False)


def _parse(raw: bytes, base_url: str, *, http_failed: bool) -> Any:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BitrixError("Битрикс24 вернул ответ в неожиданном виде.")
    if not isinstance(data, dict):
        raise BitrixError("Битрикс24 вернул ответ в неожиданном виде.")
    error = data.get("error")
    if error or http_failed:
        raise BitrixError(_error_message(str(error or ""), str(data.get("error_description") or ""), base_url))
    return data.get("result")


def _error_message(code: str, description: str, base_url: str) -> str:
    known = {
        "expired_token": "Срок вебхука Битрикс24 истёк. Обновите B24_URL в файле .env.",
        "INVALID_CREDENTIALS": "Битрикс24 не принял вебхук. Проверьте B24_URL в файле .env.",
        "NO_AUTH_FOUND": "Битрикс24 не принял вебхук. Проверьте B24_URL в файле .env.",
        "insufficient_scope": "У вебхука не хватает прав. Нужны CRM, задачи и уведомления.",
        "ACCESS_DENIED": "У сотрудника, создавшего вебхук, не хватает прав на это действие в CRM.",
        "QUERY_LIMIT_EXCEEDED": "Битрикс24 ограничил частоту запросов. Подождите минуту и повторите.",
        "OPERATION_TIME_LIMIT": "Битрикс24 не успел обработать запрос. Повторите через минуту.",
    }
    if code in known:
        return known[code]
    safe = _redact(description, base_url)
    if safe and "http" not in safe.casefold() and len(safe) <= 180:
        return f"Битрикс24 вернул ошибку: {safe}"
    return "Битрикс24 вернул ошибку. Карточка на странице сохранена."


def _redact(text: str, base_url: str) -> str:
    token = [part for part in urlparse(base_url).path.split("/") if part]
    secret = token[-1] if token else ""
    cleaned = text.replace(base_url, "")
    if secret:
        cleaned = cleaned.replace(secret, "")
    return " ".join(cleaned.split())


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split()) if "\n" not in text else text.strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"
