import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory

from apps.kernel import ratelimit
from apps.kernel.actor import Actor

A = uuid.UUID("11111111-1111-4111-8111-111111111111")
B = uuid.UUID("22222222-2222-4222-8222-222222222222")
rf = RequestFactory()


def req(addr="203.0.113.7", **extra):
    return rf.get("/", REMOTE_ADDR=addr, **extra)


def account_key(actor, request):
    return ratelimit.client_key(actor, request, "account")


def client_key(actor, request):
    return ratelimit.client_key(actor, request, "client")


def test_accounts_have_separate_keys_regardless_of_address():
    assert account_key(Actor.user(A), req()) != account_key(Actor.user(B), req())
    assert account_key(Actor.user(A), req("198.51.100.1")) == account_key(
        Actor.user(A), req("198.51.100.2")
    )


def test_account_kind_refuses_a_visitor():
    with pytest.raises(ImproperlyConfigured):
        account_key(Actor.visitor(), req())


def test_client_kind_ignores_the_account_so_signing_in_does_not_escape_it():
    assert client_key(Actor.user(A), req()) == client_key(Actor.visitor(), req())
    assert client_key(Actor.user(A), req()) == client_key(Actor.user(B), req())
    assert client_key(Actor.user(A), req()) != account_key(Actor.user(A), req())


def test_unknown_key_kind_is_a_configuration_error():
    with pytest.raises(ImproperlyConfigured):
        ratelimit.client_key(Actor.visitor(), req(), "other")


def test_visitor_key_differs_by_address_and_session():
    visitor = Actor.visitor()
    base = client_key(visitor, req())
    assert base != client_key(visitor, req("203.0.113.8"))
    with_session = req()
    with_session.session = type("S", (), {"session_key": "abc123"})()
    assert base != client_key(visitor, with_session)


def test_key_is_hmac_hex_with_no_raw_values():
    key = client_key(Actor.visitor(), req())
    assert len(key) == 64 and "203.0.113.7" not in key
    assert str(A) not in account_key(Actor.user(A), req())


def test_ipv6_clients_share_a_key_within_a_64():
    visitor = Actor.visitor()
    one = client_key(visitor, req("2001:db8:1:2::1"))
    assert one == client_key(visitor, req("2001:db8:1:2:ffff:ffff:ffff:ffff"))
    assert one != client_key(visitor, req("2001:db8:1:3::1"))


def test_spoofed_forwarded_header_from_untrusted_source_is_ignored(settings):
    settings.TRUSTED_PROXIES = []
    visitor = Actor.visitor()
    plain = client_key(visitor, req())
    for spoof in ("1.1.1.1", "2.2.2.2, 3.3.3.3", "garbage"):
        assert client_key(visitor, req(HTTP_X_FORWARDED_FOR=spoof)) == plain


def test_trusted_proxy_uses_forwarded_client(settings):
    settings.TRUSTED_PROXIES = ["10.0.0.0/8"]
    visitor = Actor.visitor()
    via_proxy = client_key(visitor, req("10.0.0.5", HTTP_X_FORWARDED_FOR="198.51.100.9, 10.0.0.3"))
    direct = client_key(visitor, req("198.51.100.9"))
    assert via_proxy == direct
    # a spoofed leftmost entry cannot change the client
    forged = client_key(
        visitor, req("10.0.0.5", HTTP_X_FORWARDED_FOR="9.9.9.9, 198.51.100.9, 10.0.0.3")
    )
    assert forged == direct
