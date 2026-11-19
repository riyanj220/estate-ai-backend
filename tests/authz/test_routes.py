"""Route-permission test: fails any route without an explicit declaration (AD-22)."""

import pytest

from apps.presentation.api.authz_matrix import route_table


def undeclared(urlconf):
    return [f"{r.path} ({r.view})" for r in route_table(urlconf) if not r.is_explicit]


def assert_all_explicit(urlconf):
    missing = undeclared(urlconf)
    assert not missing, "routes without an explicit permission: " + "; ".join(missing)


def test_every_real_route_declares_its_permission():
    assert route_table("config.urls")
    assert_all_explicit("config.urls")


def test_tests_only_good_urlconf_declares_too():
    assert_all_explicit("tests.authz.urls")


def test_failing_fixture_names_each_offending_route():
    found = undeclared("tests.authz.bad_urls")
    assert len(found) == 6
    joined = " ".join(found)
    assert "/undeclared" in joined
    assert "/allow-any" in joined
    assert "/plain" in joined
    for route in ("/composed", "/overriding", "/spoof"):
        assert route in joined


def test_assertion_message_would_name_the_route():
    with pytest.raises(AssertionError, match="/undeclared"):
        assert_all_explicit("tests.authz.bad_urls")
