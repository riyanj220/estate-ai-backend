"""Sample data for development and tests (Stories 2.16 and 11.1): accounts and published Listings.
Never runs in production.

`seed()` creates, as the system actor and through the real services, 8 Individual and 6 Agency
accounts (Active, email-verified, on the reserved `sample.estate-ai.invalid` domain) and a number
of published Karachi Listings with drawn pictures, some with a floor plan and a tour link. Every
Listing is created as a Draft with `apply_updates`, gets its pictures through `upload_image` as
the owner, and is published with `publication.publish(kind="seed")`: no quota is used. `reset()`
removes exactly those records; `reseed()` is both. The data comes from a fixed table, so two runs
make the same Listings. Asking prices follow the same invented locality rates as the price model
(`apps/valuation/data`), so estimates sit near them.

Everything that needs a higher layer (messages, reports, verification and so on) is in
`apps.governance.sample_data`, which calls this module; `manage.py seed_sample_data` runs that.
Nothing here is imported by a migration, a start-up path or an HTTP route.
"""

import contextlib
import os
import random
import secrets
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

from apps.catalog import selectors as catalog
from apps.identity import passwords, services
from apps.identity import selectors as identity
from apps.kernel import sample_art
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.listings import publication, selectors
from apps.listings import services as listings

DEFAULT_LISTINGS = 40
MAX_LISTINGS = 120
CITY_NAME = "Karachi"
LOCAL_MODULES = frozenset({"config.settings.local", "config.settings.test"})
PRODUCTION_MODULE = "config.settings.prod"
SYSTEM = Actor.system("sample-data")


class SeedRefused(Exception):
    """The command may not run here, or its arguments or reference data are wrong. The message is
    one plain line with no private value."""


def _individual(number: int, name: str) -> tuple:
    return ("individual", f"sample.individual.{number}", name, "")


def _agency(number: int, name: str, description: str) -> tuple:
    return ("agency", f"sample.agency.{number}", name, description)


# (kind, email local part, name, description). Invented names; the address domain is
# `identity.SAMPLE_EMAIL_DOMAIN` and cannot receive mail, which is what identifies them.
ACCOUNTS = (
    _individual(1, "Sana Qureshi"),
    _individual(2, "Bilal Ahmed"),
    _individual(3, "Hina Siddiqui"),
    _agency(
        1,
        "Harbour Gate Estates",
        "Residential and commercial property in Clifton and Defence. We list only homes we have "
        "visited, with clear terms and honest advice for first time buyers.",
    ),
    _agency(
        2,
        "Indus Key Realty",
        "Family homes and apartments across Gulshan, Jauhar and North Nazimabad, for sale and "
        "for rent.",
    ),
    _agency(
        3,
        "Margalla Square Properties",
        "Plots and files in Bahria Town, DHA City and Scheme 33, with help on paperwork.",
    ),
    _agency(4, "Seaview Property Partners", "Offices and shops for growing businesses."),
    _agency(5, "Crescent Homes Karachi", "A small family agency working in central Karachi."),
    _agency(6, "Blue Minar Associates", ""),
    _individual(4, "Usman Tariq"),
    _individual(5, "Ayesha Khan"),
    _individual(6, "Farhan Sheikh"),
    _individual(7, "Nadia Hussain"),
    _individual(8, "Omar Farooq"),
)


def _row(owner, kind, title, text, locality, price, area, **more) -> dict:
    purpose, subtype = kind
    category = {"residential_plot": "plot", "commercial_plot": "plot", "plot_file": "plot"}.get(
        subtype, "commercial" if subtype in ("shop", "office") else subtype
    )
    return {
        "owner": owner,
        "kind": (purpose, category, subtype),
        "title": title,
        "text": text,
        "locality": locality,
        "price": (price, "total" if purpose == "sale" else "rental_period"),
        "area": area,
        "extra": {},
        "amenities": (),
        **({"period": "month"} if purpose == "rent" else {}),
        **more,
    }


def _installments(down: str, total: str, monthly: str, remaining: int, kind="installments") -> dict:
    return {
        "payment_type": kind,
        "down_payment": down,
        "total_installment_price": total,
        "monthly_installment": monthly,
        "remaining_installments": remaining,
    }


def _papers(document: str, mortgage: str = "none", approval: str = "", reference: str = "") -> dict:
    values = {"title_document_type": document, "mortgage_declaration": mortgage}
    if approval:
        values["approval_information"] = approval
    if reference:
        values["approval_reference_number"] = reference
    return values


_APARTMENT_AMENITIES = ("elevator", "intercom", "electricity_backup", "nearby_mosque")
_HOUSE_AMENITIES = ("electricity_backup", "nearby_park", "nearby_school", "nearby_mosque")
_SOCIETY_AMENITIES = ("community_mosque", "childrens_play_area", "community_lawn", "nearby_park")
_OFFICE_AMENITIES = ("conference_room", "broadband_internet", "elevator", "atm_in_building")

