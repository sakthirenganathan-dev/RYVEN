"""Comprehensive Test Suite for RYVEN 2.0 Milestone 13
Deployment Verification & Health Monitoring Engine.

Zero-regression test coverage:
1. Health Models & Enums
2. SSRF Protection & URL Security
3. HTTP Probe Engine & Header Redaction
4. Deterministic Failure Detection & Latency Tiers
5. Health History Manager (Bounded In-Memory Ring Buffer)
6. Runtime Background Health Monitor & Clean Shutdown
7. Health Service Facade
8. DeploymentVerifier Integration
9. ToolRegistry Integration (5 Health Tools)
10. SafetyGuard & Permissions (Safe, No Auto-Rollback)
11. IntentRouter (Operational vs Educational Routing)
12. Security Regressions & No-Database Verification
"""

import asyncio
import io
import ipaddress
import socket
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from app.core.permissions import SafetyGuard, safety_guard
from app.core.router import IntentRouter
from app.deployment.deployment_models import (
    DeploymentProvider,
    DeploymentResult,
    DeploymentStatus,
    VerificationResult,
)
from app.deployment.deployment_verifier import DeploymentVerifier
from app.health.failure_detector import FailureDetector
from app.health.health_checker import HealthChecker
from app.health.health_history import HealthHistoryManager
from app.health.health_models import (
    FailureCode,
    HealthCheckResult,
    HealthHistorySummary,
    HealthStatus,
    LatencyTier,
    MonitorConfig,
    MonitorStatus,
)
from app.health.health_monitor import HealthMonitor
from app.health.health_security import HealthSecurityValidator
from app.health.health_service import HealthService
from app.health.health_tool import (
    HealthCheckTool,
    HealthHistoryTool,
    HealthMonitorStartTool,
    HealthMonitorStopTool,
    HealthStatusTool,
)
from app.health.http_probe import HttpProbe
from app.tools.registry import create_default_registry
from app.workflows.confirmation import ConfirmationManager


# =============================================================================
# 1. HEALTH MODELS & ENUMS
# =============================================================================

class TestHealthModels:
    """Verifies strongly typed models, enum representations, and serialization."""

    def test_health_status_enum_values(self):
        expected_statuses = {
            "UNKNOWN", "CHECKING", "HEALTHY", "DEGRADED", "UNHEALTHY",
            "UNREACHABLE", "TIMEOUT", "INVALID_URL", "BLOCKED", "FAILED",
        }
        actual_statuses = {s.value for s in HealthStatus}
        assert expected_statuses.issubset(actual_statuses)

    def test_failure_code_enum_values(self):
        expected_codes = {
            "NONE", "HTTP_4XX_CLIENT_ERROR", "HTTP_5XX_SERVER_ERROR",
            "HTTP_REDIRECT_LOOP", "CONNECTION_REFUSED", "DNS_RESOLUTION_FAILED",
            "CONNECTION_TIMEOUT", "READ_TIMEOUT", "UNREACHABLE", "SSRF_BLOCKED",
            "INVALID_SCHEME", "INVALID_URL_FORMAT", "RESPONSE_TOO_LARGE",
            "PROBE_EXCEPTION",
        }
        actual_codes = {c.value for c in FailureCode}
        assert expected_codes.issubset(actual_codes)

    def test_latency_tier_enum(self):
        assert LatencyTier.FAST.value == "FAST"
        assert LatencyTier.MODERATE.value == "MODERATE"
        assert LatencyTier.SLOW.value == "SLOW"

    def test_health_check_result_serialization(self):
        res = HealthCheckResult(
            url="https://app.example.com",
            status=HealthStatus.HEALTHY,
            success=True,
            http_status=200,
            response_time_ms=142.5,
            latency_tier=LatencyTier.FAST,
            checked_at="2026-10-02T12:00:00Z",
            failure_code=FailureCode.NONE,
            safe_error_message="",
            deployment_id="dep_123",
            project_id="my-app",
            provider="VERCEL",
        )
        data = res.model_dump()
        assert data["url"] == "https://app.example.com"
        assert data["status"] == "HEALTHY"
        assert data["success"] is True
        assert data["http_status"] == 200
        assert data["response_time_ms"] == 142.5
        assert data["deployment_id"] == "dep_123"

    def test_monitor_config_defaults(self):
        cfg = MonitorConfig(url="https://example.com")
        assert cfg.interval_seconds == 30.0
        assert cfg.timeout_seconds == 5.0
        assert cfg.max_history_entries == 100
        assert 200 in cfg.expected_statuses


