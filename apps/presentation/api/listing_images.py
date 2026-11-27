"""Draft image endpoints: upload one image, remove one (Story 2.9), reorder and choose the cover,
and the media endpoint that serves an image (Story 2.10). Rules live in `listings`; the pixels
are checked and re-encoded by `kernel.images`."""

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ErrorDetail
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kernel.images import MAX_BYTES, ImageRejected
from apps.kernel.security_headers import MEDIA_CSP
from apps.kernel.upload_limit import body_too_large
from apps.listings import selectors, services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import ConflictWithCurrent, exact_fields, field_errors
from apps.presentation.api.caching import cache_publicly
from apps.presentation.api.listings import (
    CONTENT_TYPES,
    FloorPlanSerializer,
    ImageSerializer,
    ListingConflictSerializer,
    ListingSerializer,
    image_data,
    listing_data,
)
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import AllowPublic, RequireSignedIn
from apps.presentation.api.throttling import PolicyThrottle

ORDER_BODY = ("base_revision", "order", "cover_id")


class ImageUploadSerializer(serializers.Serializer):
    """Documentation of the multipart body: exactly one part, named `file`."""

    file = serializers.FileField(
        help_text="A JPEG, PNG or WebP image of up to 5 MB (5,242,880 bytes)."
    )


def _upload_bytes(request) -> bytes:
    """The one `file` part's bytes. Any other part, a non-file `file`, none or several is a
    `validation_error`; an oversize body is `file: too_large`."""
    if body_too_large(request):
        raise field_errors({"file": "too_large"})
    parts = set(request.data.keys())
    if parts - {"file"}:
        raise serializers.ValidationError(
            {"non_field_errors": [ErrorDetail("unknown_field", code="unknown_field")]}
        )
    uploads = request.FILES.getlist("file")
    if request.POST.getlist("file") or len(uploads) > 1:
        raise field_errors({"file": "invalid"})
    if not uploads:
        raise field_errors({"file": "required"})
    upload = uploads[0]
    if upload.size is None or upload.size > MAX_BYTES:
        raise field_errors({"file": "too_large"})
    return upload.read()  # the name and declared content type are never read or kept


class ImageOrderSerializer(serializers.Serializer):
    base_revision = serializers.IntegerField(
        min_value=1, help_text="The `revision` of the Listing the client last read."
    )
    order = serializers.ListField(
        child=serializers.UUIDField(),
        help_text="Every image id of the Listing, exactly once, in the wanted order.",
    )
    cover_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="The image to make the cover; null or omitted keeps the current cover.",
    )

    # Same as the field PATCH: `base_revision` and `order` are always required.
    @property
    def partial(self):
        return False

    @partial.setter
    def partial(self, value):
        pass

    def to_internal_value(self, data):
        # Only the three keys are accepted; shapes and values are judged by the service.
        values = exact_fields(data, ORDER_BODY)
        return {name: values.get(name) for name in ORDER_BODY}


