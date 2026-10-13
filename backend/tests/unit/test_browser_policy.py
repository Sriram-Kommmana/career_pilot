"""``app.browser.policy`` — the one place "which URLs may the agent ever open"
is decided. Every case here is something ``docs/SAFETY.md`` section 6 names
explicitly: unsupported schemes, local/private-network targets, and origins
outside the supported employer list.
"""

from __future__ import annotations

import pytest

from app.browser.policy import SUPPORTED_ORIGINS, OriginRejectionReason, classify_origin

pytestmark = pytest.mark.unit


class TestSupportedOrigins:
    def test_a_supported_greenhouse_origin_is_allowed(self) -> None:
        decision = classify_origin("https://job-boards.greenhouse.io/gitlab/jobs/8860302002")
        assert decision.allowed is True
        assert decision.origin == "https://job-boards.greenhouse.io"

    def test_the_second_supported_origin_is_also_allowed(self) -> None:
        decision = classify_origin("https://boards.greenhouse.io/acme/jobs/1")
        assert decision.allowed is True

    def test_a_real_but_unsupported_employer_domain_is_refused(self) -> None:
        decision = classify_origin("https://jobs.lever.co/acme/123")
        assert decision.allowed is False
        assert decision.reason is OriginRejectionReason.UNSUPPORTED_ORIGIN

    def test_a_subdomain_of_a_supported_origin_is_not_equivalent(self) -> None:
        decision = classify_origin("https://evil.job-boards.greenhouse.io/x")
        assert decision.allowed is False
        assert decision.reason is OriginRejectionReason.UNSUPPORTED_ORIGIN

    def test_plain_http_for_a_supported_host_is_not_equivalent_to_https(self) -> None:
        decision = classify_origin("http://job-boards.greenhouse.io/gitlab/jobs/1")
        assert decision.allowed is False


class TestUnsupportedSchemes:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "chrome://settings",
            "about:blank",
            "data:text/html,<script>alert(1)</script>",
            "ftp://example.com/file",
            "blob:https://job-boards.greenhouse.io/abcd",
        ],
    )
    def test_a_non_http_scheme_is_always_refused(self, url: str) -> None:
        decision = classify_origin(url)
        assert decision.allowed is False
        assert decision.reason is OriginRejectionReason.UNSUPPORTED_SCHEME


class TestLocalAndPrivateNetworkTargets:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000/applications",
            "http://127.0.0.1/admin",
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://10.0.0.5/internal",
            "http://192.168.1.1/router",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://myhost.local/",
        ],
    )
    def test_a_local_or_private_network_target_is_refused(self, url: str) -> None:
        decision = classify_origin(url)
        assert decision.allowed is False
        assert decision.reason in (
            OriginRejectionReason.LOCAL_OR_PRIVATE_NETWORK,
            # A bracketed IPv6 loopback's hostname includes brackets under some
            # urlsplit versions; either rejection path is acceptable as long as
            # it is never ALLOWED.
            OriginRejectionReason.UNSUPPORTED_ORIGIN,
        )

    def test_a_private_network_target_is_refused_even_if_it_were_on_the_allowlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defense in depth: the local/private check applies independently of
        SUPPORTED_ORIGINS, so a misconfigured allowlist entry could not reopen
        this hole."""
        import app.browser.policy as policy_module

        monkeypatch.setattr(
            policy_module, "SUPPORTED_ORIGINS", frozenset({"http://127.0.0.1"})
        )
        decision = classify_origin("http://127.0.0.1/apply")
        assert decision.allowed is False
        assert decision.reason is OriginRejectionReason.LOCAL_OR_PRIVATE_NETWORK


class TestMalformedUrls:
    def test_a_url_with_no_host_is_refused(self) -> None:
        decision = classify_origin("https:///jobs/1")
        assert decision.allowed is False
        assert decision.reason is OriginRejectionReason.MISSING_HOST

    def test_an_empty_string_is_refused(self) -> None:
        decision = classify_origin("")
        assert decision.allowed is False


def test_every_supported_origin_is_https() -> None:
    """A defensive invariant on the allowlist itself, not just the function."""
    assert all(origin.startswith("https://") for origin in SUPPORTED_ORIGINS)
