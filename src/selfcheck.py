from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from src.config import PROJECT_ROOT
from src.database import init_db
from src.handlers.global_errors import global_error_handler
from src.handlers.img_commands import (
    READY_IDEA_CATEGORIES,
    READY_IDEA_ITEMS,
    _READY_IDEA_STATIC_REF_BY_TITLE,
)
from src.handlers.routers import register_routers


@dataclass
class SelfCheckResult:
    ok: bool
    checks: list[str]
    errors: list[str]


def _check_ready_ideas() -> tuple[list[str], list[str]]:
    checks: list[str] = []
    errors: list[str] = []

    category_slugs = [slug for slug, _ in READY_IDEA_CATEGORIES]
    for slug in category_slugs:
        if slug not in READY_IDEA_ITEMS:
            errors.append(f"Category '{slug}' is missing in READY_IDEA_ITEMS.")
    checks.append("Categories have matching READY_IDEA_ITEMS entries.")

    titles: set[str] = set()
    for category, items in READY_IDEA_ITEMS.items():
        if not isinstance(items, list):
            errors.append(f"Category '{category}' must be a list of idea tuples.")
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, tuple) or len(item) != 4:
                errors.append(f"Invalid tuple at {category}[{idx}] (expected 4 elements).")
                continue
            title, preview, prompt, photos_required = item
            if not isinstance(title, str) or not title.strip():
                errors.append(f"Empty title at {category}[{idx}].")
            if title in titles:
                errors.append(f"Duplicate idea title found: '{title}'.")
            titles.add(title)
            if not isinstance(preview, str) or not preview.strip():
                errors.append(f"Empty preview at {category}[{idx}] for '{title}'.")
            if not isinstance(prompt, str) or not prompt.strip():
                errors.append(f"Empty prompt at {category}[{idx}] for '{title}'.")
            if photos_required not in (1, 2):
                errors.append(
                    f"Invalid photos_required at {category}[{idx}] for '{title}' (expected 1 or 2)."
                )
    checks.append("Ready idea tuples validated (title/preview/prompt/photos_required).")

    for title, path in _READY_IDEA_STATIC_REF_BY_TITLE.items():
        p = Path(path)
        if not p.is_file():
            errors.append(f"Static reference for '{title}' does not exist: {path}")
    checks.append("Static reference files exist.")

    mc_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "minecraft_preview.png"
    if not mc_listing.is_file():
        errors.append(
            "Minecraft listing preview image missing: assets/ready_ideas/minecraft_preview.png"
        )
    checks.append("Minecraft ready-idea listing preview file exists.")

    cr_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "clash_royale_preview.png"
    if not cr_listing.is_file():
        errors.append(
            "Clash Royale listing preview image missing: assets/ready_ideas/clash_royale_preview.png"
        )
    checks.append("Clash Royale ready-idea listing preview file exists.")

    gta_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "gta_vice_city_preview.png"
    if not gta_listing.is_file():
        errors.append(
            "GTA Vice City listing preview image missing: assets/ready_ideas/gta_vice_city_preview.png"
        )
    checks.append("GTA Vice City ready-idea listing preview file exists.")

    got_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "game_of_thrones_preview.png"
    if not got_listing.is_file():
        errors.append(
            "Game of Thrones listing preview image missing: assets/ready_ideas/game_of_thrones_preview.png"
        )
    checks.append("Game of Thrones ready-idea listing preview file exists.")

    av_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "avatar_preview.png"
    if not av_listing.is_file():
        errors.append("Avatar listing preview image missing: assets/ready_ideas/avatar_preview.png")
    checks.append("Avatar ready-idea listing preview file exists.")

    pn_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "putin_negotiations_preview.png"
    if not pn_listing.is_file():
        errors.append(
            "Putin negotiations listing preview missing: assets/ready_ideas/putin_negotiations_preview.png"
        )
    checks.append("Putin negotiations ready-idea listing preview file exists.")

    ali_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "muhammad_ali_victory_preview.png"
    if not ali_listing.is_file():
        errors.append(
            "Muhammad Ali victory listing preview missing: assets/ready_ideas/muhammad_ali_victory_preview.png"
        )
    checks.append("Muhammad Ali victory ready-idea listing preview file exists.")

    hb_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "homelander_butcher_preview.png"
    if not hb_listing.is_file():
        errors.append(
            "Homelander and Butcher listing preview missing: assets/ready_ideas/homelander_butcher_preview.png"
        )
    checks.append("Homelander and Butcher ready-idea listing preview file exists.")

    rost_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "rostomer_preview.png"
    if not rost_listing.is_file():
        errors.append("Rostomer listing preview missing: assets/ready_ideas/rostomer_preview.png")
    checks.append("Rostomer (height chart) ready-idea listing preview file exists.")

    it_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "italy_preview.png"
    if not it_listing.is_file():
        errors.append("Italy listing preview missing: assets/ready_ideas/italy_preview.png")
    checks.append("Italy (Amalfi yacht) ready-idea listing preview file exists.")

    br_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "backrooms_preview.png"
    if not br_listing.is_file():
        errors.append("Backrooms listing preview missing: assets/ready_ideas/backrooms_preview.png")
    checks.append("Backrooms ready-idea listing preview file exists.")

    or_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "orange_color_preview.png"
    if not or_listing.is_file():
        errors.append("Orange color listing preview missing: assets/ready_ideas/orange_color_preview.png")
    checks.append("Orange color ready-idea listing preview file exists.")

    bs_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "black_studio_preview.png"
    if not bs_listing.is_file():
        errors.append("Black studio listing preview missing: assets/ready_ideas/black_studio_preview.png")
    checks.append("Black studio ready-idea listing preview file exists.")

    sb_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "suit_bouquet_preview.png"
    if not sb_listing.is_file():
        errors.append("Suit and bouquet listing preview missing: assets/ready_ideas/suit_bouquet_preview.png")
    checks.append("Suit and bouquet ready-idea listing preview file exists.")

    gu_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "gucci_editorial_preview.png"
    if not gu_listing.is_file():
        errors.append("Gucci editorial listing preview missing: assets/ready_ideas/gucci_editorial_preview.png")
    checks.append("Gucci editorial ready-idea listing preview file exists.")

    kl_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "knight_lady_preview.png"
    if not kl_listing.is_file():
        errors.append("Knight and lady listing preview missing: assets/ready_ideas/knight_lady_preview.png")
    checks.append("Knight and lady ready-idea listing preview file exists.")

    li_listing = PROJECT_ROOT / "assets" / "ready_ideas" / "love_is_preview.png"
    if not li_listing.is_file():
        errors.append("Love is listing preview missing: assets/ready_ideas/love_is_preview.png")
    checks.append("Love is ready-idea listing preview file exists.")

    return checks, errors


async def run_self_check() -> SelfCheckResult:
    checks: list[str] = []
    errors: list[str] = []

    try:
        await init_db()
        checks.append("Database initialization OK.")
    except Exception as exc:  # pragma: no cover - defensive guard
        errors.append(f"Database initialization failed: {exc}")

    try:
        dp = Dispatcher(storage=MemoryStorage())
        register_routers(dp)
        checks.append("Routers registration OK.")
        if not any(getattr(h, "callback", None) is global_error_handler for h in dp.errors.handlers):
            errors.append("Global error handler is not registered on the dispatcher.")
        else:
            checks.append("Global error handler registered.")
    except Exception as exc:  # pragma: no cover - defensive guard
        errors.append(f"Routers registration failed: {exc}")

    idea_checks, idea_errors = _check_ready_ideas()
    checks.extend(idea_checks)
    errors.extend(idea_errors)

    return SelfCheckResult(ok=not errors, checks=checks, errors=errors)


async def _main() -> None:
    result = await run_self_check()
    print("SELF-CHECK REPORT")
    for line in result.checks:
        print(f"[OK] {line}")
    if result.errors:
        for line in result.errors:
            print(f"[ERROR] {line}")
        raise SystemExit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(_main())

