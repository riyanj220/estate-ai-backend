"""The full sample marketplace (Story 11.1). Development only; never runs in production.

`seed()` runs `apps.listings.sample_data.seed` (accounts and published Listings) and then, once,
the activity around them: pictures, Verification requests, Drafts, views, favourites,
Conversations, Reports, flags, moderation, sold, rented and archived Listings, support requests,
a closure request, a suspended account and read notifications. `reset()` removes all of it.

It lives in `governance`, the highest domain layer, because it must reach `messaging` too.
Everything is made through the owning apps' services, as the sample account concerned, so nothing
calls an AI provider or the network and no publication quota is used. Administrator decisions are
made as `REVIEWER`, an administrator id that no account has: nobody can sign in with it and no
real administrator is touched.

The activity is made only when none exists yet (the first Verification request of a sample Agency
marks it), so a second run changes nothing. `--reseed` starts again.
"""

import random
import uuid

from django.db.models import Q

from apps.catalog import selectors as catalog
from apps.governance import closure, flags, moderation, reports, services, support
from apps.governance.models import (
    ClosureRequest,
    ListingFlag,
    ModerationAction,
    Report,
    SupportRequest,
    VerificationRequest,
)
from apps.identity import selectors as identity
from apps.identity import services as identity_services
from apps.kernel import notifications, sample_art
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.listings import sample_data as base
from apps.listings import selectors as listing_selectors
from apps.listings import services as listings
from apps.listings import status
from apps.messaging import sample_data as sample_conversations
from apps.messaging import selectors as messaging_selectors
from apps.messaging import services as messaging

SYSTEM = base.SYSTEM
# An administrator id with no account behind it: it only names who decided.
REVIEWER = Actor.admin(uuid.uuid5(uuid.NAMESPACE_DNS, "sample-data.invalid"), "sample-data")

SeedRefused = base.SeedRefused

# local part -> initials of its logo or picture. agency.6 and the other individuals have none,
# so the empty picture is on screen too.
_LOGOS = {"agency.1": "HG", "agency.2": "IK", "agency.3": "MS", "agency.4": "SP", "agency.5": "CH"}
_PICTURES = {"individual.1": "SQ", "individual.2": "BA", "individual.5": "AK"}

# Agency -> what happens to its Verification request (agency.6 never asks).
_VERIFICATIONS = {
    "agency.1": "approved",
    "agency.2": "approved",
    "agency.3": "pending",
    "agency.4": "rejected",
    "agency.5": "revoked",
}

# (owner, (purpose, category, subtype), values, number of pictures). `locality` is a name.
_DRAFTS = (
    (
        "agency.1",
        ("sale", "house", "house"),
        {
            "title": "Draft: 600 sq yd house in DHA Phase 6",
            "description": "Ready to publish once the price is agreed with the owner.",
            "locality": "DHA Phase 6",
            "asking_price": "165000000",
            "price_basis": "total",
            "area_value": "600",
            "area_unit": "sqyd",
            "bedrooms": 5,
            "bathrooms": 6,
        },
        3,
    ),
    (
        "agency.1",
        ("rent", "apartment", "apartment"),
        {"title": "Draft: two bedroom rental in Clifton", "locality": "Clifton"},
        1,
    ),
    (
        "agency.2",
        ("sale", "plot", "residential_plot"),
        {
            "title": "Draft: plot in Scheme 33, photos to follow",
            "locality": "Scheme 33",
            "area_value": "240",
            "area_unit": "sqyd",
        },
        0,
    ),
    (
        "individual.1",
        ("rent", "apartment", "apartment"),
        {"title": "Draft: room and lounge near the university", "bedrooms": 1},
        0,
    ),
    ("individual.2", ("sale", "house", "house"), {}, 0),  # just started, no title yet
)

# account -> the Listing rows it saves (some are later sold, rented, archived or removed).
_FAVOURITES = {
    "individual.1": (1, 4, 13, 14, 16, 17, 23, 24, 26, 28, 34, 36),
    "individual.2": (0, 5, 13, 15),
    "individual.4": (14, 18, 31),
    "individual.5": (2, 8, 26),
    "agency.2": (9, 15, 22),
}