class ListingImagesView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]
    parser_classes = [MultiPartParser]
    throttle_classes = [PolicyThrottle]

    # `POST` uploads a file (multipart, the `image_upload` limit); `PATCH` is a small JSON edit
    # (a 64 KiB body cap, its own `image_order` limit).
    @property
    def rate_limit_scope(self):
        method = getattr(getattr(self, "request", None), "method", None)
        return "image_order" if method == "PATCH" else "image_upload"

    def get_parsers(self):
        if self.request is not None and self.request.method == "PATCH":
            return [JSONParser()]
        return super().get_parsers()

    @extend_schema(
        operation_id="upload_listing_image",
        summary="Add an image to one of the signed-in account's own editable Listings",
        description=(
            "Needs `X-CSRFToken` and a `multipart/form-data` body with exactly one part, `file` "
            "(any other part, a non-file `file` or more than one file is `validation_error`). The "
            "request body may be at most 6 MB and the image at most 5 MB (5,242,880 bytes). The "
            "file name and declared content type are ignored and never stored: the type is "
            "decided from the decoded image (JPEG, PNG or WebP only; animated images, GIF, SVG, "
            "TIFF, BMP, HEIC and the like are refused). The image is decoded, rotated by its EXIF "
            "orientation, stripped of ALL metadata (EXIF, GPS, XMP, comments, colour profile) and "
            "encoded again, with its long side at most 2,048 px; the response describes that "
            "stored file. `400 validation_error` carries a code under `fields.file`: `too_large`, "
            "`unsupported_type`, `too_large_dimensions` (above 10,000 px on a side or 40 "
            "megapixels), `too_small` (below 200 x 200), `invalid_image`, `required` or "
            "`invalid`. Another account's, an unknown or a malformed Listing is `404 not_found`; "
            "an account that may not edit, or a Listing that is not editable (sold, rented, "
            "removed or restricted), is `403 "
            "forbidden`. `409` carries `too_many_images` (a Listing holds at most 10) or "
            "`storage_cap_exceeded` (200 MB for an Individual, 1 GB for an Agency, counted on "
            "the stored size). `429` is the upload limit (30 per hour per account) with "
            "`Retry-After`. `503 busy` (with `Retry-After`) means image processing is at "
            "capacity or too slow: retry. The Listing `revision` rises by one, so a concurrent "
            "editor gets `409 stale_revision`. Never cached. The first image of a Listing becomes "
            "its cover; a later upload never changes it."
        ),
        request={"multipart/form-data": ImageUploadSerializer},
        responses={
            201: OpenApiResponse(ImageSerializer, description="The stored image"),
            409: OpenApiResponse(description="`too_many_images` or `storage_cap_exceeded`"),
            503: OpenApiResponse(description="`busy`: image processing is at capacity; retry"),
        },
    )
    def post(self, request, listing_id):
        actor = get_actor(request)
        services.require_image_access(actor, listing_id)  # ownership first, whatever the body is
        data = _upload_bytes(request)
        try:
            view = services.upload_image(actor, listing_id, data)
        except ImageRejected as exc:
            raise field_errors({"file": exc.reason}) from None
        return Response(image_data(view), status=status.HTTP_201_CREATED)

    @extend_schema(
        operation_id="set_listing_image_order",
        summary="Reorder the images of an own Draft and/or choose its cover",
        description=(
            "Needs `X-CSRFToken`. Body: `{base_revision, order, cover_id?}` and nothing else (any "
            "other key is `validation_error` `unknown_field`). `order` is the full list of the "
            "Listing's image ids in the wanted order, each exactly once; the positions become "
            "0..n-1 in that order. `cover_id` (optional; null or omitted keeps the cover) must "
            "be one of the Listing's images. The Listing always has exactly one cover while it has "
            "images. `400 validation_error` carries `invalid` under `fields.order` (a missing, "
            "duplicate, unknown or another Listing's id, or not a list of ids), `fields.cover_id` "
            "or `fields.base_revision` (`required` or `invalid`). A `base_revision` that is not "
            "the current `revision` is `409 stale_revision` with the current Listing under "
            "`current` and nothing written (a replay is also a 409: reload). A request that "
            "changes nothing is `200` and keeps the revision; otherwise the revision rises by "
            "one. Another account's, an unknown or a malformed Listing is `404 not_found`; an "
            "account that may not edit, or a Listing that is not editable (sold, rented, removed "
            "or restricted), is `403 "
            "forbidden`. The body may be at most 64 KiB (`400` with `fields.order` `invalid`) and "
            "the `image_order` limit (120 per hour per account) applies: `429` with `Retry-After`. "
            "Files are never touched. Never cached."
        ),
        request=ImageOrderSerializer,
        responses={
            200: OpenApiResponse(ListingSerializer, description="The Listing, with its media"),
            409: OpenApiResponse(
                ListingConflictSerializer,
                description="Stale revision: the Listing changed; `current` holds it",
            ),
        },
    )
    def patch(self, request, listing_id):
        actor = get_actor(request)
        services.require_image_access(actor, listing_id)  # ownership first, whatever the body is
        if body_too_large(request):
            raise field_errors({"order": "invalid"})
        serializer = ImageOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data
        try:
            view = services.set_image_order(
                actor, listing_id, body["base_revision"], body["order"], body["cover_id"]
            )
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        except services.StaleRevision as exc:
            raise ConflictWithCurrent(listing_data(exc.current)) from None
        return Response(listing_data(view))