# One row per Listing, in order. `owner` names an account, prices are PKR text, `extra` holds
# registry field ids beyond the common ones, `amenities` are catalogue codes, `private` is the
# street and house number (private fields, kept on some so private-field tests have data).
# `then` is what `apps.governance.sample_data` does with the Listing after publication, `plan`
# adds a floor plan and `tour` a tour link. The first thirteen rows have thirteen different owners
# (individual.8 stays empty, for the empty screens).
_ROWS = (
    _row(
        "individual.1",
        ("sale", "house"),
        "Spacious 500 sq yd bungalow with a garden",
        "A well kept double storey bungalow with a lawn, a covered car porch and large "
        "bedrooms, close to parks and schools. Quiet street, ready to move in.",
        "DHA Phase 6",
        "138000000",
        ("500", "sqyd"),
        rooms=(5, 5),
        extra={
            "construction_year": 2018,
            "condition": "excellent",
            "total_floors": 2,
            "parking_spaces": 3,
            "servant_quarters": 1,
            "drawing_rooms": 1,
            "possession_availability": "within_one_month",
            "price_negotiable": True,
            **_papers("sale_deed", approval="Building plan approved"),
        },
        amenities=("electricity_backup", "community_lawn", "nearby_park", "nearby_school"),
        private=("Sample Street", "12-A"),
        society="DHA Phase 6",
        block="Block A",
        plan=True,
        tour="clifton-bungalow",
    ),
    _row(
        "individual.2",
        ("sale", "apartment"),
        "Sea facing 3 bedroom apartment in Clifton",
        "Bright apartment on a high floor with a balcony, two parking spaces and round the "
        "clock building security. Walking distance to shops and the promenade.",
        "Clifton",
        "48500000",
        ("2100", "sqft"),
        rooms=(3, 3),
        extra={
            "floor_number": 7,
            "building_floors": 12,
            "lift": True,
            "parking_spaces": 2,
            "backup_electricity": True,
            "building_security": True,
            "condition": "excellent",
            "maintenance_charges": "15000",
            **_papers("sale_deed"),
        },
        amenities=_APARTMENT_AMENITIES,
        plan=True,
    ),
    _row(
        "individual.3",
        ("sale", "residential_plot"),
        "Residential plot of 10 marla in a gated society",
        "Level corner plot in a developed block with paved roads, underground utilities "
        "and a boundary wall. Possession is ready and the file is complete.",
        "Bahria Town Karachi",
        "16500000",
        ("10", "marla", "272.25"),
        extra={
            "possession_status": "possession_ready",
            "development_status": "developed",
            "corner_plot": True,
            "road_width_ft": "40",
            "boundary_wall": True,
            **_installments("4500000", "18800000", "300000", 30, "cash_or_installments"),
            **_papers("allotment_letter", approval="Society approved layout"),
        },
        amenities=_SOCIETY_AMENITIES,
        private=("Sample Avenue", "44"),
    ),
    _row(
        "agency.1",
        ("sale", "shop"),
        "Ground floor shop on a busy main road",
        "Corner shop with a wide frontage and a shutter, on a road with constant foot "
        "traffic. Suitable for retail or a showroom.",
        "Saddar",
        "12500000",
        ("450", "sqft"),
        extra={
            "business_use": "retail",
            "road_access": "main_road",
            "floor_number": 0,
            "frontage_ft": "18",
            **_papers("lease_deed"),
        },
        amenities=("atm_in_building",),
    ),
    _row(
        "agency.2",
        ("rent", "house"),
        "Family house for rent near schools",
        "Three storey house with a drawing room, a separate dining room and a servant "
        "quarter. Gas and water connections are in place; available for a family.",
        "Gulshan-e-Iqbal",
        "120000",
        ("240", "sqyd"),
        rooms=(4, 3),
        extra={
            "security_deposit": "240000",
            "occupant_restrictions": "families_only",
            "total_floors": 3,
            "advance_rent": "120000",
            "minimum_lease_months": 12,
        },
        amenities=("electricity_backup", "nearby_school"),
        private=("Sample Lane", "8"),
    ),
    _row(
        "agency.3",
        ("rent", "apartment"),
        "Furnished 2 bedroom apartment for rent",
        "Fully furnished apartment with air conditioning in both bedrooms, a modern "
        "kitchen and a lounge. Internet ready, lift and backup power in the building.",
        "Bahadurabad",
        "85000",
        ("1100", "sqft"),
        rooms=(2, 2),
        extra={
            "furnishing": "furnished",
            "floor_number": 3,
            "building_floors": 6,
            "lift": True,
            "security_deposit": "170000",
            "utility_internet_included": True,
        },
        amenities=("central_air_conditioning", "broadband_internet", "elevator"),
        tour="bahadurabad-apartment",
    ),
    _row(
        "agency.4",
        ("rent", "office"),
        "Open plan office space for rent",
        "Open plan office on the third floor with a reception area, a meeting room and "
        "a pantry. Central air conditioning and standby power included.",
        "PECHS",
        "150000",
        ("1800", "sqft"),
        extra={
            "business_use": "office_work",
            "floor_number": 3,
            "building_floors": 8,
            "lift": True,
            "conference_rooms": 1,
            "security_deposit": "450000",
        },
        amenities=("conference_room", "business_centre"),
        private=("Sample Boulevard", "3-B"),
        plan=True,
    ),
    _row(
        "agency.5",
        ("sale", "house"),
        "Renovated 240 sq yd house in North Nazimabad",
        "Recently renovated house with new fittings, a modern kitchen and a roof "
        "terrace. Close to the main market and public transport.",
        "North Nazimabad",
        "32500000",
        ("240", "sqyd"),
        rooms=(4, 3),
        extra={
            "condition": "good",
            "parking_spaces": 2,
            "total_floors": 2,
            "construction_year": 2008,
            **_papers("sale_deed", "mortgaged"),
        },
        amenities=("waste_disposal", "nearby_hospital", "nearby_school"),
    ),
    _row(
        "agency.6",
        ("sale", "apartment"),
        "Affordable 2 bedroom flat near university road",
        "Compact flat in a well managed building with a lift, a dedicated parking spot "
        "and a small balcony. A good first home or a rental investment.",
        "Gulistan-e-Jauhar",
        "8600000",
        ("950", "sqft"),
        rooms=(2, 2),
        extra={
            "floor_number": 2,
            "building_floors": 5,
            "balcony_or_terrace": True,
            **_installments("2500000", "9800000", "150000", 48),
            **_papers("transfer_letter"),
        },
        amenities=("elevator", "nearby_school"),
    ),
    _row(
        "individual.4",
        ("sale", "commercial_plot"),
        "Commercial plot on a 120 foot wide road",
        "Prime commercial plot with road access on two sides, suitable for a plaza or "
        "a showroom. Clear papers and all development charges paid.",
        "Scheme 33",
        "41000000",
        ("400", "sqyd"),
        extra={
            "possession_status": "possession_ready",
            "road_width_ft": "120",
            **_papers("lease_deed", approval="Commercial use approved"),
        },
        amenities=(),
    ),
    _row(
        "individual.5",
        ("rent", "house"),
        "Compact 3 bedroom house for rent in Malir",
        "Single storey house with a small courtyard, tiled floors and a covered parking "
        "space. Peaceful neighbourhood with shops and a mosque close by.",
        "Malir",
        "32000",
        ("120", "sqyd"),
        rooms=(3, 2),
        extra={"occupant_restrictions": "no_restriction", "security_deposit": "64000"},
        amenities=("nearby_mosque",),
        private=("Sample Road", "21"),
    ),
    _row(
        "individual.6",
        ("rent", "shop"),
        "Shop for rent in a busy Korangi market",
        "Small shop with a glass front in a crowded market, ready for a tailor, a "
        "pharmacy or a mobile accessories store. Electricity and water available.",
        "Korangi",
        "25000",
        ("300", "sqft"),
        extra={"business_use": "retail", "road_access": "main_road", "floor_number": 0},
        amenities=(),
    ),
    _row(
        "individual.7",
        ("sale", "house"),
        "Askari 5 house with a lawn and basement",
        "Well maintained house in a secure compound with a front lawn, a basement hall "
        "and covered parking for two cars.",
        "Askari 5",
        "59000000",
        ("375", "sqyd"),
        rooms=(4, 4),
        extra={"construction_year": 2015, "condition": "good", "basement": True},
        amenities=_HOUSE_AMENITIES,
    ),
    _row(
        "agency.1",
        ("sale", "apartment"),
        "Clifton Block 5 apartment with a wide balcony",
        "Three bedroom apartment with a wide balcony, a store room and reserved parking in "
        "a quiet building near the park.",
        "Clifton Block 5",
        "47000000",
        ("1800", "sqft"),
        rooms=(3, 3),
        extra={
            "floor_number": 4,
            "building_floors": 9,
            "lift": True,
            "condition": "excellent",
            "balcony_or_terrace": True,
            **_papers("sale_deed"),
        },
        amenities=_APARTMENT_AMENITIES,
        plan=True,
        tour="clifton-block-five",
    ),
    _row(
        "agency.1",
        ("sale", "house"),
        "Luxury 1000 sq yd house with a pool in DHA Phase 8",
        "A large modern house with a swimming pool, a home office, a double height lounge "
        "and landscaped gardens on a wide street.",
        "DHA Phase 8",
        "245000000",
        ("1000", "sqyd"),
        rooms=(6, 7),
        extra={
            "construction_year": 2022,
            "condition": "new",
            "total_floors": 3,
            "parking_spaces": 5,
            "study_rooms": 1,
            "servant_quarters": 2,
            **_papers(
                "sale_deed", approval="Completion certificate issued", reference="SBCA/AP/114"
            ),
        },
        amenities=("swimming_pool", "community_gym", "electricity_backup", "bbq_area"),
        society="DHA Phase 8",
        block="Block C",
        plan=True,
        tour="phase-eight-house",
    ),
    _row(
        "agency.3",
        ("sale", "residential_plot"),
        "500 sq yd plot on a boulevard in DHA Phase 8",
        "Park facing plot on a wide boulevard, level ground and ready to build.",
        "DHA Phase 8",
        "83000000",
        ("500", "sqyd"),
        extra={
            "possession_status": "possession_ready",
            "development_status": "developed",
            "park_facing": True,
            "main_boulevard": True,
            "road_width_ft": "80",
            **_papers("transfer_letter"),
        },
        amenities=("nearby_park",),
        society="DHA Phase 8",
    ),
    _row(
        "agency.1",
        ("sale", "apartment"),
        "Two bedroom apartment in DHA Phase 5",
        "Neat apartment on a middle floor with a lounge, a fitted kitchen and good light.",
        "DHA Phase 5",
        "37000000",
        ("1500", "sqft"),
        rooms=(2, 2),
        extra={"floor_number": 2, "building_floors": 4, "condition": "good"},
        amenities=("intercom", "nearby_mosque"),
        then="sold",
    ),
    _row(
        "agency.2",
        ("sale", "house"),
        "400 sq yd family house in PECHS",
        "Established house on a tree lined street with a large lounge and a rear courtyard. "
        "Some updating would suit a new owner.",
        "PECHS",
        "64000000",
        ("400", "sqyd"),
        rooms=(5, 4),
        extra={
            "construction_year": 1998,
            "condition": "needs_renovation",
            "total_floors": 2,
            **_papers("sale_deed", "disputed"),
        },
        amenities=_HOUSE_AMENITIES,
    ),
    _row(
        "agency.2",
        ("sale", "apartment"),
        "Bright 3 bed apartment in Gulshan-e-Iqbal",
        "Three bedrooms, two balconies and a spacious lounge, a short walk from the main road "
        "and bus stops.",
        "Gulshan-e-Iqbal",
        "13500000",
        ("1250", "sqft"),
        rooms=(3, 2),
        extra={"floor_number": 5, "building_floors": 8, "lift": True, "backup_electricity": True},
        amenities=_APARTMENT_AMENITIES,
        plan=True,
    ),
    _row(
        "agency.3",
        ("sale", "residential_plot"),
        "120 sq yd plot in a developing block",
        "Affordable plot in a block where houses are already going up.",
        "Scheme 33",
        "7600000",
        ("120", "sqyd"),
        extra={
            "possession_status": "possession_pending",
            "development_status": "partially_developed",
            "road_width_ft": "30",
            **_installments("1500000", "8800000", "120000", 60),
        },
        amenities=(),
    ),
    _row(
        "agency.5",
        ("sale", "house"),
        "160 sq yd house in Federal B Area",
        "Solid two storey house with three bedrooms, a terrace and a car porch.",
        "Federal B Area",
        "17000000",
        ("160", "sqyd"),
        rooms=(3, 3),
        extra={"construction_year": 2005, "condition": "good"},
        amenities=("nearby_school", "nearby_mosque"),
        then="archived",
    ),
    _row(
        "agency.2",
        ("sale", "apartment"),
        "North Nazimabad apartment near the market",
        "Second floor apartment with west facing windows, close to shopping and clinics.",
        "North Nazimabad",
        "12800000",
        ("1150", "sqft"),
        rooms=(3, 2),
        extra={"floor_number": 2, "building_floors": 5},
        amenities=("nearby_hospital", "intercom"),
    ),
    _row(
        "agency.3",
        ("sale", "plot_file"),
        "500 sq yd plot file in DHA City Karachi",
        "Open file for a residential plot, offered with the payment record. The buyer takes "
        "over the remaining installments.",
        "DHA City Karachi",
        "16000000",
        ("500", "sqyd"),
        extra={
            "possession_status": "file_only",
            "plot_file_status": "open_file",
            "balloting_status": "balloting_pending",
            **_installments("6000000", "17500000", "250000", 36),
        },
        amenities=(),
    ),
    _row(
        "agency.3",
        ("sale", "house"),
        "New 250 sq yd villa in Bahria Town",
        "Newly built villa in a managed precinct with parks, a mosque and shops inside the gates.",
        "Bahria Town Karachi",
        "22000000",
        ("250", "sqyd"),
        rooms=(4, 4),
        extra={
            "construction_year": 2024,
            "condition": "new",
            **_installments("7000000", "25500000", "350000", 42, "cash_or_installments"),
        },
        amenities=_SOCIETY_AMENITIES,
        tour="bahria-villa",
        plan=True,
    ),
    _row(
        "agency.3",
        ("sale", "residential_plot"),
        "300 sq yd plot in DHA City Karachi",
        "Plot in a planned sector with development work under way.",
        "DHA City Karachi",
        "13500000",
        ("300", "sqyd"),
        extra={
            "possession_status": "possession_pending",
            "development_status": "partially_developed",
        },
        amenities=(),
        then="remove",
    ),
    _row(
        "agency.4",
        ("sale", "office"),
        "Corporate office floor for sale in Clifton",
        "Fitted office floor with a reception, two meeting rooms and reserved parking.",
        "Clifton",
        "38000000",
        ("1200", "sqft"),
        extra={
            "business_use": "office_work",
            "floor_number": 5,
            "building_floors": 10,
            "lift": True,
            "conference_rooms": 2,
        },
        amenities=_OFFICE_AMENITIES,
    ),
    _row(
        "agency.1",
        ("rent", "apartment"),
        "Spacious apartment for rent in DHA Phase 6",
        "Three bedroom apartment with a large lounge, fitted wardrobes and covered parking.",
        "DHA Phase 6",
        "140000",
        ("1600", "sqft"),
        rooms=(3, 3),
        extra={
            "furnishing": "semi_furnished",
            "security_deposit": "280000",
            "minimum_lease_months": 11,
            "floor_number": 1,
            "building_floors": 4,
        },
        amenities=_APARTMENT_AMENITIES,
        tour="phase-six-rental",
    ),
    _row(
        "agency.1",
        ("rent", "house"),
        "500 sq yd house for rent in DHA Phase 4",
        "Independent house with a lawn, five bedrooms and separate staff quarters.",
        "DHA Phase 4",
        "460000",
        ("500", "sqyd"),
        rooms=(5, 5),
        extra={
            "furnishing": "unfurnished",
            "security_deposit": "1380000",
            "servant_quarters": 2,
            "pets_allowed": True,
        },
        amenities=_HOUSE_AMENITIES,
        then="restrict",
        plan=True,
    ),
    _row(
        "agency.2",
        ("rent", "apartment"),
        "Cosy apartment for rent in Gulistan-e-Jauhar",
        "Two bedroom apartment with good ventilation, suitable for a small family.",
        "Gulistan-e-Jauhar",
        "30000",
        ("900", "sqft"),
        rooms=(2, 2),
        extra={"security_deposit": "60000", "utility_water_included": True},
        amenities=("nearby_school",),
        then="rented",
    ),
    _row(
        "agency.4",
        ("rent", "office"),
        "Small office suite for rent on Tariq Road",
        "Ready office suite with partitions, a washroom and a kitchenette.",
        "Tariq Road",
        "70000",
        ("900", "sqft"),
        extra={
            "business_use": "office_work",
            "furnishing": "furnished",
            "floor_number": 2,
            "building_floors": 6,
        },
        amenities=_OFFICE_AMENITIES,
    ),
    _row(
        "agency.4",
        ("rent", "shop"),
        "Showroom style shop for rent in Bahadurabad",
        "Double front shop with display windows on a popular shopping street.",
        "Bahadurabad",
        "80000",
        ("500", "sqft"),
        extra={"business_use": "showroom", "road_access": "main_road", "frontage_ft": "24"},
        amenities=("atm_in_building",),
    ),
    _row(
        "individual.2",
        ("sale", "house"),
        "120 sq yd house in Gulshan-e-Maymar",
        "Tidy single storey house with two bedrooms and room to build above.",
        "Gulshan-e-Maymar",
        "8100000",
        ("120", "sqyd"),
        rooms=(2, 2),
        extra={"construction_year": 2012, "condition": "good"},
        amenities=("nearby_mosque", "nearby_park"),
        then="sold",
    ),
    _row(
        "agency.5",
        ("sale", "apartment"),
        "Apartment in a new project at Scheme 33",
        "Two bedroom apartment in a recently completed project with lifts and parking.",
        "Scheme 33",
        "7400000",
        ("1000", "sqft"),
        rooms=(2, 2),
        extra={
            "condition": "new",
            "lift": True,
            **_installments("1800000", "8600000", "90000", 72),
        },
        amenities=("elevator", "childrens_play_area"),
    ),
    _row(
        "agency.6",
        ("sale", "house"),
        "200 sq yd house in Nazimabad",
        "Older house on a wide street, structurally sound, with four rooms on two floors.",
        "Nazimabad",
        "19500000",
        ("200", "sqyd"),
        rooms=(4, 3),
        extra={"construction_year": 1992, "condition": "needs_renovation"},
        amenities=("nearby_hospital",),
        then="restrict",
    ),
    _row(
        "individual.4",
        ("sale", "residential_plot"),
        "125 sq yd plot in Bahria Town",
        "Plot in an occupied precinct, close to the entrance and the main mosque.",
        "Bahria Town Karachi",
        "7500000",
        ("125", "sqyd"),
        extra={"possession_status": "possession_ready", "development_status": "developed"},
        amenities=("community_mosque",),
        then="archived",
    ),
    _row(
        "individual.1",
        ("rent", "house"),
        "Upper portion house for rent in North Nazimabad",
        "Large house with an open roof, wide rooms and parking for two cars.",
        "North Nazimabad",
        "185000",
        ("400", "sqyd"),
        rooms=(4, 4),
        extra={"security_deposit": "370000", "occupant_restrictions": "families_only"},
        amenities=_HOUSE_AMENITIES,
    ),
    _row(
        "agency.1",
        ("rent", "apartment"),
        "Furnished sea view apartment for rent in Clifton",
        "Furnished apartment with a sea view lounge, three bedrooms and building security.",
        "Clifton",
        "165000",
        ("2000", "sqft"),
        rooms=(3, 3),
        extra={
            "furnishing": "furnished",
            "security_deposit": "495000",
            "building_security": True,
            "floor_number": 9,
            "building_floors": 14,
        },
        amenities=_APARTMENT_AMENITIES,
        plan=True,
    ),
    _row(
        "agency.5",
        ("sale", "residential_plot"),
        "160 sq yd plot in Naya Nazimabad",
        "Developed plot in a gated project with its own parks and a clinic.",
        "Naya Nazimabad",
        "11500000",
        ("160", "sqyd"),
        extra={
            "possession_status": "possession_ready",
            "development_status": "developed",
            "approval_authority": "private_society",
        },
        amenities=("medical_facility", "community_lawn"),
        then="remove",
    ),
    # Deliberately close to another Listing of the same owner: it raises a duplicate flag.
    _row(
        "agency.1",
        ("sale", "apartment"),
        "Clifton Block 5 apartment with wide balcony",
        "Three bedroom apartment with a balcony and reserved parking near the park.",
        "Clifton Block 5",
        "47500000",
        ("1800", "sqft"),
        rooms=(3, 3),
        extra={"floor_number": 4, "building_floors": 9},
        amenities=("elevator",),
    ),
    _row(
        "agency.1",
        ("sale", "house"),
        "350 sq yd house in Malir Cantonment",
        "Single owner house in a quiet lane with a lawn, a terrace and two car porch.",
        "Malir Cantonment",
        "40000000",
        ("350", "sqyd"),
        rooms=(4, 4),
        extra={"construction_year": 2010, "condition": "good"},
        amenities=_HOUSE_AMENITIES,
        then="remove",
    ),
)