# (buyer, Listing row, [(from the buyer?, text)], who has not read the end: None, "buyer", "owner")
_THREADS = (
    (
        "individual.1",
        28,
        (
            (True, "Hello, is this apartment still available for rent from next month?"),
            (False, "Yes, it is. Would you like to visit this weekend?"),
            (True, "Saturday afternoon works for me."),
            (False, "Saturday at four is fine. See you at the gate."),
        ),
        None,
    ),
    (
        "individual.4",
        16,
        (
            (True, "Is the price final or is there room to talk?"),
            (False, "A little room for a serious buyer who can close quickly."),
        ),
        None,
    ),
    (
        "individual.7",
        5,
        (
            (True, "Does the rent include the internet connection?"),
            (False, "Internet is set up, the monthly bill is separate."),
        ),
        None,
    ),
    (
        "individual.1",
        4,
        (
            (True, "Is the servant quarter included in the rent?"),
            (False, "Yes, the whole house is included."),
            (True, "Thank you, I will discuss it with my family."),
        ),
        None,
    ),
    (
        "individual.5",
        26,
        (
            (True, "Can we move in at the start of next month?"),
            (False, "Yes, the current tenant leaves before then."),
        ),
        None,
    ),
    (
        "agency.2",
        15,
        (
            (True, "We have a client for this plot. Is possession ready?"),
            (False, "Possession is ready and the transfer can start any day."),
        ),
        None,
    ),
    (
        "individual.2",
        3,
        (
            (True, "How wide is the frontage of the shop?"),
            (False, "About eighteen feet, facing the main road."),
            (True, "Is a visit possible on a weekday evening?"),
        ),
        "owner",
    ),
    (
        "individual.2",
        0,
        (
            (True, "Assalam o alaikum, is the bungalow still available?"),
            (True, "Could you share when it was last painted?"),
        ),
        "owner",
    ),
    (
        "individual.4",
        14,
        (
            (True, "Is the swimming pool maintained at the moment?"),
            (False, "Yes, it is cleaned every week."),
            (True, "You people waste everyone's time with these prices."),
        ),
        "owner",
    ),
    (
        "individual.3",
        36,
        ((True, "Is the apartment available for a one year lease?"),),
        "owner",
    ),
    (
        "individual.1",
        1,
        (
            (True, "Which side of the building does the balcony face?"),
            (False, "It faces the sea, so it gets the evening breeze."),
            (False, "I can show it to you tomorrow if you like."),
        ),
        "buyer",
    ),
    (
        "individual.1",
        13,
        (
            (True, "Hello, I am interested in this apartment. Is it still on the market?"),
            (False, "Yes, it is. Are you buying for yourself or as an investment?"),
            (True, "For my family. Are two parking spaces available?"),
            (False, "One reserved space, and visitor parking is usually free."),
            (True, "And the maintenance charges?"),
            (False, "They are modest and cover the lift, guards and water."),
            (False, "Let me know a time and I will arrange a visit."),
        ),
        "buyer",
    ),
)

# (reporter, Listing row, reason, description, final status)
_LISTING_REPORTS = (
    ("individual.1", 17, "misleading", "The pictures do not match the house.", "reviewing"),
    ("individual.4", 17, "duplicate", "I saw the same house posted elsewhere.", "pending"),
    ("individual.5", 38, "duplicate", "This apartment appears twice.", "pending"),
    ("individual.2", 27, "misleading", "The house is not for rent any more.", "resolved"),
    ("individual.3", 24, "prohibited_content", "This file cannot be sold like this.", "resolved"),
    ("individual.1", 7, "other", "The price looks too low to be true.", "dismissed"),
    ("individual.5", 23, "ai_generated_content", "The description reads as generated.", "pending"),
)

# (reporter, buyer of the thread, Listing row, reason, description, final status)
_THREAD_REPORTS = (
    ("agency.1", "individual.4", 14, "abusive", "Rude message from this person.", "pending"),
    ("individual.1", "individual.1", 13, "other", "Pressed me to decide quickly.", "reviewing"),
)