class ListingImageDetailView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="delete_listing_image",
        summary="Remove an image from one of the signed-in account's own Drafts",
        description=(
            "Needs `X-CSRFToken`. Deletes the image and its stored file, gives its bytes back to "
            "the account's storage, renumbers the other images 0..n-1 (removing the cover makes "
            "the image with the lowest remaining position the cover) and raises the Listing "
            "`revision` by one. `204` has no body. "
            "Another account's, an unknown or an already removed image or Listing is `404 "
            "not_found`; an account that may not edit, or a Listing that is not editable "
            "(sold, rented, removed or restricted), is `403 forbidden`. A `published` Listing "
            "can never lose its last image: `400 validation_error` with `images_required` under "
            "`fields.images`, nothing changed. Images of a Draft, Published or Archived "
            "Listing may be removed. Never cached."
        ),
        responses={204: OpenApiResponse(description="Removed")},
    )
    def delete(self, request, listing_id, image_id):
        try:
            services.remove_image(get_actor(request), listing_id, image_id)
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListingFloorPlanView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]
    parser_classes = [MultiPartParser]
    throttle_classes = [PolicyThrottle]

    @property
    def rate_limit_scope(self):
        method = getattr(getattr(self, "request", None), "method", None)
        return "image_order" if method == "DELETE" else "image_upload"

    @extend_schema(
        operation_id="set_listing_floor_plan",
        summary="Add or replace the floor plan of one of the own editable Listings",
        description=(
            "Needs `X-CSRFToken` and the same `multipart/form-data` body as an image upload "
            "(one part, `file`), with the same checks, re-encoding, refusals, storage cap and "
            "upload limit. A Listing has at most one floor plan: a new one replaces it. It is "
            "not one of `media`: never a photo, a cover or part of the image limit. The "
            "Listing `revision` rises by one. Never cached."
        ),
        request={"multipart/form-data": ImageUploadSerializer},
        responses={
            200: OpenApiResponse(FloorPlanSerializer, description="The stored floor plan"),
            409: OpenApiResponse(description="`storage_cap_exceeded`"),
            503: OpenApiResponse(description="`busy`: image processing is at capacity; retry"),
        },
    )
    def put(self, request, listing_id):
        actor = get_actor(request)
        services.require_image_access(actor, listing_id)
        data = _upload_bytes(request)
        try:
            view = services.set_floor_plan(actor, listing_id, data)
        except ImageRejected as exc:
            raise field_errors({"file": exc.reason}) from None
        return Response(FloorPlanSerializer(view).data)

    @extend_schema(
        operation_id="delete_listing_floor_plan",
        summary="Remove the floor plan of one of the own editable Listings",
        description=(
            "Needs `X-CSRFToken`. Deletes the floor plan and its file, gives its bytes back and "
            "raises the Listing `revision` by one. `404 not_found` when there is none, and the "
            "same refusals as removing an image. Never cached."
        ),
        responses={204: OpenApiResponse(description="Removed")},
    )
    def delete(self, request, listing_id):
        services.remove_floor_plan(get_actor(request), listing_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class IgnoreAcceptNegotiation(BaseContentNegotiation):
    """The media endpoint answers with the stored image whatever `Accept` says; errors are JSON."""

    def select_parser(self, request, parsers):
        return parsers[0] if parsers else None

    def select_renderer(self, request, renderers, format_suffix=None):
        renderer = JSONRenderer()
        return renderer, renderer.media_type


class ListingImageFileView(NoStoreMixin, APIView):
    """THE media endpoint (AD-15, AD-22): the stored bytes of one image, authorised at request
    time by `listings.selectors.image_for_serving`. Every refusal is the same `404 not_found`
    (a Visitor included, so existence is never confirmed). `HEAD` works; `Range` and conditional
    requests are ignored (always `200` with the whole file, no `ETag`)."""

    permission_classes = [AllowPublic]
    content_negotiation_class = IgnoreAcceptNegotiation

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            if getattr(response, "public_image", False):
                cache_publicly(request, response)
            else:
                response["Cache-Control"] = "private, no-store"
        return response

    @extend_schema(
        operation_id="get_listing_image_file",
        summary="The bytes of one image of a Listing the caller may see",
        description=(
            "The media endpoint: the only place an image is served, decided at request time by the "
            "single visibility policy. An image of a public Listing (Published, not restricted, "
            "owner Active) goes to anyone, a Visitor and an administrator included, with "
            "`Cache-Control: public, max-age=60` (so it may stay in a cache for up to a minute "
            "after the Listing stops being public), no cookie and nothing varying by cookie, "
            "whatever cookies the request carries. An image of any other Listing (a Draft, a "
            "restricted one, one whose owner is Suspended or Deactivated) goes only to the owning "
            "account (Suspended and Email-unverified owners included, a Deactivated one not) with "
            "`Cache-Control: private, no-store`; an administrator also gets non-Draft images for "
            "moderation review, never cached. Anyone else (another account, a "
            "Visitor), an unknown or malformed id and another Listing's image get the same `404 "
            "not_found` (a Visitor gets 404, never 401). The body is the stored, re-encoded image "
            "with its stored `Content-Type` (`image/jpeg`, `image/png` or `image/webp`), "
            "`Content-Length`, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline` "
            "(no file name) and `Content-Security-Policy: default-src 'none'; sandbox`. `HEAD` "
            "answers the same headers without a body. `Range` is ignored (always `200`, the "
            "whole file) and there is no `ETag`. `Accept` is ignored."
        ),
        responses={
            (200, content_type): OpenApiResponse(
                OpenApiTypes.BINARY, description="The stored image bytes"
            )
            for content_type in CONTENT_TYPES
        },
    )
    def get(self, request, listing_id, image_id):
        served = selectors.image_for_serving(get_actor(request), listing_id, image_id)
        return self._image(served, served.data)

    def head(self, request, listing_id, image_id):
        """Headers only: the same access decision, no file read (the size is the asset row's)."""
        served = selectors.image_for_serving(
            get_actor(request), listing_id, image_id, with_bytes=False
        )
        return self._image(served, b"")

    @staticmethod
    def _image(served, body: bytes) -> HttpResponse:
        response = HttpResponse(body, content_type=served.content_type)
        response["Content-Length"] = str(served.size)
        response["Content-Disposition"] = "inline"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = MEDIA_CSP
        response.public_image = served.public
        return response
