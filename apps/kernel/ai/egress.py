"""The hosts an AI adapter may reach (AD-8). An adapter passes every base URL through `check_url`
and must not follow redirects."""

from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset({"api.groq.com", "generativelanguage.googleapis.com"})


class EgressRefused(Exception):
    pass


def check_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_HOSTS
        or parts.netloc not in (parts.hostname, f"{parts.hostname}:443")
    ):
        raise EgressRefused("this URL is not an allowed AI provider host")
    return url
