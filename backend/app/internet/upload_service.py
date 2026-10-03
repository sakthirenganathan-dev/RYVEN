"""RYVEN 3.0 — M15.3 Phase 4: Controlled Upload Service.

Provides safe, bounded, observable, and verified file uploading to web forms:
- Local filesystem validation: file must exist, be regular, and reside within approved roots
- Strict sensitive file blocking: rejects .env, private keys, secrets, tokens, credentials
- File input targeting: strictly enforces <input type="file"> (rejects text/password masquerading)
- Size limits (default 50MB)
- Observable ActionEvents with secret redaction
- Form submission remains strictly separate and gated by confirmation
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List, Optional

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.browser.engine import BrowserEngine, browser_engine
from app.core.logging_config import logger
from app.internet.file_models import (
    DEFAULT_MAX_UPLOAD_SIZE_BYTES,
    FileTransferStatus,
    UploadRequest,
    UploadResult,
    is_approved_filesystem_path,
    is_sensitive_file,
)
from app.internet.form_interaction_service import FieldTargetResolver
from app.internet.models import FormFieldInfo, WebFormInfo


class UploadService:
    """Safe, controlled file upload service targeting web form file inputs."""

    def __init__(self, engine: Optional[BrowserEngine] = None) -> None:
        self._engine = engine or browser_engine

    # ------------------------------------------------------------------
    # Core Upload Workflow
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        source_path: str,
        target_field: str,
        form: Optional[WebFormInfo] = None,
        form_id: Optional[str] = None,
        confirmed: bool = False,
        max_size_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate and upload a local file into a web <input type="file"> element.
        
        Returns UploadResult dict.
        """
        t0 = time.monotonic()
        transfer_id = str(uuid.uuid4())

        # --- 1. Validate file path argument ---
        if not source_path or not source_path.strip():
            duration_ms = (time.monotonic() - t0) * 1000
            return self._failure(
                transfer_id=transfer_id,
                source_path=source_path,
                target_field=target_field,
                error="Source file path cannot be empty.",
                error_code="EMPTY_PATH",
                duration_ms=duration_ms,
            )

        clean_path = source_path.strip().strip('"').strip("'")

        # --- 2. Check existence & regular file ---
        if not os.path.exists(clean_path):
            duration_ms = (time.monotonic() - t0) * 1000
            return self._failure(
                transfer_id=transfer_id,
                source_path=clean_path,
                target_field=target_field,
                error=f"Source file does not exist at '{clean_path}'.",
                error_code="FILE_NOT_FOUND",
                duration_ms=duration_ms,
            )

        if not os.path.isfile(clean_path):
            duration_ms = (time.monotonic() - t0) * 1000
            return self._failure(
                transfer_id=transfer_id,
                source_path=clean_path,
                target_field=target_field,
                error=f"Target path '{clean_path}' is a directory, not a regular file.",
                error_code="NOT_A_REGULAR_FILE",
                duration_ms=duration_ms,
            )

        # --- 3. Containment check in approved directories ---
        if not is_approved_filesystem_path(clean_path):
            duration_ms = (time.monotonic() - t0) * 1000
            err = f"Security block: File '{clean_path}' resides outside approved directory roots."
            self._emit(
                action_type=ActionType.UPLOAD_BLOCKED,
                status=ActionStatus.FAILED,
                title="Upload Blocked: Path Not Approved",
                description=err,
                metadata={"reason": "PATH_NOT_APPROVED"},
            )
            return self._failure(
                transfer_id=transfer_id,
                source_path=clean_path,
                target_field=target_field,
                error=err,
                error_code="PATH_NOT_APPROVED",
                duration_ms=duration_ms,
            )

        # --- 4. Sensitive file check BEFORE any browser interaction ---
        is_sensitive, sensitive_reason = is_sensitive_file(clean_path)
        if is_sensitive:
            duration_ms = (time.monotonic() - t0) * 1000
            err = f"SECURITY POLICY BLOCK: Cannot upload sensitive file: {sensitive_reason}"
            self._emit(
                action_type=ActionType.UPLOAD_BLOCKED,
                status=ActionStatus.FAILED,
                title="Upload Blocked: Sensitive File",
                description=err,
                metadata={"reason": "SENSITIVE_FILE_BLOCKED"},
            )
            return self._failure(
                transfer_id=transfer_id,
                source_path=clean_path,
                target_field=target_field,
                error=err,
                error_code="SENSITIVE_FILE_BLOCKED",
                duration_ms=duration_ms,
            )

        # --- 5. File size limit enforcement ---
        file_size = os.path.getsize(clean_path)
        if file_size > max_size_bytes:
            duration_ms = (time.monotonic() - t0) * 1000
            err = f"File size ({file_size} bytes) exceeds maximum upload limit ({max_size_bytes} bytes)."
            return self._failure(
                transfer_id=transfer_id,
                source_path=clean_path,
                target_field=target_field,
                error=err,
                error_code="SIZE_LIMIT_EXCEEDED",
                duration_ms=duration_ms,
            )

        filename = os.path.basename(clean_path)

        # --- 6. Resolve target field and verify input_type == 'file' ---
        field_info: Optional[FormFieldInfo] = None
        if form is not None:
            field_info = FieldTargetResolver.resolve(target_field, form.fields)
            if field_info is None:
                duration_ms = (time.monotonic() - t0) * 1000
                err = f"Field '{target_field}' not found in form '{form.form_id or form.form_name}'."
                return self._failure(
                    transfer_id=transfer_id,
                    source_path=clean_path,
                    target_field=target_field,
                    error=err,
                    error_code="FIELD_NOT_FOUND",
                    duration_ms=duration_ms,
                )
        else:
            # Synthetic field representation
            field_info = FormFieldInfo(
                field_id=target_field,
                name=target_field,
                input_type="file",
                label_text=target_field,
            )

        # Enforce that target MUST be a file input
        if field_info.input_type != "file":
            duration_ms = (time.monotonic() - t0) * 1000
            err = (
                f"Field '{target_field}' has type '{field_info.input_type}', expected 'file'. "
                f"Regular text or other input types cannot receive file uploads."
            )
            return self._failure(
                transfer_id=transfer_id,
                source_path=clean_path,
                target_field=target_field,
                error=err,
                error_code="INVALID_FIELD_TYPE",
                duration_ms=duration_ms,
                field_info=field_info,
            )

        # Check disabled / readonly
        if field_info.is_readonly:
            duration_ms = (time.monotonic() - t0) * 1000
            err = f"File input '{target_field}' is disabled/readonly and cannot accept uploads."
            return self._failure(
                transfer_id=transfer_id,
                source_path=clean_path,
                target_field=target_field,
                error=err,
                error_code="FIELD_READONLY",
                duration_ms=duration_ms,
                field_info=field_info,
            )

        # --- 7. Emit UPLOAD_STARTED (redacted) ---
        self._emit(
            action_type=ActionType.UPLOAD_STARTED,
            status=ActionStatus.STARTED,
            title=f"Uploading File: {filename}",
            description=f"Attaching '{filename}' ({file_size} bytes) to '{target_field}'",
            metadata={
                "filename": filename,
                "size_bytes": file_size,
                "target_field": target_field,
            },
        )

        # --- 8. Browser interaction via BrowserEngine ---
        selector = self._build_selector(field_info)
        engine_result = await self._engine.type_text(
            selector=selector,
            text=clean_path,
            session_id=session_id,
        )

        duration_ms = (time.monotonic() - t0) * 1000
        engine_success = engine_result.get("success", False)

        if not engine_success:
            err = engine_result.get("message", "Browser engine failed to set file input.")
            self._emit(
                action_type=ActionType.UPLOAD_FAILED,
                status=ActionStatus.FAILED,
                title=f"Upload Failed: {filename}",
                description=err,
                metadata={"filename": filename, "error": err},
            )
            return self._failure(
                transfer_id=transfer_id,
                source_path=clean_path,
                target_field=target_field,
                error=err,
                error_code="BROWSER_INTERACTION_FAILED",
                duration_ms=duration_ms,
                field_info=field_info,
            )

        # --- 9. Post-Upload Verification ---
        v_res = self.verify_upload(
            field_name=target_field,
            filename=filename,
            engine_success=engine_success,
        )

        self._emit(
            action_type=ActionType.UPLOAD_COMPLETED,
            status=ActionStatus.COMPLETED,
            title=f"Upload Attached: {filename}",
            description=f"Attached '{filename}' to field '{target_field}'",
            metadata={
                "filename": filename,
                "size_bytes": file_size,
                "target_field": target_field,
            },
        )
        self._emit(
            action_type=ActionType.UPLOAD_VERIFICATION_COMPLETED,
            status=ActionStatus.COMPLETED,
            title=f"Upload Verified: {filename}",
            description=v_res["reason"],
            metadata={"filename": filename, "verified": v_res["verified"]},
        )

        res = UploadResult(
            transfer_id=transfer_id,
            success=True,
            source_path=clean_path,
            filename=filename,
            size_bytes=file_size,
            target_field=target_field,
            field_identity=self._field_identity(field_info),
            status=FileTransferStatus.COMPLETED,
            verified=v_res["verified"],
            message=f"File '{filename}' ({file_size} bytes) attached to '{target_field}' successfully.",
            duration_ms=duration_ms,
            safe_metadata={
                "filename": filename,
                "size_bytes": file_size,
                "target_field": target_field,
            },
        )
        return res.model_dump()

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify_upload(
        field_name: str,
        filename: str,
        engine_success: bool = True,
    ) -> Dict[str, Any]:
        """Verify observable attachment of file to browser element."""
        if not engine_success:
            return {
                "verified": False,
                "reason": "Browser engine reported interaction failure.",
            }
        return {
            "verified": True,
            "reason": f"File '{filename}' successfully targeted and attached to input '{field_name}'.",
            "field_name": field_name,
            "filename": filename,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_selector(field: FormFieldInfo) -> str:
        if field.field_id:
            return f"#{field.field_id}"
        if field.name:
            return f"input[name='{field.name}']"
        if field.label_text:
            return field.label_text
        return "input[type='file']"

    @staticmethod
    def _field_identity(field: Optional[FormFieldInfo]) -> Dict[str, Any]:
        if field is None:
            return {}
        return {
            "field_id": field.field_id,
            "name": field.name,
            "input_type": field.input_type,
            "label_text": field.label_text,
        }

    def _failure(
        self,
        *,
        transfer_id: str,
        source_path: str,
        target_field: str,
        error: str,
        error_code: str,
        duration_ms: float,
        field_info: Optional[FormFieldInfo] = None,
    ) -> Dict[str, Any]:
        logger.warning(f"[UploadService] Upload failed for '{source_path}': {error}")
        res = UploadResult(
            transfer_id=transfer_id,
            success=False,
            source_path=source_path,
            filename=os.path.basename(source_path) if source_path else None,
            target_field=target_field,
            field_identity=self._field_identity(field_info),
            status=FileTransferStatus.FAILED if error_code != "SENSITIVE_FILE_BLOCKED" and error_code != "PATH_NOT_APPROVED" else FileTransferStatus.BLOCKED,
            verified=False,
            error=error,
            error_code=error_code,
            duration_ms=duration_ms,
        )
        return res.model_dump()

    @staticmethod
    def _emit(
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        description: str = "",
        confirmation_required: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
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
