"""Route table for the authorisation matrix (AD-21, AD-22). Pure walk of the URL configuration."""

from dataclasses import dataclass

from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.permissions import BasePermission
from rest_framework.views import APIView

from apps.presentation.api.permissions import EXPLICIT

BEGIN = "<!-- BEGIN GENERATED ROUTES -->"
END = "<!-- END GENERATED ROUTES -->"
_NOT_DRF = "(not a DRF view)"


@dataclass(frozen=True)
class Route:
    path: str
    methods: tuple[str, ...]
    view: str
    permissions: tuple[str, ...]  # display names; empty means no declaration
    declared: bool
    explicit: bool  # only the three allowed classes, by identity, and no get_permissions override

    @property
    def is_explicit(self) -> bool:
        return self.explicit


def _declared_permissions(callback) -> tuple[bool, list[type[BasePermission]]]:
    """A view declares permissions when `permission_classes` is set on its own class
    chain below DRF's own base views, or passed to `as_view()`."""
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if cls is None:
        return False, []
    initkwargs = getattr(callback, "initkwargs", {}) or {}
    if "permission_classes" in initkwargs:
        return True, list(initkwargs["permission_classes"])
    for klass in cls.__mro__:
        if klass.__module__.startswith("rest_framework."):
            break
        if "permission_classes" in vars(klass):
            return True, list(vars(klass)["permission_classes"])
    return False, []


def _name(permission) -> str:
    """Composed permissions (`A | B`) are instances without `__name__`."""
    if isinstance(permission, type):
        return permission.label if permission in EXPLICIT else permission.__name__
    first = getattr(permission, "op1_class", None)
    second = getattr(permission, "op2_class", None)
    if first is None:
        return type(permission).__name__
    if second is None:
        return f"~{_name(first)}"
    symbol = "|" if getattr(permission.operator_class, "__name__", "") == "OR" else "&"
    return f"{_name(first)} {symbol} {_name(second)}"


def _overrides_get_permissions(callback) -> bool:
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    return cls is not None and cls.get_permissions is not APIView.get_permissions


def _methods(callback) -> tuple[str, ...]:
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if cls is None:
        return ()
    names = getattr(cls, "http_method_names", [])
    return tuple(sorted(m.upper() for m in names if m != "options" and hasattr(cls, m)))


def _walk(patterns, prefix: str):
    for pattern in patterns:
        part = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, part)
        elif isinstance(pattern, URLPattern):
            yield part, pattern.callback


def route_table(urlconf: str | None = None) -> list[Route]:
    rows = []
    for path, callback in _walk(get_resolver(urlconf).url_patterns, ""):
        declared, classes = _declared_permissions(callback)
        cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        explicit = (
            declared
            and bool(classes)
            and all(c in EXPLICIT for c in classes)
            and not _overrides_get_permissions(callback)
        )
        rows.append(
            Route(
                path="/" + path,
                methods=_methods(callback),
                view=f"{cls.__module__}.{cls.__qualname__}" if cls else _NOT_DRF,
                permissions=tuple(_name(c) for c in classes),
                declared=declared,
                explicit=explicit,
            )
        )
    return sorted(rows, key=lambda r: (r.path, r.methods))


def render_table(rows: list[Route]) -> str:
    lines = [
        "| Route | Methods | Permission | View |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        names = ", ".join(r.permissions)
        if r.is_explicit:
            permission = names
        elif r.declared:
            permission = f"NOT EXPLICIT ({names or 'get_permissions override'})"
        else:
            permission = "UNDECLARED"
        cells = [f"`{r.path}`", ", ".join(r.methods) or "-", permission, f"`{r.view}`"]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(lines)


def render_document(existing: str, urlconf: str | None = None) -> str:
    """Replace the generated block of `existing`, leaving the hand-written text alone."""
    if (
        existing.count(BEGIN) != 1
        or existing.count(END) != 1
        or existing.index(BEGIN) > existing.index(END)
    ):
        raise ValueError(f"expected exactly one {BEGIN} followed by one {END}")
    head, _, rest = existing.partition(BEGIN)
    _, _, tail = rest.partition(END)
    table = render_table(route_table(urlconf))
    return f"{head}{BEGIN}\n{table}\n{END}{tail}"
