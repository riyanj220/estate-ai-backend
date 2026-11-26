"""Story 4.2: the forecast scenario parameters are versioned, documented and fixed."""

import hashlib

from apps.valuation.data import forecast, localities

RELEASED = {
    1: "c14d0855b83e7562725d7d35cf589f66216b9a24748bf9833e5e84ee6fbfc0ba"
}  # a released file never changes: add a new version instead


def test_the_parameters_cover_every_tier_and_are_ordered():
    parameters = forecast.load()
    assert parameters.horizon_years == 5
    assert parameters.localities_version == localities.load().version
    assert set(parameters.annual_growth) == set(localities.load().tiers.values())
    assert all(g.low <= g.base <= g.high for g in parameters.annual_growth.values())


def test_released_files_are_unchanged():
    assert set(RELEASED) == set(range(1, forecast.FORECAST_VERSION + 1))
    for version, expected in RELEASED.items():
        assert hashlib.sha256(forecast.path_of(version).read_bytes()).hexdigest() == expected
