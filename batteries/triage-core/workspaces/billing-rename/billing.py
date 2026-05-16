"""Billing calculations for the invoicing system."""


def claculate_total(items: list[dict]) -> float:
    """Calculate the total price across a list of line items.

    Each item must have a 'price' key (float) and optionally 'qty' (int, default 1).
    """
    total = 0.0
    for item in items:
        total += item["price"] * item.get("qty", 1)
    return round(total, 2)


def apply_discount(total: float, pct: float) -> float:
    """Return total after applying a percentage discount (0–100)."""
    return round(total * (1 - pct / 100), 2)
