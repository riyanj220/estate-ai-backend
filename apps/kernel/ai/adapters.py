"""Adapter selection by `settings.AI_PROVIDER`. Only the recorded stub exists so far."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.kernel.ai.stub import stub

ADAPTERS = {"stub": stub}


def get_adapter():
    """One object that implements the LLM, embeddings and speech ports."""
    try:
        return ADAPTERS[settings.AI_PROVIDER]
    except KeyError:
        raise ImproperlyConfigured("AI_PROVIDER names no known adapter.") from None
