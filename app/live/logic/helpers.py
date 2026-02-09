from typing import Optional

from ..exchanges.exchange_models import BookTicker

from .models import HedgeOffsetsPctX10000


def pct_x10000_mul_price(base_price_units, pct_x10000):
    """
    Multiply integer price (units) by percent (x10000) and return delta in the same units.

    Input:
     - base_price_units: int price in exchange units (must be > 0)
     - pct_x10000: int percent in x10000 scale (0..1_000_000 => 0%..100%)

    Output:
     - int delta_units = base_price_units * (pct_x10000 / 10000) / 100

    Errors:
     - Raises on None, wrong types and invalid ranges.
    """
    if base_price_units is None:
        raise RuntimeError('pct_x10000_mul_price: base_price_units is None')

    if pct_x10000 is None:
        raise RuntimeError('pct_x10000_mul_price: pct_x10000 is None')

    if not isinstance(base_price_units, int):
        raise RuntimeError(f'pct_x10000_mul_price: base_price_units is not int: {type(base_price_units)}')

    if not isinstance(pct_x10000, int):
        raise RuntimeError(f'pct_x10000_mul_price: pct_x10000 is not int: {type(pct_x10000)}')

    if base_price_units <= 0:
        raise RuntimeError(f'pct_x10000_mul_price: base_price_units must be > 0, got: {base_price_units}')

    if pct_x10000 < 0:
        raise RuntimeError(f'pct_x10000_mul_price: pct_x10000 must be >= 0, got: {pct_x10000}')

    # delta_units = base * pct / 100
    # pct_x10000 means pct = pct_x10000 / 10000
    # delta = base * pct_x10000 / (100 * 10000) = base * pct_x10000 / 1_000_000
    return int((int(base_price_units) * int(pct_x10000)) // 1_000_000)


def mid_price_units(book):
    """
    Calculate mid-price in integer units from BookTicker.

    Input:
     - book: BookTicker (bid_price and ask_price are int units)

    Output:
     - int mid = (bid + ask) // 2

    Preconditions:
     - bid > 0, ask > 0
     - bid <= ask
    """
    if book is None:
        raise RuntimeError('mid_price_units: book is None')

    if not isinstance(book, BookTicker):
        raise RuntimeError(f'mid_price_units: book is not BookTicker: {type(book)}')

    bid = int(book.bid_price)
    ask = int(book.ask_price)

    if bid <= 0 or ask <= 0:
        raise RuntimeError(f'mid_price_units: bad bid/ask: bid={bid} ask={ask}')

    if bid > ask:
        raise RuntimeError(f'mid_price_units: bid > ask: bid={bid} ask={ask}')

    return int((bid + ask) // 2)


def validate_pct_x10000(value, name):
    """
    Validate that pct_x10000 is an int in the inclusive range [0..1_000_000].

    Input:
     - value: int percent in x10000 scale
     - name: str field name for error messages

    Output:
     - None (raises on invalid input)
    """
    if value is None:
        raise RuntimeError(f'validate_pct_x10000: {name} is None')

    if not isinstance(value, int):
        raise RuntimeError(f'validate_pct_x10000: {name} is not int: {type(value)}')

    if int(value) < 0 or int(value) > 1_000_000:
        raise RuntimeError(f'validate_pct_x10000: {name} must be in [0..1_000_000], got: {value}')


def validate_offsets(name, offsets):
    """
    Validate HedgeOffsetsPctX10000 structure.

    Input:
     - name: str field prefix for error messages
     - offsets: HedgeOffsetsPctX10000 (StrictModel)

    Contract:
     - offsets.long and offsets.short must be valid pct_x10000 values.
    """
    if offsets is None:
        raise RuntimeError(f'validate_offsets: {name} is None')

    if not isinstance(offsets, HedgeOffsetsPctX10000):
        raise RuntimeError(f'validate_offsets: {name} is not HedgeOffsetsPctX10000: {type(offsets)}')

    validate_pct_x10000(int(offsets.long), f'{name}.long')
    validate_pct_x10000(int(offsets.short), f'{name}.short')


def validate_reason(reason, ctx):
    """
    Validate required reason string.

    Input:
     - reason: str (required)
     - ctx: str context prefix for error messages

    Output:
     - str(reason) (raises on invalid input)
    """
    if ctx is None:
        raise RuntimeError('validate_reason: ctx is None')

    if reason is None:
        raise RuntimeError(f'validate_reason: reason is None ctx={ctx}')

    if not isinstance(reason, str):
        raise RuntimeError(f'validate_reason: reason is not str: {type(reason)} ctx={ctx}')

    return str(reason)


def validate_optional_reason(reason, ctx) -> Optional[str]:
    """
    Validate optional reason string.

    Input:
     - reason: Optional[str]
     - ctx: str context prefix for error messages

    Output:
     - None when reason is None
     - str(reason) when provided (raises on invalid input)
    """
    if ctx is None:
        raise RuntimeError('validate_optional_reason: ctx is None')

    if reason is None:
        return None

    if not isinstance(reason, str):
        raise RuntimeError(f'validate_optional_reason: reason is not str: {type(reason)} ctx={ctx}')

    return str(reason)

