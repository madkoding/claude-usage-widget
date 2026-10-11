import unittest
from claude_usage_widget import fetch_delay


class FetchDelay(unittest.TestCase):
    def test_disabled(self):
        self.assertEqual(fetch_delay(0, 3, 900), 0)

    def test_minimum(self):
        self.assertEqual(fetch_delay(30), 120)

    def test_backoff(self):
        self.assertEqual([fetch_delay(300, n) for n in range(6)], [300, 600, 1200, 2400, 4800, 4800])

    def test_retry_after(self):
        self.assertEqual(fetch_delay(300, 0, 900), 900)
        self.assertEqual(fetch_delay(300, 2, 900), 1200)
        self.assertEqual(fetch_delay(300, 0, 99999), 3600)


if __name__ == "__main__":
    unittest.main()
