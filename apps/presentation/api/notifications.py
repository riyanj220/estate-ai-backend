"""In-app notification list, unread count and mark-read (Story 1.24). Rules live in the kernel."""

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kernel import notifications
from apps.kernel.models import Notification
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.pagination import StandardCursorPagination
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn


class NotificationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.CharField(help_text="`domain.verb`, registered by the producing feature.")
    payload = serializers.DictField(
        help_text="Public values only, shaped by the kind's registered schema."
    )
    created_at = serializers.DateTimeField()
    read = serializers.BooleanField()


class NotificationPagination(StandardCursorPagination):
    page_size = 20
    max_page_size = 50

    def decode_cursor(self, request):
        cursor = super().decode_cursor(request)
        # The position is a `seq`; anything else is a tampered cursor, refused before a query.
        if cursor is not None and not (
            cursor.position is not None
            and cursor.position.isascii()
            and cursor.position.isdigit()
            and len(cursor.position) <= 18
        ):
            raise NotFound("Invalid cursor")
        return cursor


class UnreadCountSerializer(serializers.Serializer):
    unread = serializers.IntegerField(min_value=0)


class MarkedSerializer(serializers.Serializer):
    marked = serializers.IntegerField(min_value=0)


def _item(row: Notification) -> dict:
    return NotificationSerializer(
        {
            "id": row.id,
            "kind": row.kind,
            "payload": row.payload,
            "created_at": row.created_at,
            "read": row.read_at is not None,
        }
    ).data


class NotificationListView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]
    cursor_ordering = "-seq"

    @extend_schema(
        operation_id="list_notifications",
        summary="The signed-in account's notifications, newest first",
        description=(
            "Own notifications only, with cursor pagination (`next` and `previous` links, 20 per "
            "page by default, `page_size` up to 50; an invalid `page_size` falls back to the "
            "default). A bad `cursor` is `validation_error` (`fields.cursor`). `state` is `all` "
            "(default) or `unread`; anything else is "
            "`validation_error`. Never cached."
        ),
        parameters=[
            OpenApiParameter("state", str, enum=list(notifications.STATES), required=False),
            OpenApiParameter("page_size", int, required=False),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={
            200: OpenApiResponse(
                inline_serializer(
                    "NotificationPage",
                    {
                        "next": serializers.CharField(allow_null=True),
                        "previous": serializers.CharField(allow_null=True),
                        "results": NotificationSerializer(many=True),
                    },
                ),
                description="One page",
            )
        },
    )
    def get(self, request):
        state = request.query_params.get("state", "all")
        if state not in notifications.STATES:
            raise field_errors({"state": "invalid_choice"})
        rows = notifications.list_for(get_actor(request), state)
        paginator = NotificationPagination()
        try:
            page = paginator.paginate_queryset(rows, request, view=self)
        except NotFound:  # a garbage or tampered cursor: a stable code, never the value
            raise field_errors({"cursor": "invalid"}) from None
        return paginator.get_paginated_response([_item(row) for row in page])


class UnreadCountView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="get_unread_notification_count",
        summary="How many notifications are unread",
        responses={200: OpenApiResponse(UnreadCountSerializer, description="The count")},
    )
    def get(self, request):
        return Response({"unread": notifications.count_unread(get_actor(request))})


class MarkReadView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="mark_notification_read",
        summary="Mark one notification read",
        description=(
            "Needs `X-CSRFToken`. Idempotent: a second call keeps the first read time. Another "
            "account's, an unknown and a malformed id are all `404 not_found`."
        ),
        request=None,
        responses={200: OpenApiResponse(NotificationSerializer, description="The notification")},
    )
    def post(self, request, notification_id):
        return Response(_item(notifications.mark_read(get_actor(request), notification_id)))


class MarkAllReadView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="mark_all_notifications_read",
        summary="Mark every unread notification read",
        description="Needs `X-CSRFToken`. Only the caller's own notifications change.",
        request=None,
        responses={200: OpenApiResponse(MarkedSerializer, description="How many changed")},
    )
    def post(self, request):
        return Response({"marked": notifications.mark_all_read(get_actor(request))})