# The pictures of each subtype, in order; a Listing gets the first few.
_SCENES = {
    "house": ("house", "lounge", "kitchen", "bedroom", "house"),
    "apartment": ("tower", "lounge", "bedroom", "kitchen"),
    "residential_plot": ("plot", "plot", "plot"),
    "commercial_plot": ("plot", "plot"),
    "plot_file": ("plot", "plot"),
    "shop": ("shop", "shop", "shop"),
    "office": ("tower", "office", "office", "office"),
}


@dataclass
class Report:
    """What a run did, as plain counts. `generated_password` is set only when a new account was
    created without a given password; the caller prints it once and never logs it."""

    accounts_created: int = 0
    accounts_existing: int = 0
    listings_created: int = 0
    images_created: int = 0
    floor_plans_created: int = 0
    activity: dict[str, int] = field(default_factory=dict)  # what `governance` added or removed
    accounts_removed: int = 0
    listings_removed: int = 0
    events_removed: int = 0
    generated_password: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:  # never the password, even in a debugger or a failed assertion
        return (
            f"Report(accounts_created={self.accounts_created}, "
            f"accounts_existing={self.accounts_existing}, "
            f"listings_created={self.listings_created}, images_created={self.images_created}, "
            f"accounts_removed={self.accounts_removed}, listings_removed={self.listings_removed}, "
            f"events_removed={self.events_removed})"
        )


