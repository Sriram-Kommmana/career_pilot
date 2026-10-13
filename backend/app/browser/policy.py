"""Which URLs the browser agent may ever navigate to.

Pure: string parsing only (``urllib.parse``), no network, no Playwright import, no
database. Checked server-side before a session is created, again on every
navigation the session observes, and again immediately before a fill — a page
that redirects to an unsupported origin mid-session must never be treated as
having passed this check once and for all.

``docs/SAFETY.md`` section 6: "Restrict navigation to explicitly supported
employer application origins... reject file:, javascript:, browser-internal
URLs, and arbitrary local-network targets." This module is where that rule lives
as code, not as a comment someone has to remember to apply.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

#: Employer application origins this phase supports. Scheme and host must match
#: exactly — a subdomain or a plain-http variant is deliberately not equivalent.
SUPPORTED_ORIGINS: Final[frozenset[str]] = frozenset(
    {
        "https://job-boards.greenhouse.io",
        "https://boards.greenhouse.io",
    }
)

#: Schemes a browser session is ever allowed to navigate to. Everything else —
#: file:, javascript:, data:, chrome:, about:, ftp:, blob: — is refused outright.
_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

#: Hostnames that are never a real employer, regardless of scheme.
_BLOCKED_HOSTNAMES: Final[frozenset[str]] = frozenset(
    {"localhost", "0.0.0.0", "metadata.google.internal"}  # noqa: S104 - a blocklist entry, not a bind address
)


class OriginRejectionReason(StrEnum):
    UNSUPPORTED_SCHEME = "UNSUPPORTED_SCHEME"
    MISSING_HOST = "MISSING_HOST"
    LOCAL_OR_PRIVATE_NETWORK = "LOCAL_OR_PRIVATE_NETWORK"
    UNSUPPORTED_ORIGIN = "UNSUPPORTED_ORIGIN"


@dataclass(frozen=True, slots=True)
class OriginDecision:
    url: str
    allowed: bool
    reason: OriginRejectionReason | None = None
    origin: str | None = None

    def explain(self) -> str:
        if self.allowed:
            return f"{self.origin} is a supported employer origin."
        return {
            OriginRejectionReason.UNSUPPORTED_SCHEME: (
                "Only http/https pages may be opened."
            ),
            OriginRejectionReason.MISSING_HOST: "The URL has no host to validate.",
            OriginRejectionReason.LOCAL_OR_PRIVATE_NETWORK: (
                "Local and private-network addresses are never a real employer site."
            ),
            OriginRejectionReason.UNSUPPORTED_ORIGIN: (
                "This origin is not one of CareerPilot's supported employer "
                "application sites."
            ),
        }.get(self.reason, "This URL is not allowed.")  # type: ignore[arg-type]


def _is_local_or_private(hostname: str) -> bool:
    """True for loopback, private, link-local, or otherwise non-public hosts.

    Defense in depth: even if ``SUPPORTED_ORIGINS`` were ever misconfigured, a
    local or private-network target is refused independently of that list.
    """
    lowered = hostname.lower()
    if lowered in _BLOCKED_HOSTNAMES or lowered.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def classify_origin(url: str) -> OriginDecision:
    """Decide whether a browser session may navigate to ``url``.

    Every check fails closed: a URL that cannot be parsed, has no scheme, or has
    no host is refused, never defaulted to allowed.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return OriginDecision(url=url, allowed=False, reason=OriginRejectionReason.MISSING_HOST)

    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        return OriginDecision(
            url=url, allowed=False, reason=OriginRejectionReason.UNSUPPORTED_SCHEME
        )

    hostname = parts.hostname
    if not hostname:
        return OriginDecision(url=url, allowed=False, reason=OriginRejectionReason.MISSING_HOST)

    if _is_local_or_private(hostname):
        return OriginDecision(
            url=url, allowed=False, reason=OriginRejectionReason.LOCAL_OR_PRIVATE_NETWORK
        )

    port = f":{parts.port}" if parts.port else ""
    origin = f"{scheme}://{hostname}{port}"
    if origin not in SUPPORTED_ORIGINS:
        return OriginDecision(
            url=url, allowed=False, reason=OriginRejectionReason.UNSUPPORTED_ORIGIN, origin=origin
        )

    return OriginDecision(url=url, allowed=True, origin=origin)
