"""Normalised area in square feet (Story 2.3). Pure, exact decimal arithmetic, no floats.

`sqft` is the value, `sqyd` is 9 sq ft, `acre` is 43 560 sq ft, a `marla` is the owner's stated
basis (225, 250 or 272.25 sq ft) and a `kanal` is 20 marla. Marla and kanal without a basis, and
a missing value or unit, are not computable: the result is `None` (never a guess).
"""

from decimal import ROUND_HALF_UP, Decimal, localcontext

SQUARE_FEET_PER = {"sqft": Decimal(1), "sqyd": Decimal(9), "acre": Decimal(43560)}
MARLA_PER = {"marla": Decimal(1), "kanal": Decimal(20)}  # units measured in marlas
MARLA_UNITS = tuple(MARLA_PER)  # the units that need a marla basis
SCALE = Decimal("0.0001")


def basis_text(basis: Decimal | None) -> str | None:
    """A stored basis as its enum string: `225`, `250` or `272.25`."""
    if basis is None:
        return None
    return format(basis.normalize(), "f")


def normalised_sqft(
    value: Decimal | None, unit: str | None, basis: Decimal | None
) -> Decimal | None:
    """Square feet rounded half up to four decimals, or None when it is not certain."""
    if value is None or unit is None:
        return None
    with localcontext() as context:
        context.prec = 60
        if unit in SQUARE_FEET_PER:
            exact = value * SQUARE_FEET_PER[unit]
        elif unit in MARLA_PER:
            if basis is None:
                return None
            exact = value * MARLA_PER[unit] * basis
        else:
            return None
        return exact.quantize(SCALE, rounding=ROUND_HALF_UP)