def require_allowed() -> None:
    """Refuse to run under the production settings, or under any settings other than local and
    test unless `SEED_ALLOWED` is set. One plain line, no setting values."""
    # `settings.SETTINGS_MODULE` is None when a test runner configured the settings itself
    module = getattr(settings, "SETTINGS_MODULE", None) or os.environ.get(
        "DJANGO_SETTINGS_MODULE", ""
    )
    if module == PRODUCTION_MODULE:
        raise SeedRefused("Refused: sample data never runs under the production settings.")
    if module not in LOCAL_MODULES and getattr(settings, "SEED_ALLOWED", False) is not True:
        raise SeedRefused(
            "Refused: sample data runs under the local and test settings only (SEED_ALLOWED allows "
            "other development settings)."
        )
    if not _database_is_local() and getattr(settings, "SEED_ALLOWED", False) is not True:
        raise SeedRefused(
            "Refused: the database is not on this machine (SEED_ALLOWED allows a remote "
            "development database)."
        )


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _database_is_local() -> bool:
    host = str(settings.DATABASES.get("default", {}).get("HOST") or "").strip().lower()
    return host == "" or host in LOOPBACK_HOSTS or host.startswith("/")  # empty or a unix socket


def privileged_alias() -> str:
    """The connection that may delete publication events: the migrator alias where one is
    configured (local development), otherwise the default connection (tests, which run as the
    schema owner). The runtime role has no DELETE right on the events table."""
    return "migrator" if "migrator" in settings.DATABASES else "default"


