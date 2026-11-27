"""Conversations and Messages (Story 7.1). Rules live in `messaging`."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.listings.selectors import HEADER_AVAILABILITY
from apps.messaging import selectors, services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import exact_fields, field_errors
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn
from apps.presentation.api.serializers import ErrorBodySerializer


class ConversationListingSerializer(serializers.Serializer):
    availability = serializers.ChoiceField(choices=HEADER_AVAILABILITY)
    title = serializers.CharField(
        allow_null=True, help_text="Only while the Listing is publicly visible."
    )


class ConversationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    listing_id = serializers.UUIDField()
    listing = ConversationListingSerializer()
    role = serializers.ChoiceField(choices=(selectors.ROLE_INITIATOR, selectors.ROLE_OWNER))
    counterpart_name = serializers.CharField(
        allow_null=True, help_text="Null when the other account is no longer available."
    )
    writable = serializers.BooleanField(help_text="Whether the caller may send now.")
    read_only_reason = serializers.ChoiceField(choices=selectors.READ_ONLY_REASONS, allow_null=True)
    last_seq = serializers.IntegerField()
    last_message_at = serializers.DateTimeField()
    created_at = serializers.DateTimeField()


class MessageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    conversation_id = serializers.UUIDField()
    seq = serializers.IntegerField(help_text="Order within the Conversation, from 1.")
    body = serializers.CharField()
    mine = serializers.BooleanField()
    client_message_id = serializers.CharField(
        allow_null=True, help_text="The sender's `Idempotency-Key`; null on the other's."
    )
    created_at = serializers.DateTimeField()


class ConversationListSerializer(serializers.Serializer):
    results = ConversationSerializer(many=True)


class MessagePageSerializer(serializers.Serializer):
    results = MessageSerializer(many=True)
    has_more = serializers.BooleanField(help_text="Ask again with `after` = the last `seq`.")


class SentSerializer(serializers.Serializer):
    conversation = ConversationSerializer()
    message = MessageSerializer(
        allow_null=True, help_text="Null: the existing thread is read-only; nothing was saved."
    )


class StartBodySerializer(serializers.Serializer):
    listing_id = serializers.UUIDField()
    body = serializers.CharField(max_length=services.MAX_BODY)


class MessageBodySerializer(serializers.Serializer):
    body = serializers.CharField(max_length=services.MAX_BODY)


def conversation_data(view: selectors.ConversationView) -> dict:
    return {
        "id": view.id,
        "listing_id": view.listing_id,
        "listing": {"availability": view.listing_availability, "title": view.listing_title},
        "role": view.role,
        "counterpart_name": view.counterpart_name,
        "writable": view.read_only_reason is None,
        "read_only_reason": view.read_only_reason,
        "last_seq": view.last_seq,
        "last_message_at": view.last_message_at,
        "created_at": view.created_at,
    }


def _refusals(**meanings: str) -> dict:
    return {
        int(code[1:]): OpenApiResponse(ErrorBodySerializer, meaning)
        for code, meaning in meanings.items()
    }


_KEY_HEADER = OpenApiParameter(
    "Idempotency-Key", str, location=OpenApiParameter.HEADER, required=True
)
_SEND_403 = (
    "`messaging_email_unverified`, `messaging_unavailable` (suspended), `forbidden` "
    "(administrator) or a missing CSRF token"
)
_SEND_RULES = (
    "Needs `X-CSRFToken` and an `Idempotency-Key` header, which is the client message id: the "
    "same key and body again answers with the Message already saved, another body is "
    "`422 idempotency_key_reused`. `body` is 1 to 2,000 characters. A Message in a response is "
    "saved. 60 Messages a minute per account."
)


class ConversationsView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="find_conversation",
        summary="The caller's own inquiry about a Listing",
        description="`results` holds at most one Conversation. Never cached.",
        parameters=[OpenApiParameter("listing_id", str, required=True)],
        responses={
            200: ConversationListSerializer,
            **_refusals(s400="`validation_error`", s401="`unauthenticated`", s404="`not_found`"),
        },
    )
    def get(self, request):
        listing_id = request.query_params.get("listing_id")
        if not listing_id:
            raise field_errors({"listing_id": "required"})
        found = selectors.own_inquiry(get_actor(request), listing_id)
        results = [] if found is None else [conversation_data(found)]
        return Response(ConversationListSerializer({"results": results}).data)

    @extend_schema(
        operation_id="start_conversation",
        summary="Send an inquiry about a Listing",
        description=(
            f"{_SEND_RULES} At most one Conversation per Listing and account: starting again "
            "sends into it (`200`), or only returns it with `message: null` when it is "
            "read-only. 20 new Conversations a day per account."
        ),
        parameters=[_KEY_HEADER],
        request=StartBodySerializer,
        responses={
            201: OpenApiResponse(SentSerializer, "A new Conversation"),
            200: OpenApiResponse(SentSerializer, "The existing Conversation, or a replay"),
            **_refusals(
                s400="`validation_error`",
                s401="`unauthenticated`",
                s403=_SEND_403,
                s404="`not_found`: no such public Listing",
                s409="`own_listing`",
                s422="`idempotency_key_reused`",
                s429="`throttled`",
            ),
        },
    )
    def post(self, request):
        values = exact_fields(request.data, ("listing_id", "body"))
        if values.get("listing_id") is None:
            raise field_errors({"listing_id": "required"})
        try:
            result = services.start_conversation(
                get_actor(request),
                values["listing_id"],
                values.get("body"),
                request.headers.get("Idempotency-Key"),
            )
        except services.MessageInvalid as exc:
            raise field_errors(exc.fields) from None
        message = result.message
        return Response(
            SentSerializer(
                {"conversation": conversation_data(result.conversation), "message": message}
            ).data,
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )


class ConversationDetailView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="get_conversation",
        summary="One Conversation of the caller's",
        description="Participants only; anyone else gets `not_found`. Never cached.",
        responses={
            200: ConversationSerializer,
            **_refusals(s401="`unauthenticated`", s404="`not_found`"),
        },
    )
    def get(self, request, conversation_id):
        found = selectors.get_conversation(get_actor(request), conversation_id)
        return Response(ConversationSerializer(conversation_data(found)).data)


class ConversationMessagesView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="list_messages",
        summary="The Messages of a Conversation, oldest first",
        description=(
            f"Those with `seq` above `after` (default 0), at most {selectors.MESSAGE_PAGE}. "
            "Participants only. Never cached."
        ),
        parameters=[OpenApiParameter("after", int, required=False)],
        responses={
            200: MessagePageSerializer,
            **_refusals(s400="`validation_error`", s401="`unauthenticated`", s404="`not_found`"),
        },
    )
    def get(self, request, conversation_id):
        raw = request.query_params.get("after", "0")
        if not (len(raw) <= 9 and raw.isascii() and raw.isdigit()):
            raise field_errors({"after": "invalid"})
        page = selectors.messages_after(get_actor(request), conversation_id, int(raw))
        return Response(
            MessagePageSerializer({"results": page.items, "has_more": page.has_more}).data
        )

    @extend_schema(
        operation_id="send_message",
        summary="Send a Message in a Conversation",
        description=_SEND_RULES,
        parameters=[_KEY_HEADER],
        request=MessageBodySerializer,
        responses={
            201: MessageSerializer,
            **_refusals(
                s400="`validation_error`",
                s401="`unauthenticated`",
                s403=_SEND_403,
                s404="`not_found`",
                s409="`conversation_read_only` or `counterpart_unavailable`",
                s422="`idempotency_key_reused`",
                s429="`throttled`",
            ),
        },
    )
    def post(self, request, conversation_id):
        values = exact_fields(request.data, ("body",))
        try:
            message = services.send_message(
                get_actor(request),
                conversation_id,
                values.get("body"),
                request.headers.get("Idempotency-Key"),
            )
        except services.MessageInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)
