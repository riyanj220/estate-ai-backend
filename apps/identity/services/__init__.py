"""Write path for identity. Only this package writes the account table.

Every state change and its audit record share one transaction. The code is split by area into
sibling modules; this file re-exports the public surface so `from apps.identity import services`
and `services.<name>` keep working unchanged (see `docs/architecture-notes.md`).

Names are re-exported only where code outside the package reads them. A patch of a name that a
module looks up (`make_password`, `check_password`, `emit_*`, `city_is_active`, `_email_taken`)
must target the module that uses it, not this package.
"""

from django.utils import timezone

from apps.identity import notices, selectors
from apps.identity.models import Account, AgencyProfile, EmailChangeToken, IndividualProfile
from apps.identity.services._common import _view
from apps.identity.services.accounts import create_account, deactivate, reactivate, suspend
from apps.identity.services.admin import (
    AdminRefused,
    disable_admin,
    enable_admin,
    provision_admin,
    remove_admin,
    reset_admin_password,
)
from apps.identity.services.email_changes import (
    EmailChangeDelivery,
    confirm_email_change,
    issue_email_change,
    request_email_change,
)
from apps.identity.services.email_verification import (
    confirm_email,
    issue_verification_token,
    mark_email_verified,
    request_verification_link,
)
from apps.identity.services.password_flows import (
    PasswordInvalid,
    change_password,
    confirm_password_reset,
    issue_password_reset_token,
    request_password_reset,
)
from apps.identity.services.profile import (
    FieldsInvalid,
    get_agency_profile,
    get_profile,
    require_agency,
    update_agency_profile,
    update_profile,
)
from apps.identity.services.profile_images import (
    purge_profile_image,
    remove_profile_image,
    require_profile_image_access,
    set_profile_image,
)
from apps.identity.services.registration import (
    RegistrationInvalid,
    register_agency,
    register_individual,
)
from apps.identity.services.sessions_auth import (
    SignedIn,
    admin_sign_in,
    end_account_sessions,
    refresh_session,
    sign_in,
    sign_out,
)
from apps.kernel import audit, ratelimit

__all__ = [
    "Account",
    "AdminRefused",
    "AgencyProfile",
    "EmailChangeDelivery",
    "EmailChangeToken",
    "FieldsInvalid",
    "IndividualProfile",
    "PasswordInvalid",
    "RegistrationInvalid",
    "SignedIn",
    "_view",
    "admin_sign_in",
    "audit",
    "change_password",
    "confirm_email",
    "confirm_email_change",
    "confirm_password_reset",
    "create_account",
    "deactivate",
    "disable_admin",
    "enable_admin",
    "end_account_sessions",
    "get_agency_profile",
    "get_profile",
    "issue_email_change",
    "issue_password_reset_token",
    "issue_verification_token",
    "mark_email_verified",
    "notices",
    "provision_admin",
    "purge_profile_image",
    "ratelimit",
    "reactivate",
    "refresh_session",
    "register_agency",
    "register_individual",
    "remove_admin",
    "remove_profile_image",
    "request_email_change",
    "request_password_reset",
    "request_verification_link",
    "require_agency",
    "require_profile_image_access",
    "reset_admin_password",
    "selectors",
    "set_profile_image",
    "sign_in",
    "sign_out",
    "suspend",
    "timezone",
    "update_agency_profile",
    "update_profile",
]
