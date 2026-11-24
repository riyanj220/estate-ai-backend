from django.db import DEFAULT_DB_ALIAS, migrations

# Drafted for the owner's approval (Story 2.7); editable later by a migration or an admin story.
# Names are plain: letters, digits, spaces, hyphens; no parentheses.
AREAS = (
    "Gulshan-e-Iqbal",
    "Gulistan-e-Jauhar",
    "North Nazimabad",
    "Nazimabad",
    "Federal B Area",
    "Clifton",
    "Saddar",
    "Bahadurabad",
    "PECHS",
    "Tariq Road",
    "Korangi",
    "Landhi",
    "Malir",
    "Shah Faisal Colony",
    "North Karachi",
    "New Karachi",
    "Orangi Town",
    "Liaquatabad",
    "Lyari",
    "Gulberg",
    "Gulshan-e-Maymar",
    "Scheme 33",
    "Surjani Town",
    "Garden",
    "Jamshed Town",
    "Baldia Town",
    "SITE",
    "Manghopir",
    "Kemari",
    "Zamzama",
    "Bath Island",
    "Defence View",
    "Block 13-D Gulshan",
    "Safoora Goth",
    "Gadap Town",
    "Sharfabad",
    "Soldier Bazaar",
    "Mehmoodabad",
    "Clifton Block 5",
)
SOCIETIES = (
    "DHA Phase 1",
    "DHA Phase 2",
    "DHA Phase 2 Extension",
    "DHA Phase 4",
    "DHA Phase 5",
    "DHA Phase 6",
    "DHA Phase 7",
    "DHA Phase 8",
    "DHA City Karachi",
    "Bahria Town Karachi",
    "Naya Nazimabad",
    "Askari 4",
    "Askari 5",
    "Askari 11",
    "Falcon Society",
    "Saima Arabian Villas",
    "KDA Scheme 1",
    "KDA Scheme 33 Society",
    "Karachi Cooperative Housing Society",
    "KAECHS",
    "Gulshan-e-Hadeed",
    "Malir Cantonment",
    "Cantt Karachi",
    "Emaar Crescent Bay",
    "Sunset Boulevard",
    "Gulistan-e-Johar Society",
    "Precinct 1 Bahria",
    "Pakistan Employees Cooperative Housing Society",
    "Memon Society",
    "Al-Hilal Society",
    "Taj Complex",
    "Federal B Area Society",
)


def seed_karachi_localities(apps, schema_editor):
    # Use the migrating connection: the runtime role has no rights until post_migrate.
    alias = schema_editor.connection.alias if schema_editor else DEFAULT_DB_ALIAS
    city = apps.get_model("catalog", "City").objects.using(alias).filter(name__iexact="karachi")
    city = city.first()
    if city is None:  # no Karachi (an admin removed or renamed it): nothing to attach to
        return
    localities = apps.get_model("catalog", "Locality").objects.using(alias)
    # Idempotent: a row of any casing, edited or not, is left exactly as it is.
    existing = {
        name.lower() for name in localities.filter(city=city).values_list("name", flat=True)
    }
    for kind, names in (("area", AREAS), ("society", SOCIETIES)):
        for name in names:
            if name.lower() not in existing:
                localities.create(city=city, name=name, kind=kind)
                existing.add(name.lower())


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_locality")]

    operations = [migrations.RunPython(seed_karachi_localities, migrations.RunPython.noop)]
