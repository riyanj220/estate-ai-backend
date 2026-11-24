"""Draft image endpoints (Story 2.9): upload one image, remove one. Rules live in `listings`;
the pixels are checked and re-encoded by `kernel.images`. No endpoint here serves a file."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ErrorDetail
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kernel.images import MAX_BYTES, ImageRejected
from apps.kernel.upload_limit import body_too_large
from apps.listings import services
from apps.listings.selectors import ImageView
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn
from apps.presentation.api.throttling import PolicyThrottle

CONTENT_TYPES = ["image/jpeg", "image/png", "image/webp"]


class ImageSerializer(serializers.Serializer):
    """An attached image. There is no URL and no storage key: serving is Story 2.10."""

    id = serializers.UUIDField()
    position = serializers.IntegerField(help_text="1-based order; the lowest is the cover.")
    content_type = serializers.ChoiceField(choices=CONTENT_TYPES)
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    bytes = serializers.IntegerField(help_text="Size of the stored (re-encoded) file.")


class ImageUploadSerializer(serializers.Serializer):
    """Documentation of the multipart body: exactly one part, named `file`."""

    file = serializers.FileField(
        help_text="A JPEG, PNG or WebP image of up to 5 MB (5,242,880 bytes)."
    )


def _data(view: ImageView) -> dict:
    return ImageSerializer(
        {
            "id": view.id,
            "position": view.position,
            "content_type": view.content_type,
            "width": view.width,
            "height": view.height,
            "bytes": view.bytes,
        }
    ).data


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


class ListingImagesView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]
    parser_classes = [MultiPartParser]
    throttle_classes = [PolicyThrottle]
    rate_limit_scope = "image_upload"

    @extend_schema(
        operation_id="upload_listing_image",
        summary="Add an image to one of the signed-in account's own Drafts",
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
            "an account that may not edit, or a Listing that is not an editable Draft, is `403 "
            "forbidden`. `409` carries `too_many_images` (a Listing holds at most 10) or "
            "`storage_cap_exceeded` (200 MB for an Individual, 1 GB for an Agency, counted on "
            "the stored size). `429` is the upload limit (30 per hour per account) with "
            "`Retry-After`. `503 busy` (with `Retry-After`) means image processing is at "
            "capacity or too slow: retry. The Listing `revision` rises by one, so a concurrent "
            "editor gets `409 stale_revision`. Never cached; no URL or storage key is returned."
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
        return Response(_data(view), status=status.HTTP_201_CREATED)


class ListingImageDetailView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="delete_listing_image",
        summary="Remove an image from one of the signed-in account's own Drafts",
        description=(
            "Needs `X-CSRFToken`. Deletes the image and its stored file, gives its bytes back to "
            "the account's storage, and raises the Listing `revision` by one. `204` has no body. "
            "Another account's, an unknown or an already removed image or Listing is `404 "
            "not_found`; an account that may not edit, or a Listing that is not an editable "
            "Draft, is `403 forbidden`. Never cached."
        ),
        responses={204: OpenApiResponse(description="Removed")},
    )
    def delete(self, request, listing_id, image_id):
        services.remove_image(get_actor(request), listing_id, image_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
