"""RYVEN 3.0 — M15.3 Phase 4: Download & Upload Management Models and Security Helpers.

Defines strongly-typed Pydantic models for file transfer requests, results,
and security checks, plus filesystem allowlist verification and filename sanitization.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.tools.folder_tool import get_approved_directories


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FileTransferStatus(str, Enum):
    """Lifecycle status of a file transfer operation."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


# ---------------------------------------------------------------------------
# Security Policy Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_DOWNLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
DEFAULT_MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024    # 50 MB

# Extensions of executable or potentially harmful files that require user confirmation
DANGEROUS_DOWNLOAD_EXTENSIONS = frozenset({
    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".js",
    ".scr", ".dll", ".com", ".pif", ".reg", ".vbe", ".jse",
    ".wsf", ".wsh", ".hta", ".cpl", ".jar",
})

# Filenames and patterns for sensitive files that must NEVER be uploaded
SENSITIVE_FILE_PATTERNS = frozenset({
    ".env", ".env.local", ".env.production", ".env.development", ".env.staging",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts", "authorized_keys",
    ".git-credentials", ".netrc", ".npmrc", ".pypirc", "credentials.json",
    "service_account.json", "client_secret.json", "private_key.pem",
})

SENSITIVE_FILENAME_KEYWORDS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "credential", "credentials", "cookie", "bearer", "private_key",
    "access_token", "refresh_token", "client_secret",
})

SENSITIVE_FILE_EXTENSIONS = frozenset({
    ".pem", ".key", ".pfx", ".p12", ".kdbx", ".wallet",
})


# ---------------------------------------------------------------------------
# Security & Containment Helpers
# ---------------------------------------------------------------------------


def is_approved_filesystem_path(target_path: str) -> bool:
    """Verify that target_path resides strictly within one of the approved directory roots.
    
    Roots: Desktop, Documents, Downloads, Workspace / Project.
    Rejects: Windows system folders, arbitrary drive roots, UNC paths, path traversal.
    """
    if not target_path or not isinstance(target_path, str):
        return False

    clean = target_path.strip().strip('"').strip("'")
    if not clean:
        return False

    # Block UNC paths and obvious drive traversal
    if clean.startswith("\\\\") or clean.startswith("//"):
        return False

    # Resolve real absolute path
    try:
        real_target = os.path.realpath(clean)
    except Exception:
        return False

    # Never allow writing directly to a drive root like C:\ or D:\
    norm = os.path.normpath(real_target)
    drive, rest = os.path.splitdrive(norm)
    if rest in ("", os.sep, "/", "\\"):
        return False

    # Block known Windows system directories
    lower_norm = norm.lower()
    system_dirs = ["c:\\windows", "c:\\program files", "c:\\program files (x86)", "c:\\system32"]
    for sdir in system_dirs:
        if lower_norm == sdir or lower_norm.startswith(sdir + os.sep):
            return False

    approved = get_approved_directories()
    for approved_root in approved.values():
        if approved_root:
            try:
                real_root = os.path.realpath(approved_root)
                if (
                    lower_norm == real_root.lower()
                    or lower_norm.startswith(real_root.lower() + os.sep)
                ):
                    return True
            except Exception:
                continue

    return False


def is_sensitive_file(file_path: str) -> Tuple[bool, str]:
    """Check if a file path points to a sensitive configuration or secret file.
    
    Returns (is_sensitive, reason).
    """
    if not file_path:
        return True, "File path is empty."

    basename = os.path.basename(file_path).strip().lower()

    # 1. Exact match against sensitive files (.env, id_rsa, etc.)
    if basename in SENSITIVE_FILE_PATTERNS:
        return True, f"Blocked sensitive credential file '{basename}'."

    # 2. Check for .env.* variants
    if basename.startswith(".env.") or basename == ".env":
        return True, f"Blocked environment/secrets configuration file '{basename}'."

    # 3. Check sensitive file extensions (.pem, .key, .pfx, etc.)
    _, ext = os.path.splitext(basename)
    if ext in SENSITIVE_FILE_EXTENSIONS:
        return True, f"Blocked sensitive private key/certificate file type '{ext}'."

    # 4. Check sensitive keywords in filename
    tokens = set(re.split(r"[^a-z0-9]+", basename))
    matched = tokens & SENSITIVE_FILENAME_KEYWORDS
    if matched:
        return True, f"Blocked file containing sensitive keyword(s): {', '.join(sorted(matched))}."

    # Also check substring for compounds like 'apikey' or 'password'
    for kw in ("password", "passwd", "secret", "token", "apikey", "api_key", "credential", "private_key"):
        if kw in basename:
            return True, f"Blocked file containing sensitive keyword: '{kw}'."

    return False, ""


