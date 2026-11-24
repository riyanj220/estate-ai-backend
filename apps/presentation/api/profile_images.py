"""Profile picture and agency logo endpoints (Story 2.11): upload or replace, remove, the owner's
own file, and the public logo. Rules live in `identity`; the pixels are checked and re-encoded by
`kernel.images`. An Individual's picture has no public route."""

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity import selectors, services
from apps.kernel.images import ImageRejected
from apps.kernel.security_headers import MEDIA_CSP
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.listing_images import (
    IgnoreAcceptNegotiation,
    ImageUploadSerializer,
    _upload_bytes,
)
from apps.presentation.api.listings import CONTENT_TYPES
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import AllowPublic, RequireSignedIn
from apps.presentation.api.throttling import PolicyThrottle

LOGO_CACHE_SECONDS = 60


class ProfileImageSerializer(serializers.Serializer):
    content_type = serializers.ChoiceField(choices=CONTENT_TYPES)
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    bytes = serializers.IntegerField(help_text="The stored size.")
    url = serializers.CharField(
        help_text="The owner-only route that serves the image (relative). Never a public URL."
    )


def profile_image_data(view: selectors.ProfileImageView | None) -> dict | None:
    if view is None:
        return None
    return ProfileImageSerializer(
        {
            "content_type": view.content_type,
            "width": view.width,
            "height": view.height,
            "bytes": view.bytes,
            "url": view.url,
        }
    ).data


def media_response(served: selectors.ServedProfileImage, body: bytes) -> HttpResponse:
    response = HttpResponse(body, content_type=served.content_type)
    response["Content-Length"] = str(served.size)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = MEDIA_CSP
    return response


_UPLOAD_DESCRIPTION = (
    "Needs `X-CSRFToken` and a `multipart/form-data` body with exactly one part, `file` (any "
    "other part, a non-file `file` or more than one file is `validation_error`). The request body "
    "may be at most 6 MB and the image at most 5 MB (5,242,880 bytes). Same pipeline and `fields."
    "file` codes as the Listing image upload (`too_large`, `unsupported_type`, "
    "`too_large_dimensions`, `too_small`, `invalid_image`, `required`, `invalid`): the file name "
    "and declared type are ignored and never stored, the image is decoded, stripped of ALL "
    "metadata and encoded again (long side at most 2,048 px). Uploading again replaces the "
    "previous image, whose file and storage bytes are released in the same step. The account "
    "type is checked before the body (`403 forbidden` for the other type and administrators, "
    "`401` for a Visitor); a Deactivated account has no session. The account state follows the "
    "profile update (Active, Email-unverified and Suspended may change it). `409 "
    "storage_cap_exceeded` (counted on the stored size, replacement counted at its net size), "
    "`429` is the upload limit (10 per hour per account) with `Retry-After`, `503 busy` (with "
    "`Retry-After`) means image processing is at capacity: retry. Never cached."
)


class _ImageRoute(NoStoreMixin, APIView):
    """Shared behaviour of the two owner write routes. `kind` is the account type served."""

    kind = ""
    permission_classes = [RequireSignedIn]
    parser_classes = [MultiPartParser]
    rate_limit_scope = "profile_image_upload"

    def check_throttles(self, request):
        # DRF runs permissions, then throttles. The account-type and state gate runs first for a
        # PUT, so a refused caller (wrong type, administrator, Deactivated) never burns the
        # hourly quota. The body is not parsed yet, so an upload that then fails validation
        # still counts (every attempt that reaches the pipeline counts).
        if request.method == "PUT":
            services.require_profile_image_access(get_actor(request), kind=self.kind)
        super().check_throttles(request)

    def get_throttles(self):
        # Only the upload counts against the limit; removing is not limited.
        if self.request is not None and self.request.method == "PUT":
            return [PolicyThrottle()]
        return []

    def _put(self, request):
        actor = get_actor(request)
        services.require_profile_image_access(actor, kind=self.kind)  # before the body is read
        data = _upload_bytes(request)
        try:
            view = services.set_profile_image(actor, data, kind=self.kind)
        except ImageRejected as exc:
            raise field_errors({"file": exc.reason}) from None
        return Response(profile_image_data(view))

    def _delete(self, request):
        services.remove_profile_image(get_actor(request), kind=self.kind)
        return Response(status=status.HTTP_204_NO_CONTENT)


_PUT_RESPONSES = {
    200: OpenApiResponse(ProfileImageSerializer, description="The stored image"),
    409: OpenApiResponse(description="`storage_cap_exceeded`"),
    503: OpenApiResponse(description="`busy`: image processing is at capacity; retry"),
}


class ProfileImageView(_ImageRoute):
    kind = "individual"

    @extend_schema(
        operation_id="put_profile_image",
        summary="Upload or replace the signed-in Individual's profile picture",
        description="Individual accounts only. " + _UPLOAD_DESCRIPTION,
        request={"multipart/form-data": ImageUploadSerializer},
        responses=_PUT_RESPONSES,
    )
    def put(self, request):
        return self._put(request)

    @extend_schema(
        operation_id="delete_profile_image",
        summary="Remove the signed-in Individual's profile picture",
        description=(
            "Needs `X-CSRFToken`. Deletes the image and its stored file and gives its bytes back "
            "to the account's storage. `204` has no body, also when there is no picture (a repeat "
            "is harmless). Same account refusals as the upload. Never cached."
        ),
        responses={204: OpenApiResponse(description="Removed (or there was none)")},
    )
    def delete(self, request):
        return self._delete(request)