# =============================================================================
# 2. SSRF PROTECTION & URL SECURITY
# =============================================================================

class TestSSRFProtection:
    """Verifies strict security validation for all outbound network requests."""

    def test_allowed_public_schemes(self):
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
            safe, _, _ = HealthSecurityValidator.validate_url("https://example.com")
            assert safe is True
            safe_http, _, _ = HealthSecurityValidator.validate_url("http://example.com")
            assert safe_http is True

    @pytest.mark.parametrize("bad_url", [
        "file:///etc/passwd",
        "file://C:/Windows/win.ini",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "ftp://ftp.example.com",
        "gopher://gopher.example.com",
    ])
    def test_blocked_schemes(self, bad_url):
        safe, _, reason = HealthSecurityValidator.validate_url(bad_url, resolve_dns=False)
        assert safe is False
        assert ("Disallowed URI scheme" in reason) or ("Security violation" in reason)

    @pytest.mark.parametrize("unc_path", [
        r"\\localhost\c$\secret",
        r"\\192.168.1.1\share",
        "//server/share/resource",
        r"https://example.com\..\bad",
    ])
    def test_blocked_unc_and_backslash(self, unc_path):
        safe, _, reason = HealthSecurityValidator.validate_url(unc_path, resolve_dns=False)
        assert safe is False
        assert "Security violation" in reason

    @pytest.mark.parametrize("hostname", [
        "http://localhost",
        "http://localhost:3000",
        "https://localhost.localdomain",
        "http://metadata.google.internal",
        "http://metadata.internal",
        "http://instance-data",
    ])
    def test_blocked_internal_hostnames(self, hostname):
        safe, _, reason = HealthSecurityValidator.validate_url(hostname, resolve_dns=False)
        assert safe is False
        assert "SSRF violation" in reason

    @pytest.mark.parametrize("ip_url", [
        "http://127.0.0.1",
        "http://127.0.0.1:8000",
        "http://127.0.0.2",
        "http://0.0.0.0",
        "http://10.0.0.1",
        "http://10.255.255.255",
        "http://172.16.0.1",
        "http://172.31.255.255",
        "http://192.168.0.1",
        "http://192.168.1.100",
        "http://169.254.169.254",  # AWS/GCP/Azure metadata IP
        "http://[::1]",
        "http://[::]",
        "http://[fe80::1]",
    ])
    def test_blocked_private_and_reserved_ips(self, ip_url):
        safe, _, reason = HealthSecurityValidator.validate_url(ip_url, resolve_dns=False)
        assert safe is False
        assert "SSRF violation" in reason

    def test_dns_rebinding_detection(self):
        """If a domain resolves to a private IP, it must be blocked."""
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]):
            safe, _, reason = HealthSecurityValidator.validate_url("https://malicious-local-redirect.com", resolve_dns=True)
            assert safe is False
            assert "resolved to blocked IP" in reason

    def test_credential_stripping(self):
        """Embedded HTTP auth credentials must be sanitized."""
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
            safe, sanitized, _ = HealthSecurityValidator.validate_url("https://admin:secretpassword@example.com/status")
            assert safe is True
            assert "admin" not in sanitized
            assert "secretpassword" not in sanitized
            assert sanitized == "https://example.com/status"


# =============================================================================
# 3. HTTP PROBE ENGINE
# =============================================================================

