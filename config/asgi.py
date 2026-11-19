import os

from django.core.asgi import get_asgi_application

# No default: the settings module must be chosen explicitly (scripts/dev_server.sh
# selects local), so a deployment can never fall back to development settings.
if "DJANGO_SETTINGS_MODULE" not in os.environ:
    raise RuntimeError("DJANGO_SETTINGS_MODULE must be set.")

application = get_asgi_application()
