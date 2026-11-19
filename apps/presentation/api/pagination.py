from django.core.exceptions import ImproperlyConfigured
from rest_framework.pagination import CursorPagination


class StandardCursorPagination(CursorPagination):
    """Cursor pagination with an explicit, required ordering.

    A view sets `cursor_ordering` (or a subclass sets `ordering`) to exactly one
    unique field such as `id` (or `-id`): DRF positions the cursor on the first
    field only, so a multi-field ordering could skip or repeat rows.
    """

    page_size = 20
    max_page_size = 100
    page_size_query_param = "page_size"
    ordering = None

    def get_ordering(self, request, queryset, view):
        ordering = getattr(view, "cursor_ordering", None) or self.ordering
        if not ordering:
            raise ImproperlyConfigured("Cursor pagination requires an explicit ordering.")
        ordering = (ordering,) if isinstance(ordering, str) else tuple(ordering)
        if len(ordering) != 1:
            raise ImproperlyConfigured("Cursor ordering must be exactly one unique field.")
        return ordering
