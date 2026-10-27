from decimal import Decimal, ROUND_HALF_UP


def discounted_price(price, discount):
    """Return the discounted unit price in dollars, rounded to cents."""
    return Decimal(str(price))  # Deliberate demonstration defect: ignores discount.
