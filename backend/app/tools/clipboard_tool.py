"""Clipboard tools for reading and writing text via native Win32 APIs."""

import ctypes
from ctypes import wintypes
from typing import Any, Dict
from app.core.logging_config import logger
from app.tools.base import BaseTool

# Win32 Constants
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Configure 64-bit function signatures
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
user32.OpenClipboard.argtypes = [wintypes.HWND]


def _read_win32_clipboard() -> str:
    """Read text from Windows clipboard safely."""
    if not user32.OpenClipboard(None):
        return ""
    try:
        h_mem = user32.GetClipboardData(CF_UNICODETEXT)
        if not h_mem:
            return ""
        p_mem = kernel32.GlobalLock(h_mem)
        if not p_mem:
            return ""
        try:
            return str(ctypes.wstring_at(p_mem))
        finally:
            kernel32.GlobalUnlock(h_mem)
    finally:
        user32.CloseClipboard()


def _write_win32_clipboard(text: str) -> bool:
    """Write text to Windows clipboard safely."""
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        encoded = text.encode("utf-16le") + b"\x00\x00"
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not h_mem:
            return False
        p_mem = kernel32.GlobalLock(h_mem)
        if not p_mem:
            return False
        try:
            ctypes.memmove(p_mem, encoded, len(encoded))
        finally:
            kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        return True
    finally:
        user32.CloseClipboard()


class GetClipboardTool(BaseTool):
    """Tool that reads the current text contents of the Windows clipboard."""

    name = "get_clipboard"
    description = "Reads and returns the current text content stored in the system clipboard."
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute clipboard read."""
        try:
            content = _read_win32_clipboard()
            if not content:
                return {
                    "success": True,
                    "tool": self.name,
                    "text": "",
                    "message": "The system clipboard is currently empty.",
                }

            display_preview = content[:150] + ("..." if len(content) > 150 else "")
            return {
                "success": True,
                "tool": self.name,
                "text": content,
                "length": len(content),
                "message": f"Clipboard content ({len(content)} characters): \"{display_preview}\"",
            }
        except Exception as exc:
            logger.error(f"Error reading clipboard: {exc}")
            return {
                "success": False,
                "tool": self.name,
                "text": "",
                "message": f"Unable to read clipboard: {str(exc)}",
            }


class SetClipboardTool(BaseTool):
    """Tool that copies user-specified text to the Windows clipboard."""

    name = "set_clipboard"
    description = "Copies the provided text content onto the system clipboard."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to place on the clipboard",
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    MAX_LENGTH = 10000

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute clipboard write."""
        raw_text = kwargs.get("text", "")
        if not raw_text:
            return {
                "success": False,
                "tool": self.name,
                "message": "No text provided to copy to clipboard.",
            }

        if len(raw_text) > self.MAX_LENGTH:
            return {
                "success": False,
                "tool": self.name,
                "message": f"Clipboard copy rejected: text exceeds maximum length of {self.MAX_LENGTH} characters.",
            }

        try:
            ok = _write_win32_clipboard(raw_text)
            if ok:
                preview = raw_text[:80] + ("..." if len(raw_text) > 80 else "")
                return {
                    "success": True,
                    "tool": self.name,
                    "text": raw_text,
                    "length": len(raw_text),
                    "message": f"Copied to clipboard: \"{preview}\"",
                }
            else:
                return {
                    "success": False,
                    "tool": self.name,
                    "message": "Failed to access clipboard for writing.",
                }
        except Exception as exc:
            logger.error(f"Error writing to clipboard: {exc}")
            return {
                "success": False,
                "tool": self.name,
                "message": f"Unable to set clipboard: {str(exc)}",
            }