def email_of(local: str) -> str:
    return f"{local}@{identity.SAMPLE_EMAIL_DOMAIN}"


def accounts_missing() -> int:
    """How many of the sample accounts do not exist yet (0 means no password is needed)."""
    present = identity.sample_account_ids()
    return sum(1 for _, local, _, _ in ACCOUNTS if email_of(local).lower() not in present)


def check_count(count) -> int:
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_LISTINGS:
        raise SeedRefused(f"--listings must be a whole number from 1 to {MAX_LISTINGS}.")
    return count


def _new_password() -> str:
    while True:
        candidate = secrets.token_urlsafe(14)
        if passwords.password_error(candidate) is None:
            return candidate


def _city():
    city = next((c for c in catalog.list_cities() if c.name == CITY_NAME), None)
    if city is None:
        raise SeedRefused("The Karachi reference data is missing; run the migrations first.")
    return city


def _ensure_accounts(report: Report, password: str | None, city, announce) -> dict:
    """The owner actors by email local part, creating any account that does not exist (each
    through the identity service, which writes its own audit record)."""
    present = identity.sample_account_ids()
    actors = {}
    for kind, local, name, description in ACCOUNTS:
        email = email_of(local)
        account_id = present.get(email.lower())
        if account_id is None:
            if password is None:
                password = report.generated_password = _new_password()
            try:
                view = services.create_sample_account(
                    SYSTEM,
                    email,
                    password,
                    kind,
                    name,
                    city_id=city.id if kind == "agency" else None,
                    description=description,
                )
            except services.PasswordInvalid:
                raise SeedRefused("The password does not meet the password policy.") from None
            account_id = view.id
            report.accounts_created += 1
        else:
            report.accounts_existing += 1
        actors[local] = Actor.user(account_id, "sample-data")
    if report.generated_password and announce is not None:
        announce(report.generated_password)  # at once: a later failure must not lose it
    return actors


