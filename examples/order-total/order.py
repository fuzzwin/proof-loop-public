from decimal import Decimal
from pricing import discounted_price


def order_total(lines):
    """Sum (price, quantity, discount) lines into a Decimal total."""
    return sum((discounted_price(price, discount) for price, quantity, discount in lines), Decimal('0.00'))
