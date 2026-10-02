"""Deterministic Context Reduction Benchmark for RYVEN M11.5.

Executes the exact same user development task under two conditions:
1. TEST A: Unoptimized baseline (full project source files loaded).
2. TEST B: Graph-optimized targeted context package.

Measures:
- Files read
- Total characters
- Total lines
- Estimated tokens
- Reduction percentage
- Execution duration & graph overhead
"""

import time
from pathlib import Path
import pytest
from app.knowledge_graph.service import KnowledgeGraphService
from app.knowledge_graph.cache import GraphCacheManager


@pytest.fixture
def benchmark_project(tmp_path: Path):
    """Create a realistic multi-module project for benchmarking."""
    proj = tmp_path / "enterprise_service_app"
    proj.mkdir(parents=True, exist_ok=True)

    # Relevant Domain & Authentication Modules (4 files)
    (proj / "user_model.py").write_text(
        '"""User domain model."""\n\n'
        'class UserModel:\n'
        '    def __init__(self, user_id: str, email: str, role: str = "member"):\n'
        '        self.user_id = user_id\n'
        '        self.email = email\n'
        '        self.role = role\n\n'
        '    def is_admin(self) -> bool:\n'
        '        return self.role == "admin"\n'
        * 10,
        encoding="utf-8",
    )

    (proj / "token_service.py").write_text(
        '"""JWT Token management."""\n\n'
        'class TokenService:\n'
        '    def generate_access_token(self, user_id: str) -> str:\n'
        '        return f"access_{user_id}_jwt"\n\n'
        '    def validate_access_token(self, token: str) -> bool:\n'
        '        return token.startswith("access_")\n'
        * 10,
        encoding="utf-8",
    )

    (proj / "auth_service.py").write_text(
        'from user_model import UserModel\n'
        'from token_service import TokenService\n\n'
        'class AuthService:\n'
        '    """Core enterprise authentication handler."""\n'
        '    def __init__(self):\n'
        '        self.tokens = TokenService()\n\n'
        '    def authenticate_credentials(self, email: str, password: str) -> UserModel:\n'
        '        user = UserModel("u-99", email)\n'
        '        token = self.tokens.generate_access_token(user.user_id)\n'
        '        return user\n\n'
        '    def revoke_session(self, token: str) -> bool:\n'
        '        return True\n'
        * 10,
        encoding="utf-8",
    )

    (proj / "auth_router.py").write_text(
        'from auth_service import AuthService\n\n'
        'class AuthRouter:\n'
        '    def __init__(self):\n'
        '        self.auth = AuthService()\n\n'
        '    def post_login_route(self, email: str, password: str) -> dict:\n'
        '        user = self.auth.authenticate_credentials(email, password)\n'
        '        return {"user": user.email, "admin": user.is_admin()}\n'
        * 10,
        encoding="utf-8",
    )

    # Unrelated Domain Modules (6 large files)
    (proj / "billing_engine.py").write_text(
        'class BillingInvoiceProcessor:\n'
        '    def process_recurring_invoice(self, acc: str, amt: float) -> dict:\n'
        '        return {"invoice_id": "inv-001", "acc": acc, "amt": amt * 1.18}\n'
        * 100,
        encoding="utf-8",
    )

    (proj / "shipping_service.py").write_text(
        'class ShippingFulfillment:\n'
        '    def calculate_freight(self, weight: float, dest: str) -> float:\n'
        '        return weight * 4.5\n'
        * 100,
        encoding="utf-8",
    )

    (proj / "analytics_collector.py").write_text(
        'class MetricsTelemetryCollector:\n'
        '    def record_counter(self, name: str, value: int):\n'
        '        pass\n'
        * 100,
        encoding="utf-8",
    )

    (proj / "warehouse_inventory.py").write_text(
        'class WarehouseInventoryManagement:\n'
        '    def reserve_stock(self, sku: str, qty: int) -> bool:\n'
        '        return True\n'
        * 100,
        encoding="utf-8",
    )

    (proj / "compliance_audit.py").write_text(
        'class ComplianceAuditLog:\n'
        '    def write_event(self, actor: str, action: str):\n'
        '        pass\n'
        * 100,
        encoding="utf-8",
    )

    (proj / "notification_dispatcher.py").write_text(
        'class EmailNotificationDispatcher:\n'
        '    def send_broadcast(self, recipients: list, subject: str, body: str):\n'
        '        pass\n'
        * 100,
        encoding="utf-8",
    )

    return proj


