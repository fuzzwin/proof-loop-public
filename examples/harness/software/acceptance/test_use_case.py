import unittest
from app import add
class AgreedCases(unittest.TestCase):
    def test_negative(self): self.assertEqual(-1, add(-3, 2))
    def test_zero(self): self.assertEqual(8, add(0, 8))
