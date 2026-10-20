"""Запрос к GigaChat: ключ читается только из .env и в браузер не попадает."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from gigachat import GigaChat
from gigachat.exceptions import (
    AuthenticationError,
    BadRequestError,
    ForbiddenError,
    GigaChatException,
    NotFoundError,
    RateLimitError,
    RequestEntityTooLargeError,
)
from gigachat.models import Chat, Messages, MessagesRole

ENV_PATH = Path(__file__).resolve().parent / ".env"


def _load_env() -> None:
    """Читает .env заново: страница могла открыться до того, как в файл вписали ключ."""
    load_dotenv(ENV_PATH, override=True)


_load_env()

DEFAULT_SCOPE = "GIGACHAT_API_PERS"
DEFAULT_MODEL = "GigaChat-2"
LEAD_TYPES = ("целевой", "нецелевой", "неясно")

SYSTEM_PROMPT = """Ты помощник менеджера компании, которая строит частные дома (ИЖС) под ключ.
По тексту заявки подготовь структурированный разбор для сотрудника. Ничего не выдумывай.

Верни только один JSON-объект без пояснений и без markdown. Форма:
{
  "company": "название компании клиента или «не указано»",
  "summary": "суть запроса в одном-двух предложениях",
  "lead_type": "целевой или нецелевой или неясно",
  "key_data": ["Параметр: значение из текста"],
  "missing_data": ["чего не хватает"],
  "next_step": "что сделать дальше",
  "reply_draft": "черновик ответа клиенту",
  "needs_human": false,
  "human_reason": "почему нужен человек или «не требуется»"
}

Тип лида, ровно одно из трёх значений:
- «целевой» — заявка про строительство частного дома, клиент назвал бюджет конкретной суммой, и виден ближний срок: старт стройки в ближайший месяц или раньше, либо прямая просьба прислать коммерческое предложение при уже указанных бюджете и параметрах дома. Такой запрос можно передать в отдел продаж.
- «нецелевой» — запрос не про строительство частного дома. Примеры: ремонт квартиры, отделка без стройки дома, покупка участка без строительства. Вопрос «сколько стоит построить дом» нецелевым не бывает: это заявка про дом, в которой не хватает данных.
- «неясно» — это строительство дома, но не хватает данных, чтобы решить уверенно. Если нет бюджета, этажности или срока — ставь «неясно». Срок вроде «март 2027» или «когда-нибудь потом» тоже не делает заявку целевой.

Правила фактов:
- Бери только то, что прямо написано в заявке. Если факта нет, пиши «не указано», а не догадку.
- Имя человека — не компания. Если компании нет, company = «не указано».
- В key_data перечисли только известные факты короткими строками «Параметр: значение». Не теряй участок, если он назван. Не пиши в key_data строки со значением «не указано». Чего в тексте нет — в key_data не добавляй. Если не известно ничего, верни ["не указано"].
- Не оценивай, «нормальный» ли бюджет по рынку, и не называй сумму неприемлемой. Неприемлемая цена — только если клиент прямо просит бесплатно или отказывается платить.
- Не обещай цену, срок работ, скидку, дату звонка и любые условия, которых нет во входном тексте. В reply_draft запрещены обещания: «рассчитаем стоимость», «назовём сроки», «оперативно рассчитаем», «свяжемся с вами», «ожидайте звонка».

Данные для полного коммерческого предложения. Это закрытый список, другие пункты не добавляй:
1. Регион или населённый пункт.
2. Участок: есть ли он и какая площадь.
3. Площадь дома.
4. Этажность.
5. Материал стен.
6. Комплектация: тёплый контур, коробка или под ключ.
7. Бюджет.
8. Срок начала строительства.
Не спрашивай фундамент, геологию, кадастровый номер, проект, состав семьи, отделку и телефон, если задача — собрать данные для КП.