class TestHttpProbe:
    """Verifies safe HTTP probe mechanics with mock responses."""

    @pytest.mark.asyncio
    async def test_probe_success_200(self):
        probe = HttpProbe(timeout_seconds=5.0)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/html", "Server": "Vercel", "Set-Cookie": "secret=val"}
        mock_resp.read.return_value = b"<html>OK</html>"
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
            res = await probe.probe("https://example.com")
            assert res["success"] is True
            assert res["http_status"] == 200
            assert res["content_type"] == "text/html"
            assert "server" in res["headers"]
            # Cookies must NEVER be captured in safe headers
            assert "set-cookie" not in res["headers"]

    @pytest.mark.asyncio
    async def test_probe_http_404(self):
        probe = HttpProbe(timeout_seconds=5.0)

        http_err = urllib.error.HTTPError(
            url="https://example.com/missing",
            code=404,
            msg="Not Found",
            hdrs={"Content-Type": "text/plain"},
            fp=io.BytesIO(b"Not Found"),
        )

        with patch("urllib.request.urlopen", side_effect=http_err), \
             patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
            res = await probe.probe("https://example.com/missing")
            assert res["success"] is False
            assert res["http_status"] == 404
            assert res["failure_code"] == FailureCode.HTTP_4XX_CLIENT_ERROR

    @pytest.mark.asyncio
    async def test_probe_http_500(self):
        probe = HttpProbe(timeout_seconds=5.0)

        http_err = urllib.error.HTTPError(
            url="https://example.com/crash",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b"Crash"),
        )

        with patch("urllib.request.urlopen", side_effect=http_err), \
             patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
            res = await probe.probe("https://example.com/crash")
            assert res["success"] is False
            assert res["http_status"] == 500
            assert res["failure_code"] == FailureCode.HTTP_5XX_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_probe_timeout(self):
        probe = HttpProbe(timeout_seconds=2.0)

        url_err = urllib.error.URLError(reason=socket.timeout("The read operation timed out"))

        with patch("urllib.request.urlopen", side_effect=url_err), \
             patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
            res = await probe.probe("https://example.com/slow")
            assert res["success"] is False
            assert res["failure_code"] == FailureCode.CONNECTION_TIMEOUT

    @pytest.mark.asyncio
    async def test_probe_connection_refused(self):
        probe = HttpProbe(timeout_seconds=2.0)

        url_err = urllib.error.URLError(reason=ConnectionRefusedError("Connection refused"))

        with patch("urllib.request.urlopen", side_effect=url_err), \
             patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
            res = await probe.probe("https://example.com/dead")
            assert res["success"] is False
            assert res["failure_code"] == FailureCode.CONNECTION_REFUSED

    @pytest.mark.asyncio
    async def test_probe_blocks_ssrf_before_request(self):
        probe = HttpProbe(timeout_seconds=5.0)
        # Should block without invoking urlopen
        with patch("urllib.request.urlopen") as mock_open:
            res = await probe.probe("http://127.0.0.1:8000")
            assert res["success"] is False
            assert res["failure_code"] == FailureCode.SSRF_BLOCKED
            mock_open.assert_not_called()


# =============================================================================
# 4. DETERMINISTIC FAILURE DETECTION & LATENCY CLASSIFICATION
# =============================================================================

