import unittest

from src.database.time_utils import (
    TIMESTAMP_MS_THRESHOLD,
    is_expired_timestamp_value,
    is_millisecond_timestamp,
    retention_cutoffs,
)


class DatabaseTimeUtilsTest(unittest.TestCase):
    def test_detects_second_and_millisecond_timestamps(self):
        self.assertFalse(is_millisecond_timestamp(1_783_600_000))
        self.assertTrue(is_millisecond_timestamp(1_783_600_000_000))
        self.assertFalse(is_millisecond_timestamp(None))
        self.assertFalse(is_millisecond_timestamp("bad"))
        self.assertTrue(TIMESTAMP_MS_THRESHOLD > 1_783_600_000)

    def test_retention_cutoffs_return_both_units(self):
        cutoff_ms, cutoff_seconds = retention_cutoffs(3600, now_value=1_783_600_000.123)

        self.assertEqual(cutoff_ms, 1_783_596_400_123)
        self.assertEqual(cutoff_seconds, 1_783_596_400)

    def test_expiration_works_for_mixed_timestamp_units(self):
        now_value = 1_783_600_000.0
        two_hours = 2 * 3600

        self.assertTrue(is_expired_timestamp_value(1_783_592_799, two_hours, now_value))
        self.assertFalse(is_expired_timestamp_value(1_783_592_801, two_hours, now_value))
        self.assertTrue(is_expired_timestamp_value(1_783_592_799_999, two_hours, now_value))
        self.assertFalse(is_expired_timestamp_value(1_783_592_800_001, two_hours, now_value))

    def test_invalid_or_missing_timestamps_are_not_expired(self):
        self.assertFalse(is_expired_timestamp_value(None, 3600, 1_783_600_000.0))
        self.assertFalse(is_expired_timestamp_value("bad", 3600, 1_783_600_000.0))


if __name__ == "__main__":
    unittest.main()
