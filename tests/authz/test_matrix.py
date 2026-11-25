from pathlib import Path

import pytest

from apps.presentation.api.authz_matrix import BEGIN, END, render_document, route_table

pytestmark = pytest.mark.guard

DOC = Path(__file__).resolve().parents[2] / "docs" / "authorisation-matrix.md"


def test_committed_matrix_is_fresh():
    committed = DOC.read_text()
    assert render_document(committed, "config.urls") == committed, (
        "docs/authorisation-matrix.md is stale. Regenerate it with: "
        "uv run python scripts/authz_matrix.py"
    )


def test_matrix_lists_every_route_with_its_permission():
    text = DOC.read_text()
    rows = route_table("config.urls")
    assert rows
    for row in rows:
        assert f"| `{row.path}` |" in text
    health = next(r for r in rows if r.path == "/api/v1/health")
    assert health.permissions == ("public",)
    assert "UNDECLARED" not in text


def test_stale_matrix_is_detected():
    stale = DOC.read_text().replace("/api/v1/health", "/api/v1/old")
    assert render_document(stale, "config.urls") != stale


def test_hand_written_text_survives_regeneration():
    text = f"before\n{BEGIN}\nold\n{END}\nafter"
    out = render_document(text)
    assert out.startswith("before\n") and out.endswith("\nafter")
    assert "old" not in out


def rows_by_path(urlconf="tests.authz.bad_urls"):
    return {r.path: r for r in route_table(urlconf)}


def test_undeclared_and_non_explicit_routes_are_marked():
    out = render_document(f"{BEGIN}\n{END}", "tests.authz.bad_urls")
    assert out.count("UNDECLARED") == 2
    assert out.count("NOT EXPLICIT") == 4


def test_composed_permission_is_reported_not_explicit():
    row = rows_by_path()["/composed"]
    assert row.declared
    assert not row.is_explicit
    assert row.permissions == ("public | admin",)


def test_get_permissions_override_is_not_explicit():
    assert not rows_by_path()["/overriding"].is_explicit


def test_label_spoofing_does_not_make_a_route_explicit():
    row = rows_by_path()["/spoof"]
    assert not row.is_explicit
    assert row.permissions == ("SpoofedLabel",)


def test_pipes_in_cells_are_escaped():
    out = render_document(f"{BEGIN}\n{END}", "tests.authz.bad_urls")
    composed = next(line for line in out.splitlines() if "/composed" in line)
    assert "\\|" in composed
    assert composed.count("|") - composed.count("\\|") == 5


@pytest.mark.parametrize(
    "text",
    [
        "no markers",
        f"{BEGIN}\nonly begin",
        f"only end\n{END}",
        f"{END}\n{BEGIN}",
        f"{BEGIN}\n{END}\n{BEGIN}\n{END}",
    ],
)
def test_missing_or_misordered_markers_raise(text):
    with pytest.raises(ValueError, match="BEGIN GENERATED ROUTES"):
        render_document(text, "config.urls")
