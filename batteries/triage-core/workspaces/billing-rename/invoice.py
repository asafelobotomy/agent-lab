"""Invoice generation — builds a summary from a list of line items."""

from billing import claculate_total


def generate_invoice(items: list[dict], discount_pct: float = 0.0) -> dict:
    """Return a structured invoice dict."""
    subtotal = claculate_total(items)
    discount = round(subtotal * discount_pct / 100, 2)
    total = round(subtotal - discount, 2)
    return {
        "items": items,
        "subtotal": subtotal,
        "discount": discount,
        "total": total,
    }
