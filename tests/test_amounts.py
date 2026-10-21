import unittest
from decimal import Decimal

from chariow_bot.main import _integer_amount


class IntegerAmountTests(unittest.TestCase):
    def test_integer_values(self):
        self.assertEqual(_integer_amount("100"), 100)
        self.assertEqual(_integer_amount("100.00"), 100)
        self.assertEqual(_integer_amount(0), 0)

    def test_fractional_values_fail_closed(self):
        self.assertIsNone(_integer_amount("100.50"))
        self.assertIsNone(_integer_amount(Decimal("0.25")))

    def test_invalid_values_fail_closed(self):
        for value in (None, True, False, "abc", "NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                self.assertIsNone(_integer_amount(value))


if __name__ == "__main__":
    unittest.main()
