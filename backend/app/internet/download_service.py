"""RYVEN 3.0 — M15.3 Phase 4: Controlled Download Service.

Provides safe, bounded, observable, and verified file downloading:
- Protocol validation: strictly HTTP/HTTPS (blocks file:, javascript:, data:, etc.)
- SSRF prevention: blocks private IPs, localhost, cloud metadata endpoints
- Filesystem containment: destination must reside within approved directories (Downloads, Desktop, Documents, Workspace)
- Safe filename sanitization & collision resolution
- Size limits & streaming I/O
- Confirmation gating for executable or potentially dangerous extensions (.exe, .bat, .ps1, etc.)
- Post-download verification: file existence, size, containment check
- Observable ActionEvents with secret redaction
- NEVER executes or opens downloaded files
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
import uuid
from typing import Any, Dict, Optional, Tuple

import httpx

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.browser.engine import BrowserEngine, browser_engine
from app.browser.security import BrowserSecurityValidator
from app.core.logging_config import logger
from app.internet.file_models import (
    DEFAULT_MAX_DOWNLOAD_SIZE_BYTES,
    DownloadRequest,
    DownloadResult,
    FileTransferStatus,
    is_approved_filesystem_path,
    is_dangerous_download_extension,
    resolve_safe_destination,
    resolve_unique_filename,
    sanitize_download_filename,
)
from app.tools.folder_tool import get_approved_directories


class DownloadService:
    """Safe, controlled web file download service for RYVEN 3.0."""

    def __init__(
        self,
        engine: Optional[BrowserEngine] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._engine = engine or browser_engine
        self._client = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RYVEN/3.0 BrowserAgent"},
        )
        return self._client

    # ------------------------------------------------------------------
    # Core Download Workflow
    # ------------------------------------------------------------------

    async def download_file(
        self,
        url: str,
        destination_folder: Optional[str] = "downloads",
        custom_filename: Optional[str] = None,
        confirmed: bool = False,
        max_size_bytes: int = DEFAULT_MAX_DOWNLOAD_SIZE_BYTES,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Download a file from url to an approved local directory.
        
        Returns DownloadResult dict.
        """
        t0 = time.monotonic()
        transfer_id = str(uuid.uuid4())

        # --- 1. Protocol & SSRF validation ---
        is_safe_url, validated_url, url_error = BrowserSecurityValidator.validate_url(
            url, allow_search=False
        )
        if not is_safe_url:
            duration_ms = (time.monotonic() - t0) * 1000
            error_msg = f"Download blocked by security policy: {url_error}"
            self._emit(
                action_type=ActionType.DOWNLOAD_BLOCKED,
                status=ActionStatus.FAILED,
                title=f"Download Blocked: {url[:60]}",
                description=error_msg,
                metadata={"url": url[:100], "reason": url_error},
            )
            res = DownloadResult(
                transfer_id=transfer_id,
                success=False,
                source_url=url,
                status=FileTransferStatus.BLOCKED,
                error=error_msg,
                error_code="URL_SECURITY_BLOCKED",
                duration_ms=duration_ms,
            )
            return res.model_dump()

        # --- 2. Initial filename candidate from URL or custom_filename ---
        if custom_filename and custom_filename.strip():
            candidate_filename = sanitize_download_filename(custom_filename.strip())
        else:
            url_path = urllib.parse.urlsplit(validated_url).path
            raw_base = os.path.basename(url_path)
            candidate_filename = sanitize_download_filename(raw_base, fallback="download.dat")

        # --- 3. Preflight destination path containment check ---
        is_safe_dest, resolved_dest, dest_error = resolve_safe_destination(
            folder_or_path=destination_folder,
            filename=candidate_filename,
        )
        if not is_safe_dest:
            duration_ms = (time.monotonic() - t0) * 1000
            self._emit(
                action_type=ActionType.DOWNLOAD_BLOCKED,
                status=ActionStatus.FAILED,
                title=f"Destination Blocked: {destination_folder}",
                description=dest_error,
                metadata={"destination": str(destination_folder), "reason": dest_error},
            )
            res = DownloadResult(
                transfer_id=transfer_id,
                success=False,
                source_url=validated_url,
                status=FileTransferStatus.BLOCKED,
                error=dest_error,
                error_code="DESTINATION_NOT_APPROVED",
                duration_ms=duration_ms,
            )
            return res.model_dump()

        # --- 4. Risky file extension confirmation check ---
        if is_dangerous_download_extension(candidate_filename) and not confirmed:
            token = f"dl-{uuid.uuid4().hex[:8]}"
            duration_ms = (time.monotonic() - t0) * 1000
            self._emit(
                action_type=ActionType.DOWNLOAD_CONFIRMATION_REQUESTED,
                status=ActionStatus.WAITING_CONFIRMATION,
                title="Download Confirmation Required",
                description=(
                    f"Downloading '{candidate_filename}' (executable/script type) "
                    f"requires explicit user approval. Re-call with confirmed=True."
                ),
                confirmation_required=True,
                metadata={"filename": candidate_filename, "token": token},
            )
            res = DownloadResult(
                transfer_id=transfer_id,
                success=False,
                source_url=validated_url,
                filename=candidate_filename,
                destination_path=resolved_dest,
                status=FileTransferStatus.WAITING_CONFIRMATION,
                requires_confirmation=True,
                confirmation_token=token,
                message=(
                    f"CONFIRMATION REQUIRED: Download potentially dangerous file "
                    f"'{candidate_filename}'? Call download_file(confirmed=True) to proceed."
                ),
                duration_ms=duration_ms,
            )
            return res.model_dump()

        # --- 5. Start Download Stream ---
        self._emit(
            action_type=ActionType.DOWNLOAD_STARTED,
            status=ActionStatus.STARTED,
            title=f"Downloading: {candidate_filename}",
            description=f"Initiating stream from {validated_url[:60]}",
            metadata={"filename": candidate_filename, "destination": resolved_dest},
        )

        try:
            client = await self._get_client()
            async with client.stream("GET", validated_url) as response:
                if response.status_code >= 400:
                    duration_ms = (time.monotonic() - t0) * 1000
                    err = f"HTTP {response.status_code} while downloading {validated_url}"
                    self._emit(
                        action_type=ActionType.DOWNLOAD_FAILED,
                        status=ActionStatus.FAILED,
                        title=f"Download Failed: {candidate_filename}",
                        description=err,
                        metadata={"filename": candidate_filename, "status_code": response.status_code},
                    )
                    res = DownloadResult(
                        transfer_id=transfer_id,
                        success=False,
                        source_url=validated_url,
                        filename=candidate_filename,
                        status=FileTransferStatus.FAILED,
                        error=err,
                        error_code="HTTP_ERROR",
                        duration_ms=duration_ms,
                    )
                    return res.model_dump()

                # Extract Content-Disposition header if available and not custom_filename
                if not custom_filename:
                    cd = response.headers.get("content-disposition", "")
                    cd_name = self._parse_content_disposition_filename(cd)
                    if cd_name:
                        new_candidate = sanitize_download_filename(cd_name)
                        if new_candidate != candidate_filename:
                            candidate_filename = new_candidate
                            # Check again for dangerous extension on the Content-Disposition filename
                            if is_dangerous_download_extension(candidate_filename) and not confirmed:
                                token = f"dl-{uuid.uuid4().hex[:8]}"
                                duration_ms = (time.monotonic() - t0) * 1000
                                self._emit(
                                    action_type=ActionType.DOWNLOAD_CONFIRMATION_REQUESTED,
                                    status=ActionStatus.WAITING_CONFIRMATION,
                                    title="Download Confirmation Required",
                                    description=(
                                        f"Server Content-Disposition filename '{candidate_filename}' "
                                        f"has executable extension and requires confirmation."
                                    ),
                                    confirmation_required=True,
                                    metadata={"filename": candidate_filename, "token": token},
                                )
                                res = DownloadResult(
                                    transfer_id=transfer_id,
                                    success=False,
                                    source_url=validated_url,
                                    filename=candidate_filename,
                                    status=FileTransferStatus.WAITING_CONFIRMATION,
                                    requires_confirmation=True,
                                    confirmation_token=token,
                                    message=f"CONFIRMATION REQUIRED for '{candidate_filename}'.",
                                    duration_ms=duration_ms,
                                )
                                return res.model_dump()

                # Check Content-Length if present
                content_length_header = response.headers.get("content-length")
                expected_length = None
                if content_length_header and content_length_header.isdigit():
                    expected_length = int(content_length_header)
                    if expected_length > max_size_bytes:
                        duration_ms = (time.monotonic() - t0) * 1000
                        err = f"Content-Length ({expected_length} bytes) exceeds limit ({max_size_bytes} bytes)."
                        self._emit(
                            action_type=ActionType.DOWNLOAD_FAILED,
                            status=ActionStatus.FAILED,
                            title=f"Download Too Large: {candidate_filename}",
                            description=err,
                            metadata={"filename": candidate_filename, "size_bytes": expected_length},
                        )
                        res = DownloadResult(
                            transfer_id=transfer_id,
                            success=False,
                            source_url=validated_url,
                            filename=candidate_filename,
                            status=FileTransferStatus.FAILED,
                            error=err,
                            error_code="SIZE_LIMIT_EXCEEDED",
                            duration_ms=duration_ms,
                        )
                        return res.model_dump()

                # Resolve unique filename to avoid overwrites
                target_dir = os.path.dirname(resolved_dest)
                final_path = resolve_unique_filename(target_dir, candidate_filename)
                final_filename = os.path.basename(final_path)

                # Stream to disk in chunks
                downloaded_bytes = 0
                os.makedirs(target_dir, exist_ok=True)
                with open(final_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        downloaded_bytes += len(chunk)
                        if downloaded_bytes > max_size_bytes:
                            # Abort and remove partial file
                            f.close()
                            if os.path.exists(final_path):
                                try:
                                    os.remove(final_path)
                                except Exception:
                                    pass
                            duration_ms = (time.monotonic() - t0) * 1000
                            err = f"Downloaded bytes exceeded maximum allowed limit ({max_size_bytes} bytes)."
                            self._emit(
                                action_type=ActionType.DOWNLOAD_FAILED,
                                status=ActionStatus.FAILED,
                                title=f"Download Exceeded Limit: {final_filename}",
                                description=err,
                                metadata={"filename": final_filename, "size_bytes": downloaded_bytes},
                            )
                            res = DownloadResult(
                                transfer_id=transfer_id,
                                success=False,
                                source_url=validated_url,
                                filename=final_filename,
                                status=FileTransferStatus.FAILED,
                                error=err,
                                error_code="SIZE_LIMIT_EXCEEDED",
                                duration_ms=duration_ms,
                            )
                            return res.model_dump()
                        f.write(chunk)

                content_type = response.headers.get("content-type", "application/octet-stream")

        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error(f"[DownloadService] Stream error for '{url}': {exc}")
            self._emit(
                action_type=ActionType.DOWNLOAD_FAILED,
                status=ActionStatus.FAILED,
                title=f"Download Network Error: {candidate_filename}",
                description=str(exc)[:200],
                metadata={"filename": candidate_filename, "error": str(exc)[:200]},
            )
            res = DownloadResult(
                transfer_id=transfer_id,
                success=False,
                source_url=validated_url,
                filename=candidate_filename,
                status=FileTransferStatus.FAILED,
                error=f"Network error during download: {exc}",
                error_code="NETWORK_ERROR",
                duration_ms=duration_ms,
            )
            return res.model_dump()

        # --- 6. Post-download Verification ---
        v_res = self.verify_download(
            file_path=final_path,
            expected_size=expected_length,
        )
        duration_ms = (time.monotonic() - t0) * 1000

        if not v_res["verified"]:
            self._emit(
                action_type=ActionType.DOWNLOAD_FAILED,
                status=ActionStatus.FAILED,
                title=f"Download Verification Failed: {final_filename}",
                description=v_res["reason"],
                metadata={"filename": final_filename, "reason": v_res["reason"]},
            )
            res = DownloadResult(
                transfer_id=transfer_id,
                success=False,
                source_url=validated_url,
                destination_path=final_path,
                filename=final_filename,
                size_bytes=downloaded_bytes,
                content_type=content_type,
                status=FileTransferStatus.VERIFICATION_FAILED,
                verified=False,
                error=v_res["reason"],
                error_code="VERIFICATION_FAILED",
                duration_ms=duration_ms,
            )
            return res.model_dump()

        # Success!
        self._emit(
            action_type=ActionType.DOWNLOAD_COMPLETED,
            status=ActionStatus.COMPLETED,
            title=f"Download Completed: {final_filename}",
            description=f"Saved {downloaded_bytes} bytes to {final_path}",
            metadata={
                "filename": final_filename,
                "size_bytes": downloaded_bytes,
                "content_type": content_type,
            },
        )
        self._emit(
            action_type=ActionType.DOWNLOAD_VERIFICATION_COMPLETED,
            status=ActionStatus.COMPLETED,
            title=f"Download Verified: {final_filename}",
            description=v_res["reason"],
            metadata={"filename": final_filename, "verified": True},
        )

        res = DownloadResult(
            transfer_id=transfer_id,
            success=True,
            source_url=validated_url,
            destination_path=final_path,
            filename=final_filename,
            size_bytes=downloaded_bytes,
            content_type=content_type,
            status=FileTransferStatus.COMPLETED,
            verified=True,
            message=f"Downloaded '{final_filename}' ({downloaded_bytes} bytes) successfully.",
            duration_ms=duration_ms,
            safe_metadata={
                "filename": final_filename,
                "size_bytes": downloaded_bytes,
                "content_type": content_type,
                "destination_dir": os.path.dirname(final_path),
            },
        )
        return res.model_dump()

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify_download(
        file_path: str,
        expected_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Verify that a downloaded file exists, is non-empty, and resides in an approved folder."""
        if not file_path or not os.path.exists(file_path):
            return {
                "verified": False,
                "reason": f"Target file does not exist at '{file_path}'.",
            }

        if not os.path.isfile(file_path):
            return {
                "verified": False,
                "reason": f"Target path '{file_path}' is not a regular file.",
            }

        # Containment check
        if not is_approved_filesystem_path(file_path):
            return {
                "verified": False,
                "reason": f"File path '{file_path}' escapes approved directories.",
            }

        try:
            actual_size = os.path.getsize(file_path)
        except Exception as exc:
            return {
                "verified": False,
                "reason": f"Unable to read file size: {exc}",
            }

        if actual_size == 0:
            return {
                "verified": False,
                "reason": "Downloaded file is 0 bytes (empty).",
            }

        if expected_size is not None and expected_size > 0 and actual_size != expected_size:
            return {
                "verified": False,
                "reason": f"File size mismatch: got {actual_size} bytes, expected {expected_size} bytes.",
            }

        return {
            "verified": True,
            "reason": f"File verified: exists, size={actual_size} bytes, within approved directory.",
            "size_bytes": actual_size,
            "file_path": file_path,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_content_disposition_filename(cd_header: str) -> Optional[str]:
        """Extract filename from Content-Disposition header."""
        if not cd_header:
            return None
        # RFC 5987 / RFC 6266 utf-8 filename*
        m_star = re.search(r"filename\*\s*=\s*utf-8''([^;]+)", cd_header, re.IGNORECASE)
        if m_star:
            return urllib.parse.unquote(m_star.group(1).strip().strip('"'))
        # Standard filename="name.ext"
        m = re.search(r'filename\s*=\s*"([^"]+)"', cd_header, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m_unquoted = re.search(r'filename\s*=\s*([^;]+)', cd_header, re.IGNORECASE)
        if m_unquoted:
            return m_unquoted.group(1).strip().strip('"')
        return None

    @staticmethod
    def _emit(
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        description: str = "",
        confirmation_required: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit an ActionEvent with credential redaction."""
        event = ActionEvent(
            action_type=action_type,
            status=status,
            title=title,
            description=description,
            confirmation_required=confirmation_required,
        )
        if metadata:
            event.with_metadata(metadata)
        action_bus.emit(event)