def _png(variant: int) -> bytes:
    """A drawn picture (also used by tests that just need a real image)."""
    return sample_art.scene(sample_art.SCENES[variant % len(sample_art.SCENES)], variant)


def row_for(index: int) -> dict:
    """The data of Listing `index` (0-based): the table row. Each later lap is varied so that
    titles are unique, sizes and prices differ and nothing happens to it after publication,
    deterministically."""
    base = _ROWS[index % len(_ROWS)]
    lap = index // len(_ROWS)
    row = dict(base)
    if lap:
        jitter = random.Random(2026 + index).randint(-5, 5)  # noqa: S311 - not a secret
        scale = Decimal(100 + 15 * lap) / 100
        amount, basis = base["price"]
        value, *unit = base["area"]
        row["area"] = (str(int(Decimal(value) * scale)), *unit)
        row["price"] = (str(max(1, int(int(amount) * scale * (100 + jitter) / 100))), basis)
        row["title"] = f"{base['title']} (no. {lap + 1})"
        row.pop("then", None)
    return row


def owner_local(row: dict) -> str:
    return f"sample.{row['owner']}"


def _values(row: dict, locality_ids: dict, city) -> dict:
    values = {
        "title": row["title"],
        "description": row["text"],
        "asking_price": row["price"][0],
        "price_basis": row["price"][1],
        "area_value": row["area"][0],
        "area_unit": row["area"][1],
        "city": str(city.id),
        "locality": str(locality_ids[row["locality"]]),
        **row["extra"],
    }
    if len(row["area"]) > 2:
        values["marla_basis"] = row["area"][2]
    if row.get("tour"):
        values["video_tour_url"] = f"https://tours.example.com/{row['tour']}"
    if "period" in row:
        values["rental_period"] = row["period"]
    if "rooms" in row:
        values["bedrooms"], values["bathrooms"] = row["rooms"]
    if row.get("society"):
        values["society"] = row["society"]
    if row.get("block"):
        values["block"] = row["block"]
    if row.get("private"):
        values["street"], values["house_number"] = row["private"]
    if row["amenities"]:
        values["amenities"] = dict.fromkeys(row["amenities"], "present")
    return values


