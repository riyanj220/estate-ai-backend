"""Audit trail review (Story 10.7). Read only: no route changes or deletes a record."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response

from apps.identity import selectors as identity
from apps.kernel import audit
from apps.presentation.adminapi.params import (
    REFUSALS,
    AdminView,
    choice_param,
    day_start,
    id_param,
)
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.listings import _link


class AuditRecordSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    actor_kind = serializers.CharField()
    actor_id = serializers.UUIDField(allow_null=True)
    actor_email = serializers.CharField(allow_null=True, help_text="Masked.")
    action = serializers.CharField()
    target_type = serializers.CharField()
    target_id = serializers.UUIDField()
    reason = serializers.CharField(allow_blank=True, help_text="Reasoned private reads only.")
    details = serializers.DictField()


class AuditPageSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = AuditRecordSerializer(many=True)
    actions = serializers.ListField(
        child=serializers.CharField(), help_text="Every action that can be recorded."
    )


class AuditTrailView(AdminView):
    @extend_schema(
        operation_id="list_admin_audit_records",
        summary="Audit records, newest first",
        description=(
            "50 per page; `next` is a link, `previous` is always null. `actor` and `target` are "
            "ids; the dates are Karachi calendar days, inclusive. " + REFUSALS
        ),
        parameters=[
            OpenApiParameter("actor", str, required=False),
            OpenApiParameter("action", str, required=False),
            OpenApiParameter("target_type", str, required=False),
            OpenApiParameter("target", str, required=False),
            OpenApiParameter("date_from", str, required=False, description="YYYY-MM-DD"),
            OpenApiParameter("date_to", str, required=False, description="YYYY-MM-DD"),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(AuditPageSerializer, description="One page")},
    )
    def get(self, request):
        actions = sorted(audit.registered_actions())
        target_type = request.query_params.get("target_type") or None
        if target_type is not None and (len(target_type) > 64 or "\x00" in target_type):
            raise field_errors({"target_type": "invalid"})
        try:
            rows, cursor = audit.page(
                get_actor(request),
                actor_id=id_param(request, "actor"),
                action=choice_param(request, "action", actions),
                target_type=target_type,
                target_id=id_param(request, "target"),
                since=day_start(request, "date_from"),
                before=day_start(request, "date_to", days_later=1),
                cursor=id_param(request, "cursor"),
            )
        except audit.InvalidCursor:
            raise field_errors({"cursor": "invalid"}) from None
        contacts = identity.masked_contacts({r.actor_id for r in rows if r.actor_id})
        results = [
            {
                "id": r.id,
                "created_at": r.created_at,
                "actor_kind": r.actor_kind,
                "actor_id": r.actor_id,
                "actor_email": (found := contacts.get(r.actor_id)) and found.masked_email,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "reason": r.reason,
                "details": r.metadata,
            }
            for r in rows
        ]
        return Response(
            AuditPageSerializer(
                {
                    "next": _link(request, cursor),
                    "previous": None,
                    "results": results,
                    "actions": actions,
                }
            ).data
        )
