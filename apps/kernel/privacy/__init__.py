"""Privacy helpers: reasoned private reads and the shared contact-detail patterns."""

from apps.kernel.privacy.patterns import (
    EMAIL_ADDRESS_SHAPE,
    LONG_DIGIT_RUN,
    ContactKind,
    Finding,
    find_contact_details,
    reject_contact_details,
)

_READS = ("PrivateTargetRef", "record_private_read", "register_private_target")


def __getattr__(name):
    # Lazy: `reads` imports the audit writer, which imports `patterns` from this package.
    if name in _READS:
        from apps.kernel.privacy import reads

        return getattr(reads, name)
    raise AttributeError(name)


__all__ = [
    "EMAIL_ADDRESS_SHAPE",
    "LONG_DIGIT_RUN",
    "ContactKind",
    "Finding",
    "PrivateTargetRef",
    "find_contact_details",
    "record_private_read",
    "register_private_target",
    "reject_contact_details",
]
