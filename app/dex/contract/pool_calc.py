import math

def split_capital_into_tokens(total_quote, price, price_lower, price_upper):
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

    denominator = (1.0 / sqrt_p - 1.0 / sqrt_pu) * float(price) + (sqrt_p - sqrt_pl)
    if denominator <= 0:
        raise RuntimeError('invalid range or price')

    l_val = float(total_quote) / denominator
    amt_base = l_val * (1.0 / sqrt_p - 1.0 / sqrt_pu)
    amt_quote = l_val * (sqrt_p - sqrt_pl)

    return float(amt_base), float(amt_quote), float(l_val)