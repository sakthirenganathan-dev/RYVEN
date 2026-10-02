"""Low-level Safe HTTP/HTTPS Probe Engine for RYVEN Health Engine (M13)."""

from __future__ import annotations

import asyncio
import http.client
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from app.core.logging_config import logger
from app.health.health_models import FailureCode
from app.health.health_security import HealthSecurityValidator


class HttpProbe:
    """Safe, bounded HTTP/HTTPS endpoint prober."""

    MAX_RESPONSE_BYTES = 32768  # 32 KB safety cap
    USER_AGENT = "RYVEN-Health-Probe/2.0 (+https://github.com/sakthirenganathan-dev/RYVEN)"

    # Headers safe to inspect for diagnostic purposes
    SAFE_HEADER_WHITELIST = {
        "content-type",
        "content-length",
        "server",
        "date",
        "x-request-id",
        "x-powered-by",
        "x-vercel-id",
        "x-render-origin-server",
        "x-railway-request-id",
        "location",
    }

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    def _filter_headers(self, headers: Any) -> Dict[str, str]:
        """Extract only whitelisted diagnostic headers, redacting cookies/auth."""
        safe_headers: Dict[str, str] = {}
        if not headers:
            return safe_headers
        for k, v in headers.items():
            if k.lower() in self.SAFE_HEADER_WHITELIST:
                safe_headers[k.lower()] = v[:100]  # truncate long values
        return safe_headers

    def _sync_probe(
        self,
        url: str,
        timeout: float,
        method: str = "GET",
    ) -> Dict[str, Any]:
        """Synchronous HTTP probe executed inside threadpool."""
        start_time = time.perf_counter()
        req = urllib.request.Request(
            url,
            headers={"User-Agent": self.USER_AGENT, "Accept": "*/*"},
            method=method,
        )

        # Build SSL context with standard validation
        ssl_ctx = ssl.create_default_context()

        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as response:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                status_code = response.status
                headers_dict = self._filter_headers(dict(response.headers))
                content_type = response.headers.get_content_type() if hasattr(response.headers, "get_content_type") else response.headers.get("Content-Type")

                # Read capped response bytes to prevent buffer exhaustion
                body = response.read(self.MAX_RESPONSE_BYTES)
                return {
                    "success": True,
                    "http_status": status_code,
                    "response_time_ms": round(duration_ms, 2),
                    "headers": headers_dict,
                    "content_type": content_type,
                    "failure_code": FailureCode.NONE,
                    "error_message": "",
                    "bytes_read": len(body),
                }

        except urllib.error.HTTPError as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            headers_dict = self._filter_headers(dict(e.headers)) if hasattr(e, "headers") else {}
            content_type = e.headers.get_content_type() if hasattr(e, "headers") and hasattr(e.headers, "get_content_type") else None

            # Classify 4xx vs 5xx
            if 400 <= e.code < 500:
                fail_code = FailureCode.HTTP_4XX_CLIENT_ERROR
            elif 500 <= e.code < 600:
                fail_code = FailureCode.HTTP_5XX_SERVER_ERROR
            else:
                fail_code = FailureCode.PROBE_EXCEPTION

            return {
                "success": False,
                "http_status": e.code,
                "response_time_ms": round(duration_ms, 2),
                "headers": headers_dict,
                "content_type": content_type,
                "failure_code": fail_code,
                "error_message": f"HTTP {e.code} ({e.reason})",
                "bytes_read": 0,
            }

        except urllib.error.URLError as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            reason_str = str(e.reason)
            fail_code = FailureCode.UNREACHABLE

            if "timed out" in reason_str.lower() or isinstance(e.reason, (TimeoutError, socket.timeout)):
                fail_code = FailureCode.CONNECTION_TIMEOUT
            elif "refused" in reason_str.lower() or isinstance(e.reason, ConnectionRefusedError):
                fail_code = FailureCode.CONNECTION_REFUSED
            elif "getaddrinfo failed" in reason_str.lower() or "nodename nor servname provided" in reason_str.lower():
                fail_code = FailureCode.DNS_RESOLUTION_FAILED
            else:
                fail_code = FailureCode.UNREACHABLE

            return {
                "success": False,
                "http_status": None,
                "response_time_ms": round(duration_ms, 2),
                "headers": {},
                "content_type": None,
                "failure_code": fail_code,
                "error_message": f"Network error: {reason_str}",
                "bytes_read": 0,
            }

        except (TimeoutError, socket.timeout):
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return {
                "success": False,
                "http_status": None,
                "response_time_ms": round(duration_ms, 2),
                "headers": {},
                "content_type": None,
                "failure_code": FailureCode.CONNECTION_TIMEOUT,
                "error_message": f"Probe timed out after {timeout:.1f}s",
                "bytes_read": 0,
            }

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return {
                "success": False,
                "http_status": None,
                "response_time_ms": round(duration_ms, 2),
                "headers": {},
                "content_type": None,
                "failure_code": FailureCode.PROBE_EXCEPTION,
                "error_message": f"Unexpected probe error: {str(e)}",
                "bytes_read": 0,
            }

    async def probe(
        self,
        url: str,
        timeout: Optional[float] = None,
        prefer_head: bool = True,
    ) -> Dict[str, Any]:
        """Perform asynchronous, bounded probe of target URL."""
        eff_timeout = timeout or self.timeout_seconds

        # 1. SSRF and URL validation
        is_safe, sanitized_url, security_err = HealthSecurityValidator.validate_url(url)
        if not is_safe:
            return {
                "success": False,
                "http_status": None,
                "response_time_ms": 0.0,
                "headers": {},
                "content_type": None,
                "failure_code": FailureCode.SSRF_BLOCKED,
                "error_message": security_err,
                "bytes_read": 0,
            }

        # 2. Execute GET probe inside worker thread
        # We use GET with max-bytes cap directly for maximum compatibility
        res = await asyncio.to_thread(self._sync_probe, sanitized_url, eff_timeout, "GET")
        return res