def _locality_ids(city) -> dict:
    """The ids of the localities the table names. A missing one is an error (the reference data
    differs from what the table expects), never a silent substitute."""
    found = {loc.name: loc.id for loc in catalog.list_localities(city.id) or ()}
    missing = sorted({row["locality"] for row in _ROWS} - set(found))
    if missing:
        raise SeedRefused(f"Karachi locality reference data is missing: {', '.join(missing)}.")
    return found


def _make_listing(index: int, owner: Actor, city, localities: dict, report: Report) -> None:
    row = row_for(index)
    draft = listings.create_draft(owner, *row["kind"])
    try:
        listings.apply_updates(owner, draft.id, draft.revision, _values(row, localities, city))
        scenes = _SCENES[row["kind"][2]]
        for number, name in enumerate(scenes[: 2 + index % 4]):
            listings.upload_image(owner, draft.id, sample_art.scene(name, index * 7 + number))
            report.images_created += 1
        if row.get("plan"):
            listings.set_floor_plan(owner, draft.id, sample_art.floor_plan(index))
            report.floor_plans_created += 1
        current = selectors.get_owned_listing(owner, draft.id)
        publication.publish(
            SYSTEM,
            draft.id,
            current.revision,
            f"seed-{index:03d}",
            kind=publication.KIND_SEED,
        )
    except BaseException:
        with contextlib.suppress(DomainError):  # no half-made sample Listing stays behind
            listings.delete_draft(owner, draft.id)
        raise
    report.listings_created += 1