def test_deterministic_context_reduction_benchmark(benchmark_project: Path):
    """Run comparative benchmark: unoptimized baseline vs graph-optimized context."""
    task = "Fix the authentication credentials login validation bug"
    service = KnowledgeGraphService()
    proj_path = str(benchmark_project)

    # --------------------------------------------------------------------------
    # TEST A: WITHOUT GRAPH OPTIMIZATION (Load all project files)
    # --------------------------------------------------------------------------
    t0_baseline = time.perf_counter()
    cache_mgr = GraphCacheManager(proj_path)
    all_files = cache_mgr.scan_current_files()

    baseline_files_content = {}
    for f in all_files.keys():
        p = benchmark_project / f
        if p.exists():
            baseline_files_content[f] = p.read_text(encoding="utf-8", errors="replace")

    baseline_files_count = len(baseline_files_content)
    baseline_characters = sum(len(c) for c in baseline_files_content.values())
    baseline_lines = sum(len(c.splitlines()) for c in baseline_files_content.values())
    baseline_estimated_tokens = sum(max(1, len(c) // 4) for c in baseline_files_content.values())
    t_baseline_ms = (time.perf_counter() - t0_baseline) * 1000.0

    # --------------------------------------------------------------------------
    # TEST B: WITH GRAPH OPTIMIZATION (Targeted context via Knowledge Graph)
    # --------------------------------------------------------------------------
    t0_opt = time.perf_counter()
    package = service.build_optimized_context(
        proj_path,
        task=task,
        target_symbol="AuthService",
    )
    t_opt_ms = (time.perf_counter() - t0_opt) * 1000.0
    budget = package.budget

    optimized_files_count = budget.optimized_files_count
    optimized_characters = budget.optimized_characters
    optimized_estimated_tokens = budget.optimized_estimated_tokens
    reduction_pct = budget.reduction_percentage

    # --------------------------------------------------------------------------
    # ASSERTIONS & VERIFICATIONS
    # --------------------------------------------------------------------------
    # 1. Total files reduced significantly
    assert optimized_files_count < baseline_files_count
    assert optimized_files_count <= 4  # Only auth-relevant files

    # 2. Characters & tokens reduced by > 75%
    assert reduction_pct >= 75.0

    # 3. Only relevant files included
    assert "auth_service.py" in package.relevant_files
    assert "user_model.py" in package.relevant_files
    assert "billing_engine.py" not in package.relevant_files
    assert "shipping_service.py" not in package.relevant_files
    assert "compliance_audit.py" not in package.relevant_files

    # 4. Durations remain within local sub-second SLAs
    assert t_opt_ms < 2000.0

    # Print report for audit visibility
    print("\n" + "=" * 60)
    print("DETERMINISTIC CONTEXT REDUCTION BENCHMARK RESULTS")
    print("=" * 60)
    print(f"Task: '{task}'")
    print("-" * 60)
    print("TEST A — WITHOUT GRAPH OPTIMIZATION:")
    print(f"  Files Read:             {baseline_files_count}")
    print(f"  Characters:             {baseline_characters:,}")
    print(f"  Lines:                  {baseline_lines:,}")
    print(f"  Estimated Tokens:       {baseline_estimated_tokens:,}")
    print(f"  Read Duration:          {t_baseline_ms:.2f} ms")
    print("-" * 60)
    print("TEST B — WITH GRAPH OPTIMIZATION:")
    print(f"  Files Selected:         {optimized_files_count}")
    print(f"  Characters:             {optimized_characters:,}")
    print(f"  Estimated Tokens:       {optimized_estimated_tokens:,}")
    print(f"  Character Reduction:    {budget.character_reduction:,} chars")
    print(f"  Token Reduction:        {budget.token_reduction:,} tokens")
    print(f"  Reduction Percentage:   {reduction_pct:.2f}%")
    print(f"  Graph / Context Time:   {t_opt_ms:.2f} ms")
    print("=" * 60)
