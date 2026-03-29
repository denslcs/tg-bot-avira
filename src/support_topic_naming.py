"""Имена тем форума для тикетов поддержки."""

TAG_EMOJI = {
    "bug": "🐛",
    "payment": "💳",
    "general": "💬",
}

VALID_TAGS = frozenset(TAG_EMOJI.keys())


def topic_title(
    ticket_id: int,
    username: str,
    status: str = "OPEN",
    tag: str | None = None,
) -> str:
    prefix = ""
    if tag and tag in TAG_EMOJI:
        prefix = f"{TAG_EMOJI[tag]} "
    return f"{prefix}[{status}] Тикет #{ticket_id} | {username}"[:120]
