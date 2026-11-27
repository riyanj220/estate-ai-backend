"""Removal of the sample accounts' Conversations (Story 11.1). Development tooling only: the
sample-data command (`apps.governance.sample_data`) is the one caller; no route reaches it."""

from django.db.models import Q

from apps.identity import selectors as identity
from apps.kernel.actor import Actor, ActorKind
from apps.kernel.errors import DomainError
from apps.messaging.models import Conversation


def _of(account_ids):
    ids = [key for key in account_ids if identity.is_sample_account(key)]  # sample accounts only
    return Conversation.objects.filter(
        Q(initiator_account_id__in=ids) | Q(owner_account_id__in=ids)
    )


def count_conversations(account_ids) -> int:
    return _of(account_ids).count()


def remove_conversations(actor: Actor, account_ids) -> int:
    """Delete every Conversation a sample account takes part in, with its Messages. System actor
    only (`forbidden` otherwise); an id that is not a sample account is ignored."""
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("forbidden")
    found = _of(account_ids)
    count = found.count()
    found.delete()
    return count
