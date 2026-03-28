from __future__ import annotations

import random
import time
from dataclasses import dataclass


@dataclass
class _SpamState:
    last_norm: str = ""
    streak: int = 0
    captcha_expected: int | None = None
    captcha_fails: int = 0
    cooldown_until: float = 0.0


_USER_SPAM: dict[int, _SpamState] = {}


def _norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


def reset_user_spam(user_id: int) -> None:
    _USER_SPAM.pop(user_id, None)


def check_spam_private_message(
    user_id: int,
    text: str,
    *,
    duplicate_threshold: int = 5,
    cooldown_seconds: int = 600,
) -> tuple[bool, str | None]:
    """
    Returns (blocked, optional_reply_text).
    blocked=True: stop processing; send optional_reply_text if it is not None.
    """
    now = time.time()
    st = _USER_SPAM.setdefault(user_id, _SpamState())
    if now < st.cooldown_until:
        left = max(1, int(st.cooldown_until - now))
        return True, (
            "Слишком много одинаковых сообщений подряд. Подожди немного "
            f"({left // 60} мин.) и напиши снова."
        )

    if st.captcha_expected is not None:
        if text.strip().isdigit() and int(text.strip()) == st.captcha_expected:
            st.captcha_expected = None
            st.captcha_fails = 0
            st.streak = 0
            st.last_norm = ""
            return True, "Проверка пройдена ✅ Можешь продолжить."
        st.captcha_fails += 1
        if st.captcha_fails >= 3:
            st.captcha_expected = None
            st.captcha_fails = 0
            st.streak = 0
            st.last_norm = ""
            st.cooldown_until = now + cooldown_seconds
            return True, (
                "Несколько неверных ответов. Бот временно ограничил сообщения из‑за подозрения на спам."
            )
        return True, "Неверно. Ответь одним числом — результат примера выше."

    n = _norm(text)
    if not n:
        st.streak = 0
        st.last_norm = ""
        return False, None

    if n == st.last_norm:
        st.streak += 1
    else:
        st.last_norm = n
        st.streak = 1

    if 2 <= st.streak < duplicate_threshold:
        return True, None

    if st.streak == duplicate_threshold:
        return True, (
            "Ты отправил одно и то же несколько раз подряд — отвечаю один раз: "
            "не дублируй сообщения. Если нужно уточнение — переформулируй вопрос."
        )

    if st.streak == duplicate_threshold + 1:
        a = random.randint(2, 12)
        b = random.randint(2, 12)
        st.captcha_expected = a + b
        st.streak = 0
        st.last_norm = ""
        return True, (
            f"Подтверди, что ты не бот: сколько будет {a} + {b}?\n"
            "Ответь одним числом."
        )

    if st.streak > duplicate_threshold + 1:
        st.cooldown_until = now + cooldown_seconds
        st.streak = 0
        st.last_norm = ""
        return True, (
            "Слишком много повторов подряд. Попробуй чуть позже — бот временно ограничил диалог."
        )

    return False, None
