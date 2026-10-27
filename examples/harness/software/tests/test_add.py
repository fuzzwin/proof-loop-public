import unittest
from app import add
class Addition(unittest.TestCase):
    def test_positive(self): self.assertEqual(5, add(2, 3))
