"""Story 4.2: the model's inputs are registry fields flagged `valuation_input`, and no others."""

import math

import pytest

from apps.catalog import registry
from apps.valuation import features

VALUES = {
    "subtype": "house",
    "locality": "Clifton",
    "area_value": "10",
    "area_unit": "marla",
    "marla_basis": "225",
    "bedrooms": 4,
    "amenities": {"nearby_park": "present", "gym": "none"},
}


class Recording(dict):
    def __init__(self, *args):
        super().__init__(*args)
        self.read = set()

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)


@pytest.mark.guard
def test_the_feature_sources_are_a_subset_of_the_flagged_valuation_inputs():
    flagged = {f.id for f in registry.fields() if f.valuation_input}
    private = {f.id for f in registry.fields() if f.field_class == "private"}
    assert set(features.SOURCE_FIELDS) <= flagged
    assert not set(features.SOURCE_FIELDS) & (private | {"asking_price", "price_basis", "phone"})


@pytest.mark.guard
def test_private_address_phone_and_asking_price_are_never_read():
    canaries = {
        "street": "CANARY STREET",
        "house_number": "CANARY-12",
        "latitude": "24.8",
        "longitude": "67.0",
        "phone": "+923001234567",
        "asking_price": "123456789",
        "title": "CANARY TITLE",
    }
    values = Recording({**VALUES, **canaries})
    built = features.build_features(values)
    assert values.read == set(features.SOURCE_FIELDS)
    assert repr(built) == repr(features.build_features(VALUES))
    assert tuple(built) == features.FEATURE_NAMES


def test_area_is_normalised_and_missing_optionals_are_nan():
    built = features.build_features(VALUES)
    assert built["log_area_sqft"] == pytest.approx(math.log(2250))
    assert built["amenity_count"] == 1 and math.isnan(built["bathrooms"])
    assert math.isnan(built["building_age"]) and math.isnan(built["condition"])


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"subtype": "shop"}, "subtype"),
        ({"locality": None}, "locality"),
        ({"marla_basis": None}, "area"),
        ({"area_value": None}, "area"),
    ],
)
def test_unusable_inputs_are_refused(change, reason):
    with pytest.raises(features.UnsupportedInput, match=reason):
        features.build_features({**VALUES, **change})


def test_a_locality_the_model_does_not_know_is_refused():
    with pytest.raises(features.UnsupportedInput, match="locality"):
        features.encode(features.build_features(VALUES), ["Saddar"])
