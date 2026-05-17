from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from src import database as db  # noqa: E402
from src.services.wata_orders import (  # noqa: E402
    build_wata_order_id,
    parse_wata_order_id,
    parse_wata_start_payload,
)


class WataOrderIdTests(unittest.TestCase):
    def test_build_and_parse_roundtrip(self) -> None:
        order_id = build_wata_order_id(user_id=12345, kind="plan", item_id="nova")
        parsed = parse_wata_order_id(order_id)
        self.assertEqual(parsed, (12345, "plan", "nova"))

    def test_parse_pack_order(self) -> None:
        order_id = build_wata_order_id(user_id=99, kind="pack", item_id="pack1000")
        self.assertEqual(parse_wata_order_id(order_id), (99, "pack", "pack1000"))

    def test_parse_invalid_returns_none(self) -> None:
        self.assertIsNone(parse_wata_order_id(""))
        self.assertIsNone(parse_wata_order_id("bad_order"))


class WataStartPayloadTests(unittest.TestCase):
    def test_wata_ok_legacy(self) -> None:
        self.assertIsNone(parse_wata_start_payload("wata_ok"))

    def test_wata_order_id(self) -> None:
        oid = build_wata_order_id(user_id=1, kind="plan", item_id="starter")
        self.assertEqual(parse_wata_start_payload(f"wata_{oid}"), oid)

    def test_unknown_payload(self) -> None:
        self.assertIsNone(parse_wata_start_payload("wata_bad"))


class WataDbLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._old_db_path = db.DB_PATH
        db.DB_PATH = self._tmp.name
        await db.init_db()
        self.uid = 7770002
        await db.ensure_user(self.uid, "wata_lock_user")
        self.order_id = build_wata_order_id(user_id=self.uid, kind="pack", item_id="pack1000")
        await db.create_wata_payment_order(
            order_id=self.order_id,
            user_id=self.uid,
            kind="pack",
            item_id="pack1000",
            amount_rub=100,
        )

    async def asyncTearDown(self) -> None:
        db.DB_PATH = self._old_db_path
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    async def test_lock_and_mark_paid(self) -> None:
        self.assertEqual(await db.try_lock_wata_order_for_finalize(self.order_id), "locked")
        self.assertEqual(await db.try_lock_wata_order_for_finalize(self.order_id), "processing")
        ok = await db.mark_wata_payment_order_paid(
            self.order_id, wata_transaction_id="txn-test-1"
        )
        self.assertTrue(ok)
        row = await db.get_wata_payment_order(self.order_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "paid")

    async def test_claim_belongs_to(self) -> None:
        cid = "wata:txn-abc"
        self.assertTrue(await db.try_claim_star_payment(cid, self.uid))
        self.assertFalse(await db.try_claim_star_payment(cid, self.uid))
        self.assertTrue(await db.star_payment_claim_belongs_to(cid, self.uid))
        self.assertFalse(await db.star_payment_claim_belongs_to(cid, self.uid + 1))


if __name__ == "__main__":
    unittest.main()
