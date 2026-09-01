import functools
import ipaddress
import socket
from urllib.parse import urlsplit

from lcc.models import ValidationError, is_blocked_address


@functools.lru_cache(maxsize=1024)
def resolves_to_private(host: str) -> bool:
    """
    True if this hostname resolves to any address we must never reach.

    Cached: One lookup per host per process.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False  # A literal was already judged in normalize_url

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False

    return any(is_blocked_address(ipaddress.ip_address(i[4][0])) for i in infos)


def guard(url: str) -> None:
    """Raise unless this URL is safe to request. Call on every redirect hop too."""
    host = urlsplit(url).hostname or ""

    if resolves_to_private(host):
        raise ValidationError(f"Refusing to fetch internal host: {host!r}")
