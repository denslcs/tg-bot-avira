from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from src.config import DB_PATH, START_CREDITS
from src.subscription_catalog import PLANS, plan_limit_generations


@dataclass
class DialogMessage:
    role: str
    content: str


@dataclass
class SupportTicket:
    ticket_id: int
    user_id: int
    username: str
    thread_id: int
    status: str


@dataclass
class UserAdminProfile:
    user_id: int
    username: str | None
    credits: int
    created_at: str
    subscription_ends_at: str | None
    subscription_plan: str | None


@dataclass
class SupportTicketDetail:
    ticket_id: int
    user_id: int
    username: str
    thread_id: int
    status: str
    created_at: str
    first_admin_reply_at: str | None
    tag: str | None


@dataclass
class LastImageContext:
    """Последняя успешная генерация: для кнопки «Ещё раз». kind: text | edit."""

    kind: str
    prompt: str
    model: str
    cost: int
    model_name: str
    photo_file_id: str | None


async def _migrate_schema(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(users)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "subscription_ends_at" not in cols:
        await db.execute(
            "ALTER TABLE users ADD COLUMN subscription_ends_at TEXT",
        )


async def _migrate_support_tickets(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(support_tickets)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "first_admin_reply_at" not in cols:
        await db.execute("ALTER TABLE support_tickets ADD COLUMN first_admin_reply_at TEXT")
    if "tag" not in cols:
        await db.execute("ALTER TABLE support_tickets ADD COLUMN tag TEXT")


async def _migrate_support_ratings_feedback(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(support_ratings)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "feedback_text" not in cols:
        await db.execute("ALTER TABLE support_ratings ADD COLUMN feedback_text TEXT")


async def _migrate_subscription_plan(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(users)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "subscription_plan" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN subscription_plan TEXT")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                credits INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS dialog_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS support_tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                thread_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS support_ratings (
                ticket_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS support_ticket_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                admin_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES support_tickets(ticket_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_daily_usage (
                user_id INTEGER NOT NULL,
                day_utc TEXT NOT NULL,
                msg_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day_utc)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_referrals (
                invitee_user_id INTEGER PRIMARY KEY,
                inviter_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_monthly_image_usage (
                user_id INTEGER NOT NULL,
                month_utc TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, month_utc)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS star_payment_charges (
                charge_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_last_image_context (
                user_id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                prompt TEXT NOT NULL,
                model TEXT NOT NULL,
                cost INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                photo_file_id TEXT
            )
            """
        )
        await _migrate_schema(db)
        await _migrate_support_tickets(db)
        await _migrate_support_ratings_feedback(db)
        await _migrate_subscription_plan(db)
        await db.commit()


async def ensure_user(user_id: int, username: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username, credits)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username)
            """,
            (user_id, username, START_CREDITS),
        )
        await db.commit()


async def save_last_image_context(
    user_id: int,
    kind: str,
    prompt: str,
    model: str,
    cost: int,
    model_name: str,
    photo_file_id: str | None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_last_image_context (
                user_id, kind, prompt, model, cost, model_name, photo_file_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                kind=excluded.kind,
                prompt=excluded.prompt,
                model=excluded.model,
                cost=excluded.cost,
                model_name=excluded.model_name,
                photo_file_id=excluded.photo_file_id
            """,
            (user_id, kind, prompt, model, cost, model_name, photo_file_id),
        )
        await db.commit()


async def get_last_image_context(user_id: int) -> LastImageContext | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT kind, prompt, model, cost, model_name, photo_file_id
            FROM user_last_image_context
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return LastImageContext(
        kind=str(row[0]),
        prompt=str(row[1]),
        model=str(row[2]),
        cost=int(row[3]),
        model_name=str(row[4]),
        photo_file_id=str(row[5]) if row[5] else None,
    )


async def get_credits(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT credits FROM users WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def spend_one_credit(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE users
            SET credits = credits - 1
            WHERE user_id = ? AND credits > 0
            """,
            (user_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def add_dialog_message(user_id: int, role: str, content: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO dialog_messages (user_id, role, content)
            VALUES (?, ?, ?)
            """,
            (user_id, role, content),
        )
        await db.commit()


async def get_last_dialog_messages(user_id: int, limit: int = 10) -> list[DialogMessage]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT role, content
            FROM dialog_messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()

    rows.reverse()
    return [DialogMessage(role=row[0], content=row[1]) for row in rows]


async def clear_dialog_messages(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM dialog_messages WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


async def add_credits(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE users
            SET credits = credits + ?
            WHERE user_id = ?
            """,
            (amount, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def take_credits(user_id: int, amount: int) -> bool:
    """Списывает ровно amount, только если баланса хватает. Иначе баланс не меняется."""
    if amount <= 0:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE users
            SET credits = credits - ?
            WHERE user_id = ? AND credits >= ?
            """,
            (amount, user_id, amount),
        )
        await db.commit()
        return cur.rowcount > 0


async def apply_referral(invitee_user_id: int, inviter_user_id: int) -> bool:
    """Apply referral once. Inviter gets +10 credits, invitee gets +5 credits."""
    if invitee_user_id == inviter_user_id:
        logging.info("referral: skip self-ref invitee=%s", invitee_user_id)
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        # Already applied for this invitee.
        async with db.execute(
            "SELECT 1 FROM user_referrals WHERE invitee_user_id = ?",
            (invitee_user_id,),
        ) as cur:
            if await cur.fetchone():
                logging.info("referral: skip already registered invitee=%s", invitee_user_id)
                return False

        # Inviter must exist.
        async with db.execute(
            "SELECT 1 FROM users WHERE user_id = ?",
            (inviter_user_id,),
        ) as cur:
            if not await cur.fetchone():
                logging.warning(
                    "referral: inviter not in users invitee=%s inviter=%s",
                    invitee_user_id,
                    inviter_user_id,
                )
                return False

        await db.execute("BEGIN")
        try:
            await db.execute(
                """
                INSERT INTO user_referrals (invitee_user_id, inviter_user_id)
                VALUES (?, ?)
                """,
                (invitee_user_id, inviter_user_id),
            )
            await db.execute(
                "UPDATE users SET credits = credits + 10 WHERE user_id = ?",
                (inviter_user_id,),
            )
            await db.execute(
                "UPDATE users SET credits = credits + 5 WHERE user_id = ?",
                (invitee_user_id,),
            )
            await db.commit()
            return True
        except sqlite3.IntegrityError:
            await db.rollback()
            logging.info(
                "referral: integrity error (race/dup) invitee=%s inviter=%s",
                invitee_user_id,
                inviter_user_id,
            )
            return False
        except Exception:
            await db.rollback()
            raise


async def get_referral_count(inviter_user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM user_referrals WHERE inviter_user_id = ?",
            (inviter_user_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def create_support_ticket(user_id: int, username: str, thread_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO support_tickets (user_id, username, thread_id, status)
            VALUES (?, ?, ?, 'open')
            """,
            (user_id, username, thread_id),
        )
        await db.commit()
        return int(cur.lastrowid)


async def get_open_ticket_by_user(user_id: int) -> SupportTicket | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ticket_id, user_id, username, thread_id, status
            FROM support_tickets
            WHERE user_id = ? AND status = 'open'
            ORDER BY ticket_id DESC
            LIMIT 1
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return SupportTicket(
        ticket_id=int(row[0]),
        user_id=int(row[1]),
        username=str(row[2]),
        thread_id=int(row[3]),
        status=str(row[4]),
    )


async def get_latest_ticket_by_user(user_id: int) -> SupportTicket | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ticket_id, user_id, username, thread_id, status
            FROM support_tickets
            WHERE user_id = ?
            ORDER BY ticket_id DESC
            LIMIT 1
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return SupportTicket(
        ticket_id=int(row[0]),
        user_id=int(row[1]),
        username=str(row[2]),
        thread_id=int(row[3]),
        status=str(row[4]),
    )


async def get_open_ticket_by_thread(thread_id: int) -> SupportTicket | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ticket_id, user_id, username, thread_id, status
            FROM support_tickets
            WHERE thread_id = ? AND status = 'open'
            ORDER BY ticket_id DESC
            LIMIT 1
            """,
            (thread_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return SupportTicket(
        ticket_id=int(row[0]),
        user_id=int(row[1]),
        username=str(row[2]),
        thread_id=int(row[3]),
        status=str(row[4]),
    )


async def get_open_ticket_by_id(ticket_id: int) -> SupportTicket | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ticket_id, user_id, username, thread_id, status
            FROM support_tickets
            WHERE ticket_id = ? AND status = 'open'
            LIMIT 1
            """,
            (ticket_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return SupportTicket(
        ticket_id=int(row[0]),
        user_id=int(row[1]),
        username=str(row[2]),
        thread_id=int(row[3]),
        status=str(row[4]),
    )


async def get_ticket_by_id(ticket_id: int) -> SupportTicket | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ticket_id, user_id, username, thread_id, status
            FROM support_tickets
            WHERE ticket_id = ?
            LIMIT 1
            """,
            (ticket_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return SupportTicket(
        ticket_id=int(row[0]),
        user_id=int(row[1]),
        username=str(row[2]),
        thread_id=int(row[3]),
        status=str(row[4]),
    )


async def close_ticket(ticket_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE support_tickets
            SET status = 'closed',
                closed_at = CURRENT_TIMESTAMP
            WHERE ticket_id = ?
            """,
            (ticket_id,),
        )
        await db.commit()


async def reopen_ticket(ticket_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE support_tickets
            SET status = 'open',
                closed_at = NULL,
                first_admin_reply_at = NULL
            WHERE ticket_id = ?
            """,
            (ticket_id,),
        )
        await db.commit()


async def update_ticket_thread(ticket_id: int, thread_id: int, username: str | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if username is None:
            await db.execute(
                """
                UPDATE support_tickets
                SET thread_id = ?
                WHERE ticket_id = ?
                """,
                (thread_id, ticket_id),
            )
        else:
            await db.execute(
                """
                UPDATE support_tickets
                SET thread_id = ?,
                    username = ?
                WHERE ticket_id = ?
                """,
                (thread_id, username, ticket_id),
            )
        await db.commit()


def _parse_dt_utc(raw: str) -> datetime:
    s = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def subscription_is_active(ends_at: str | None) -> bool:
    if not ends_at:
        return False
    try:
        return _parse_dt_utc(ends_at) > datetime.now(timezone.utc)
    except ValueError:
        return False


def _month_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def monthly_image_generation_limit(
    subscription_ends_at: str | None, subscription_plan: str | None
) -> int:
    return plan_limit_generations(
        subscription_plan,
        subscription_is_active(subscription_ends_at),
    )


async def get_monthly_image_generation_usage(user_id: int) -> tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT subscription_ends_at, subscription_plan
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return 0, plan_limit_generations(None, False)
    ends, plan = row[0], row[1]
    limit = monthly_image_generation_limit(
        str(ends) if ends else None,
        str(plan) if plan else None,
    )
    month = _month_utc_now()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT count FROM user_monthly_image_usage
            WHERE user_id = ? AND month_utc = ?
            """,
            (user_id, month),
        ) as cur:
            urow = await cur.fetchone()
    used = int(urow[0]) if urow else 0
    return used, limit


async def try_reserve_monthly_image_generation(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT subscription_ends_at, subscription_plan
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.rollback()
            return False
        limit = monthly_image_generation_limit(
            str(row[0]) if row[0] else None,
            str(row[1]) if row[1] else None,
        )
        month = _month_utc_now()
        async with db.execute(
            """
            SELECT count FROM user_monthly_image_usage
            WHERE user_id = ? AND month_utc = ?
            """,
            (user_id, month),
        ) as cur:
            urow = await cur.fetchone()
        used = int(urow[0]) if urow else 0
        if used >= limit:
            await db.rollback()
            return False
        if urow:
            await db.execute(
                """
                UPDATE user_monthly_image_usage
                SET count = count + 1
                WHERE user_id = ? AND month_utc = ?
                """,
                (user_id, month),
            )
        else:
            await db.execute(
                """
                INSERT INTO user_monthly_image_usage (user_id, month_utc, count)
                VALUES (?, ?, 1)
                """,
                (user_id, month),
            )
        await db.commit()
    return True


async def release_monthly_image_generation(user_id: int) -> None:
    month = _month_utc_now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT count FROM user_monthly_image_usage
            WHERE user_id = ? AND month_utc = ?
            """,
            (user_id, month),
        ) as cur:
            row = await cur.fetchone()
        if not row or int(row[0]) <= 0:
            await db.rollback()
            return
        new_c = int(row[0]) - 1
        if new_c <= 0:
            await db.execute(
                """
                DELETE FROM user_monthly_image_usage
                WHERE user_id = ? AND month_utc = ?
                """,
                (user_id, month),
            )
        else:
            await db.execute(
                """
                UPDATE user_monthly_image_usage
                SET count = ?
                WHERE user_id = ? AND month_utc = ?
                """,
                (new_c, user_id, month),
            )
        await db.commit()


def _add_days_subscription(existing_iso: str | None, days: int) -> str:
    now = datetime.now(timezone.utc)
    if existing_iso:
        try:
            end = _parse_dt_utc(existing_iso)
            base = max(end, now)
        except ValueError:
            base = now
    else:
        base = now
    new_end = base + timedelta(days=max(0, days))
    return new_end.isoformat()


async def try_claim_star_payment(charge_id: str, user_id: int) -> bool:
    """Зарезервировать charge_id (идемпотентность Stars). Пустой charge_id — True без записи."""
    if not charge_id:
        return True
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO star_payment_charges (charge_id, user_id)
                VALUES (?, ?)
                """,
                (charge_id, user_id),
            )
            await db.commit()
    except sqlite3.IntegrityError:
        return False
    return True


async def release_star_payment_claim(charge_id: str) -> None:
    if not charge_id:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM star_payment_charges WHERE charge_id = ?",
            (charge_id,),
        )
        await db.commit()


async def extend_subscription(user_id: int, days: int, plan: str | None = None) -> str | None:
    if days <= 0:
        return None
    if plan is not None and plan not in PLANS:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT subscription_ends_at FROM users WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        new_iso = _add_days_subscription(row[0] if row[0] else None, days)
        if plan is not None:
            await db.execute(
                """
                UPDATE users
                SET subscription_ends_at = ?, subscription_plan = ?
                WHERE user_id = ?
                """,
                (new_iso, plan, user_id),
            )
        else:
            await db.execute(
                "UPDATE users SET subscription_ends_at = ? WHERE user_id = ?",
                (new_iso, user_id),
            )
        await db.commit()
    return new_iso


async def add_subscription_days(user_id: int, days: int) -> str | None:
    return await extend_subscription(user_id, days, None)


async def clear_subscription(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users
            SET subscription_ends_at = NULL, subscription_plan = NULL
            WHERE user_id = ?
            """,
            (user_id,),
        )
        await db.commit()


async def count_users_total() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def count_users_active_subscription() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT subscription_ends_at FROM users WHERE subscription_ends_at IS NOT NULL",
        ) as cur:
            rows = await cur.fetchall()
    n = 0
    for (ends,) in rows:
        if subscription_is_active(str(ends) if ends else None):
            n += 1
    return n


async def sum_users_credits() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COALESCE(SUM(credits), 0) FROM users") as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def count_dialog_messages_total() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM dialog_messages") as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def count_tickets_created_since_days(days: int) -> int:
    d = max(1, int(days))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*) FROM support_tickets
            WHERE datetime(created_at) >= datetime('now', ?)
            """,
            (f"-{d} days",),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def count_tickets_closed_since_days(days: int) -> int:
    d = max(1, int(days))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*) FROM support_tickets
            WHERE closed_at IS NOT NULL
              AND datetime(closed_at) >= datetime('now', ?)
            """,
            (f"-{d} days",),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def get_support_rating_rollups_since_days(days: int) -> tuple[float | None, int]:
    d = max(1, int(days))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT AVG(rating), COUNT(*) FROM support_ratings
            WHERE datetime(created_at) >= datetime('now', ?)
            """,
            (f"-{d} days",),
        ) as cur:
            row = await cur.fetchone()
    if not row or row[1] == 0:
        return None, 0
    avg = float(row[0]) if row[0] is not None else None
    return avg, int(row[1])


async def get_rating_distribution_since_days(days: int) -> list[tuple[int, int]]:
    d = max(1, int(days))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT rating, COUNT(*) FROM support_ratings
            WHERE datetime(created_at) >= datetime('now', ?)
            GROUP BY rating
            ORDER BY rating
            """,
            (f"-{d} days",),
        ) as cur:
            rows = await cur.fetchall()
    return [(int(r[0]), int(r[1])) for r in rows]


async def count_open_tickets_by_tag() -> list[tuple[str | None, int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT tag, COUNT(*) FROM support_tickets
            WHERE status = 'open'
            GROUP BY tag
            ORDER BY COUNT(*) DESC
            """,
        ) as cur:
            rows = await cur.fetchall()
    out: list[tuple[str | None, int]] = []
    for r in rows:
        tag = r[0] if r[0] else None
        out.append((tag, int(r[1])))
    return out


async def count_open_tickets() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE status = 'open'",
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def list_open_tickets_preview(*, limit: int = 15) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ticket_id, user_id, username, created_at
            FROM support_tickets
            WHERE status = 'open'
            ORDER BY ticket_id DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    lines: list[str] = []
    for row in rows:
        lines.append(
            f"#{row[0]} | user {row[1]} | {row[2]} | открыт {row[3]}",
        )
    return lines


async def count_dialog_messages(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM dialog_messages WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def get_user_admin_profile(user_id: int) -> UserAdminProfile | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT user_id, username, credits, created_at, subscription_ends_at, subscription_plan
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return UserAdminProfile(
        user_id=int(row[0]),
        username=row[1],
        credits=int(row[2]),
        created_at=str(row[3]),
        subscription_ends_at=row[4] if row[4] else None,
        subscription_plan=row[5] if row[5] else None,
    )


async def record_support_rating(
    ticket_id: int, user_id: int, rating: int, feedback_text: str | None = None
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO support_ratings (ticket_id, user_id, rating, feedback_text)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ticket_id) DO UPDATE SET
                rating = excluded.rating,
                user_id = excluded.user_id,
                feedback_text = CASE
                    WHEN excluded.feedback_text IS NOT NULL THEN excluded.feedback_text
                    ELSE support_ratings.feedback_text
                END
            """,
            (ticket_id, user_id, rating, feedback_text),
        )
        await db.commit()


async def get_support_rating_rollups() -> tuple[float | None, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT AVG(rating), COUNT(*) FROM support_ratings
            """,
        ) as cur:
            row = await cur.fetchone()
    if not row or row[1] == 0:
        return None, 0
    avg = float(row[0]) if row[0] is not None else None
    return avg, int(row[1])


async def get_ticket_detail_by_id(ticket_id: int) -> SupportTicketDetail | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ticket_id, user_id, username, thread_id, status, created_at,
                   first_admin_reply_at, tag
            FROM support_tickets
            WHERE ticket_id = ?
            LIMIT 1
            """,
            (ticket_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return SupportTicketDetail(
        ticket_id=int(row[0]),
        user_id=int(row[1]),
        username=str(row[2]),
        thread_id=int(row[3]),
        status=str(row[4]),
        created_at=str(row[5]),
        first_admin_reply_at=row[6] if row[6] else None,
        tag=row[7] if row[7] else None,
    )


async def get_ticket_detail_by_thread(thread_id: int) -> SupportTicketDetail | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ticket_id, user_id, username, thread_id, status, created_at,
                   first_admin_reply_at, tag
            FROM support_tickets
            WHERE thread_id = ?
            LIMIT 1
            """,
            (thread_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return SupportTicketDetail(
        ticket_id=int(row[0]),
        user_id=int(row[1]),
        username=str(row[2]),
        thread_id=int(row[3]),
        status=str(row[4]),
        created_at=str(row[5]),
        first_admin_reply_at=row[6] if row[6] else None,
        tag=row[7] if row[7] else None,
    )


async def mark_first_reply_to_user(ticket_id: int) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE support_tickets
            SET first_admin_reply_at = COALESCE(first_admin_reply_at, ?)
            WHERE ticket_id = ?
            """,
            (now, ticket_id),
        )
        await db.commit()


async def set_ticket_tag(ticket_id: int, tag: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE support_tickets SET tag = ? WHERE ticket_id = ?",
            (tag, ticket_id),
        )
        await db.commit()


async def add_support_ticket_note(ticket_id: int, admin_id: int, body: str) -> None:
    text = body.strip()
    if not text:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO support_ticket_notes (ticket_id, admin_id, body)
            VALUES (?, ?, ?)
            """,
            (ticket_id, admin_id, text),
        )
        await db.commit()


async def list_support_ticket_notes(ticket_id: int, *, limit: int = 30) -> list[tuple[int, int, str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT id, admin_id, body, created_at
            FROM support_ticket_notes
            WHERE ticket_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (ticket_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [(int(r[0]), int(r[1]), str(r[2]), str(r[3])) for r in rows]


async def get_meta(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_meta WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
    return str(row[0]) if row else None


async def set_meta(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO bot_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        await db.commit()


async def count_new_users_days(days: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*) FROM users
            WHERE datetime(created_at) >= datetime('now', ?)
            """,
            (f"-{int(days)} days",),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def list_open_tickets_sla_rows() -> list[SupportTicketDetail]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT ticket_id, user_id, username, thread_id, status, created_at,
                   first_admin_reply_at, tag
            FROM support_tickets
            WHERE status = 'open'
            ORDER BY ticket_id ASC
            """,
        ) as cur:
            rows = await cur.fetchall()
    out: list[SupportTicketDetail] = []
    for row in rows:
        out.append(
            SupportTicketDetail(
                ticket_id=int(row[0]),
                user_id=int(row[1]),
                username=str(row[2]),
                thread_id=int(row[3]),
                status=str(row[4]),
                created_at=str(row[5]),
                first_admin_reply_at=row[6] if row[6] else None,
                tag=row[7] if row[7] else None,
            )
        )
    return out


async def increment_daily_user_messages(user_id: int) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_daily_usage (user_id, day_utc, msg_count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, day_utc) DO UPDATE SET
                msg_count = msg_count + 1
            """,
            (user_id, day),
        )
        await db.commit()
        async with db.execute(
            "SELECT msg_count FROM user_daily_usage WHERE user_id = ? AND day_utc = ?",
            (user_id, day),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 1


async def get_daily_user_messages(user_id: int) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT msg_count FROM user_daily_usage WHERE user_id = ? AND day_utc = ?",
            (user_id, day),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0

