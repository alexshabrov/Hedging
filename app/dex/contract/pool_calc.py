import math

def split_capital_into_tokens(total_quote, price, price_lower, price_upper):
    total_quote = float(total_quote)
    price = float(price)
    price_lower = float(price_lower)
    price_upper = float(price_upper)

    if float(total_quote) <= 0:
        raise RuntimeError('total_quote must be > 0')
    if float(price) <= 0:
        raise RuntimeError('price must be > 0')
    if float(price_lower) <= 0 or float(price_upper) <= 0:
        raise RuntimeError('price bounds must be > 0')
    if float(price_lower) >= float(price_upper):
        raise RuntimeError('price_lower must be < price_upper')

    sqrt_p = math.sqrt(float(price))
    sqrt_pl = math.sqrt(float(price_lower))
    sqrt_pu = math.sqrt(float(price_upper))

    base_leg = (1.0 / sqrt_p) - (1.0 / sqrt_pu)
    quote_leg = sqrt_p - sqrt_pl
    if float(base_leg) < 0 or float(quote_leg) < 0:
        raise RuntimeError('invalid range or price')

    # 1) Start from a fixed quote seed and derive base.
    # 2) Scale both amounts proportionally to hit total_quote as closely as possible.
    if float(quote_leg) > 0:
        seed_quote = float(total_quote) * 0.5
        seed_l = float(seed_quote) / float(quote_leg)
        seed_base = float(seed_l) * float(base_leg)
    elif float(base_leg) > 0:
        seed_quote = 0.0
        seed_base = float(total_quote) / float(price)
        seed_l = float(seed_base) / float(base_leg)
    else:
        raise RuntimeError('invalid range or price')

    seed_total_quote = float(seed_quote) + float(seed_base) * float(price)
    if float(seed_total_quote) <= 0:
        raise RuntimeError('invalid seed total quote')

    scale = float(total_quote) / float(seed_total_quote)
    amt_base = float(seed_base) * float(scale)
    amt_quote = float(seed_quote) * float(scale)
    l_val = float(seed_l) * float(scale)

    # Final normalization removes floating drift.
    final_total_quote = float(amt_quote) + float(amt_base) * float(price)
    if float(final_total_quote) <= 0:
        raise RuntimeError('invalid final total quote')
    correction = float(total_quote) / float(final_total_quote)
    amt_base = float(amt_base) * float(correction)
    amt_quote = float(amt_quote) * float(correction)
    l_val = float(l_val) * float(correction)

    return float(amt_base), float(amt_quote), float(l_val)