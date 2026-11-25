"""The reserved address domain of the sample accounts (Story 2.16): the ONE definition.

`.invalid` is reserved by RFC 2606 and can never receive mail. Two matchers share one rule, a
lowercase suffix match on the exact domain (`SUFFIX`): `is_sample_email` in Python and
`selectors.sample_accounts_q` in SQL. A subdomain, another TLD, a lookalike or a non-ASCII
spelling is NOT a sample address. `is_reserved_domain` is wider (the domain and any subdomain):
no registration, email change, reset request or administrator may use it, except the
system-only sample service through an explicit internal bypass.
"""

SAMPLE_EMAIL_DOMAIN = "sample.estate-ai.invalid"
SUFFIX = "@" + SAMPLE_EMAIL_DOMAIN


def is_sample_email(email) -> bool:
    """Exactly an address on the reserved domain (what reset and removal may touch)."""
    if not isinstance(email, str) or not email.isascii():
        return False  # ASCII only: Python and PostgreSQL lowercase non-ASCII letters differently
    return email.strip().lower().endswith(SUFFIX)


def is_reserved_domain(email) -> bool:
    """The reserved domain or any subdomain of it: refused as input everywhere else."""
    if not isinstance(email, str):
        return False
    domain = email.strip().lower().rpartition("@")[2]
    return domain == SAMPLE_EMAIL_DOMAIN or domain.endswith("." + SAMPLE_EMAIL_DOMAIN)