# A row's `then`: a moderation action and its reason (any other value is the owner's own move).
_MODERATION = {
    "restrict": "Please confirm the property is still available.",
    "remove": "The listing details could not be supported by the seller.",
}
_LABELS = {"restrict": "listings restricted", "remove": "listings taken down"}

# (account, category, message, closed?)
_SUPPORT = (
    (
        "individual.1",
        "technical",
        "Pictures took a long time to appear on my phone yesterday evening.",
        True,
    ),
    ("individual.1", "other", "Can I change the city of my account later on?", False),
    (
        "agency.1",
        "decision",
        "Please explain why one of our listings was removed and how we can correct it.",
        False,
    ),
    ("individual.5", "account", "I would like to know how to change my email address.", True),
    ("agency.4", "decision", "We would like to apply for verification again.", False),
    ("individual.7", "decision", "I believe my account was suspended by mistake.", False),
)

_CLOSING = "individual.6"
_SUSPENDED = "individual.7"
_ALL_READ = ("individual.4", "individual.5", "agency.2")


class _Cast:
    """The sample accounts as actors, and the seeded Listings by table row."""

    def __init__(self) -> None:
        self.actors = {}
        for email, account_id in identity.sample_account_ids().items():
            local = email.partition("@")[0].removeprefix("sample.")
            self.actors[local] = Actor.user(account_id, "sample-data")
        self.rows = {
            index: (row, owner, listing_id)
            for index, row, owner, listing_id, _ in base.seeded_listings()
        }

    def public(self, index: int):
        """(owner actor, listing id) of a row anyone can see now, or None."""
        found = self.rows.get(index)
        if found is None:
            return None
        try:
            listing_selectors.public_listing(Actor.visitor("sample-data"), found[2])
        except DomainError:
            return None
        return found[1], found[2]


def _count(report: base.Report, label: str, by: int = 1) -> None:
    report.activity[label] = report.activity.get(label, 0) + by


def _pictures(cast: _Cast, report: base.Report) -> None:
    for number, (local, initials) in enumerate({**_LOGOS, **_PICTURES}.items()):
        kind = local.partition(".")[0]
        identity_services.set_profile_image(
            cast.actors[local], sample_art.badge(initials, number), kind=kind
        )
        _count(report, "profile pictures")


def _verification(cast: _Cast, report: base.Report) -> None:
    for number, (local, outcome) in enumerate(_VERIFICATIONS.items(), start=1):
        actor = cast.actors[local]
        name = next(a[2] for a in base.ACCOUNTS if a[1] == f"sample.{local}")
        services.submit_request(actor, name, f"REG-SAMPLE-{number}", "Office visits are welcome.")
        _count(report, "verification requests")
        if outcome == "pending":
            continue
        request_id = VerificationRequest.objects.get(
            agency_account_id=actor.account_id, status="pending"
        ).id
        if outcome == "rejected":
            services.reject_request(
                REVIEWER, request_id, "The registration number could not be matched."
            )
            continue
        services.approve_request(REVIEWER, request_id)
        if outcome == "revoked":
            services.revoke_verification(
                REVIEWER, request_id, "The registration lapsed and was not renewed."
            )


def _drafts(cast: _Cast, report: base.Report) -> None:
    city = next(c for c in catalog.list_cities() if c.name == base.CITY_NAME)
    localities = {loc.name: loc.id for loc in catalog.list_localities(city.id) or ()}
    for number, (local, kind, values, pictures) in enumerate(_DRAFTS):
        actor = cast.actors[local]
        draft = listings.create_draft(actor, *kind)
        updates = dict(values)
        if "locality" in updates:
            updates["city"] = str(city.id)
            updates["locality"] = str(localities[updates["locality"]])
        if updates:
            listings.apply_updates(actor, draft.id, draft.revision, updates)
        for picture in range(pictures):
            scene = ("house", "lounge", "bedroom")[picture]
            listings.upload_image(actor, draft.id, sample_art.scene(scene, 900 + number + picture))
        _count(report, "drafts")


def _views(cast: _Cast, report: base.Report) -> None:
    for index in sorted(cast.rows):
        found = cast.public(index)
        if found is None:
            continue
        rng = random.Random(f"views-{index}")  # noqa: S311 - not a secret
        views = rng.randrange(40, 90) if index in (0, 1, 13, 14) else rng.randrange(0, 30)
        for number in range(views):
            listings.record_view(Actor.visitor("sample-data"), found[1], f"sample-{number}")
        _count(report, "views", views)


