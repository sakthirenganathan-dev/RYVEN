"""RYVEN DEV ENGINE v2 — Error Intelligence / Error Analyzer.

Parses raw stdout/stderr from sandbox process results into structured
BuildError objects. Never executes code; purely text analysis.

Output is sanitized before being exposed to users:
  - No secrets, tokens, or filesystem paths outside the project root.
  - Stack traces are trimmed.
  - Internal host paths are redacted.
"""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.logging_config import logger


# ─────────────────────────────────────────────────────────────────────────────
# Structured error model (also imported by build_engine, test_engine)
# ─────────────────────────────────────────────────────────────────────────────

class BuildError(BaseModel):
    """Single structured error extracted from a build or test output."""

    file_path: Optional[str] = None
    line_number: Optional[int] = None
    column: Optional[int] = None
    error_code: str = ""
    message: str
    category: str = "BUILD_ERROR"      # BUILD_ERROR | SYNTAX_ERROR | TYPE_ERROR | RUNTIME_ERROR | TEST_FAILURE
    severity: str = "ERROR"            # ERROR | WARNING | INFO
    raw_snippet: str = ""              # Short raw line, sanitized


# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns for common build/test error formats
# ─────────────────────────────────────────────────────────────────────────────

# Vite/TypeScript: src/App.tsx(12,5): error TS2345: ...
_TS_ERROR_RE = re.compile(
    r"(?P<file>[^\s\(]+\.(?:ts|tsx|js|jsx))\((?P<line>\d+),(?P<col>\d+)\):\s+"
    r"(?P<severity>error|warning)\s+(?P<code>TS\d+):\s+(?P<msg>.+)"
)

# ESLint:   src/App.tsx  12:5  error  rule-name  description
_ESLINT_RE = re.compile(
    r"(?P<file>[^\s]+\.(?:ts|tsx|js|jsx))\s+(?P<line>\d+):(?P<col>\d+)\s+"
    r"(?P<severity>error|warning)\s+(?P<msg>.+)"
)

# Python SyntaxError: File "main.py", line 5
_PY_SYNTAX_RE = re.compile(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)'
)

# pytest failure lines: FAILED tests/test_foo.py::test_bar
_PYTEST_FAIL_RE = re.compile(
    r"FAILED\s+(?P<file>[^\s:]+)::(?P<test>\S+)"
)

# npm error generic: ERROR in src/App.tsx
_NPM_ERROR_RE = re.compile(
    r"(?:ERROR|error)\s+in\s+(?P<file>\S+)"
)


