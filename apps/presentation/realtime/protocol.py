"""The shapes of the events the WebSocket sends (AD-13, AD-19), and the committed JSON Schema.

Events carry ids and numbers only, never message text or anything about a person: a client that
hears of a Message reads it over REST, where the participant rule is enforced, and resumes after a
drop the same way. `events.schema.json` is generated from here
(`manage.py events_schema --file events.schema.json`); a test fails when it is stale.
"""

import json
import uuid

READY = "ready"
MESSAGE_CREATED = "message.created"

# Close codes the client acts on (4000-4999 are for the application).
CLOSE_RECEIVE_ONLY = 4403  # the client sent a frame
CLOSE_SESSION_ENDED = 4401  # signed out, expired, suspended or deactivated: do not reconnect
CLOSE_IDLE = 4408  # reconnect at will
CLOSE_TOO_MANY = 4429  # too many connections of this account: back off

_UUID = {"type": "string", "format": "uuid"}


def _event(name: str, description: str, **properties) -> dict:
    return {
        "type": "object",
        "description": description,
        "properties": {"type": {"const": name}, **properties},
        "required": ["type", *properties],
        "additionalProperties": False,
    }


EVENTS = {
    "Ready": _event(
        READY,
        "Sent once, when events start flowing. Read what came before it over REST.",
    ),
    "MessageCreated": _event(
        MESSAGE_CREATED,
        "A Message was saved in a Conversation the account takes part in. Fetch it with "
        "GET /api/v1/conversations/{conversation_id}/messages?after={last seq held}.",
        conversation_id=_UUID,
        message_id=_UUID,
        seq={"type": "integer", "minimum": 1},
    ),
}


def ready() -> dict:
    return {"type": READY}


def message_created(conversation_id: uuid.UUID, message_id: uuid.UUID, seq: int) -> dict:
    return {
        "type": MESSAGE_CREATED,
        "conversation_id": str(conversation_id),
        "message_id": str(message_id),
        "seq": seq,
    }


def schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "EstateAI WebSocket events",
        "description": (
            "Server to client only, over /ws. Authenticated by the session cookie; the Origin must "
            "be a configured one. A client frame closes the socket. Close codes: "
            f"{CLOSE_SESSION_ENDED} session ended, {CLOSE_RECEIVE_ONLY} client sent a frame, "
            f"{CLOSE_IDLE} idle, {CLOSE_TOO_MANY} too many connections."
        ),
        "oneOf": [{"$ref": f"#/$defs/{name}"} for name in EVENTS],
        "$defs": EVENTS,
    }


def schema_text() -> str:
    return json.dumps(schema(), indent=2) + "\n"