@contextlib.contextmanager
def estimates_in_this_thread():
    """Publishing starts a price estimate in the background. A one-process tool has nobody
    waiting, so here each estimate runs in the calling thread and is stored when it returns."""
    before = settings.VALUATION_BACKGROUND_ESTIMATES
    settings.VALUATION_BACKGROUND_ESTIMATES = False
    try:
        yield
    finally:
        settings.VALUATION_BACKGROUND_ESTIMATES = before


def seed(
    listings_wanted: int = DEFAULT_LISTINGS, password: str | None = None, announce=None
) -> Report:
    """Create what is missing: accounts, then Listings 0..N-1 that do not exist yet (found by
    owner and title). Safe to repeat: a second run creates and changes nothing. A password is
    needed only when an account is created; none given means a random one (in the report, and
    passed to `announce` as soon as the accounts exist). A Listing whose owner may no longer
    publish (a suspended sample account) is skipped."""
    require_allowed()
    wanted = check_count(listings_wanted)
    report = Report()
    city = _city()
    localities = _locality_ids(city)
    owners = _ensure_accounts(report, password, city, announce)
    existing = {local: selectors.titles_owned_by(a.account_id) for local, a in owners.items()}
    active = {
        local
        for local, actor in owners.items()
        if identity.get_account(actor.account_id).state is identity.AccountState.ACTIVE
    }
    with estimates_in_this_thread():
        for index in range(wanted):
            row = row_for(index)
            local = owner_local(row)
            found = existing[local].get(row["title"])
            if found is not None and found[1] != "draft":
                continue  # already there
            if local not in active:
                continue
            if found is not None:  # a Draft left by an interrupted run: start that Listing again
                listings.delete_draft(owners[local], found[0])
            _make_listing(index, owners[local], city, localities, report)
    return report


def seeded_listings(count: int = MAX_LISTINGS) -> list[tuple]:
    """(index, row, owner actor, listing id, status) of each table Listing that exists."""
    accounts = identity.sample_account_ids()
    titles: dict = {}
    found = []
    for index in range(count):
        row = row_for(index)
        account_id = accounts.get(email_of(owner_local(row)))
        if account_id is None:
            continue
        if account_id not in titles:
            titles[account_id] = selectors.titles_owned_by(account_id)
        entry = titles[account_id].get(row["title"])
        if entry is not None:
            actor = Actor.user(account_id, "sample-data")
            found.append((index, row, actor, entry[0], entry[1]))
    return found


def reset() -> Report:
    """Remove every record the command created and nothing else: the Listings of the sample
    accounts with their images, the `seed` publication events of those accounts (through the
    privileged connection), then the sample accounts. Audit records stay, as audit."""
    require_allowed()
    report = Report()
    accounts = identity.sample_account_ids()
    for listing_id in selectors.listing_ids_owned_by(accounts.values()):
        if listings.remove_sample_listing(SYSTEM, listing_id):
            report.listings_removed += 1
    report.events_removed = publication.remove_seed_events(
        SYSTEM, accounts.values(), using=privileged_alias()
    )
    for account_id in accounts.values():
        if services.remove_sample_account(SYSTEM, account_id):
            report.accounts_removed += 1
    return report


def preview_reset() -> Report:
    """What `reset` would remove, as counts (nothing is changed)."""
    require_allowed()
    accounts = identity.sample_account_ids()
    report = Report()
    report.accounts_removed = len(accounts)
    report.listings_removed = len(selectors.listing_ids_owned_by(accounts.values()))
    report.events_removed = publication.count_seed_events(accounts.values())
    return report


def reseed(
    listings_wanted: int = DEFAULT_LISTINGS, password: str | None = None, announce=None
) -> Report:
    """`reset` then `seed`; the report adds both."""
    check_count(listings_wanted)
    require_allowed()
    removed = reset()
    made = seed(listings_wanted, password, announce)
    made.accounts_removed = removed.accounts_removed
    made.listings_removed = removed.listings_removed
    made.events_removed = removed.events_removed
    return made
