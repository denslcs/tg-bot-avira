"""Простой лимит частоты сообщений в личке (скользящее окно 60 с)."""

from __future__ import annotations

import time

from src.config import PRIVATE_MESSAGES_PER_MINUTE

_WINDOW_SEC = 60.0
_TIMESTAMPS: dict[int, list[float]] = {}


def check_private_message_rate(user_id: int) -> tuple[bool, str | None]:
    """
    Возвращает (blocked, текст_ответа).
    При каждом НЕзаблокированном запросе добавляется отметка времени.
    """
    now = time.time()
    cutoff = now - _WINDOW_SEC
    lst = _TIMESTAMPS.setdefault(user_id, [])
    while lst and lst[0] < cutoff:
        lst.pop(0)
    if len(lst) >= PRIVATE_MESSAGES_PER_MINUTE:
        return True, (
            "Слишком много сообщений за короткое время. "
            "Подожди минуту и продолжи — так мы защищаем бота от перегрузки."
        )
    lst.append(now)
    return False, None


def reset_private_rate(user_id: int) -> None:
    _TIMESTAMPS.pop(user_id, None)
