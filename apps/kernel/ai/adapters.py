"""Adapter selection by `settings.AI_PROVIDER`: the recorded stub (default) or Gemini."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.kernel.ai.gemini import gemini
from apps.kernel.ai.stub import stub

ADAPTERS = {"stub": stub, "gemini": gemini}


def get_adapter():
    """One object that implements the LLM, embeddings and speech ports."""
    try:
        adapter = ADAPTERS[settings.AI_PROVIDER]
    except KeyError:
        raise ImproperlyConfigured("AI_PROVIDER names no known adapter.") from None
    if adapter is gemini and not settings.GEMINI_API_KEY:
        raise ImproperlyConfigured("AI_PROVIDER=gemini needs GEMINI_API_KEY.")
    return adapter
