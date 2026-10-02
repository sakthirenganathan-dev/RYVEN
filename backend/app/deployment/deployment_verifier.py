"""Post-deployment endpoint verification and HTTP health probe."""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional
from app.deployment.deployment_models import VerificationResult


class DeploymentVerifier:
    """Verifies that a deployed endpoint is well-formed, reachable, and returns valid HTTP status."""

    def verify_url_format(self, url: str) -> bool:
        """Ensure the URL is a syntactically valid public HTTP or HTTPS URI."""
        if not url or not isinstance(url, str):
            return False
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc) and not re.search(r"[<>\s\"']", url)

    async def verify_endpoint(
        self,
        url: str,
        timeout_seconds: float = 10.0,
        expected_status_codes: tuple[int, ...] = (200, 201, 204, 301, 302, 307, 308),
    ) -> VerificationResult:
        """Perform an HTTP request to verify the deployed application responds."""
        if not self.verify_url_format(url):
            return VerificationResult(
                reachable=False,
                status_code=None,
                url=url,
                message=f"Invalid deployment URL format: '{url}'",
            )

        from app.health.health_service import health_service

        health_res = await health_service.check(
            url=url,
            timeout_seconds=timeout_seconds,
            expected_statuses=list(expected_status_codes),
        )

        msg = health_res.safe_error_message or (
            f"Endpoint is reachable (HTTP {health_res.http_status}) in {health_res.response_time_ms:.1f}ms."
            if health_res.success
            else f"Endpoint returned unexpected HTTP status {health_res.http_status}."
        )

        return VerificationResult(
            reachable=health_res.success,
            status_code=health_res.http_status,
            response_time_ms=health_res.response_time_ms,
            url=health_res.url,
            message=msg,
            headers_checked=health_res.headers_checked,
        )
