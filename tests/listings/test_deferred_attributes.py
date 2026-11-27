"""Stories 11.3 and 11.4: the deferred sale attributes (`ListingTerms.extra_attributes`) and the
video or virtual tour link."""

import pytest

from apps.catalog import extraction, filters, registry
from apps.listings import publication, selectors, services, validation
from apps.listings.models import ListingTerms, PropertyListing
from tests.listings.support import (
    DEFERRED_IDS,
    HOUSE,
    SYSTEM,
    make_account,
    publish_ready,
    random_email,
    seed_cap,
    user,
)

RENT_HOUSE = ("rent", "house", "house")
VALUES = {
    "payment_type": "installments",
    "down_payment": "500000",
    "total_installment_price": "9000000.50",
    "monthly_installment": "25000",
    "remaining_installments": 36,
    "title_document_type": "allotment_letter",
    "approval_information": "Approved by the society office",
    "approval_reference_number": "KDA/AB-12",
    "mortgage_declaration": "none",
}


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()


def draft(kind=HOUSE):
    account = make_account(random_email())
    return account, services.create_draft(user(account), *kind)


def update(account, view, updates, origin="manual"):
    return services.apply_updates(user(account), view.id, view.revision, updates, origin)


def codes(account, view, updates):
    with pytest.raises(services.FieldsInvalid) as caught:
        update(account, view, updates)
    return caught.value.fields


def test_the_values_are_stored_in_extra_attributes_and_read_back():
    account, view = draft()
    saved = update(account, view, VALUES)
    assert ListingTerms.objects.get().extra_attributes == {
        **VALUES,
        "down_payment": "500000.00",
        "total_installment_price": "9000000.50",
        "monthly_installment": "25000.00",
    }
    assert saved.fields["down_payment"] == "500000.00"
    assert saved.fields["remaining_installments"] == 36
    again = selectors.get_owned_listing(user(account), view.id)
    assert {name: again.fields[name] for name in DEFERRED_IDS} == {
        name: saved.fields[name] for name in DEFERRED_IDS
    }
    same = update(account, saved, {"down_payment": "500000.00"})
    assert same.revision == saved.revision  # an unchanged value writes nothing
    cleared = update(account, saved, {"down_payment": None, "payment_type": None})
    assert cleared.fields["down_payment"] is None
    assert "down_payment" not in ListingTerms.objects.get().extra_attributes


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("payment_type", "barter", "invalid_choice"),
        ("down_payment", "-1", "invalid_price"),
        ("remaining_installments", 601, "invalid_range"),
        ("remaining_installments", "3", "invalid"),
        ("approval_information", "Call 0300 1234567", "contains_contact_details"),
        ("approval_reference_number", "x" * 61, "too_long"),
        ("mortgage_declaration", True, "invalid"),
    ],
)
def test_bad_values_are_refused(name, value, code):
    account, view = draft()
    assert codes(account, view, {name: value}) == {name: code}
    assert ListingTerms.objects.get().extra_attributes == {}


@pytest.mark.parametrize("name", DEFERRED_IDS)
def test_they_apply_to_sale_listings_only(name):
    account, view = draft(RENT_HOUSE)
    assert codes(account, view, {name: VALUES[name]}) == {name: "not_applicable"}


def test_none_is_a_model_input_or_identity_and_claims_are_never_extracted():
    for name in (*DEFERRED_IDS, "video_tour_url"):
        definition = registry.field(name)
        assert not (definition.valuation_input or definition.quota_identity), name
        assert definition.requirement == "optional" and definition.field_class == "public"
        assert not (definition.seller_declared and definition.ai_extractable), name
    sale = extraction.extraction_schema("sale", "house")["properties"]
    assert {"payment_type", "down_payment", "remaining_installments"} <= set(sale)
    assert not {"title_document_type", "mortgage_declaration", "video_tour_url"} & set(sale)
    assert "payment_type" not in extraction.extraction_schema("rent", "house")["properties"]


def test_the_enums_are_filters_and_the_fingerprint_ignores_every_new_field():
    searchable = {item.id for item in filters.filterable()}
    assert {"payment_type", "title_document_type", "mortgage_declaration"} <= searchable
    account = make_account(random_email())
    listing_id = publish_ready(account.id)
    view = selectors.get_owned_listing(user(account), listing_id)
    before = publication.identity_fingerprint(view)
    edited = update(account, view, {**VALUES, "video_tour_url": "https://youtu.be/dQw4w9WgXcQ"})
    assert publication.identity_fingerprint(edited) == before

    def found(**params):
        spec = filters.parse({name: [value] for name, value in params.items()})
        return [e.listing.id for e in selectors.search_public(SYSTEM, spec).items]

    assert found(payment_type="installments,cash") == [listing_id]
    assert found(mortgage_declaration="none", title_document_type="allotment_letter") == [
        listing_id
    ]
    assert found(payment_type="cash") == []
    assert found(prefer_payment_type="installments") == [listing_id]
    shown = selectors.public_listing(SYSTEM, listing_id).fields
    assert shown["payment_type"] == "installments"
    assert shown["video_tour_url"] == "https://youtu.be/dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://vimeo.com/76979871",
        "https://my.matterport.com/show/?m=SxQL3iGyoDo",
    ],
)
def test_a_tour_link_is_accepted(url):
    account, view = draft(RENT_HOUSE)
    assert update(account, view, {"video_tour_url": f"  {url} "}).fields["video_tour_url"] == url
    assert PropertyListing.objects.get().video_tour_url == url


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://youtu.be/abc", "invalid_url"),
        ("javascript:alert(1)", "invalid_url"),
        ("youtu.be/abc", "invalid_url"),
        ("https://localhost/tour", "invalid_url"),
        ("https://127.0.0.1/tour", "invalid_url"),
        ("https://user@example.com/tour", "invalid_url"),
        ("https://example.com:8443/tour", "invalid_url"),
        ("https://example.com/a tour", "invalid_url"),
        ("https://", "invalid_url"),
        ("https://wa.me/923001234567", "contains_contact_details"),
        ("https://chat.whatsapp.com/abc", "contains_contact_details"),
        ("https://t.me/someone", "contains_contact_details"),
        ("https://example.com/call-0300-1234567", "contains_contact_details"),
        ("https://example.com/?mail=seller%40example.com", "contains_contact_details"),
        ("https://example.com/" + "a" * 300, "too_long"),
        (5, "invalid"),
    ],
)
def test_a_bad_link_is_refused(url, code):
    account, view = draft()
    assert codes(account, view, {"video_tour_url": url}) == {"video_tour_url": code}
    assert validation.RAW_TEXT_LIMIT  # the cap is shared with text
    assert PropertyListing.objects.get().video_tour_url is None
