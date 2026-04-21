from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

# src.config требует TELEGRAM_BOT_TOKEN при импорте модулей.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from src import database as db  # noqa: E402
from src.handlers.payments import _expected_stars_amount  # noqa: E402
from src.subscription_catalog import BONUS_PACKS, PLANS  # noqa: E402


class PaymentStarsRulesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._old_db_path = db.DB_PATH
        db.DB_PATH = self._tmp.name
        await db.init_db()
        self.uid = 8880001
        await db.ensure_user(self.uid, "pay_rules_user")

    async def asyncTearDown(self) -> None:
        db.DB_PATH = self._old_db_path
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    async def test_expected_stars_plan_matches_catalog(self) -> None:
        expected = await _expected_stars_amount(kind="plan", item_id="nova", user_id=self.uid)
        self.assertEqual(expected, int(PLANS["nova"].stars))

    async def test_expected_stars_pack_without_active_discount(self) -> None:
        expected = await _expected_stars_amount(kind="pack", item_id="pack1000", user_id=self.uid)
        self.assertEqual(expected, int(BONUS_PACKS["pack1000"].stars))

    async def test_expected_stars_pack_with_active_universe_discount(self) -> None:
        end = await db.reset_subscription_days(self.uid, 30, "universe")
        self.assertIsNotNone(end)
        expected = await _expected_stars_amount(kind="pack", item_id="pack1000", user_id=self.uid)
        self.assertIsNotNone(expected)
        self.assertLess(int(expected), int(BONUS_PACKS["pack1000"].stars))

    async def test_expected_stars_pack_after_expired_subscription_no_discount(self) -> None:
        end = await db.reset_subscription_days(self.uid, 3, "universe")
        self.assertIsNotNone(end)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        async with db.open_db() as conn:
            await conn.execute(
                "UPDATE users SET subscription_ends_at = ? WHERE user_id = ?",
                (past, self.uid),
            )
            await conn.commit()
        expected = await _expected_stars_amount(kind="pack", item_id="pack1000", user_id=self.uid)
        self.assertEqual(expected, int(BONUS_PACKS["pack1000"].stars))

    async def test_expected_stars_returns_none_for_unknown_item(self) -> None:
        self.assertIsNone(await _expected_stars_amount(kind="plan", item_id="unknown", user_id=self.uid))
        self.assertIsNone(await _expected_stars_amount(kind="pack", item_id="unknown", user_id=self.uid))
        self.assertIsNone(await _expected_stars_amount(kind="other", item_id="pack1000", user_id=self.uid))


if __name__ == "__main__":
    unittest.main()