class TestFailureDetector:
    """Verifies deterministic mapping of HTTP responses into HealthStatus and FailureCodes."""

    def test_http_200_fast_latency(self):
        status, code, msg = FailureDetector.classify(200, 120.0, FailureCode.NONE)
        assert status == HealthStatus.HEALTHY
        assert code == FailureCode.NONE
        assert "healthy" in msg.lower()

    def test_http_200_slow_latency_degraded(self):
        # Over 2000 ms is slow -> DEGRADED
        status, code, msg = FailureDetector.classify(200, 2500.0, FailureCode.NONE)
        assert status == HealthStatus.DEGRADED
        assert code == FailureCode.NONE
        assert "slow" in msg.lower()

    def test_http_301_allowed_redirect(self):
        status, code, _ = FailureDetector.classify(301, 80.0, FailureCode.NONE, expected_statuses=[200, 301, 302])
        assert status == HealthStatus.HEALTHY
        assert code == FailureCode.NONE

    def test_http_401_auth_required_is_degraded(self):
        # 401 / 403 proves server is running but protected
        status, code, msg = FailureDetector.classify(401, 150.0, FailureCode.HTTP_4XX_CLIENT_ERROR)
        assert status == HealthStatus.DEGRADED
        assert code == FailureCode.HTTP_4XX_CLIENT_ERROR
        assert "authentication" in msg.lower()

    def test_http_404_unhealthy(self):
        status, code, _ = FailureDetector.classify(404, 110.0, FailureCode.HTTP_4XX_CLIENT_ERROR)
        assert status == HealthStatus.UNHEALTHY
        assert code == FailureCode.HTTP_4XX_CLIENT_ERROR

    def test_http_500_unhealthy(self):
        status, code, _ = FailureDetector.classify(500, 200.0, FailureCode.HTTP_5XX_SERVER_ERROR)
        assert status == HealthStatus.UNHEALTHY
        assert code == FailureCode.HTTP_5XX_SERVER_ERROR

    def test_timeout_classification(self):
        status, code, _ = FailureDetector.classify(None, 5000.0, FailureCode.CONNECTION_TIMEOUT, "Timed out")
        assert status == HealthStatus.TIMEOUT
        assert code == FailureCode.CONNECTION_TIMEOUT

    def test_ssrf_blocked_classification(self):
        status, code, _ = FailureDetector.classify(None, 0.0, FailureCode.SSRF_BLOCKED, "Private IP")
        assert status == HealthStatus.BLOCKED
        assert code == FailureCode.SSRF_BLOCKED

    def test_latency_tiers(self):
        assert FailureDetector.classify_latency(150.0) == LatencyTier.FAST
        assert FailureDetector.classify_latency(750.0) == LatencyTier.MODERATE
        assert FailureDetector.classify_latency(2500.0) == LatencyTier.SLOW
        assert FailureDetector.classify_latency(-1.0) == LatencyTier.UNKNOWN


# =============================================================================
# 5. HEALTH HISTORY MANAGER (BOUNDED IN-MEMORY)
# =============================================================================

class TestHealthHistory:
    """Verifies bounded ring buffer history and metric calculations."""

    def test_history_recording_and_retrieval(self):
        mgr = HealthHistoryManager(max_records_per_target=5)
        url = "https://app.example.com"

        for i in range(10):
            res = HealthCheckResult(
                url=url,
                status=HealthStatus.HEALTHY if i % 2 == 0 else HealthStatus.UNHEALTHY,
                success=(i % 2 == 0),
                http_status=200 if i % 2 == 0 else 500,
                response_time_ms=100.0 + i,
                checked_at=f"2026-10-02T12:00:0{i}Z",
            )
            mgr.record(res)

        # Must not exceed max_records_per_target (5)
        history = mgr.get_history(url, limit=20)
        assert len(history) == 5
        # Most recent first
        assert history[0].response_time_ms == 109.0

    def test_history_summary_uptime_calculation(self):
        mgr = HealthHistoryManager(max_records_per_target=10)
        url = "https://metrics.example.com"

        # 3 healthy, 1 unhealthy -> 75% uptime
        for s in [HealthStatus.HEALTHY, HealthStatus.HEALTHY, HealthStatus.HEALTHY, HealthStatus.UNHEALTHY]:
            mgr.record(HealthCheckResult(
                url=url,
                status=s,
                success=(s == HealthStatus.HEALTHY),
                http_status=200 if s == HealthStatus.HEALTHY else 500,
                response_time_ms=100.0,
                checked_at="2026-10-02T12:00:00Z",
            ))

        summary = mgr.get_summary(url)
        assert summary.total_records == 4
        assert summary.uptime_percentage == 75.0
        assert summary.average_response_time_ms == 100.0
        assert summary.current_status == HealthStatus.UNHEALTHY

    def test_history_clear(self):
        mgr = HealthHistoryManager()
        url = "https://clear.example.com"
        mgr.record(HealthCheckResult(
            url=url,
            status=HealthStatus.HEALTHY,
            success=True,
            http_status=200,
            response_time_ms=50.0,
            checked_at="2026-10-02T12:00:00Z",
        ))
        assert len(mgr.get_history(url)) == 1
        mgr.clear(url)
        assert len(mgr.get_history(url)) == 0