Как заполнять missing_data и reply_draft для новой заявки на дом:
- В missing_data перечисли только пункты закрытого списка, которых нет в тексте. Если все восемь уже есть, верни ровно ["нет"], а не «не указано».
- reply_draft — одно готовое сообщение, которое менеджер отдела продаж может отправить клиенту.
- Если каких-то пунктов списка нет, попроси прислать только их, чтобы подготовить коммерческое предложение. Формулировка: «Чтобы подготовить коммерческое предложение, пришлите, пожалуйста: …». Не переспрашивай известное. Не обещай саму цену и срок стройки.
- Если все пункты списка уже есть, вопросов не задавай. Напиши только: данных достаточно, заявку передают в отдел продаж для подготовки коммерческого предложения.
- Если для квалификации не хватает бюджета, этажности или срока, lead_type = «неясно», а next_step — «Менеджеру отдела продаж отправить клиенту запрос недостающих данных для КП». Не ставь «целевой» и не ставь «нецелевой».

Жалоба, конфликт, угроза суда или отказа, требование возврата и пропавший сотрудник — эти правила важнее списка для КП:
- needs_human = true, lead_type = «неясно».
- human_reason объясняет жалобу или требование. Не пиши «не требуется».
- missing_data = ["нет"]. next_step = «Передать сотруднику». Не проси данные для КП и не пиши, что менеджер отдела продаж запросит параметры дома.
- Черновик: «Мы получили ваше сообщение и передали его сотруднику». Без возврата, расторжения, руководителя, сроков и «конструктивного решения».
- Если человек не нужен, needs_human = false и human_reason = «не требуется».
"""


class LeadAssistantError(Exception):
    """Ошибка, которую можно показать на странице обычными словами."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LeadAnalysis(BaseModel):
    """Карточка заявки. Пустые поля здесь не остаются пустыми."""

    model_config = ConfigDict(extra="ignore")

    company: str
    summary: str
    lead_type: str
    key_data: list[str]
    missing_data: list[str]
    next_step: str
    reply_draft: str
    needs_human: bool
    human_reason: str

    @field_validator("company", "summary", "next_step", "reply_draft", "human_reason", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        if value is None:
            return "не указано"
        text = str(value).strip()
        return text or "не указано"

    @field_validator("lead_type", mode="before")
    @classmethod
    def _lead_type(cls, value: Any) -> str:
        return _normalize_lead_type(value)

    @field_validator("key_data", "missing_data", mode="before")
    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        return _as_str_list(value)

    @field_validator("needs_human", mode="before")
    @classmethod
    def _human_flag(cls, value: Any) -> bool:
        return _as_bool(value)

    @model_validator(mode="after")
    def _reason_when_no_human(self) -> "LeadAnalysis":
        reason = self.human_reason.lower()
        if self.needs_human and reason in {"не указано", "нет", "не требуется", "—", "-"}:
            self.needs_human = False
            self.human_reason = "не требуется"
        elif not self.needs_human and reason in {"не указано", "нет", "—", "-"}:
            self.human_reason = "не требуется"
        return self


def analyze_lead(text: str) -> LeadAnalysis:
    """Отправляет текст заявки в GigaChat и возвращает карточку."""
    _load_env()
    cleaned = text.strip()
    if not cleaned:
        raise LeadAssistantError("Вставьте текст заявки или выберите тестовый пример.")

    request = Chat(
        model=_setting("GIGACHAT_MODEL", DEFAULT_MODEL),
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
            Messages(
                role=MessagesRole.USER,
                content=f"Разбери заявку и верни JSON.\n\nТекст заявки:\n{cleaned}",
            ),
        ],
        temperature=0,
        max_tokens=2000,
    )

    try:
        with _build_client() as client:
            response = client.chat(request)
    except LeadAssistantError:
        raise
    except AuthenticationError as exc:
        raise LeadAssistantError(
            "GigaChat не принял ключ доступа. Откройте файл .env и проверьте "
            "GIGACHAT_CREDENTIALS и GIGACHAT_SCOPE. Ключ в чат отправлять не нужно."
        ) from exc
    except ForbiddenError as exc:
        raise LeadAssistantError(
            "Доступ к GigaChat запрещён. Проверьте GIGACHAT_SCOPE в файле .env: "
            "для физического лица обычно GIGACHAT_API_PERS, для компании — "
            "GIGACHAT_API_B2B или GIGACHAT_API_CORP."
        ) from exc
    except NotFoundError as exc:
        raise LeadAssistantError(
            "Указанная модель не найдена. В файле .env проверьте GIGACHAT_MODEL. "
            "Частый вариант — GigaChat-2."
        ) from exc
    except RateLimitError as exc:
        raise LeadAssistantError(
            "GigaChat получил слишком много запросов. Подождите минуту и нажмите «Обработать» ещё раз."
        ) from exc
    except RequestEntityTooLargeError as exc:
        raise LeadAssistantError("Текст заявки слишком длинный. Сократите его и повторите.") from exc
    except BadRequestError as exc:
        raise LeadAssistantError(
            "GigaChat не принял запрос. Проверьте в файле .env значение GIGACHAT_SCOPE "
            "(для физического лица — GIGACHAT_API_PERS) и GIGACHAT_MODEL (часто GigaChat-2)."
        ) from exc
    except GigaChatException as exc:
        if _is_ssl_error(exc):
            raise LeadAssistantError(_ssl_message()) from exc
        raise LeadAssistantError(
            "Сервис GigaChat вернул ошибку. Проверьте интернет и файл .env, затем повторите."
        ) from exc
    except Exception as exc:
        if _is_ssl_error(exc):
            raise LeadAssistantError(_ssl_message()) from exc
        if _is_timeout(exc):
            raise LeadAssistantError(
                "GigaChat не ответил вовремя. Проверьте интернет и нажмите «Обработать» ещё раз."
            ) from exc
        if _is_connection_error(exc):
            raise LeadAssistantError(
                "Не удалось связаться с GigaChat. Проверьте подключение к интернету и повторите."
            ) from exc
        raise LeadAssistantError(
            "Не удалось обработать заявку. Проверьте интернет и файл .env, затем повторите."
        ) from exc

    try:
        analysis = analysis_from_text(_response_text(response))
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise LeadAssistantError(
            "Модель вернула ответ в неожиданном виде. Нажмите «Обработать» ещё раз."
        ) from exc

    return _prepare_for_manager(cleaned, analysis)