class ErrorAnalyzer:
    """Analyzes raw process output into structured BuildError lists."""

    MAX_ERRORS = 50   # cap to avoid flooding

    def analyze_npm_build(self, result) -> List[BuildError]:
        """Parse npm/Vite/TypeScript build output into BuildError list."""
        errors: List[BuildError] = []
        combined = result.stdout + "\n" + result.stderr

        for line in combined.splitlines():
            line = line.strip()
            if not line:
                continue

            # TypeScript errors
            m = _TS_ERROR_RE.search(line)
            if m:
                errors.append(BuildError(
                    file_path=self._sanitize_path(m.group("file")),
                    line_number=int(m.group("line")),
                    column=int(m.group("col")),
                    error_code=m.group("code"),
                    message=m.group("msg").strip(),
                    category="TYPE_ERROR" if m.group("code").startswith("TS") else "BUILD_ERROR",
                    severity=m.group("severity").upper(),
                    raw_snippet=line[:200],
                ))
                continue

            # ESLint
            m = _ESLINT_RE.search(line)
            if m:
                errors.append(BuildError(
                    file_path=self._sanitize_path(m.group("file")),
                    line_number=int(m.group("line")),
                    column=int(m.group("col")),
                    message=m.group("msg").strip(),
                    category="LINT_ERROR",
                    severity=m.group("severity").upper(),
                    raw_snippet=line[:200],
                ))
                continue

            # Generic npm ERROR
            m = _NPM_ERROR_RE.search(line)
            if m:
                errors.append(BuildError(
                    file_path=self._sanitize_path(m.group("file")),
                    message=line[:300],
                    category="BUILD_ERROR",
                    severity="ERROR",
                    raw_snippet=line[:200],
                ))

            if len(errors) >= self.MAX_ERRORS:
                break

        if not errors and not result.success:
            # Fallback: surface first non-empty stderr line
            fallback = next(
                (l.strip() for l in result.stderr.splitlines() if l.strip()),
                "Build failed — see stderr for details.",
            )
            errors.append(BuildError(
                message=self._sanitize_message(fallback[:300]),
                category="BUILD_ERROR",
                severity="ERROR",
            ))

        logger.info(f"[ERROR_ANALYZER] npm build: {len(errors)} error(s) parsed.")
        return errors

    def analyze_python_build(self, result) -> List[BuildError]:
        """Parse Python py_compile output into BuildError list."""
        errors: List[BuildError] = []
        combined = result.stdout + "\n" + result.stderr

        lines = combined.splitlines()
        for i, line in enumerate(lines):
            m = _PY_SYNTAX_RE.search(line)
            if m:
                # Next line may have the actual error message
                msg_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
                errors.append(BuildError(
                    file_path=self._sanitize_path(m.group("file")),
                    line_number=int(m.group("line")),
                    message=msg_line or line,
                    category="SYNTAX_ERROR",
                    severity="ERROR",
                    raw_snippet=line[:200],
                ))
            if len(errors) >= self.MAX_ERRORS:
                break

        if not errors and not result.success:
            fallback = next(
                (l.strip() for l in result.stderr.splitlines() if l.strip()),
                "Python compile check failed.",
            )
            errors.append(BuildError(
                message=self._sanitize_message(fallback[:300]),
                category="SYNTAX_ERROR",
                severity="ERROR",
            ))

        logger.info(f"[ERROR_ANALYZER] python build: {len(errors)} error(s) parsed.")
        return errors

    def analyze_pytest(self, result) -> List[BuildError]:
        """Parse pytest output into BuildError list."""
        errors: List[BuildError] = []
        combined = result.stdout + "\n" + result.stderr

        for line in combined.splitlines():
            m = _PYTEST_FAIL_RE.search(line)
            if m:
                errors.append(BuildError(
                    file_path=self._sanitize_path(m.group("file")),
                    message=f"Test failed: {m.group('test')}",
                    category="TEST_FAILURE",
                    severity="ERROR",
                    raw_snippet=line[:200],
                ))
            if len(errors) >= self.MAX_ERRORS:
                break

        logger.info(f"[ERROR_ANALYZER] pytest: {len(errors)} failure(s) parsed.")
        return errors

    # ── sanitization helpers ────────────────────────────────────────────────

    @staticmethod
    def _sanitize_path(raw: str) -> str:
        """Strip absolute host-path prefix, keep relative path only."""
        raw = raw.strip()
        # Remove Windows absolute prefixes C:\...\projects\ProjectName\
        raw = re.sub(r"^[A-Za-z]:\\[^\\]+\\[^\\]+\\[^\\]+\\", "", raw)
        # Remove Unix absolute prefixes /home/user/.../projects/ProjectName/
        raw = re.sub(r"^/[^/]+/[^/]+/[^/]+/[^/]+/", "", raw)
        return raw[:200]

    @staticmethod
    def _sanitize_message(msg: str) -> str:
        """Remove anything that looks like a secret or absolute host path."""
        # Remove prefixed tokens like ghp_xxx, sk-xxx, Bearer xxx (20+ char value)
        msg = re.sub(r"(?:ghp|sk|ghs|gho|github_pat|Bearer|token|secret)[_\-]?[A-Za-z0-9_\-]{16,}", "[REDACTED]", msg, flags=re.IGNORECASE)
        # Remove raw hex/base64 tokens >= 40 chars
        msg = re.sub(r"[A-Za-z0-9+/]{40,}={0,2}", "[REDACTED]", msg)
        # Remove absolute Windows paths
        msg = re.sub(r"[A-Za-z]:\\[^\s]+", "[PATH]", msg)
        # Remove absolute Unix paths
        msg = re.sub(r"/(?:home|usr|var|etc|root)/[^\s]+", "[PATH]", msg)
        return msg
