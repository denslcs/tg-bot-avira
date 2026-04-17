from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

# В проекте src.config требует TELEGRAM_BOT_TOKEN на этапе импорта.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from src import database as db  # noqa: E402
from src.handlers.payments import _discount_pack_values, _has_active_universe  # noqa: E402


class SubscriptionFlowsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._old_db_path = db.DB_PATH
        db.DB_PATH = self._tmp.name
        await db.init_db()
        self.uid = 7770001
        await db.ensure_user(self.uid, "test_user")
        self.start_credits = int(db.START_CREDITS)

    async def asyncTearDown(self) -> None:
        db.DB_PATH = self._old_db_path
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    async def test_initial_plan_purchase_adds_credits_immediately(self) -> None:
        end = await db.reset_subscription_days(self.uid, 30, "nova")
        self.assertIsNotNone(end)
        ok = await db.add_credits_with_reason(
            self.uid, 450, source="subscription_bonus", details="plan nova"
        )
        self.assertTrue(ok)
        credits = await db.get_credits(self.uid)
        self.assertEqual(credits, self.start_credits + 450)

    async def test_early_renewal_sums_days(self) -> None:
        end1 = await db.reset_subscription_days(self.uid, 30, "nova")
        self.assertIsNotNone(end1)
        end2 = await db.extend_subscription(self.uid, 30, "universe")
        self.assertIsNotNone(end2)
        self.assertGreater(str(end2), str(end1))

    async def test_pending_subscription_bonus_released_only_after_due_date(self) -> None:
        end1 = await db.reset_subscription_days(self.uid, 30, "nova")
        self.assertIsNotNone(end1)
        queued = await db.queue_subscription_bonus_credits(
            self.uid,
            300,
            release_at_utc=str(end1),
            details="renewal queued",
        )
        self.assertTrue(queued)

        # До наступления срока бонус не начисляется.
        before = await db.get_credits(self.uid)
        self.assertEqual(before, self.start_credits)

        # Форсируем истёкший срок выдачи и проверяем авто-начисление.
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        async with db.open_db() as conn:
            await conn.execute(
                "UPDATE subscription_bonus_pending SET release_at_utc = ? WHERE user_id = ?",
                (past, self.uid),
            )
            await conn.commit()

        after = await db.get_credits(self.uid)
        self.assertEqual(after, self.start_credits + 300)

    async def test_full_plan_can_be_bought_while_subscription_active(self) -> None:
        end = await db.reset_subscription_days(self.uid, 30, "nova")
        self.assertIsNotNone(end)
        can_buy, reason = await db.subscription_can_purchase_plan(self.uid, "nova")
        self.assertTrue(can_buy, msg=reason)

    async def test_cannot_early_renew_to_different_plan(self) -> None:
        end = await db.reset_subscription_days(self.uid, 30, "nova")
        self.assertIsNotNone(end)
        can_buy, reason = await db.subscription_can_purchase_plan(self.uid, "galaxy")
        self.assertFalse(can_buy)
        self.assertIn("только текущий тариф", (reason or "").lower())

    async def test_universe_pack_discount_values(self) -> None:
        rub, usd, stars, discounted = _discount_pack_values(
            "pack1000", apply_universe_discount=True
        )
        self.assertTrue(discounted)
        self.assertEqual(rub, 849)
        self.assertAlmostEqual(usd, 10.58, places=2)
        self.assertEqual(stars, 796)

    async def test_pack_values_without_universe_discount(self) -> None:
        rub, usd, stars, discounted = _discount_pack_values(
            "pack1000", apply_universe_discount=False
        )
        self.assertFalse(discounted)
        self.assertEqual(rub, 999)
        self.assertAlmostEqual(usd, 12.45, places=2)
        self.assertEqual(stars, 936)

    async def test_universe_discount_tied_to_active_subscription_only(self) -> None:
        # Нет подписки -> скидки нет.
        self.assertFalse(await _has_active_universe(self.uid))

        # Активная Universe -> скидка есть.
        end = await db.reset_subscription_days(self.uid, 30, "universe")
        self.assertIsNotNone(end)
        self.assertTrue(await _has_active_universe(self.uid))

        # Подписка истекла -> скидка исчезает.
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        async with db.open_db() as conn:
            await conn.execute(
                "UPDATE users SET subscription_ends_at = ? WHERE user_id = ?",
                (past, self.uid),
            )
            await conn.commit()
        self.assertFalse(await _has_active_universe(self.uid))


if __name__ == "__main__":
    unittest.main()