def analysis_from_text(raw: str) -> LeadAnalysis:
    """Достаёт JSON из ответа модели и проверяет поля."""
    payload = _extract_json(raw)
    if not isinstance(payload, dict):
        raise ValueError("Ответ модели не является объектом JSON.")
    return LeadAnalysis.model_validate(payload)


def _build_client() -> GigaChat:
    credentials = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    if not credentials:
        raise LeadAssistantError(
            "Не найден ключ GigaChat. Скопируйте .env.example в файл .env рядом с приложением "
            "и впишите свой Authorization Key в GIGACHAT_CREDENTIALS. Ключ в чат отправлять не нужно."
        )

    ca_bundle = os.getenv("GIGACHAT_CA_BUNDLE_FILE", "").strip()
    if ca_bundle and not Path(ca_bundle).is_file():
        raise LeadAssistantError(
            "Файл сертификата из GIGACHAT_CA_BUNDLE_FILE не найден. "
            "Проверьте путь или оставьте эту строку пустой, а GIGACHAT_VERIFY_SSL_CERTS=false."
        )

    return GigaChat(
        credentials=credentials,
        scope=_setting("GIGACHAT_SCOPE", DEFAULT_SCOPE),
        model=_setting("GIGACHAT_MODEL", DEFAULT_MODEL),
        timeout=_timeout(),
        verify_ssl_certs=_verify_ssl(ca_bundle),
        ca_bundle_file=ca_bundle or None,
    )


def _setting(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _timeout() -> float:
    raw = os.getenv("GIGACHAT_TIMEOUT", "").strip()
    if not raw:
        return 90.0
    try:
        timeout = float(raw)
    except ValueError:
        return 90.0
    return timeout if timeout > 0 else 90.0


def _verify_ssl(ca_bundle: str) -> bool:
    raw = os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "").strip().lower()
    if raw in {"1", "true", "yes", "y", "да"}:
        return True
    if raw in {"0", "false", "no", "n", "нет"}:
        return False
    return bool(ca_bundle)


