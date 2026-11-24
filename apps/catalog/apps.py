from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "apps.catalog"
    label = "catalog"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.catalog import registry
        from apps.kernel import audit

        registry.load()  # refuse to start on an invalid Field registry
        audit.register_action("city.created", {})
        audit.register_action("locality.created", {})
        audit.register_action("amenity.created", {})
