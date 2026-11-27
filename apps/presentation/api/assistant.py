"""The AI listing assistant (Story 5.8). Rules live in `assistant`.

The message limit (30 an hour) is the gateway's, so these routes carry no throttle of their own.
AI text is plain text: a client must never render it as HTML or markdown."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistant import selectors, services
from apps.catalog.selectors import KNOWLEDGE_NOTICE
from apps.listings.services import FieldsInvalid, StaleRevision
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import (
    ConflictWithCurrent,
    exact_fields,
    field_errors,
    single_field,
)
from apps.presentation.api.listings import (
    ListingConflictSerializer,
    ListingSerializer,
    listing_data,
)
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn
from apps.presentation.api.serializers import ErrorBodySerializer

TURN_BODY = ("message", "client_turn_id")
OUTCOMES = ["success", "fallback", "error", "timeout", "budget_exhausted"]


class AssistantSourceSerializer(serializers.Serializer):
    title = serializers.CharField()
    source = serializers.CharField()
    version = serializers.IntegerField()


class AssistantAppliedSerializer(serializers.Serializer):
    field = serializers.CharField(help_text="A registry field id.")
    value = serializers.JSONField(help_text="As on the Listing: text, integer or boolean.")


class AssistantRejectedSerializer(serializers.Serializer):
    field = serializers.CharField()
    code = serializers.CharField(help_text="The same codes as a Listing edit.")


class AssistantNextStepSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["required", "review"])
    question = serializers.CharField(help_text="Plain text.")
    fields_asked = serializers.ListField(
        child=serializers.CharField(),
        help_text="The missing required fields asked about; empty for `review`.",
    )
    optional = serializers.ListField(
        child=serializers.CharField(), help_text="Missing optional fields the Seller may add."
    )
    in_form = serializers.BooleanField(help_text="True when only the manual form can set it.")


class AssistantFactSerializer(serializers.Serializer):
    field = serializers.CharField(help_text="A registry field id.")
    value = serializers.CharField(help_text="Plain text.")
    confirmed = serializers.BooleanField(help_text="False for an unconfirmed assistant value.")


class AssistantTurnSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    sequence = serializers.IntegerField()
    client_turn_id = serializers.UUIDField()
    message = serializers.CharField(help_text="The caller's message after redaction. Plain text.")
    reply = serializers.CharField(help_text="The assistant's reply. Plain text.")
    sources = AssistantSourceSerializer(
        many=True, help_text="Knowledge base passages given to the assistant for this reply."
    )
    created_at = serializers.DateTimeField()
    applied = AssistantAppliedSerializer(many=True, help_text="Saved to the Draft, unconfirmed.")
    rejected = AssistantRejectedSerializer(many=True, help_text="Understood but not saved.")
    conflicts = serializers.ListField(
        child=serializers.CharField(),
        help_text="Fields the Seller changed while the assistant worked: left as they were.",
    )
    notices = serializers.ListField(child=serializers.CharField(), help_text="Plain text.")


class AssistantSessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    listing_id = serializers.UUIDField()
    registry_version = serializers.IntegerField()
    schema_version = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    knowledge_notice = serializers.CharField(help_text="Show it beside any source.")
    turns = AssistantTurnSerializer(many=True, help_text="Oldest first.")
    next_step = AssistantNextStepSerializer(
        allow_null=True, help_text="Null when the Listing is no longer a Draft."
    )
    summary = AssistantFactSerializer(many=True, help_text="What the Listing holds now.")


class AssistantTurnResultSerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(choices=OUTCOMES)
    message = serializers.CharField(
        allow_blank=True, help_text="A notice to show; empty when there is nothing to say."
    )
    turn = AssistantTurnSerializer(
        allow_null=True, help_text="Null unless `success`: nothing was stored."
    )
    next_step = AssistantNextStepSerializer(allow_null=True, help_text="Null unless `success`.")
    summary = AssistantFactSerializer(many=True, help_text="Empty unless `success`.")


class AssistantDescriptionBodySerializer(serializers.Serializer):
    base_revision = serializers.IntegerField(
        min_value=1, help_text="The `revision` of the Draft the client last read."
    )
    replace = serializers.BooleanField(
        required=False, help_text="True to overwrite a description the Seller wrote."
    )

    def to_internal_value(self, data):
        values = exact_fields(data, ("base_revision", "replace"))
        return {
            "base_revision": values.get("base_revision"),
            "replace": values.get("replace", False),
        }


class AssistantClaimSerializer(serializers.Serializer):
    text = serializers.CharField(help_text="One sentence of the description. Plain text.")
    fields = serializers.ListField(
        child=serializers.CharField(), help_text="The confirmed fields it rests on."
    )


class AssistantDescriptionResultSerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(choices=OUTCOMES)
    message = serializers.CharField(allow_blank=True)
    listing = ListingSerializer(
        allow_null=True,
        help_text="With the saved description (unconfirmed); null unless `success`.",
    )
    claims = AssistantClaimSerializer(many=True, help_text="The per-claim checklist.")
    left_out = serializers.ListField(
        child=serializers.CharField(), help_text="Fields not used: not confirmed yet."
    )


class AssistantSessionOpenSerializer(serializers.Serializer):
    listing_id = serializers.UUIDField(help_text="The caller's own Draft.")

    def to_internal_value(self, data):
        return {"listing_id": single_field(data, "listing_id")}


class AssistantTurnBodySerializer(serializers.Serializer):
    message = serializers.CharField(max_length=services.MAX_MESSAGE)
    client_turn_id = serializers.UUIDField(
        help_text="A new UUID for each message; send the same one to repeat a request."
    )

    def to_internal_value(self, data):
        # Only these keys are accepted; the service judges the values, after the access refusals.
        values = exact_fields(data, TURN_BODY)
        return {name: values.get(name) for name in TURN_BODY}


def _session_data(view: selectors.SessionView) -> dict:
    return AssistantSessionSerializer({**vars(view), "knowledge_notice": KNOWLEDGE_NOTICE}).data


def _errors(**statuses: str) -> dict:
    return {
        int(key[1:]): OpenApiResponse(ErrorBodySerializer, text) for key, text in statuses.items()
    }


_ACCESS = {
    "e401": "`unauthenticated`: a Visitor",
    "e403": "`forbidden`: a Suspended or Deactivated account, or a missing CSRF token",
    "e404": "`not_found`: not the account's own",
}


class _AssistantView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]


class AssistantSessionsView(_AssistantView):
    @extend_schema(
        operation_id="open_assistant_session",
        summary="Create or open the assistant session of a Draft",
        description="Needs `X-CSRFToken`. One open session per Draft.",
        request=AssistantSessionOpenSerializer,
        responses={
            200: OpenApiResponse(AssistantSessionSerializer, "The Draft's open session"),
            201: OpenApiResponse(AssistantSessionSerializer, "A new session"),
            **_errors(**_ACCESS, e400="`validation_error`", e409="`conflict`: not a Draft"),
        },
    )
    def post(self, request):
        body = AssistantSessionOpenSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        view, created = services.open_session(get_actor(request), body.validated_data["listing_id"])
        return Response(_session_data(view), status=201 if created else 200)


class AssistantSessionView(_AssistantView):
    @extend_schema(
        operation_id="get_assistant_session",
        summary="An assistant session and its history",
        responses={200: AssistantSessionSerializer, **_errors(**_ACCESS)},
    )
    def get(self, request, session_id):
        return Response(_session_data(selectors.get_session(get_actor(request), session_id)))


class AssistantTurnsView(_AssistantView):
    @extend_schema(
        operation_id="send_assistant_turn",
        summary="Send one message to the assistant",
        description=(
            "Needs `X-CSRFToken`. May take up to about 30 seconds. When the assistant cannot "
            "answer it is `200` with an `outcome` other than `success`, `turn` null and a message "
            "to show; the Draft and the history are unchanged, and the same `client_turn_id` may "
            "be sent again. Repeating an answered `client_turn_id` returns that turn without "
            "processing it again; `next_step` and `summary` show the Draft as it is now. At most "
            "30 messages an hour."
        ),
        request=AssistantTurnBodySerializer,
        responses={
            200: AssistantTurnResultSerializer,
            **_errors(
                **_ACCESS,
                e400="`validation_error`: `message` (1 to 2000 characters) or `client_turn_id`",
                e409="`conflict`: not a Draft, another message in progress, or a full session",
                e422="`idempotency_key_reused`: the `client_turn_id` had another message",
                e429="`throttled`: more than 30 messages in an hour; with `Retry-After`",
            ),
        },
    )
    def post(self, request, session_id):
        body = AssistantTurnBodySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = body.validated_data
        try:
            result = services.send_turn(
                get_actor(request), session_id, values["message"], values["client_turn_id"]
            )
        except FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(AssistantTurnResultSerializer(result).data)


class AssistantDescriptionView(_AssistantView):
    @extend_schema(
        operation_id="write_assistant_description",
        summary="Write the description from the confirmed details",
        description=(
            "Needs `X-CSRFToken`. One model call; counts as an assistant message. The text is "
            "saved to the Draft as an unconfirmed assistant value. Nothing is published."
        ),
        request=AssistantDescriptionBodySerializer,
        responses={
            200: AssistantDescriptionResultSerializer,
            409: OpenApiResponse(ListingConflictSerializer, "`stale_revision` or `conflict`"),
            **_errors(
                **_ACCESS,
                e400="`validation_error`: `description` is `manual_description` (send "
                "`replace`) or `nothing_confirmed`",
                e429="`throttled`: the assistant message limit; with `Retry-After`",
            ),
        },
    )
    def post(self, request, session_id):
        body = AssistantDescriptionBodySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = body.validated_data
        try:
            result = services.write_description(
                get_actor(request), session_id, values["base_revision"], values["replace"]
            )
        except FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        except StaleRevision as exc:
            raise ConflictWithCurrent(listing_data(exc.current)) from None
        return Response(
            {
                "outcome": result.outcome,
                "message": result.message,
                "listing": listing_data(result.listing) if result.listing else None,
                "claims": [{"text": c.text, "fields": list(c.fields)} for c in result.claims],
                "left_out": list(result.left_out),
            }
        )
