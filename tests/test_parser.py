from datetime import datetime, timezone
import unittest

from trialtracker.app import calculate_primary_reminder
from trialtracker.parser import parse_trial_text


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)

    def test_parse_pipe_format_with_optional_price(self) -> None:
        draft = parse_trial_text("ChatGPT | 14 дней | 20 долларов", timezone_name="Europe/Berlin", now=self.now)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.service_name, "ChatGPT")
        self.assertEqual(draft.amount_minor, 2000)
        self.assertEqual(draft.currency_code, "USD")

    def test_parse_without_price(self) -> None:
        draft = parse_trial_text("Cursor на месяц", timezone_name="Europe/Berlin", now=self.now)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.service_name, "Cursor")
        self.assertIsNone(draft.amount_minor)
        self.assertIsNotNone(draft.billing_at)

    def test_parse_spoken_aliases_and_word_numbers(self) -> None:
        draft = parse_trial_text("сейчас гпт на две недели", timezone_name="Europe/Berlin", now=self.now)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.service_name, "ChatGPT")
        self.assertIsNotNone(draft.billing_at)

    def test_parse_cloud_as_claude_with_currency_word(self) -> None:
        draft = parse_trial_text("Cloud на три месяца 10 долларов", timezone_name="Europe/Berlin", now=self.now)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.service_name, "Claude")
        self.assertEqual(draft.amount_minor, 1000)
        self.assertEqual(draft.currency_code, "USD")

    def test_parse_half_month_and_uah(self) -> None:
        draft = parse_trial_text("Notion | полмесяца | 300 грн", timezone_name="Europe/Berlin", now=self.now)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.service_name, "Notion")
        self.assertEqual(draft.amount_minor, 30000)
        self.assertEqual(draft.currency_code, "UAH")

    def test_ignore_plain_text_without_trial_signal(self) -> None:
        draft = parse_trial_text("просто привет как дела", timezone_name="Europe/Berlin", now=self.now)
        self.assertIsNone(draft)

    def test_reminder_is_day_based(self) -> None:
        reminder = calculate_primary_reminder(
            billing_at_iso="2026-05-06T10:00:00+00:00",
            now_iso="2026-04-22T12:00:00+00:00",
            timezone_name="Europe/Berlin",
        )
        self.assertTrue(reminder.startswith("2026-05-05"))


if __name__ == "__main__":
    unittest.main()
