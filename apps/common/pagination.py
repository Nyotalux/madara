"""Pagination HTML simple pour les listes du back-office."""

from __future__ import annotations

from django.core.handlers.wsgi import WSGIRequest
from django.core.paginator import Paginator

DEFAULT_PER_PAGE = 25
PER_PAGE_CHOICES = (25, 50, 100)


def paginate(request: WSGIRequest, queryset, per_page: int = DEFAULT_PER_PAGE) -> dict:
    """Découpe ``queryset`` selon ``?page=`` et ``?per_page=``.

    Les paramètres d'URL autres que la page sont conservés dans
    ``querystring`` pour que les liens de pagination ne perdent pas les filtres.
    """
    try:
        per_page = int(request.GET.get("per_page", per_page))
    except (TypeError, ValueError):
        per_page = DEFAULT_PER_PAGE
    if per_page not in PER_PAGE_CHOICES:
        per_page = DEFAULT_PER_PAGE

    paginator = Paginator(queryset, per_page)
    page = paginator.get_page(request.GET.get("page"))
    return {
        "page_obj": page,
        "paginator": paginator,
        "per_page": per_page,
        "per_page_choices": PER_PAGE_CHOICES,
        "querystring": _querystring_without_page(request),
        "has_other_pages": page.has_other_pages(),
    }


def _querystring_without_page(request) -> str:
    query = request.GET.copy()
    query.pop("page", None)
    encoded = query.urlencode()
    return f"&{encoded}" if encoded else ""