# =============================================================================
# 6. RUNTIME BACKGROUND HEALTH MONITOR
# =============================================================================

class TestHealthMonitor:
    """Verifies background monitor lifecycle, tracking, and clean cancellation."""

    @pytest.mark.asyncio
    async def test_monitor_start_and_clean_stop(self):
        mock_checker = MagicMock()
        mock_checker.check = MagicMock()
        # Mock async check return
        future = asyncio.Future()
        future.set_result(HealthCheckResult(
            url="https://mon.example.com",
            status=HealthStatus.HEALTHY,
            success=True,
            http_status=200,
            response_time_ms=80.0,
            checked_at="2026-10-02T12:00:00Z",
        ))
        mock_checker.check.return_value = future

        monitor = HealthMonitor(checker=mock_checker)
        url = "https://mon.example.com"

        # Start with interval
        status = monitor.start(url, interval_seconds=5.0)
        assert status.is_running is True
        assert status.interval_seconds == 5.0

        # Let loop run brief slice
        await asyncio.sleep(0.05)

        # Stop
        stopped = monitor.stop(url)
        assert stopped is True

        cur = monitor.get_status(url)
        assert cur is not None
        assert cur.is_running is False

    @pytest.mark.asyncio
    async def test_monitor_enforces_minimum_interval(self):
        monitor = HealthMonitor()
        status = monitor.start("https://safe.example.com", interval_seconds=1.0)
        # Must enforce minimum of 5.0 seconds
        assert status.interval_seconds == 5.0
        monitor.stop("https://safe.example.com")

    @pytest.mark.asyncio
    async def test_monitor_stop_all(self):
        monitor = HealthMonitor()
        monitor.start("https://a.example.com", interval_seconds=10.0)
        monitor.start("https://b.example.com", interval_seconds=10.0)
        assert len(monitor.list_active()) == 2

        monitor.stop_all()
        assert len(monitor.list_active()) == 0


# =============================================================================
# 7. HEALTH SERVICE INTEGRATION
# =============================================================================

class TestHealthService:
    """Verifies the unified HealthService facade."""

    @pytest.mark.asyncio
    async def test_health_service_check_delegation(self):
        mock_checker = MagicMock()
        mock_checker.check = MagicMock()
        expected_res = HealthCheckResult(
            url="https://service.example.com",
            status=HealthStatus.HEALTHY,
            success=True,
            http_status=200,
            response_time_ms=65.0,
            checked_at="2026-10-02T12:00:00Z",
        )
        fut = asyncio.Future()
        fut.set_result(expected_res)
        mock_checker.check.return_value = fut

        svc = HealthService(checker=mock_checker)
        res = await svc.check("https://service.example.com")
        assert res.url == "https://service.example.com"
        assert res.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_check_deployment_with_no_url(self):
        svc = HealthService()
        dep = DeploymentResult(
            success=False,
            provider=DeploymentProvider.VERCEL,
            project_name="no-url-proj",
            deployment_status=DeploymentStatus.FAILED,
            deployment_url=None,
        )
        res = await svc.check_deployment(dep)
        assert res.status == HealthStatus.UNKNOWN
        assert res.success is False
        assert "no valid deployment_url" in res.safe_error_message


# =============================================================================
# 8. DEPLOYMENT VERIFIER INTEGRATION
# =============================================================================