def _favourites(cast: _Cast, report: base.Report) -> None:
    for local, wanted in _FAVOURITES.items():
        for index in wanted:
            found = cast.public(index)
            if found is not None and found[0] != cast.actors[local]:
                listings.save_favourite(cast.actors[local], found[1])
                _count(report, "favourites")


def _conversations(cast: _Cast, report: base.Report) -> dict:
    """The threads in order (the last is the newest). Returns (buyer, row) -> Conversation id."""
    made = {}
    for number, (buyer_local, index, lines, unread) in enumerate(_THREADS):
        found = cast.public(index)
        buyer = cast.actors[buyer_local]
        if found is None or found[0] == buyer:
            continue
        owner, listing_id = found
        conversation_id = None
        for position, (from_buyer, text) in enumerate(lines):
            key = f"seed-{number}-{position}"
            if conversation_id is None:
                result = messaging.start_conversation(buyer, listing_id, text, key)
                conversation_id = result.conversation.id
            else:
                sender = buyer if from_buyer else owner
                messaging.send_message(sender, conversation_id, text, key)
            _count(report, "messages")
        # A sender has read its own Messages; the other side reads unless it is to stay unread.
        for side, actor in (("buyer", buyer), ("owner", owner)):
            if side != unread:
                messaging.mark_read(actor, conversation_id, len(lines))
        made[(buyer_local, index)] = conversation_id
        _count(report, "conversations")
    return made


def _review(report_id, final: str) -> None:
    if final == "pending":
        return
    reports.start_review(REVIEWER, report_id, "Looking into it.")
    if final == "resolved":
        reports.resolve_report(REVIEWER, report_id, "Action was taken on the listing.")
    elif final == "dismissed":
        reports.dismiss_report(REVIEWER, report_id, "Nothing wrong was found.")


def _reports(cast: _Cast, report: base.Report, threads: dict) -> None:
    for local, index, reason, text, final in _LISTING_REPORTS:
        found = cast.public(index)
        if found is None:
            continue
        made = reports.report_listing(cast.actors[local], found[1], reason, text)
        _review(made.id, final)
        _count(report, "reports")
    for local, buyer_local, index, reason, text, final in _THREAD_REPORTS:
        conversation_id = threads.get((buyer_local, index))
        if conversation_id is None:
            continue
        reporter = cast.actors[local]
        page = messaging_selectors.messages_after(reporter, conversation_id)
        theirs = [str(m.id) for m in page.items if not m.mine]
        made = reports.report_conversation(reporter, conversation_id, theirs[-1:], reason, text)
        _review(made.id, final)
        _count(report, "reports")


def _flags(cast: _Cast, report: base.Report) -> None:
    ids = [listing_id for _, _, listing_id in cast.rows.values()]
    flags.check_suspicious(ids)
    for listing_id in ids:
        flags.check_duplicates(listing_id)
    _count(report, "flags", ListingFlag.objects.filter(listing_id__in=ids).count())


def _outcomes(cast: _Cast, report: base.Report) -> None:
    """What the table says happens to a Listing after publication."""
    for index in sorted(cast.rows):
        row, owner, listing_id = cast.rows[index]
        then = row.get("then")
        if then is None or cast.public(index) is None:
            continue
        if then in _MODERATION:
            moderation.moderate(REVIEWER, listing_id, then, _MODERATION[then])
        else:
            revision = listing_selectors.get_owned_listing(owner, listing_id).revision
            status.change_status(owner, listing_id, revision, then)
        _count(report, _LABELS.get(then, f"listings {then}"))


def _support(cast: _Cast, report: base.Report) -> None:
    for local, category, message, closed in _SUPPORT:
        made = support.submit_request(cast.actors[local], category, message)
        if closed:
            support.close_request(REVIEWER, made.id)
        _count(report, "support requests")


def _accounts(cast: _Cast, report: base.Report) -> None:
    closure.request_closure(cast.actors[_CLOSING], "deletion")
    _count(report, "closure requests")
    identity_services.suspend_account(
        REVIEWER,
        cast.actors[_SUSPENDED].account_id,
        "Several buyers reported listings that were not available.",
    )
    _count(report, "suspended accounts")


