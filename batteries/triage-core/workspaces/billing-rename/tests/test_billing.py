"""Unit tests for billing module."""

from billing import claculate_total, apply_discount


def test_claculate_total_basic():
    items = [{"price": 10.0}, {"price": 5.0, "qty": 2}]
    assert claculate_total(items) == 20.0


def test_claculate_total_empty():
    assert claculate_total([]) == 0.0


def test_apply_discount():
    assert apply_discount(100.0, 10) == 90.0