class AgencyProfileImageView(_ImageRoute):
    kind = "agency"

    @extend_schema(
        operation_id="put_agency_logo",
        summary="Upload or replace the signed-in Agency's logo",
        description=(
            "Agency accounts only. The logo is then public through `GET "
            "/api/v1/agencies/{agency_id}/logo`, but only while the Agency account is Active. "
            + _UPLOAD_DESCRIPTION
        ),
        request={"multipart/form-data": ImageUploadSerializer},
        responses=_PUT_RESPONSES,
    )
    def put(self, request):
        return self._put(request)

    @extend_schema(
        operation_id="delete_agency_logo",
        summary="Remove the signed-in Agency's logo",
        description=(
            "Needs `X-CSRFToken`. Deletes the logo and its stored file and gives its bytes back "
            "to the account's storage. `204` has no body, also when there is no logo. Same "
            "account refusals as the upload. Never cached."
        ),
        responses={204: OpenApiResponse(description="Removed (or there was none)")},
    )
    def delete(self, request):
        return self._delete(request)


_BINARY = {
    (200, content_type): OpenApiResponse(OpenApiTypes.BINARY, description="The stored image bytes")
    for content_type in CONTENT_TYPES
}


class _FileRoute(NoStoreMixin, APIView):
    permission_classes = [AllowPublic]
    content_negotiation_class = IgnoreAcceptNegotiation
    cache_control = "private, no-store"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            response["Cache-Control"] = self.cache_control
        return response


class OwnImageFileView(_FileRoute):
    """The owner's own picture or logo bytes. Every refusal is the same `404 not_found`. `HEAD`
    works from the asset row; `Range` and conditional requests are ignored."""

    kind = ""

    def get(self, request):
        served = selectors.own_image_for_serving(get_actor(request).account_id, self.kind)
        return media_response(served, served.data)

    def head(self, request):
        served = selectors.own_image_for_serving(
            get_actor(request).account_id, self.kind, with_bytes=False
        )
        return media_response(served, b"")


_OWN_FILE_DESCRIPTION = (
    "The bytes of the stored, re-encoded image with its stored `Content-Type` (`image/jpeg`, "
    "`image/png` or `image/webp`), `Content-Length`, `Cache-Control: private, no-store`, "
    "`X-Content-Type-Options: nosniff`, `Content-Disposition: inline` (no file name) and "
    "`Content-Security-Policy: default-src 'none'; sandbox`; no cookie is set. `HEAD` answers the "
    "same headers without a body. Only the owning account gets it (a Suspended or Email-unverified "
    "owner included, a Deactivated one not); everyone else (another account, the other account "
    "type, an administrator, a Visitor), an account without an image and a lost file get the same "
    "`404 not_found`. `Range` and `Accept` are ignored; there is no `ETag`."
)


class ProfileImageFileView(OwnImageFileView):
    kind = "individual"

    @extend_schema(
        operation_id="get_profile_image_file",
        summary="The signed-in Individual's own profile picture",
        description=_OWN_FILE_DESCRIPTION + " An Individual's picture has no public route.",
        responses=_BINARY,
    )
    def get(self, request):
        return super().get(request)


class AgencyProfileImageFileView(OwnImageFileView):
    kind = "agency"

    @extend_schema(
        operation_id="get_agency_logo_file",
        summary="The signed-in Agency's own logo",
        description=_OWN_FILE_DESCRIPTION,
        responses=_BINARY,
    )
    def get(self, request):
        return super().get(request)


class AgencyLogoView(_FileRoute):
    """The one unauthenticated media route. Session and cookie handling are not even attempted
    (`authentication_classes = []`), so a Visitor and a signed-in caller get the same answer and
    no cookie or `Vary: Cookie` appears."""

    authentication_classes: list = []
    cache_control = f"public, max-age={LOGO_CACHE_SECONDS}"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        # A request that arrives with a stale or malformed CSRF cookie would make Django's CSRF
        # middleware reissue it (`Set-Cookie`, `Vary: Cookie`). This public, cacheable answer
        # never carries a cookie or varies by one, whatever the request brought.
        request._request.META["CSRF_COOKIE_NEEDS_UPDATE"] = False
        return response

    @extend_schema(
        operation_id="get_agency_logo",
        summary="An Agency's public logo",
        description=(
            "`agency_id` is the public Agency profile id (never the account id), as given in "
            "`logo_url`. Served to anyone, a Visitor included, ONLY while the Agency account is "
            "Active; a Suspended, Deactivated or Email-unverified Agency, an Agency without a "
            "logo, an unknown id and a malformed id all get the identical `404 not_found`. The "
            "`200` carries the stored `Content-Type`, `Content-Length`, `Cache-Control: public, "
            "max-age=60` (a suspended Agency's logo may stay in a cache for up to a minute), "
            "`X-Content-Type-Options: nosniff`, `Content-Disposition: inline` and `Content-"
            "Security-Policy: default-src 'none'; sandbox`; no cookie is set and nothing varies "
            "by cookie. `HEAD` answers the same headers without a body. `Range` and `Accept` "
            "are ignored; there is no `ETag`."
        ),
        responses=_BINARY,
    )
    def get(self, request, agency_id):
        served = selectors.logo_for_serving(agency_id)
        return media_response(served, served.data)

    def head(self, request, agency_id):
        served = selectors.logo_for_serving(agency_id, with_bytes=False)
        return media_response(served, b"")