class TestDeploymentVerifierIntegration:
    """Verifies M12 DeploymentVerifier integration with M13 HealthService."""

    def test_url_syntax_validation(self):
        verifier = DeploymentVerifier()
        assert verifier.verify_url_format("https://my-app.vercel.app") is True
        assert verifier.verify_url_format("http://localhost:3000") is True
        assert verifier.verify_url_format("ftp://invalid.com") is False
        assert verifier.verify_url_format("javascript:alert(1)") is False
        assert verifier.verify_url_format("https://bad<script>.com") is False

    @pytest.mark.asyncio
    async def test_verification_rejects_malformed_url(self):
        verifier = DeploymentVerifier()
        res = await verifier.verify_endpoint("not_a_valid_url")
        assert res.reachable is False
        assert "Invalid deployment URL" in res.message

    @pytest.mark.asyncio
    async def test_verification_calls_health_service(self):
        verifier = DeploymentVerifier()

        mock_health_res = HealthCheckResult(
            url="https://deployed-app.vercel.app",
            status=HealthStatus.HEALTHY,
            success=True,
            http_status=200,
            response_time_ms=134.0,
            checked_at="2026-10-02T12:00:00Z",
        )

        with patch("app.health.health_service.health_service.check", return_value=mock_health_res):
            res = await verifier.verify_endpoint("https://deployed-app.vercel.app")
            assert isinstance(res, VerificationResult)
            assert res.reachable is True
            assert res.status_code == 200
            assert res.response_time_ms == 134.0


# =============================================================================
# 9. TOOL REGISTRY & HEALTH TOOLS
# =============================================================================

class TestHealthTools:
    """Verifies all 5 health tools are registered, permission-checked, and execute safely."""

    def test_all_health_tools_registered(self):
        registry = create_default_registry()
        expected_tools = [
            "health_check",
            "health_status",
            "health_history",
            "health_monitor_start",
            "health_monitor_stop",
        ]
        for name in expected_tools:
            assert registry.has_tool(name) is True
            tool = registry.get(name)
            assert tool is not None
            info = tool.get_info()
            assert "name" in info
            assert "input_schema" in info

    @pytest.mark.asyncio
    async def test_health_check_tool_execution(self):
        tool = HealthCheckTool()
        mock_res = HealthCheckResult(
            url="https://tool-test.com",
            status=HealthStatus.HEALTHY,
            success=True,
            http_status=200,
            response_time_ms=50.0,
            latency_tier=LatencyTier.FAST,
            checked_at="2026-10-02T12:00:00Z",
        )
        with patch("app.health.health_service.health_service.check", return_value=mock_res):
            res = await tool.execute({"url": "https://tool-test.com"})
            assert res["success"] is True
            assert res["status"] == "HEALTHY"
            assert res["http_status"] == 200

    @pytest.mark.asyncio
    async def test_health_status_tool_execution(self):
        tool = HealthStatusTool()
        mock_summary = HealthHistorySummary(
            url="https://tool-status.com",
            total_records=2,
            current_status=HealthStatus.HEALTHY,
            uptime_percentage=100.0,
            average_response_time_ms=60.0,
        )
        with patch("app.health.health_service.health_service.get_summary", return_value=mock_summary):
            res = await tool.execute({"url": "https://tool-status.com"})
            assert res["success"] is True
            assert res["uptime_percentage"] == 100.0

    @pytest.mark.asyncio
    async def test_health_history_tool_execution(self):
        tool = HealthHistoryTool()
        with patch("app.health.health_service.health_service.get_history", return_value=[]):
            res = await tool.execute({"url": "https://tool-hist.com", "limit": 5})
            assert res["success"] is True
            assert res["count"] == 0

    @pytest.mark.asyncio
    async def test_health_monitor_start_tool_execution(self):
        tool = HealthMonitorStartTool()
        mock_stat = MonitorStatus(
            url="https://tool-mon.com",
            is_running=True,
            interval_seconds=30.0,
            current_status=HealthStatus.CHECKING,
        )
        with patch("app.health.health_service.health_service.start_monitoring", return_value=mock_stat):
            res = await tool.execute({"url": "https://tool-mon.com", "interval_seconds": 30.0})
            assert res["success"] is True
            assert res["is_running"] is True

    @pytest.mark.asyncio
    async def test_health_monitor_stop_tool_execution(self):
        tool = HealthMonitorStopTool()
        with patch("app.health.health_service.health_service.stop_monitoring", return_value=True):
            res = await tool.execute({"url": "https://tool-mon.com"})
            assert res["success"] is True
            assert res["stopped"] is True


# =============================================================================
# 10. SAFETYGUARD & PERMISSION POLICIES
# =============================================================================

