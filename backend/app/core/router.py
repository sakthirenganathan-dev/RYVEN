"""Intent routing mechanism for RYVEN requests."""

import re
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field
from app.core.logging_config import logger


class RouteDecision(BaseModel):
    """Decision indicating whether to execute a tool, a workflow, or delegate to the AI engine."""

    intent: Literal["tool", "ai", "workflow"]
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    workflow_name: Optional[str] = None
    reason: str


class IntentRouter:
    """Evaluates user requests and determines whether to invoke tools or the AI provider."""

    # Regex patterns for time queries
    TIME_PATTERNS = [
        r"\b(?:what(?:\s+is|\s*'?s)?\s+(?:the\s+)?time)\b",
        r"\b(?:current\s+time)\b",
        r"\b(?:tell\s+me\s+the\s+time)\b",
        r"\b(?:what\s+time\s+is\s+it)\b",
        r"\b(?:what(?:\s+is|\s*'?s)?\s+(?:the\s+)?(?:date|day))\b",
        r"\b(?:what\s+day\s+is\s+it)\b",
        r"\b(?:today(?:'s)?\s+date)\b",
        r"\b(?:current\s+date)\b",
    ]

    # Regex patterns for system telemetry queries (RAM, CPU, disk, battery level)
    SYSTEM_PATTERNS = [
        r"\b(?:system\s+(?:status|telemetry|diagnostics|health|metrics))\b",
        r"\b(?:cpu\s+(?:usage|load|status|utilization|metrics|percent))\b",
        r"\b(?:ram\s+(?:usage|load|status|utilization|percent|metrics))\b",
        r"\b(?:memory\s+usage)\b",
        r"\b(?:disk\s+(?:space|usage|status))\b",
        r"\b(?:storage\s+(?:space|usage|status))\b",
        r"\b(?:battery(?:\s+level|\s+percentage))\b",
        r"\b(?:how\s+much\s+(?:ram|memory|disk|storage|cpu))\b",
        r"\b(?:check\s+(?:my\s+)?(?:ram|memory|cpu|disk))\b",
        r"\b(?:what(?:\s+is|\s*'?s)?\s+(?:my\s+)?(?:ram|cpu|memory)\s+usage)\b",
    ]

    # Safe system platform info (Windows version, hostname, network status, battery health)
    SYSTEM_INFO_PATTERNS = [
        r"\b(?:windows\s+version|os\s+version|system\s+information|system\s+info)\b",
        r"\b(?:computer\s+name|hostname|what\s+is\s+my\s+hostname)\b",
        r"\b(?:network\s+status|internet\s+status|connection\s+status)\b",
        r"\b(?:battery\s+health|battery\s+status)\b",
    ]

    # Approved applications regex
    APP_PATTERN = re.compile(
        r"\b(?:open|launch|start|run)\s+(?:the\s+)?(vs\s+code|vscode|visual\s+studio\s+code|code|chrome|google\s+chrome|browser|notepad|calculator|calc|windows\s+terminal|terminal|wt)\b",
        re.IGNORECASE,
    )

    # Approved websites regex
    WEBSITE_PATTERN = re.compile(
        r"\b(?:open|launch|go\s+to|visit)\s+(?:the\s+|my\s+)?(github|youtube|google|reddit|stackoverflow|stack\s+overflow|chatgpt|wikipedia|portfolio|https?://[^\s]+)\b",
        re.IGNORECASE,
    )

    # Approved folders regex
    FOLDER_PATTERN = re.compile(
        r"\b(?:open|show|explore)\s+(?:the\s+|my\s+)?(downloads|desktop|documents|project\s+workspace|workspace|project)\s*(?:folder|directory)?\b",
        re.IGNORECASE,
    )

    # Clipboard read regex
    CLIPBOARD_GET_PATTERN = re.compile(
        r"\b(?:what(?:\s+is|\s*'?s)?\s+(?:in|on)\s+(?:my\s+)?clipboard|get\s+(?:my\s+)?clipboard|read\s+(?:my\s+)?clipboard|show\s+(?:my\s+)?clipboard)\b",
        re.IGNORECASE,
    )

    # Clipboard write regex
    CLIPBOARD_SET_PATTERNS = [
        re.compile(
            r"\b(?:copy|set|put)\s+(?:this\s+text\s+)?to\s+(?:my\s+)?clipboard:\s*['\"]?(.*?)['\"]?$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:copy|set|put)\s+['\"](.*?)['\"]\s+to\s+(?:my\s+)?clipboard\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:copy|set|put)\s+(.*?)\s+to\s+(?:my\s+)?clipboard\b",
            re.IGNORECASE,
        ),
    ]

    # Search files regex
    SEARCH_FILES_PATTERN = re.compile(
        r"\b(?:find|search(?:\s+for)?|locate)\s+(?:my\s+)?([a-zA-Z0-9_\-\.]+?)\s+files?\b",
        re.IGNORECASE,
    )

    # Open file regex
    OPEN_FILE_PATTERN = re.compile(
        r"\b(?:open\s+(?:the\s+)?([^\n\r]+?)\s+file(?:\s+you\s+(?:just\s+)?found)?|open\s+the\s+file\s+you\s+(?:just\s+)?found)\b",
        re.IGNORECASE,
    )

    # Multi-step Workflow patterns (Phase 4 & 4.1)
    WORKSPACE_PREP_PATTERNS = [
        r"\b(?:prepare(?:\s+my)?(?:\s+(?:development|dev))?\s+workspace)\b",
        r"\b(?:set(?:\s+)?up(?:\s+my)?(?:\s+(?:development|dev))?\s+workspace)\b",
        r"\b(?:open(?:\s+my)?(?:\s+(?:development|dev))\s+workspace)\b",
        r"\b(?:initialize(?:\s+my)?(?:\s+(?:development|dev))?\s+workspace)\b",
    ]

    PROJECT_CREATION_PATTERNS = [
        r"\b(?:create|build|scaffold|generate|setup|set\s+up)\s+(?:a\s+)?(?:new\s+)?(?:(?P<type>react|python\s+api|python|web|vanilla|html|node|frontend|fastapi)\s+)?project\s+(?:called|named)\s+['\"]?(?P<name>[^\s'\"]+)['\"]?\b",
        r"\b(?:create|build|scaffold|generate|setup|set\s+up)\s+(?:a\s+)?(?:new\s+)?project\s+['\"]?(?P<name>[^\s'\"]+)['\"]?\s+(?:in|using|with)\s+(?P<type>react|python\s+api|python|web|vanilla|html|node|fastapi)\b",
        r"\b(?:create|build|scaffold|generate|setup|set\s+up)\s+(?:a\s+)?(?:new\s+)?(?:(?P<type>react|python\s+api|python|web|vanilla|html|node|frontend|fastapi)\s+)?project\s+['\"]?(?P<name>[a-zA-Z0-9_\-]+)['\"]?\b",
        r"\b(?:create|build|make(?:\s+me)?)\s+(?:a\s+)?(?:new\s+)?(?P<type>react|python|web|html|fastapi)\s+(?P<name>[a-zA-Z0-9_\-]+(?:\s+[a-zA-Z0-9_\-]+)?)\s+(?:app|application|website|tracker|dashboard|api)\b",
        r"\b(?:create|build)\s+(?:a\s+)?(?:simple\s+)?(?P<type>html|web)\s+website\b",
        r"\b(?:create|build)\s+(?:a\s+)?(?P<type>frontend)\s+dashboard\b",
        r"\b(?:build\s+a\s+portfolio\s+website)\b",
    ]

    def __init__(self) -> None:
        self._compiled_time = [re.compile(p, re.IGNORECASE) for p in self.TIME_PATTERNS]
        self._compiled_system = [re.compile(p, re.IGNORECASE) for p in self.SYSTEM_PATTERNS]
        self._compiled_system_info = [re.compile(p, re.IGNORECASE) for p in self.SYSTEM_INFO_PATTERNS]
        self._compiled_workspace_prep = [
            re.compile(p, re.IGNORECASE) for p in self.WORKSPACE_PREP_PATTERNS
        ]
        self._compiled_project_creation = [
            re.compile(p, re.IGNORECASE) for p in self.PROJECT_CREATION_PATTERNS
        ]

    def _strip_invocation_prefix(self, text: str) -> str:
        """Strip conversational address prefix such as 'RYVEN, ' or 'Jarvis '."""
        clean = text.strip()
        clean = re.sub(r"^(?:ryven|jarvis)[,\s:]+", "", clean, flags=re.IGNORECASE).strip()
        return clean

    def route(self, message: str) -> RouteDecision:
        """Analyze message intent and route to appropriate handler."""
        raw_text = message.strip()
        clean_text = self._strip_invocation_prefix(raw_text)

        # 0. Check for Multi-Step Workflows (Phase 4 & 4.1)
        for pattern in self._compiled_workspace_prep:
            if pattern.search(clean_text):
                decision = RouteDecision(
                    intent="workflow",
                    workflow_name="workspace_prep",
                    reason="Matched development workspace preparation workflow request",
                )
                logger.info(f"Router decision: workflow='workspace_prep' for message='{raw_text[:40]}...'")
                return decision

        for pattern in self._compiled_project_creation:
            match = pattern.search(clean_text)
            if match:
                g = match.groupdict()
                decision = RouteDecision(
                    intent="workflow",
                    workflow_name="create_project",
                    tool_arguments={
                        "project_name": g.get("name", "my_app"),
                        "project_type": g.get("type", "web"),
                    },
                    reason="Matched project builder workflow request",
                )
                logger.info(f"Router decision: workflow='create_project' for message='{raw_text[:40]}...'")
                return decision

        # 1. Clipboard Write (Set)
        for pattern in self.CLIPBOARD_SET_PATTERNS:
            match = pattern.search(clean_text)
            if match:
                text_to_copy = match.group(1).strip().strip("'\"")
                if text_to_copy:
                    decision = RouteDecision(
                        intent="tool",
                        tool_name="set_clipboard",
                        tool_arguments={"text": text_to_copy},
                        reason="Matched clipboard write request",
                    )
                    logger.info(f"Router decision: tool='set_clipboard' for message='{raw_text[:40]}...'")
                    return decision

        # 2. Clipboard Read (Get)
        if self.CLIPBOARD_GET_PATTERN.search(clean_text):
            decision = RouteDecision(
                intent="tool",
                tool_name="get_clipboard",
                tool_arguments={},
                reason="Matched clipboard read request",
            )
            logger.info(f"Router decision: tool='get_clipboard' for message='{raw_text[:40]}...'")
            return decision

        # 3. Open Specific File (recently found or named)
        open_file_match = self.OPEN_FILE_PATTERN.search(clean_text)
        if open_file_match:
            target_path = (open_file_match.group(1) or "").strip()
            decision = RouteDecision(
                intent="tool",
                tool_name="open_file",
                tool_arguments={"path": target_path},
                reason="Matched open file request",
            )
            logger.info(f"Router decision: tool='open_file' target='{target_path}'")
            return decision

        # 4. Search Files
        search_match = self.SEARCH_FILES_PATTERN.search(clean_text)
        if search_match:
            query = search_match.group(1).strip()
            ext = f".{query.lower()}" if not query.startswith(".") else query.lower()
            decision = RouteDecision(
                intent="tool",
                tool_name="search_files",
                tool_arguments={"query": query, "extension": ext},
                reason=f"Matched file search query for '{query}'",
            )
            logger.info(f"Router decision: tool='search_files' query='{query}'")
            return decision

        # 5. Open Folder
        folder_match = self.FOLDER_PATTERN.search(clean_text)
        if folder_match:
            folder_raw = folder_match.group(1).lower().strip()
            folder_name = "workspace" if "project" in folder_raw or "workspace" in folder_raw else folder_raw
            decision = RouteDecision(
                intent="tool",
                tool_name="open_folder",
                tool_arguments={"folder": folder_name},
                reason=f"Matched approved folder request: '{folder_name}'",
            )
            logger.info(f"Router decision: tool='open_folder' folder='{folder_name}'")
            return decision

        # 6. Open Application (VS Code, Chrome, Notepad, Calculator, Terminal)
        app_match = self.APP_PATTERN.search(clean_text)
        if app_match:
            raw_app = app_match.group(1).lower()
            app_name = "vscode" if raw_app in ("vs code", "vscode", "visual studio code", "code") else raw_app
            decision = RouteDecision(
                intent="tool",
                tool_name="open_application",
                tool_arguments={"application": app_name},
                reason=f"Matched approved application launch: '{app_name}'",
            )
            logger.info(f"Router decision: tool='open_application' app='{app_name}'")
            return decision

        # 7. Open Website (GitHub, YouTube, Google, etc. or explicit URL)
        web_match = self.WEBSITE_PATTERN.search(clean_text)
        if web_match:
            dest_url = web_match.group(1).strip()
            decision = RouteDecision(
                intent="tool",
                tool_name="open_website",
                tool_arguments={"url": dest_url},
                reason=f"Matched approved website request: '{dest_url}'",
            )
            logger.info(f"Router decision: tool='open_website' url='{dest_url}'")
            return decision

        # 8. Check for time queries
        for pattern in self._compiled_time:
            if pattern.search(clean_text):
                decision = RouteDecision(
                    intent="tool",
                    tool_name="time",
                    tool_arguments={},
                    reason="Matched time/date query pattern",
                )
                logger.info(f"Router decision: tool='time' for message='{raw_text[:40]}...'")
                return decision

        # 9. Check for system status queries (CPU / RAM / Disk telemetry)
        for pattern in self._compiled_system:
            if pattern.search(clean_text):
                decision = RouteDecision(
                    intent="tool",
                    tool_name="system_status",
                    tool_arguments={},
                    reason="Matched system telemetry query pattern",
                )
                logger.info(f"Router decision: tool='system_status' for message='{raw_text[:40]}...'")
                return decision

        # 10. Check for system information queries (Platform / OS version / hostname)
        for pattern in self._compiled_system_info:
            if pattern.search(clean_text):
                decision = RouteDecision(
                    intent="tool",
                    tool_name="system_info",
                    tool_arguments={},
                    reason="Matched safe system information query pattern",
                )
                logger.info(f"Router decision: tool='system_info' for message='{raw_text[:40]}...'")
                return decision

        # 11. Default: Route to AI Provider
        decision = RouteDecision(
            intent="ai",
            tool_name=None,
            tool_arguments={},
            reason="General knowledge or conversational query delegated to AI engine",
        )
        logger.info(f"Router decision: AI engine for message='{raw_text[:40]}...'")
        return decision