def _ssl_message() -> str:
    return (
        "Не удалось установить защищённое соединение с GigaChat. "
        "В файле .env оставьте GIGACHAT_VERIFY_SSL_CERTS=false "
        "или укажите путь к корневому сертификату в GIGACHAT_CA_BUNDLE_FILE. "
        "Как это сделать, написано в README."
    )


def _response_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("В ответе нет текста.") from exc
    text = str(content or "").strip()
    if not text:
        raise ValueError("Пустой ответ модели.")
    return text


def _extract_json(raw: str) -> Any:
    cleaned = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    for candidate in (cleaned, _relax_json(cleaned)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise json.JSONDecodeError("В ответе нет JSON-объекта", cleaned, 0)
    sliced = cleaned[start : end + 1]
    for candidate in (sliced, _relax_json(sliced)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("Не удалось прочитать JSON", sliced, 0)


def _relax_json(text: str) -> str:
    """Чинит частую ошибку модели: в списке написаны пары «ключ: значение» без фигурных скобок."""
    without_trailing_comma = re.sub(r",\s*([}\]])", r"\1", text)

    def fix_array(match: re.Match[str]) -> str:
        body = match.group(1)
        if not re.search(r'"[^"]+"\s*:', body):
            return match.group(0)
        fixed = re.sub(
            r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
            lambda pair: json.dumps(f"{pair.group(1)}: {pair.group(2)}", ensure_ascii=False),
            body,
        )
        return f"[{fixed}]"

    return re.sub(r"\[([^\[\]]*)\]", fix_array, without_trailing_comma, flags=re.DOTALL)


def _normalize_lead_type(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    if "нецелев" in text or text in {"nql", "unqualified"}:
        return "нецелевой"
    if "неясн" in text or text in {"unclear", "unknown"}:
        return "неясно"
    if "целев" in text or text in {"ql", "qualified"}:
        return "целевой"
    raise ValueError("Неизвестный тип лида.")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value or "").strip().lower().replace("ё", "е")
    if text in {"да", "yes", "true", "1", "нужен", "нужно"}:
        return True
    if text in {"нет", "no", "false", "0", "не нужен", "не нужно", "не требуется"}:
        return False
    raise ValueError("Непонятное значение needs_human.")


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return ["не указано"]
    if isinstance(value, str):
        parts = [line.strip(" -•\t") for line in re.split(r"[\n;]+", value) if line.strip()]
        return parts or ["не указано"]
    if isinstance(value, dict):
        items = [f"{key}: {item}".strip() for key, item in value.items() if str(item).strip()]
        return items or ["не указано"]
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, dict):
                items.extend(part for part in _as_str_list(item) if part != "не указано")
            else:
                text = str(item).strip()
                if text:
                    items.append(text)
        return items or ["не указано"]
    text = str(value).strip()
    return [text] if text else ["не указано"]


def _prepare_for_manager(text: str, analysis: LeadAnalysis) -> LeadAnalysis:
    """Оставляет разбор модели и поправляет карточку, если черновик обещает лишнее или не спрашивает пробелы для КП."""
    analysis = _escalate_conflict(text, analysis)
    if analysis.needs_human:
        return analysis

    lowered = text.lower().replace("ё", "е")
    about_house = any(
        phrase in lowered
        for phrase in ("построить дом", "строительств", "стоит дом", "стоимость дом", "дом под ключ", "теплый контур", "тёплый контур")
    ) or (
        "дом" in lowered
        and any(word in lowered for word in ("м2", "м²", "кв", "газобетон", "проект", "прект", "стоимость", "этаж", " тк"))
    )
    other_topic = any(phrase in lowered for phrase in ("квартир", "участок без"))
    if about_house and not other_topic and analysis.lead_type == "нецелевой":
        analysis.lead_type = "неясно"

    known = [item for item in analysis.key_data if "не указан" not in item.lower()]
    analysis.key_data = known or ["не указано"]

    if analysis.lead_type == "нецелевой":
        analysis.missing_data = ["нет"]
        analysis.next_step = "Не передавать в отдел продаж: запрос не про строительство частного дома."
        draft = analysis.reply_draft.lower()
        if any(phrase in draft for phrase in ("отдел продаж", "коммерческ", "пришлите", "рассчита")):
            analysis.reply_draft = (
                "Здравствуйте! Мы занимаемся строительством частных домов. "
                "Этот запрос к нашей работе не относится."
            )
        return analysis

    gaps = _checklist_gaps(lowered)
    if gaps and analysis.lead_type == "целевой":
        analysis.lead_type = "неясно"
    analysis.missing_data = gaps or ["нет"]
    if gaps:
        analysis.next_step = "Менеджеру отдела продаж отправить клиенту запрос недостающих данных для КП."
    else:
        analysis.next_step = "Передать в отдел продаж для подготовки коммерческого предложения."
    analysis.reply_draft = _draft_for_gaps(gaps)
    return analysis


def _checklist_gaps(text: str) -> list[str]:
    """Каких пунктов закрытого списка для КП нет в тексте заявки."""
    padded = f" {text} "
    checks = (
        (
            "Регион или населённый пункт",
            re.search(r"област|москов|петербург|краснодар|деревн|дер\.|посел|район|\bмо\b", text) is not None,
        ),
        ("Участок: есть ли он и какая площадь", "участок" in text or "соток" in text or "сотк" in text),
        ("Площадь дома", re.search(r"\d+\s*(м2|м²|кв)", text) is not None or "площад" in text),
        ("Этажность", "этаж" in text),
        (
            "Материал стен",
            any(word in text for word in ("газобетон", "кирпич", "брус", "каркас", "керамзит", "пеноблок")),
        ),
        (
            "Комплектация: тёплый контур, коробка или под ключ",
            any(word in text for word in ("тепл", "контур", "под ключ", "коробк")) or " тк " in padded,
        ),
        ("Бюджет", re.search(r"бюджет|\d+\s*млн|руб", text) is not None),
        ("Срок начала строительства", re.search(r"срок|месяц|старт|начал|202\d", text) is not None),
    )
    return [label for label, present in checks if not present]


def _draft_for_gaps(gaps: list[str]) -> str:
    if not gaps:
        return (
            "Здравствуйте! Данных достаточно для подготовки коммерческого предложения. "
            "Передаём заявку в отдел продаж."
        )
    return (
        "Здравствуйте! Чтобы подготовить коммерческое предложение, пришлите, пожалуйста: "
        + "; ".join(gaps)
        + "."
    )


def _escalate_conflict(text: str, analysis: LeadAnalysis) -> LeadAnalysis:
    """Жалоба и угроза суда не должны уходить в сбор данных для КП."""
    lowered = text.lower().replace("ё", "е")
    conflict = any(marker in lowered for marker in ("суд", "возврат", "расторг", "претенз"))
    if not conflict:
        return analysis

    analysis.needs_human = True
    analysis.missing_data = ["нет"]
    analysis.next_step = "Передать сотруднику. Не запрашивать данные для коммерческого предложения."
    if analysis.lead_type == "целевой":
        analysis.lead_type = "неясно"
    if analysis.human_reason.lower() in {"не указано", "нет", "не требуется", "—", "-"}:
        analysis.human_reason = (
            "В тексте есть жалоба, угроза суда или требование возврата. Такое обращение разбирает сотрудник."
        )
    if any(phrase in analysis.reply_draft.lower() for phrase in ("конструктивн", "возврат", "расторг", "руководитель", "свяжемся", "на почту")):
        analysis.reply_draft = "Мы получили ваше сообщение и передали его сотруднику."
    return analysis


def _is_ssl_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = f"{type(current).__name__} {current}".lower()
        if "certificate" in text or "ssl" in text or "tls" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _is_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timeout" in str(exc).lower()


def _is_connection_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    return "connect" in name or "network" in name
