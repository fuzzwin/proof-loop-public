from decimal import Decimal
import unittest
from order import order_total
from pricing import discounted_price


class OrderTests(unittest.TestCase):
    def test_discount_and_rounding(self):
        self.assertEqual(Decimal('8.00'), discounted_price('10.00', '0.2'))
        self.assertEqual(Decimal('0.05'), discounted_price('0.05', '0.1'))

    def test_multiple_lines_and_quantities(self):
        self.assertEqual(Decimal('26.00'), order_total([('10.00', 2, '0.2'), ('5.00', 2, '0')]))

    def test_empty_order(self):
        self.assertEqual(Decimal('0.00'), order_total([]))