def is_dangerous_download_extension(filename: str) -> bool:
    """Return True if filename has an executable or dangerous extension."""
    if not filename:
        return False
    _, ext = os.path.splitext(filename.lower().strip())
    return ext in DANGEROUS_DOWNLOAD_EXTENSIONS


def sanitize_download_filename(raw_name: str, fallback: str = "download") -> str:
    """Sanitize a raw filename extracted from Content-Disposition or URL.
    
    - Strips path separators and directory traversal ('..', '/', '\\')
    - Removes illegal Windows characters: < > : " / \\ | ? * and ASCII control chars
    - Limits length to 200 characters
    - Returns safe fallback if empty
    """
    if not raw_name or not isinstance(raw_name, str):
        return fallback

    name = raw_name.strip()
    # If a full URL was passed in, strip query and take URL path
    if "://" in name:
        name = urllib.parse.urlsplit(name).path

    # Strip directory components (take basename only)
    name = os.path.basename(name.replace("\\", "/"))

    # Replace forbidden Windows characters with underscore
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")

    # Collapse repeated underscores/spaces
    name = re.sub(r"[_\s]+", "_", name).strip("_")


    if not name:
        return fallback

    if len(name) > 200:
        base, ext = os.path.splitext(name)
        max_base = 200 - len(ext)
        name = (base[:max_base] + ext) if max_base > 0 else name[:200]

    return name


def resolve_safe_destination(
    folder_or_path: Optional[str] = None,
    filename: str = "download.dat",
) -> Tuple[bool, str, str]:
    """Resolve and validate a destination directory and file path.
    
    Returns: (is_safe, resolved_file_path, error_reason)
    """
    approved = get_approved_directories()

    # If folder_or_path is an approved category name ('downloads', 'desktop', etc.)
    folder_key = (folder_or_path or "downloads").strip().lower()
    if folder_key in approved:
        target_dir = approved[folder_key]
    else:
        # Caller provided an explicit path or folder
        target_dir = folder_or_path or approved.get("downloads", "")

    # Ensure target_dir itself is approved
    if not is_approved_filesystem_path(target_dir):
        return False, "", f"Destination folder '{target_dir}' is not within an approved directory."

    safe_name = sanitize_download_filename(filename)
    full_path = os.path.join(target_dir, safe_name)

    # Re-verify full destination path
    if not is_approved_filesystem_path(full_path):
        return False, "", f"Resolved path '{full_path}' escapes approved directories."

    return True, full_path, ""


def resolve_unique_filename(destination_dir: str, filename: str) -> str:
    """Resolve duplicate filenames safely using 'name (1).ext', 'name (2).ext'."""
    candidate_path = os.path.join(destination_dir, filename)
    if not os.path.exists(candidate_path):
        return candidate_path

    base, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate_name = f"{base} ({counter}){ext}"
        candidate_path = os.path.join(destination_dir, candidate_name)
        if not os.path.exists(candidate_path):
            return candidate_path
        counter += 1


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class DownloadRequest(BaseModel):
    """Request to download a file from a web URL to an approved local folder."""
    url: str
    destination_folder: Optional[str] = "downloads"
    custom_filename: Optional[str] = None
    confirmed: bool = False
    max_size_bytes: int = DEFAULT_MAX_DOWNLOAD_SIZE_BYTES
    session_id: Optional[str] = None


class DownloadResult(BaseModel):
    """Result of a download attempt."""
    transfer_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    success: bool
    source_url: str
    destination_path: Optional[str] = None
    filename: Optional[str] = None
    size_bytes: int = 0
    content_type: Optional[str] = None
    status: FileTransferStatus = FileTransferStatus.PENDING
    verified: bool = False
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    error_code: Optional[str] = None
    duration_ms: float = 0.0
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class UploadRequest(BaseModel):
    """Request to select/upload an approved local file into a web file input."""
    source_path: str
    target_field: str
    form_id: Optional[str] = None
    confirmed: bool = False
    max_size_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES
    session_id: Optional[str] = None


class UploadResult(BaseModel):
    """Result of an upload attempt."""
    transfer_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    success: bool
    source_path: Optional[str] = None
    filename: Optional[str] = None
    size_bytes: int = 0
    target_field: str = ""
    field_identity: Dict[str, Any] = Field(default_factory=dict)
    status: FileTransferStatus = FileTransferStatus.PENDING
    verified: bool = False
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    error_code: Optional[str] = None
    duration_ms: float = 0.0
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class FileSecurityResult(BaseModel):
    """Result of a security preflight evaluation for a file operation."""
    allowed: bool
    reason: str
    is_sensitive: bool = False
    requires_confirmation: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)
