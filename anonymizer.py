"""Подмена персональных данных токенами в рамках одного запроса.

Словарь «токен → исходное значение» живёт только в объекте, который вернула
функция anonymize. Его не пишут в файл, сессию и журнал.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_EMPTY_VALUES = {
    "не указано",
    "не указан",
    "нет",
    "неизвестно",
    "—",
    "-",
}

_NAME_STOP = {
    "августе",
    "апреле",
    "бюджете",
    "газобетоне",
    "году",
    "года",
    "декабре",
    "день",
    "договор",
    "договоре",
    "доме",
    "дому",
    "дома",
    "завтра",
    "здравствуйте",
    "ипотеке",
    "июле",
    "июне",
    "январе",
    "кирпиче",
    "ключе",
    "контуре",
    "конце",
    "коробке",
    "крае",
    "мае",
    "марте",
    "месяц",
    "месяце",
    "начале",
    "ноябре",
    "области",
    "октябре",
    "отделе",
    "письме",
    "потом",
    "предложении",
    "проекте",
    "продаже",
    "работе",
    "работу",
    "районе",
    "рублях",
    "сегодня",
    "сентябре",
    "смете",
    "смету",
    "строительстве",
    "суде",
    "участка",
    "участке",
    "участки",
    "феврале",
    "этом",
    "этой",
    "этот",
}

_PLACE_STOP = _NAME_STOP | {
    "другой",
    "другая",
    "какой",
    "какая",
    "какое",
    "ключе",
    "любой",
    "любая",
    "нашей",
    "одной",
    "следующем",
    "следующий",
    "следующей",
    "своей",
    "такой",
    "такая",
    "такое",
    "том",
    "той",
    "вашей",
    "всей",
    "всякой",
}

_REGION_STOP = {
    "какой",
    "какая",
    "какое",
    "такой",
    "такая",
    "такое",
    "всякой",
    "любой",
    "любая",
    "одной",
    "другой",
    "другая",
    "этой",
    "нашей",
    "вашей",
    "своей",
    "всей",
}


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    kind: str
    value: str


_PRIORITY = {
    "ПОЧТА": 100,
    "ТЕЛЕФОН": 90,
    "КАРТА": 86,
    "СНИЛС": 85,
    "ПАСПОРТ": 84,
    "ИНН": 83,
    "СЧЁТ": 82,
    "ДАТА_РОЖДЕНИЯ": 80,
    "КАДАСТР": 75,
    "АДРЕС": 70,
    "АВТО": 60,
    "НИК": 55,
    "ИМЯ": 50,
}

_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-zА-Яа-яЁё]{2,}"
)
_PHONE = re.compile(r"(?<!\d)(?:\+7|8|7)(?:[\s\-().]*\d){10}(?!\d)")
_PHONE_LABELED = re.compile(
    r"(?im)^[ \t]*(?:телефон|тел\.?)[ \t]*:[ \t]*(.+)$"
)
_EMAIL_LABELED = re.compile(
    r"(?im)^[ \t]*(?:почта|e-mail|email|эл\.?\s*почта)[ \t]*:[ \t]*(.+)$"
)
_NAME_LABELED = re.compile(r"(?im)^[ \t]*(?:имя|фио)[ \t]*:[ \t]*(.+)$")
_NAME_WORD = r"[А-ЯЁ][а-яёЁё]+"
_PATRONYMIC = rf"{_NAME_WORD}(?:ович|евич|ич|овна|евна|ична|инична)"
_SURNAME = (
    rf"{_NAME_WORD}(?:ова|ева|ёва|ина|ына|ский|цкий|ская|цкая|ских|цких|"
    rf"енко|ов|ев|ёв|ин|ын|юк|ук)"
)
_NAME_INTRO = re.compile(
    r"(?i)(?:меня\s+зовут|мо[её]\s+имя|на\s+имя|клиент|заказчик|заявитель|собственник)\s+"
    rf"([А-ЯЁA-Z][а-яёa-zЁё]+(?:\s+[А-ЯЁA-Z][а-яёa-zЁё]+){{0,2}})"
)
# Имя и отчество могут стоять и перед фамилией, и после неё.
_FIO = re.compile(
    rf"\b(?:{_NAME_WORD}\s+{_PATRONYMIC}(?:\s+{_NAME_WORD})?|"
    rf"{_SURNAME}\s+{_NAME_WORD}\s+{_PATRONYMIC}|"
    rf"{_NAME_WORD}\s+{_NAME_WORD}\s+{_PATRONYMIC})\b"
)
_NAME_SURNAME = re.compile(
    rf"\b(?:{_NAME_WORD}\s+{_SURNAME}|{_SURNAME}\s+{_NAME_WORD})\b"
)
_INITIALS = re.compile(
    rf"\b(?:[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*{_SURNAME}|{_SURNAME}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.)"
)
_ADDRESS_LABELED = re.compile(
    r"(?im)^[ \t]*(?:адрес|место(?:\s+строительства)?|локация|"
    r"населённый\s+пункт|населенный\s+пункт)[ \t]*:[ \t]*(.+)$"
)
_REGION = re.compile(
    r"(?<!\w)[А-ЯЁа-яёЁ]{4,}(?:ая|ой|ий|ый|ое)\s+област(?:ь|и|ью)"
    r"|(?<!\w)[А-ЯЁа-яёЁ]+(?:ский|ская|ской|ском)\s+кра(?:й|я|е|ю)"
    r"|(?<!\w)[Рр]еспублик(?:а|е|и|у|ой)\s+[А-ЯЁ][а-яёЁё\-]+",
    re.IGNORECASE,
)
_LOCALITY = re.compile(
    r"(?<!\w)(?:г\.|город|посёлок|поселок|пгт|село|деревня|дер\.|п\.|с\.|мкр\.|микрорайон)"
    r"\s*[А-ЯЁ][а-яёЁё\-]{2,}"
)
_FEDERAL_CITY = re.compile(
    r"(?<!\w)(?:г\.\s*)?(?:Москв(?:а|е|у|ы|ой|ою)?|"
    r"Санкт-Петербург(?:а|е|у|ом)?|Петербург(?:а|е|у|ом)?|Севастопол(?:ь|я|е|ю|ем)?)(?!\w)"
)
_STREET = re.compile(
    r"(?<!\w)(?:улиц[аеуы]|ул\.|проспект[аеу]?|пр-т|переул(?:ок|ка|ке)|пер\.|шоссе|ш\.|"
    r"набережн(?:ая|ой)|наб\.|бульвар[аеу]?|б-р|проезд[аеу]?)\s+"
    r"[А-ЯЁа-яёЁё0-9][\w\-ёЁ]*"
    r"(?:\s*,?\s*(?:д\.|дом)\s*\d+[а-яa-zА-Я]?)?"
    r"(?:\s*,?\s*(?:корп\.|корпус|кв\.|квартира|стр\.|строение)\s*\d+){0,3}",
    re.IGNORECASE,
)
_PLACE_AFTER = re.compile(
    r"(?<!\w)(?:[Вв]о?|[Ии]з|[Пп]од|[Оо]коло|[Вв]озле|[Пп]о)\s+"
    r"([А-ЯЁ][а-яёЁё\-]{3,}(?:\s+[А-ЯЁ][а-яёЁё\-]{3,})?)"
)
_BIRTH = re.compile(
    r"(?i)(?:дата\s+рождения|д\.?\s*р\.?|родил(?:ся|ась))\s*[:\-]?\s*"
    r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})"
)
_PASSPORT = re.compile(
    r"(?i)паспорт(?:\s*(?:рф|гражданина))?\s*[:№]?\s*"
    r"(\d{2}\s?\d{2}\s?\d{6}|\d{4}\s?\d{6})"
)
_SNILS = re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}\s\d{2}(?!\d)")
_INN = re.compile(r"(?i)инн\s*[:№]?\s*(\d{10}(?:\d{2})?)(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:(?:\d{4}[\s\-]){3}\d{4}|\d{16})(?!\d)")
_ACCOUNT = re.compile(
    r"(?i)(?:р/с|к/с|расч[её]тн(?:ый|ого)\s+сч[её]т|лицевой\s+сч[её]т)\s*[:№]?\s*(\d{20})"
)
_CADASTRE = re.compile(r"\b\d{2}:\d{2}:\d{6,7}:\d+\b")
_PLATE = re.compile(
    r"(?<!\w)[АВЕКМНОРСТУХ]\s?\d{3}\s?[АВЕКМНОРСТУХ]{2}\s?\d{2,3}(?!\w)",
    re.IGNORECASE,
)
_NICK = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}")


def anonymize(text: str) -> tuple[str, dict[str, str]]:
    """Возвращает текст с токенами и словарь только для этого вызова."""
    spans = _merge_addresses(text, _resolve(_find_spans(text)))
    mapping: dict[str, str] = {}
    identity_to_token: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = {}
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(_token_for(span, mapping, identity_to_token, counts))
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts), mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    """Подставляет исходные значения вместо токенов текущего запроса."""
    if not text or not mapping:
        return text
    for token in sorted(mapping, key=len, reverse=True):
        original = mapping[token]
        if token in text:
            text = text.replace(token, original)
            continue
        inner = re.escape(token[1:-1])
        text = re.sub(rf"\[\s*{inner}\s*\]|(?<!\[)\b{inner}\b(?!\])", lambda _: original, text)
    return text


def restore_data(value: Any, mapping: dict[str, str]) -> Any:
    """Возвращает копию структуры ответа, где токены снова стали исходными данными."""
    if not mapping:
        return value
    if isinstance(value, str):
        return restore(value, mapping)
    if isinstance(value, list):
        return [restore_data(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: restore_data(item, mapping) for key, item in value.items()}
    return value


def _find_spans(text: str) -> list[_Span]:
    spans: list[_Span] = []
    _add_pattern(spans, text, _EMAIL, "ПОЧТА")
    _add_pattern(spans, text, _PHONE, "ТЕЛЕФОН")
    _add_labeled(spans, text, _EMAIL_LABELED, "ПОЧТА")
    _add_labeled(spans, text, _PHONE_LABELED, "ТЕЛЕФОН")
    _add_pattern(spans, text, _CARD, "КАРТА")
    _add_pattern(spans, text, _SNILS, "СНИЛС")
    _add_group(spans, text, _PASSPORT, "ПАСПОРТ")
    _add_group(spans, text, _INN, "ИНН")
    _add_group(spans, text, _ACCOUNT, "СЧЁТ")
    _add_group(spans, text, _BIRTH, "ДАТА_РОЖДЕНИЯ")
    _add_pattern(spans, text, _CADASTRE, "КАДАСТР")
    _add_pattern(spans, text, _STREET, "АДРЕС")
    _add_pattern(spans, text, _REGION, "АДРЕС", reject=_reject_region)
    _add_pattern(spans, text, _LOCALITY, "АДРЕС")
    _add_pattern(spans, text, _FEDERAL_CITY, "АДРЕС")
    _add_pattern(spans, text, _PLACE_AFTER, "АДРЕС", group=1, reject=_reject_place)
    _add_labeled(spans, text, _ADDRESS_LABELED, "АДРЕС")
    _add_pattern(spans, text, _PLATE, "АВТО")
    _add_pattern(spans, text, _NICK, "НИК")
    _add_names(spans, text)
    return spans


def _add_names(spans: list[_Span], text: str) -> None:
    values: list[str] = []
    for match in _NAME_LABELED.finditer(text):
        piece = _slice_group(match)
        if piece is None or not _kept("ИМЯ", piece[2]):
            continue
        start, end, value = piece
        spans.append(_Span(start, end, "ИМЯ", value))
        values.append(value)
    for pattern, group in (
        (_NAME_INTRO, 1),
        (_FIO, 0),
        (_NAME_SURNAME, 0),
        (_INITIALS, 0),
    ):
        for match in pattern.finditer(text):
            value = match.group(group).strip()
            start, end = match.start(group), match.end(group)
            if not _kept("ИМЯ", value) or _reject_name(value):
                continue
            spans.append(_Span(start, end, "ИМЯ", text[start:end]))
            values.append(value)
    seen: set[str] = set()
    for value in values:
        for word in _name_words(value):
            key = word.casefold()
            if key in seen:
                continue
            seen.add(key)
            _add_literal(spans, text, word)


def _name_words(value: str) -> list[str]:
    words = [value.strip(" \t.,;:")]
    words.extend(re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", value))
    unique: list[str] = []
    seen: set[str] = set()
    for word in words:
        key = word.casefold()
        if not word or key in seen or key in _NAME_STOP or key in _EMPTY_VALUES:
            continue
        seen.add(key)
        unique.append(word)
    return unique


def _add_literal(spans: list[_Span], text: str, word: str) -> None:
    pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)
    for match in pattern.finditer(text):
        spans.append(_Span(match.start(), match.end(), "ИМЯ", match.group(0)))


def _add_pattern(
    spans: list[_Span],
    text: str,
    pattern: re.Pattern[str],
    kind: str,
    group: int = 0,
    reject: Any = None,
) -> None:
    for match in pattern.finditer(text):
        value = match.group(group)
        if not _kept(kind, value):
            continue
        if reject is not None and reject(value):
            continue
        spans.append(_Span(match.start(group), match.end(group), kind, value))


def _add_group(spans: list[_Span], text: str, pattern: re.Pattern[str], kind: str) -> None:
    _add_pattern(spans, text, pattern, kind, group=1)


def _add_labeled(spans: list[_Span], text: str, pattern: re.Pattern[str], kind: str) -> None:
    for match in pattern.finditer(text):
        piece = _slice_group(match)
        if piece is None or not _kept(kind, piece[2]):
            continue
        start, end, value = piece
        spans.append(_Span(start, end, kind, value))


def _slice_group(match: re.Match[str], group: int = 1) -> tuple[int, int, str] | None:
    raw = match.group(group)
    if raw is None:
        return None
    start = match.start(group) + (len(raw) - len(raw.lstrip()))
    end = match.end(group) - (len(raw) - len(raw.rstrip()))
    if end <= start:
        return None
    return start, end, raw.strip()


def _kept(kind: str, value: str) -> bool:
    text = value.strip()
    if not text or text.casefold() in _EMPTY_VALUES:
        return False
    if kind == "ТЕЛЕФОН" and len(re.sub(r"\D", "", text)) < 10 and len(text) < 5:
        return False
    return len(text) >= 2


def _reject_region(value: str) -> bool:
    first = value.split()[0].casefold()
    return first in _REGION_STOP


def _reject_place(value: str) -> bool:
    return any(word.casefold() in _PLACE_STOP for word in value.split())


def _reject_name(value: str) -> bool:
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", value)
    return any(word.casefold() in _NAME_STOP or word.casefold() in _PLACE_STOP for word in words)


def _resolve(spans: list[_Span]) -> list[_Span]:
    ordered = sorted(spans, key=lambda span: (-_PRIORITY[span.kind], -(span.end - span.start), span.start))
    chosen: list[_Span] = []
    for span in ordered:
        if span.end <= span.start:
            continue
        if any(span.start < other.end and other.start < span.end for other in chosen):
            continue
        chosen.append(span)
    chosen.sort(key=lambda span: span.start)
    return chosen


def _merge_addresses(text: str, spans: list[_Span]) -> list[_Span]:
    merged: list[_Span] = []
    for span in spans:
        if (
            merged
            and span.kind == "АДРЕС"
            and merged[-1].kind == "АДРЕС"
            and re.fullmatch(r"[\s,;]+", text[merged[-1].end : span.start] or "")
        ):
            previous = merged[-1]
            merged[-1] = _Span(previous.start, span.end, "АДРЕС", text[previous.start : span.end])
            continue
        merged.append(span)
    return merged


def _token_for(
    span: _Span,
    mapping: dict[str, str],
    identity_to_token: dict[tuple[str, str], str],
    counts: dict[str, int],
) -> str:
    identity = _identity(span.kind, span.value)
    existing = identity_to_token.get(identity)
    if existing:
        return existing
    counts[span.kind] = counts.get(span.kind, 0) + 1
    token = f"[{span.kind}_{counts[span.kind]}]"
    identity_to_token[identity] = token
    mapping[token] = span.value.strip()
    return token


def _identity(kind: str, value: str) -> tuple[str, str]:
    compact = " ".join(value.split())
    if kind in {"ТЕЛЕФОН", "КАРТА", "СНИЛС", "ИНН", "СЧЁТ"}:
        digits = re.sub(r"\D", "", compact)
        if digits:
            return kind, digits[-10:] if kind == "ТЕЛЕФОН" and len(digits) >= 10 else digits
    if kind == "ПОЧТА":
        return kind, compact.casefold()
    return kind, compact.casefold()
