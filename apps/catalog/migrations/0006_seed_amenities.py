from django.db import DEFAULT_DB_ALIAS, migrations

# The amenity vocabulary of Story 2.8 (owner decision OQ-1: five nearby types). Each entry is
# (code, label, kind, group). `sort_order` follows this order. Editable later by a migration or an
# admin story; never by hand-editing this list after it ran.
AMENITIES = (
    ("electricity_backup", "Electricity backup", "on_property", "utilities"),
    ("broadband_internet", "Broadband internet", "on_property", "utilities"),
    ("waste_disposal", "Waste disposal", "on_property", "utilities"),
    ("double_glazed_windows", "Double-glazed windows", "on_property", "building"),
    ("central_air_conditioning", "Central air conditioning", "on_property", "building"),
    ("central_heating", "Central heating", "on_property", "building"),
    ("elevator", "Elevator", "on_property", "building"),
    ("lobby", "Lobby", "on_property", "building"),
    ("intercom", "Intercom", "on_property", "building"),
    ("community_lawn", "Community lawn", "community", "residential_community"),
    ("swimming_pool", "Swimming pool", "community", "residential_community"),
    ("community_gym", "Gym", "community", "residential_community"),
    ("daycare", "Daycare", "community", "residential_community"),
    ("childrens_play_area", "Children's play area", "community", "residential_community"),
    ("bbq_area", "BBQ area", "community", "residential_community"),
    ("community_mosque", "Community mosque", "community", "community_services"),
    ("medical_facility", "Medical facility", "community", "community_services"),
    ("community_centre", "Community centre", "community", "community_services"),
    ("conference_room", "Conference room", "on_property", "business"),
    ("business_centre", "Business centre", "on_property", "business"),
    ("atm_in_building", "ATM in building", "on_property", "business"),
    ("nearby_mosque", "Mosque", "nearby", "nearby"),
    ("nearby_park", "Park", "nearby", "nearby"),
    ("nearby_gym", "Gym", "nearby", "nearby"),
    ("nearby_school", "School", "nearby", "nearby"),
    ("nearby_hospital", "Hospital", "nearby", "nearby"),
)


def seed_amenities(apps, schema_editor):
    # Use the migrating connection: the runtime role has no rights until post_migrate.
    alias = schema_editor.connection.alias if schema_editor else DEFAULT_DB_ALIAS
    amenities = apps.get_model("catalog", "Amenity").objects.using(alias)
    # Idempotent: a row with a known code, edited or deactivated, is left exactly as it is.
    existing = set(amenities.values_list("code", flat=True))
    for position, (code, label, kind, group) in enumerate(AMENITIES, start=1):
        if code not in existing:
            amenities.create(code=code, label=label, kind=kind, group=group, sort_order=position)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0005_amenity")]

    operations = [migrations.RunPython(seed_amenities, migrations.RunPython.noop)]
