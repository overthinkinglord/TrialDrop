from datetime import datetime, timezone
import unittest

from trialtracker.parser import parse_trial_text


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)

    def test_parse_relative_russian(self) -> None:
        draft = parse_trial_text("ChatGPT 14 дней $20", timezone_name="Europe/Berlin", now=self.now)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.service_name, "ChatGPT")
        self.assertEqual(draft.amount_minor, 2000)
        self.assertEqual(draft.currency_code, "USD")

    def test_parse_exact_date(self) -> None:
        draft = parse_trial_text("Perplexity до 5 мая €20", timezone_name="Europe/Berlin", now=self.now)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.service_name, "Perplexity")
        self.assertEqual(draft.amount_minor, 2000)
        self.assertEqual(draft.currency_code, "EUR")
        self.assertTrue(draft.billing_at.startswith("2026-05-05"))

    def test_ignore_plain_text_without_trial_signal(self) -> None:
        draft = parse_trial_text("просто привет как дела", timezone_name="Europe/Berlin", now=self.now)
        self.assertIsNone(draft)


if __name__ == "__main__":
    unittest.main()
