"""Журнал анонимизации: какие токены ушли в нейросеть, без исходных персональных данных."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LOG_PATH = Path(__file__).resolve().parent / "logs" / "anonymization.csv"
_FIELDS = ("created_at", "tokens", "anonymized_text")


def write_anonymization_log(anonymized_text: str, mapping: dict[str, str]) -> None:
    """Дописывает одну строку. В файл попадают токены и обезличенный текст, не сами данные."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_PATH.exists()
    with LOG_PATH.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(
            {
                "created_at": datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M:%S"),
                "tokens": _token_summary(mapping),
                "anonymized_text": anonymized_text.replace("\r\n", "\n").replace("\r", "\n"),
            }
        )


def _token_summary(mapping: dict[str, str]) -> str:
    counts: dict[str, int] = {}
    for token in mapping:
        kind = token.strip("[]").rsplit("_", 1)[0]
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return "нет"
    return ", ".join(f"{kind}×{count}" for kind, count in counts.items())
