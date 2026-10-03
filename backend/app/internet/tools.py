"""RYVEN 3.0 — Registered Safe Tools for the Unified Internet Agent Engine.

Exposes high-level Internet capabilities to the ToolRegistry and LLM planner:
- internet_search
- web_research
- web_task
- web_verify
- browser_tabs
- browser_snapshot
"""

from __future__ import annotations

from typing import Any, Dict
from app.internet.agent import internet_agent
from app.tools.base import BaseTool


class InternetSearchTool(BaseTool):
    """High-level tool for searching the live web and ranking authoritative results."""

    name = "internet_search"
    description = (
        "Searches the web for information, ranks authoritative domain sources, "
        "and returns verified, clean search results without hallucination."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term or question to find on the web.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of ranked results to return (default 5).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        query = kwargs.get("query", "")
        max_results = int(kwargs.get("max_results", 5))
        return await internet_agent.search(query=query, max_results=max_results)


class WebResearchTool(BaseTool):
    """Deep multi-source research tool that visits, reads, and synthesizes authoritative web pages."""

    name = "web_research"
    description = (
        "Conducts multi-source web research on a technical or general topic. "
        "Visits top authoritative sources, extracts core findings, and synthesizes a cited research report."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "The topic, library, or technology to research (e.g. 'FastAPI lifespan events', 'React 19 hooks').",
            },
            "max_sources": {
                "type": "integer",
                "description": "Number of top sources to open and read (default 3).",
            },
        },
        "required": ["topic"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        topic = kwargs.get("topic", "")
        max_sources = int(kwargs.get("max_sources", 3))
        return await internet_agent.research(topic=topic, max_sources=max_sources)


class WebTaskTool(BaseTool):
    """Executes multi-step internet workflows following the Observe -> Act -> Verify loop."""

    name = "web_task"
    description = (
        "Executes a natural language multi-step internet task (e.g. 'search YouTube for tutorials', "
        "'open GitHub issues', 'verify website login page'). Uses structured planning, element discovery, "
        "and human confirmation / authentication pause boundaries."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "Natural language description of the web task to perform.",
            },
            "auto_confirm": {
                "type": "boolean",
                "description": "Whether non-critical safe steps should proceed without explicit pause.",
            },
        },
        "required": ["goal"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        goal = kwargs.get("goal", "")
        auto_confirm = bool(kwargs.get("auto_confirm", False))
        return await internet_agent.execute_task(goal=goal, auto_confirm=auto_confirm)


class WebVerifyTool(BaseTool):
    """Verifies content, search results, or expected elements on the active page."""

    name = "web_verify"
    description = (
        "Verifies that a target keyword, element, or search result exists on the currently active web page. "
        "Used to validate that internet actions succeeded before declaring completion."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text or element keyword to verify on the page.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        query = kwargs.get("query", "")
        return await internet_agent.verify_page(query=query)


class BrowserTabsTool(BaseTool):
    """Manages browser tabs in the active browser session."""

    name = "browser_tabs"
    description = (
        "Manages tabs in the controlled browser session. Supports listing open tabs, "
        "opening a new tab, switching active tab, or closing tabs."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "new", "switch", "close"],
                "description": "Tab action to perform (list, new, switch, close).",
            },
            "tab_url": {
                "type": "string",
                "description": "Destination URL when opening a new tab.",
            },
            "tab_index": {
                "type": "integer",
                "description": "Index of the tab to switch to or close.",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        action = kwargs.get("action", "list")
        tab_url = kwargs.get("tab_url")
        tab_index = kwargs.get("tab_index")
        return await internet_agent.manage_tabs(
            action=action,
            tab_url=tab_url,
            tab_index=tab_index,
        )


class BrowserSnapshotTool(BaseTool):
    """Captures structural, accessible snapshot of the active page."""

    name = "browser_snapshot"
    description = (
        "Captures a structured snapshot of the active page including URL, page title, "
        "readable text snippet, and interactive elements."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            }
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        session_id = kwargs.get("session_id")
        return await internet_agent.browser_engine.take_snapshot(session_id=session_id)


class BrowserScreenshotTool(BaseTool):
    """Captures an in-memory visual screenshot of the active browser page."""

    name = "browser_screenshot"
    description = (
        "Captures an in-memory visual screenshot of the active browser page. "
        "Memory-only; never saved to disk or written to persistent logs."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            },
            "max_width": {
                "type": "integer",
                "description": "Maximum width for screenshot (default 800)",
            },
            "max_height": {
                "type": "integer",
                "description": "Maximum height for screenshot (default 600)",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        session_id = kwargs.get("session_id")
        max_width = kwargs.get("max_width", 800)
        max_height = kwargs.get("max_height", 600)
        return await internet_agent.browser_engine.capture_screenshot(
            session_id=session_id,
            max_width=max_width,
            max_height=max_height,
        )


class PageOCRTool(BaseTool):
    """Extracts visible text from the active page using local OCR or vision models."""

    name = "page_ocr"
    description = (
        "Extracts visible text from the active browser page using local OCR / vision models. "
        "All credentials and sensitive tokens are automatically redacted. Never uses remote providers."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        import base64
        import io
        from app.actions.event_bus import action_bus
        from app.actions.models import ActionEvent, ActionStatus, ActionType
        from app.browser.security import BrowserSecurityValidator

        session_id = kwargs.get("session_id")
        engine = internet_agent.browser_engine
        state = engine._get_or_create_session(session_id)

        snap = state.last_snapshot
        if not snap or not snap.screenshot_b64:
            capture_res = await engine.capture_screenshot(session_id=session_id)
            if not capture_res.get("success"):
                return {
                    "success": False,
                    "session_id": state.session_id,
                    "error": f"Failed to capture page for OCR: {capture_res.get('error', 'Unknown error')}",
                }
            snap = state.last_snapshot

        if not snap or not snap.screenshot_b64:
            return {
                "success": False,
                "session_id": state.session_id,
                "error": "No screenshot available for OCR.",
            }

        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.OCR_STARTED,
                status=ActionStatus.STARTED,
                title="Running local page OCR",
                safe_metadata={
                    "session_id": state.session_id,
                    "url": snap.url,
                },
            )
        )

        ocr_text = ""
        engine_used = "none"
        text_regions = []

        # Strategy 1: pytesseract if available and functional
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore

            img_bytes = base64.b64decode(snap.screenshot_b64)
            img = Image.open(io.BytesIO(img_bytes))
            raw_text = pytesseract.image_to_string(img)
            if raw_text and raw_text.strip():
                ocr_text = raw_text.strip()
                engine_used = "tesseract"
        except Exception:
            pass

        # Strategy 2: Local Vision Model via PerceptionAgent
        if not ocr_text:
            try:
                from app.ai.vision import perception_agent
                result = await perception_agent.perceive(
                    snap.screenshot_b64,
                    prompt="Extract all visible text, headings, and labels from this webpage screenshot.",
                    url=snap.url,
                )
                if result.ocr_text:
                    ocr_text = result.ocr_text
                    engine_used = "perception_agent"
                elif result.description:
                    ocr_text = result.description
                    engine_used = "perception_agent_desc"
                text_regions = [
                    {"label": el.label, "text": el.text, "bbox": el.bbox, "confidence": el.confidence}
                    for el in result.elements
                    if el.text
                ]
            except Exception:
                pass

        # Strategy 3: DOM text fallback if image OCR returned empty
        if not ocr_text and snap.text_content:
            ocr_text = snap.text_content
            engine_used = "dom_text_fallback"

        sanitized_ocr = BrowserSecurityValidator.redact_credentials(ocr_text or "")
        snap.ocr_text = sanitized_ocr

        if not sanitized_ocr:
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.OCR_FAILED,
                    status=ActionStatus.FAILED,
                    title="OCR failed to extract text",
                    safe_metadata={
                        "session_id": state.session_id,
                        "url": snap.url,
                        "error": "No text extracted from page image",
                    },
                )
            )
            return {
                "success": False,
                "session_id": state.session_id,
                "error": "No text could be extracted from page.",
            }

        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.OCR_COMPLETED,
                status=ActionStatus.COMPLETED,
                title="OCR text extraction completed",
                safe_metadata={
                    "session_id": state.session_id,
                    "url": snap.url,
                    "engine": engine_used,
                    "text_length": len(sanitized_ocr),
                },
            )
        )

        return {
            "success": True,
            "session_id": state.session_id,
            "url": snap.url,
            "engine": engine_used,
            "ocr_text": sanitized_ocr,
            "text_regions": text_regions,
            "length": len(sanitized_ocr),
        }


