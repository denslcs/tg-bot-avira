import unittest
from datetime import datetime, timedelta, timezone

from src.services.subscription_reminders import (
    should_send_subscription_reminder,
    subscription_reminder_kind_for_remaining_seconds,
)
from src.services.subscription_time import subscription_days_remaining_ceiling


class SubscriptionReminderLogicTests(unittest.TestCase):
    def test_kind_for_remaining_hours(self) -> None:
        self.assertEqual(
            subscription_reminder_kind_for_remaining_seconds(50 * 3600), "3d"
        )
        self.assertEqual(
            subscription_reminder_kind_for_remaining_seconds(12 * 3600), "1d"
        )
        self.assertIsNone(subscription_reminder_kind_for_remaining_seconds(80 * 3600))
        self.assertIsNone(subscription_reminder_kind_for_remaining_seconds(30 * 3600))

    def test_days_remaining_ceiling(self) -> None:
        now = datetime.now(timezone.utc)
        ends = (now + timedelta(hours=50)).isoformat()
        self.assertEqual(subscription_days_remaining_ceiling(ends), 3)

    def test_skip_if_already_sent_for_same_end(self) -> None:
        ends = "2030-06-15T12:00:00+00:00"
        self.assertFalse(
            should_send_subscription_reminder(
                ends_at=ends,
                kind="3d",
                remind_3d_for=ends,
                remind_1d_for=None,
            )
        )

    def test_send_after_early_renewal_new_end(self) -> None:
        now = datetime.now(timezone.utc)
        old_end = (now + timedelta(days=10)).isoformat()
        new_end = (now + timedelta(hours=50)).isoformat()
        self.assertTrue(
            should_send_subscription_reminder(
                ends_at=new_end,
                kind="3d",
                remind_3d_for=old_end,
                remind_1d_for=None,
            )
        )

    def test_only_one_kind_per_window(self) -> None:
        now = datetime.now(timezone.utc)
        ends = (now + timedelta(hours=12)).isoformat()
        self.assertEqual(
            subscription_reminder_kind_for_remaining_seconds(12 * 3600), "1d"
        )
        self.assertFalse(
            should_send_subscription_reminder(
                ends_at=ends,
                kind="1d",
                remind_3d_for=None,
                remind_1d_for=ends,
            )
        )


if __name__ == "__main__":
    unittest.main()
