import uuid

import pytest
from django.db import DatabaseError, transaction
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.kernel import audit
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord
from apps.kernel.privacy import reads
from apps.presentation.api.private_reads import ReasonedReadMixin, ReasonedReadSerializer

ADMIN = uuid.UUID("33333333-3333-4333-8333-333333333333")
TARGET = uuid.UUID("44444444-4444-4444-8444-444444444444")
REASON = "Checking a reported duplicate"


@pytest.fixture(autouse=True)
def isolated_targets(monkeypatch):
    monkeypatch.setattr(reads, "_targets", dict(reads._targets))
    reads.register_private_target("probe_phone", "test target")


def test_admin_read_writes_record_with_reason(db):
    reads.record_private_read(Actor.admin(ADMIN, "r1"), f"  {REASON}  ", ("probe_phone", TARGET))
    row = AuditRecord.objects.get()
    assert (row.actor_id, row.actor_kind, row.action) == (ADMIN, "admin", "privacy.viewed")
    assert (row.target_type, row.target_id, row.reason) == ("probe_phone", TARGET, REASON)
    assert row.metadata == {}


def test_visitor_unauthenticated_others_not_found_nothing_written(db):
    with pytest.raises(DomainError) as visitor:
        reads.record_private_read(Actor.visitor(), REASON, ("probe_phone", TARGET))
    assert visitor.value.code == "unauthenticated"
    for actor in (Actor.user(uuid.uuid4()), Actor.system()):
        with pytest.raises(DomainError) as other:
            reads.record_private_read(actor, REASON, ("probe_phone", TARGET))
        assert other.value.code == "not_found"
    assert not AuditRecord.objects.exists()


def test_unknown_target_and_bad_ref_refused(db):
    admin = Actor.admin(ADMIN)
    for ref in (("nope", TARGET), ("probe_phone", str(TARGET)), "probe_phone", None):
        with pytest.raises(ValueError):
            reads.record_private_read(admin, REASON, ref)
    assert not AuditRecord.objects.exists()


@pytest.mark.parametrize("reason", ["", "   ", "too short", "x" * 301, None, 5])
def test_bad_reason_refused(db, reason):
    with pytest.raises(ValueError):
        reads.record_private_read(Actor.admin(ADMIN), reason, ("probe_phone", TARGET))
    assert not AuditRecord.objects.exists()


@pytest.mark.parametrize(
    "reason", ["email jane@example.com now", "call 0712345678 please", "ref 1234567 checked"]
)
def test_reason_with_contact_details_refused(db, reason):
    with pytest.raises(ValueError):
        reads.record_private_read(Actor.admin(ADMIN), reason, ("probe_phone", TARGET))
    assert not AuditRecord.objects.exists()


def test_long_target_name_refused_at_registration():
    reads.register_private_target("a" * 48)
    with pytest.raises(ValueError):
        reads.register_private_target("a" * 49)


def test_reason_bounds_accepted(db):
    admin = Actor.admin(ADMIN)
    reads.record_private_read(admin, "x" * 10, ("probe_phone", TARGET))
    reads.record_private_read(admin, "y" * 300, ("probe_phone", TARGET))
    assert AuditRecord.objects.count() == 2


def test_fails_closed_when_write_fails(db, monkeypatch):
    def boom(*args, **kwargs):
        raise DatabaseError("down")

    monkeypatch.setattr(audit.AuditRecord.objects, "create", boom)
    sentinel = []

    def admin_read_probe(actor):
        reads.record_private_read(actor, REASON, ("probe_phone", TARGET))
        sentinel.append("data")

    with pytest.raises(DatabaseError):
        admin_read_probe(Actor.admin(ADMIN))
    assert sentinel == []


def test_read_rolls_back_with_enclosing_transaction(db):
    with pytest.raises(KeyError):
        with transaction.atomic():
            reads.record_private_read(Actor.admin(ADMIN), REASON, ("probe_phone", TARGET))
            raise KeyError
    assert not AuditRecord.objects.exists()


def test_target_registration_idempotent_conflict_and_format():
    reads.register_private_target("probe_phone", "test target")
    with pytest.raises(ValueError):
        reads.register_private_target("probe_phone", "different")
    with pytest.raises(ValueError):
        reads.register_private_target("Bad Name")


def test_every_registered_target_is_well_formed():
    targets = reads.registered_targets()
    assert targets
    for name, description in targets.items():
        assert reads._NAME.fullmatch(name)
        assert isinstance(description, str)


def test_serializer_requires_reason_with_bounds():
    assert not ReasonedReadSerializer(data={}).is_valid()
    assert not ReasonedReadSerializer(data={"reason": "short"}).is_valid()
    assert not ReasonedReadSerializer(data={"reason": "x" * 301}).is_valid()
    ok = ReasonedReadSerializer(data={"reason": f" {REASON} ", "extra": "ignored"})
    assert ok.is_valid()
    assert ok.validated_data == {"reason": REASON}
    assert list(ReasonedReadSerializer().fields) == ["reason"]


def test_mixin_is_post_only():
    assert ReasonedReadMixin.http_method_names == ["post", "options"]


def test_mixin_ordered_badly_is_refused():
    from rest_framework.views import APIView

    with pytest.raises(TypeError):

        class Bad(APIView, ReasonedReadMixin):
            pass


def test_serializer_rejects_integer_reason():
    assert not ReasonedReadSerializer(data={"reason": 1234567890123}).is_valid()


class _ProbeView(ReasonedReadMixin, APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        return Response({"reason": self.read_reason(request)})


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({}, 400),
        ({"reason": "short"}, 400),
        ({"reason": 1234567890123}, 400),
        ({"reason": f"  {REASON}  "}, 200),
    ],
)
def test_view_reads_reason_from_body(body, status):
    request = APIRequestFactory().post("/probe/", body, format="json")
    response = _ProbeView.as_view()(request)
    assert response.status_code == status
    if status == 200:
        assert response.data == {"reason": REASON}


def test_view_rejects_get():
    response = _ProbeView.as_view()(APIRequestFactory().get("/probe/"))
    assert response.status_code == 405