# ===========================================================================
# M15.3 Phase 3 — Form Interaction Tools
# ===========================================================================


class FormFieldFillTool(BaseTool):
    """Fills a safe text/number/email/url/search/textarea form field."""

    name = "form_field_fill"
    description = (
        "Fills an input or textarea form field with text. Password or credential "
        "fields are strictly blocked. Target can be resolved by label, name, id, or placeholder."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Field identifier: label text, name, id, or placeholder.",
            },
            "value": {
                "type": "string",
                "description": "Safe text to type. NEVER pass credentials, passwords, or tokens.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional browser session ID.",
            },
        },
        "required": ["target", "value"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        target = kwargs.get("target", "")
        value = kwargs.get("value", "")
        session_id = kwargs.get("session_id")
        return await internet_agent.fill_form_field(
            target=target,
            value=value,
            session_id=session_id,
        )


class FormOptionSelectTool(BaseTool):
    """Selects an option in a <select> dropdown element."""

    name = "form_option_select"
    description = (
        "Selects an option in a dropdown (<select>) element by option value or visible label."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Select field identifier: label text, name, or id.",
            },
            "option": {
                "type": "string",
                "description": "The option value or visible text to select.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional browser session ID.",
            },
        },
        "required": ["target", "option"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        target = kwargs.get("target", "")
        option = kwargs.get("option", "")
        session_id = kwargs.get("session_id")
        return await internet_agent.select_form_option(
            target=target,
            option=option,
            session_id=session_id,
        )


class FormCheckboxToggleTool(BaseTool):
    """Sets or toggles a checkbox form field."""

    name = "form_checkbox_toggle"
    description = (
        "Checks, unchecks, or toggles a checkbox input field."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Checkbox identifier: label text, name, or id.",
            },
            "checked": {
                "type": "boolean",
                "description": "True to check, False to uncheck, omit/null to toggle.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional browser session ID.",
            },
        },
        "required": ["target"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        target = kwargs.get("target", "")
        checked = kwargs.get("checked")
        session_id = kwargs.get("session_id")
        return await internet_agent.toggle_form_checkbox(
            target=target,
            checked=checked,
            session_id=session_id,
        )


class FormRadioSelectTool(BaseTool):
    """Selects a radio button option within a radio group."""

    name = "form_radio_select"
    description = (
        "Selects a radio button in a radio group by its value or visible label."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Radio group identifier: group name, label, or id.",
            },
            "value": {
                "type": "string",
                "description": "The value or label of the radio option to select.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional browser session ID.",
            },
        },
        "required": ["target", "value"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        target = kwargs.get("target", "")
        value = kwargs.get("value", "")
        session_id = kwargs.get("session_id")
        return await internet_agent.select_form_radio(
            target=target,
            value=value,
            session_id=session_id,
        )


class FormSubmitTool(BaseTool):
    """Submits a form with mandatory confirmation gating."""

    name = "form_submit"
    description = (
        "Submits a web form. REQUIRES explicit user confirmation (confirmed=True). "
        "Calling with confirmed=False returns a confirmation token and does NOT submit."
    )
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "form_id": {
                "type": "string",
                "description": "HTML id of the form to submit.",
            },
            "submit_label": {
                "type": "string",
                "description": "Optional label or selector of the submit button.",
            },
            "confirmed": {
                "type": "boolean",
                "description": "Must be True to proceed with submission. False triggers confirmation prompt.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional browser session ID.",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        form_id = kwargs.get("form_id")
        submit_label = kwargs.get("submit_label")
        confirmed = bool(kwargs.get("confirmed", False))
        session_id = kwargs.get("session_id")
        return await internet_agent.submit_form(
            form_id=form_id,
            submit_label=submit_label,
            confirmed=confirmed,
            session_id=session_id,
        )


# ===========================================================================
# M15.3 Phase 4 — Download & Upload Management Tools
# ===========================================================================


class WebDownloadTool(BaseTool):
    """Downloads a file from a web URL to an approved local directory."""

    name = "web_download"
    description = (
        "Downloads a file from an HTTP/HTTPS URL into an approved local directory "
        "(Downloads, Desktop, Documents, or Workspace). Potentially dangerous file types "
        "(.exe, .bat, .ps1, etc.) require explicit confirmation (confirmed=True)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP or HTTPS URL of the file to download.",
            },
            "destination_folder": {
                "type": "string",
                "description": "Approved destination category ('downloads', 'desktop', 'documents', 'workspace'). Default 'downloads'.",
            },
            "custom_filename": {
                "type": "string",
                "description": "Optional custom filename to save as.",
            },
            "confirmed": {
                "type": "boolean",
                "description": "Set to True if user approved downloading an executable/script file.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional browser session ID.",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        url = kwargs.get("url", "")
        destination_folder = kwargs.get("destination_folder", "downloads")
        custom_filename = kwargs.get("custom_filename")
        confirmed = bool(kwargs.get("confirmed", False))
        session_id = kwargs.get("session_id")
        return await internet_agent.download_file(
            url=url,
            destination_folder=destination_folder,
            custom_filename=custom_filename,
            confirmed=confirmed,
            session_id=session_id,
        )


class WebUploadTool(BaseTool):
    """Uploads an approved local file into a web form file input."""

    name = "web_upload"
    description = (
        "Attaches an approved local file to a web form <input type='file'> element. "
        "Sensitive files (.env, private keys, secrets, tokens) are strictly blocked. "
        "File selection does NOT automatically submit the form."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Absolute or canonical path of the approved local file to upload.",
            },
            "target_field": {
                "type": "string",
                "description": "Identifier (label, name, id, or placeholder) of the <input type='file'> element.",
            },
            "form_id": {
                "type": "string",
                "description": "Optional form ID containing the file input.",
            },
            "confirmed": {
                "type": "boolean",
                "description": "Whether upload has been confirmed by user.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional browser session ID.",
            },
        },
        "required": ["source_path", "target_field"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        source_path = kwargs.get("source_path", "")
        target_field = kwargs.get("target_field", "")
        form_id = kwargs.get("form_id")
        confirmed = bool(kwargs.get("confirmed", False))
        session_id = kwargs.get("session_id")
        return await internet_agent.upload_file(
            source_path=source_path,
            target_field=target_field,
            form_id=form_id,
            confirmed=confirmed,
            session_id=session_id,
        )


class WebVerifyDownloadTool(BaseTool):
    """Verifies that a downloaded file exists, is non-empty, and resides in an approved folder."""

    name = "web_verify_download"
    description = (
        "Verifies that a downloaded file exists on disk, is non-empty, and is located "
        "within approved directory roots."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Local path of the file to verify.",
            },
            "expected_size": {
                "type": "integer",
                "description": "Optional expected file size in bytes.",
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        file_path = kwargs.get("file_path", "")
        expected_size = kwargs.get("expected_size")
        return internet_agent.verify_download(
            file_path=file_path,
            expected_size=expected_size,
        )


class WebVerifyUploadTool(BaseTool):
    """Verifies that a local file was successfully attached to a web file input."""

    name = "web_verify_upload"
    description = (
        "Verifies that a local file was successfully targeted and attached to a web file input."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "field_name": {
                "type": "string",
                "description": "Name or identifier of the file input field.",
            },
            "filename": {
                "type": "string",
                "description": "Basename of the file that was attached.",
            },
        },
        "required": ["field_name", "filename"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        field_name = kwargs.get("field_name", "")
        filename = kwargs.get("filename", "")
        return internet_agent.verify_upload(
            field_name=field_name,
            filename=filename,
        )


