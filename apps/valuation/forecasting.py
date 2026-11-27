"""The 5-Year Price Forecast (Story 4.7): low, base and high scenarios from the estimate and the
versioned yearly growth rate of the locality's tier. Pure and reproducible; never a promise."""

from decimal import ROUND_HALF_UP, Decimal

from apps.valuation.data import forecast, localities


def compute(value: Decimal, locality: str, version: int = forecast.FORECAST_VERSION) -> dict:
    """The stored shape: `value * (1 + rate) ** horizon` per scenario, to the nearest PKR 1 000,
    as decimal strings. `locality` is a covered locality name."""
    parameters = forecast.load(version)
    growth = parameters.annual_growth[localities.load().tiers[locality]]

    def after(rate: Decimal) -> str:
        total = value * (1 + rate) ** parameters.horizon_years
        thousands = (total / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return f"{thousands * 1000:.2f}"

    return {
        "version": parameters.version,
        "horizon_years": parameters.horizon_years,
        "low": after(growth.low),
        "base": after(growth.base),
        "high": after(growth.high),
    }
