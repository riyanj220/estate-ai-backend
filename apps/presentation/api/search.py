"""Natural-language and voice search (Stories 6.1 to 6.4). Rules live in `search`.

The request is in a POST body, never a URL. The answer is the interpretation only: the filter
parameters to give `GET /api/v1/public/listings`, which does the searching. A voice search first
turns its recording into a transcript, which the buyer confirms and sends as a text search."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kernel import ratelimit
from apps.kernel.ai import gateway
from apps.kernel.upload_limit import body_too_large
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import enforce_csrf
from apps.presentation.api.bodies import field_errors, single_field
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import AllowPublic
from apps.presentation.api.serializers import ErrorBodySerializer
from apps.search import services


class SearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(
        help_text=f"The request in the buyer's words, at most {services.MAX_QUERY} characters."
    )


class SearchInterpretationSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=["interpreted", "fallback"],
        help_text="`fallback`: nothing was understood; offer the conventional filters.",
    )
    message = serializers.CharField(allow_blank=True, help_text="Why, on a fallback.")
    params = serializers.DictField(
        child=serializers.CharField(),
        help_text=(
            "Query parameters for `GET /api/v1/public/listings`; a `prefer_` one only ranks. "
            "Empty on a fallback."
        ),
    )
    unsupported = serializers.ListField(
        child=serializers.CharField(),
        help_text="Parts of the request that were not searched for, in the buyer's words.",
    )
    notices = serializers.ListField(child=serializers.CharField())


def _query(data) -> str:
    value = single_field(data, "query")
    if not isinstance(value, str):
        raise field_errors({"query": "invalid"})
    value = value.strip()
    if not value:
        raise field_errors({"query": "blank"})
    if len(value) > services.MAX_QUERY:
        raise field_errors({"query": "too_long"})
    return value


class PublicSearchView(NoStoreMixin, APIView):
    permission_classes = [AllowPublic]

    @extend_schema(
        operation_id="interpret_search",
        summary="Turn a search written in plain words into filters",
        description=(
            "Public; needs `X-CSRFToken`. One AI call on redacted text; the request is not "
            "stored. `200` with `status: fallback` when the AI is off, unavailable, too slow, "
            "out of budget or gave nothing usable. Limits: 10 a minute per Visitor, 30 an hour "
            "per account (`429` with `Retry-After`). Never cached."
        ),
        request=SearchRequestSerializer,
        responses={
            200: OpenApiResponse(SearchInterpretationSerializer, description="The filters"),
            400: OpenApiResponse(ErrorBodySerializer, description="`validation_error`"),
            403: OpenApiResponse(ErrorBodySerializer, description="`forbidden`: CSRF token"),
            429: OpenApiResponse(ErrorBodySerializer, description="`throttled`"),
        },
    )
    def post(self, request):
        enforce_csrf(request)
        actor = get_actor(request)
        query = _query(request.data)
        visitor_key = None
        if actor.account_id is None:
            visitor_key = ratelimit.client_key(actor, request, "client")
        result = services.interpret_query(actor, query, visitor_key=visitor_key)
        read = result.read
        return Response(
            SearchInterpretationSerializer(
                {
                    "status": "interpreted" if result.interpreted else "fallback",
                    "message": result.message,
                    "params": read.params if result.interpreted else {},
                    "unsupported": list(read.unsupported),
                    "notices": list(read.notices) if result.interpreted else [],
                }
            ).data
        )


class VoiceUploadSerializer(serializers.Serializer):
    """Documentation of the multipart body: exactly one part, named `audio`."""

    audio = serializers.FileField(
        help_text="A WebM, Ogg, MP4 (M4A) or WAV recording of up to 2 MB (2,097,152 bytes)."
    )


class TranscriptionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=["transcribed", "fallback"],
        help_text="`fallback`: there is no transcript; offer the text box.",
    )
    transcript = serializers.CharField(
        allow_blank=True, help_text="To be shown, edited and then sent to `interpret_search`."
    )
    message = serializers.CharField(allow_blank=True, help_text="Why, on a fallback.")


def _recording(request) -> bytes:
    """The one `audio` part's bytes; the file name and declared content type are never read."""
    if body_too_large(request):
        raise field_errors({"audio": "audio_too_large"})
    parts = set(request.data.keys())
    if parts - {"audio"}:
        raise serializers.ValidationError(
            {"non_field_errors": [ErrorDetail("unknown_field", code="unknown_field")]}
        )
    uploads = request.FILES.getlist("audio")
    if request.POST.getlist("audio") or len(uploads) > 1:
        raise field_errors({"audio": "invalid"})
    if not uploads:
        raise field_errors({"audio": "required"})
    if uploads[0].size > gateway.MAX_AUDIO_BYTES:
        raise field_errors({"audio": "audio_too_large"})
    return uploads[0].read()


class PublicSearchTranscriptionView(NoStoreMixin, APIView):
    permission_classes = [AllowPublic]
    parser_classes = [MultiPartParser]

    @extend_schema(
        operation_id="transcribe_search",
        summary="Turn a spoken search into text",
        description=(
            "Public; needs `X-CSRFToken` and a `multipart/form-data` body with exactly one part, "
            "`audio`. The type is decided from the bytes (WebM, Ogg, MP4 or WAV). The recording "
            "is sent to the AI provider with no account data and is not stored or logged. The "
            "transcript is not searched: send it, as the buyer confirms it, to "
            "`interpret_search`, which is what counts as a search. `200` with `status: "
            "fallback` when the AI is off, unavailable, too slow, out of budget or heard "
            "nothing. `400 validation_error` carries a code under `fields.audio`. Limits: 10 a "
            "minute per Visitor, 30 an hour per account (`429` with `Retry-After`). Never cached."
        ),
        request={"multipart/form-data": VoiceUploadSerializer},
        responses={
            200: OpenApiResponse(TranscriptionSerializer, description="The words heard"),
            400: OpenApiResponse(ErrorBodySerializer, description="`validation_error`"),
            403: OpenApiResponse(ErrorBodySerializer, description="`forbidden`: CSRF token"),
            429: OpenApiResponse(ErrorBodySerializer, description="`throttled`"),
        },
    )
    def post(self, request):
        enforce_csrf(request)
        actor = get_actor(request)
        data = _recording(request)
        visitor_key = None
        if actor.account_id is None:
            visitor_key = ratelimit.client_key(actor, request, "client")
        try:
            result = services.transcribe_query(actor, data, visitor_key=visitor_key)
        except services.AudioRejected as exc:
            raise field_errors({"audio": exc.reason}) from None
        return Response(
            TranscriptionSerializer(
                {
                    "status": "transcribed" if result.ok else "fallback",
                    "transcript": result.transcript,
                    "message": result.message,
                }
            ).data
        )
