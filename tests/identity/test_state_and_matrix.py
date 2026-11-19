import itertools
import uuid

import pytest
from django.utils import timezone

from apps.identity import capabilities as caps
from apps.identity.capabilities import MATRIX, PENDING_CAPABILITIES, Access, Capability
from apps.identity.models import Account
from apps.identity.policy import can
from apps.identity.state import AccountState, derive_state
from apps.kernel.actor import Actor

S = AccountState
C = Capability

# PRD FR-74, written out independently of the implementation.
# fmt: off
EXPECTED_ALLOWED = {
    S.EMAIL_UNVERIFIED: {
        C.SIGN_IN, C.BROWSE, C.VIEW_DRAFTS, C.EDIT_DRAFTS, C.READ_CONVERSATIONS,
        C.REPORT, C.REQUEST_SUPPORT, C.REQUEST_DEACTIVATION,
    },
    S.ACTIVE: set(C),
    S.SUSPENDED: {
        C.SIGN_IN, C.BROWSE, C.VIEW_DRAFTS, C.READ_CONVERSATIONS,
        C.REQUEST_SUPPORT, C.REQUEST_DEACTIVATION,
    },
    S.DEACTIVATED: {C.BROWSE},
}
# fmt: on


@pytest.mark.parametrize(
    ("verified", "suspended", "deactivated", "expected"),
    [
        (
            v,
            s,
            d,
            S.DEACTIVATED if d else S.SUSPENDED if s else S.ACTIVE if v else S.EMAIL_UNVERIFIED,
        )
        for v, s, d in itertools.product([False, True], repeat=3)
    ],
)
def test_state_derivation(verified, suspended, deactivated, expected):
    assert (
        derive_state(email_verified=verified, suspended=suspended, deactivated=deactivated)
        == expected
    )


def test_there_are_ten_capabilities_and_four_states():
    assert len(C) == 10
    assert set(MATRIX) == set(S)
    assert all(set(row) == set(C) for row in MATRIX.values())


@pytest.mark.parametrize("state", list(S))
@pytest.mark.parametrize("capability", list(C))
def test_every_cell_matches_fr74(state, capability):
    want = Access.ALLOWED if capability in EXPECTED_ALLOWED[state] else Access.DENIED
    assert caps.matrix_access(state, capability) == want


def test_pending_is_everything_except_sign_in_and_browse():
    assert PENDING_CAPABILITIES == set(C) - {C.SIGN_IN, C.BROWSE}


def make(**fields):
    n = uuid.uuid4().hex[:8]
    return Account.objects.create(
        email=f"{n}@example.com", password="x", account_type="individual", **fields
    )


def account_in(state):
    from django.utils import timezone

    now = timezone.now()
    return {
        S.EMAIL_UNVERIFIED: lambda: make(),
        S.ACTIVE: lambda: make(email_verified_at=now),
        S.SUSPENDED: lambda: make(email_verified_at=now, suspended_at=now, suspension_reason="r"),
        S.DEACTIVATED: lambda: make(email_verified_at=now, deactivated_at=now),
    }[state]()


@pytest.mark.parametrize("state", list(S))
@pytest.mark.parametrize("capability", list(C))
def test_can_for_users_reads_the_table_and_hides_pending(db, state, capability):
    actor = Actor.user(account_in(state).id)
    live = capability not in PENDING_CAPABILITIES
    assert can(actor, capability) == (live and capability in EXPECTED_ALLOWED[state])


@pytest.mark.parametrize("capability", list(C))
def test_activated_capability_follows_the_table(db, monkeypatch, capability):
    monkeypatch.setattr("apps.identity.policy.PENDING_CAPABILITIES", frozenset())
    for state in S:
        actor = Actor.user(account_in(state).id)
        assert can(actor, capability) == (capability in EXPECTED_ALLOWED[state])


@pytest.mark.parametrize("capability", list(C))
def test_visitor_browses_only(capability):
    assert can(Actor.visitor(), capability) == (capability is C.BROWSE)


def admin_account(**fields):
    return Account.objects.create(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password="x",
        account_type="admin",
        **fields,
    )


@pytest.mark.parametrize("capability", list(C))
def test_admin_signs_in_and_browses_only(db, capability):
    admin = Actor.admin(admin_account(email_verified_at=timezone.now()).id)
    assert can(admin, capability) == (capability in (C.SIGN_IN, C.BROWSE))


@pytest.mark.parametrize("capability", list(C))
def test_suspended_admin_keeps_sign_in_and_browse(db, capability):
    now = timezone.now()
    admin = Actor.admin(admin_account(suspended_at=now, suspension_reason="r").id)
    assert can(admin, capability) == (capability in (C.SIGN_IN, C.BROWSE))


@pytest.mark.parametrize("capability", list(C))
def test_deactivated_or_missing_admin_is_denied(db, capability):
    gone = Actor.admin(admin_account(deactivated_at=timezone.now()).id)
    assert not can(gone, capability)
    assert not can(Actor.admin(uuid.uuid4()), capability)


@pytest.mark.parametrize("capability", list(C))
def test_admin_type_account_as_user_gets_admin_capabilities_only(db, capability):
    actor = Actor.user(admin_account(email_verified_at=timezone.now()).id)
    assert can(actor, capability) == (capability in (C.SIGN_IN, C.BROWSE))


@pytest.mark.parametrize("capability", list(C))
def test_system_is_allowed_everything(capability):
    assert can(Actor.system(), capability)


@pytest.mark.parametrize("capability", [C.SIGN_IN, C.BROWSE])
def test_missing_account_is_denied(db, capability):
    assert not can(Actor.user(uuid.uuid4()), capability)


def test_deactivated_cannot_sign_in_suspended_can(db):
    assert not can(Actor.user(account_in(S.DEACTIVATED).id), C.SIGN_IN)
    assert can(Actor.user(account_in(S.SUSPENDED).id), C.SIGN_IN)


def test_user_check_is_one_query(db, django_assert_num_queries):
    actor = Actor.user(account_in(S.ACTIVE).id)
    with django_assert_num_queries(1):
        can(actor, C.BROWSE)


def test_unknown_capability_is_an_error():
    with pytest.raises(ValueError):
        can(Actor.visitor(), "fly")
