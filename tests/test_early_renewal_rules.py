from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from src import database as db  # noqa: E402


class EarlyRenewalRulesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._old_db_path = db.DB_PATH
        db.DB_PATH = self._tmp.name
        await db.init_db()
        self.uid = 7771003
        await db.ensure_user(self.uid, "early_renewal_user")

    async def asyncTearDown(self) -> None:
        db.DB_PATH = self._old_db_path
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    async def test_second_early_renewal_blocked_while_pending(self) -> None:
        end = await db.reset_subscription_days(self.uid, 30, "nova")
        self.assertIsNotNone(end)
        self.assertTrue(
            await db.queue_subscription_bonus_credits(
                self.uid, 450, release_at_utc=str(end), details="renewal"
            )
        )
        can_buy, reason = await db.subscription_can_purchase_plan(self.uid, "nova")
        self.assertFalse(can_buy)
        self.assertIn("уже оформлено продление", reason or "")

    async def test_first_early_renewal_allowed(self) -> None:
        end = await db.reset_subscription_days(self.uid, 30, "galaxy")
        self.assertIsNotNone(end)
        can_buy, reason = await db.subscription_can_purchase_plan(self.uid, "galaxy")
        self.assertTrue(can_buy, msg=reason)

if __name__ == "__main__":
    unittest.main()