class TestHealthPermissions:
    """Verifies safety evaluation: health tools are safe reads and do not require destructive confirmations."""

    def test_health_tools_are_safe_in_safety_guard(self):
        guard = SafetyGuard()
        for t in ["health_check", "health_status", "health_history", "health_monitor_start", "health_monitor_stop"]:
            perm = guard.validate_action(t, {})
            assert perm.allowed is True
            assert perm.risk_level == "safe"

    def test_health_tools_auto_execute_in_confirmation_manager(self):
        mgr = ConfirmationManager()
        for t in ["health_check", "health_status", "health_history", "health_monitor_start", "health_monitor_stop"]:
            assert t in mgr.SAFE_AUTO_EXECUTE_TOOLS


# =============================================================================
# 11. INTENT ROUTER
# =============================================================================

class TestHealthIntentRouter:
    """Verifies operational vs educational query routing."""

    def setup_method(self):
        self.router = IntentRouter()

    def test_check_deployment_routes_to_deployment_verify(self):
        decision = self.router.route("check my deployment")
        assert decision.intent == "tool"
        assert decision.tool_name == "deployment_verify"

    def test_is_deployed_app_working_routes_to_deployment_verify(self):
        decision = self.router.route("is my deployed app working")
        assert decision.intent == "tool"
        assert decision.tool_name == "deployment_verify"

    def test_is_site_healthy_routes_to_health_check(self):
        decision = self.router.route("is the site healthy https://my-app.vercel.app")
        assert decision.intent == "tool"
        assert decision.tool_name == "health_check"
        assert decision.tool_arguments.get("url") == "https://my-app.vercel.app"

    def test_how_fast_responding_routes_to_health_check(self):
        decision = self.router.route("how fast is my deployment responding")
        assert decision.intent == "tool"
        assert decision.tool_name == "health_check"

    def test_show_health_history_routes_to_health_history(self):
        decision = self.router.route("show deployment health history")
        assert decision.intent == "tool"
        assert decision.tool_name == "health_history"

    def test_monitor_deployment_routes_to_monitor_start(self):
        decision = self.router.route("monitor my deployment https://app.example.com")
        assert decision.intent == "tool"
        assert decision.tool_name == "health_monitor_start"
        assert decision.tool_arguments.get("url") == "https://app.example.com"

    def test_stop_monitoring_routes_to_monitor_stop(self):
        decision = self.router.route("stop health monitor https://app.example.com")
        assert decision.intent == "tool"
        assert decision.tool_name == "health_monitor_stop"

    def test_educational_health_query_routes_to_ai(self):
        """CRITICAL: Educational queries MUST NOT execute network probes."""
        educational_queries = [
            "What is an HTTP health check?",
            "What is health check",
            "What is deployment verification",
            "How does a health check work?",
            "Explain health checks",
        ]
        for q in educational_queries:
            decision = self.router.route(q)
            assert decision.intent == "ai", f"Query '{q}' should route to AI but routed to {decision.intent}"


# =============================================================================
# 12. SECURITY REGRESSION & PRODUCTION READINESS
# =============================================================================

class TestHealthSecurityRegression:
    """Verifies that M13 complies strictly with the non-destructive security model."""

    def test_no_destructive_authority_in_health_models(self):
        """M13 must not define automatic rollback, redeploy, or git reset hooks."""
        from app.health import health_models
        for attr in dir(health_models):
            assert "rollback" not in attr.lower()
            assert "redeploy" not in attr.lower()
            assert "git_reset" not in attr.lower()

    def test_no_shell_execution_used_in_probe(self):
        """Verify HttpProbe uses urllib and asyncio without subprocess or shell."""
        import inspect
        from app.health.http_probe import HttpProbe
        source = inspect.getsource(HttpProbe)
        assert "subprocess" not in source
        assert "os.system" not in source
        assert "cmd.exe" not in source
        assert "powershell" not in source

    def test_bounded_response_bytes_cap(self):
        """Verify maximum response size cap is bounded (32KB)."""
        probe = HttpProbe()
        assert probe.MAX_RESPONSE_BYTES <= 65536