def _read_notifications(cast: _Cast) -> None:
    for local in _ALL_READ:
        notifications.mark_all_read(cast.actors[local])
    for local in ("individual.1", "agency.1"):  # one read, the rest unread
        oldest = notifications.list_for(cast.actors[local]).last()
        if oldest is not None:
            notifications.mark_read(cast.actors[local], oldest.id)


def _activity_exists() -> bool:
    ids = identity.sample_account_ids().values()
    return VerificationRequest.objects.filter(agency_account_id__in=list(ids)).exists()


def _add_activity(report: base.Report) -> None:
    cast = _Cast()
    _verification(cast, report)  # first: it marks that the activity has been made
    _pictures(cast, report)
    _drafts(cast, report)
    _views(cast, report)
    _favourites(cast, report)
    threads = _conversations(cast, report)
    _reports(cast, report, threads)  # while the Listings are still public
    _flags(cast, report)
    _outcomes(cast, report)
    _support(cast, report)
    _accounts(cast, report)
    _read_notifications(cast)  # last: after every notification


def seed(
    listings_wanted: int = base.DEFAULT_LISTINGS, password: str | None = None, announce=None
) -> base.Report:
    """`listings.sample_data.seed`, then the activity if there is none yet."""
    report = base.seed(listings_wanted, password, announce)
    if not _activity_exists():
        with base.estimates_in_this_thread():
            _add_activity(report)
    return report


def _records(account_ids: list, listing_ids: list) -> dict:
    """The governance rows that involve sample records, by label."""
    return {
        "reports": Report.objects.filter(
            Q(reporter_account_id__in=account_ids) | Q(subject_account_id__in=account_ids)
        ),
        "moderation actions": ModerationAction.objects.filter(listing_id__in=listing_ids),
        "flags": ListingFlag.objects.filter(
            Q(listing_id__in=listing_ids) | Q(other_listing_id__in=listing_ids)
        ),
        "verification requests": VerificationRequest.objects.filter(
            agency_account_id__in=account_ids
        ),
        "support requests": SupportRequest.objects.filter(account_id__in=account_ids),
        "closure requests": ClosureRequest.objects.filter(account_id__in=account_ids),
    }


def _targets() -> tuple[list, list]:
    account_ids = list(identity.sample_account_ids().values())
    return account_ids, list(listing_selectors.listing_ids_owned_by(account_ids))


def preview_reset() -> base.Report:
    """What `reset` would remove, as counts (nothing is changed)."""
    report = base.preview_reset()
    account_ids, listing_ids = _targets()
    report.activity["conversations"] = sample_conversations.count_conversations(account_ids)
    for label, rows in _records(account_ids, listing_ids).items():
        report.activity[label] = rows.count()
    return report


def reset() -> base.Report:
    """Remove every record the command created and nothing else. Reports and Conversations that
    involve a sample account go with it, whoever else took part. The decision histories are
    append-only for the runtime role, so, like the seed publication events, they are deleted
    through the privileged connection. Audit records stay."""
    base.require_allowed()
    account_ids, listing_ids = _targets()
    activity = {"conversations": sample_conversations.remove_conversations(SYSTEM, account_ids)}
    alias = base.privileged_alias()
    for label, rows in _records(account_ids, listing_ids).items():
        activity[label] = rows.count()
        rows.using(alias).delete()  # cascades to the decision and transition history
    for account_id in account_ids:
        listings.remove_sample_favourites(SYSTEM, account_id)
    report = base.reset()
    report.activity = activity
    return report


def reseed(
    listings_wanted: int = base.DEFAULT_LISTINGS, password: str | None = None, announce=None
) -> base.Report:
    """`reset` then `seed`; the report adds both (`activity` holds what was made)."""
    base.check_count(listings_wanted)
    base.require_allowed()
    removed = reset()
    made = seed(listings_wanted, password, announce)
    made.accounts_removed = removed.accounts_removed
    made.listings_removed = removed.listings_removed
    made.events_removed = removed.events_removed
    return made
