"""Intent routing mechanism for RYVEN requests."""

import re
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field
from app.core.logging_config import logger


class RouteDecision(BaseModel):
    """Decision indicating whether to execute a tool, a workflow, or delegate to the AI engine."""

    intent: Literal["tool", "ai", "workflow", "agent"]
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
        r"\b(?:open|launch|start|run)\s+(?:the\s+)?(vs\s+code|vscode|visual\s+studio\s+code|code|chrome|google\s+chrome|browser|notepad|calculator|calc|windows\s+terminal|terminal|wt|file\s+explorer|explorer)\b",
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

    # M14.3 Controlled Browser Automation Patterns
    BROWSER_NAVIGATE_PATTERN = re.compile(
        r"\b(?:navigate\s+(?:the\s+)?browser|browse)\s+(?:to\s+)?(https?://[^\s]+|[a-zA-Z0-9\.\-]+\.[a-zA-Z]{2,}[^\s]*)\b",
        re.IGNORECASE,
    )
    BROWSER_READ_PATTERN = re.compile(
        r"\b(?:read|extract|get)\s+(?:the\s+)?(?:visible\s+)?(?:page\s+content|web\s*page|current\s+page)\b",
        re.IGNORECASE,
    )
    BROWSER_SNAPSHOT_PATTERN = re.compile(
        r"\b(?:take\s+)?(?:a\s+)?browser\s+snapshot\b",
        re.IGNORECASE,
    )
    BROWSER_CLOSE_PATTERN = re.compile(
        r"\b(?:close|quit)\s+(?:the\s+)?browser\b",
        re.IGNORECASE,
    )
    BROWSER_BACK_PATTERN = re.compile(
        r"^\s*(?:go\s+back|browser\s+back|go\s+to\s+previous\s+page)\s*$",
        re.IGNORECASE,
    )
    BROWSER_FORWARD_PATTERN = re.compile(
        r"^\s*(?:go\s+forward|return\s+forward|browser\s+forward|go\s+to\s+next\s+page)\s*$",
        re.IGNORECASE,
    )
    BROWSER_REFRESH_PATTERN = re.compile(
        r"^\s*(?:refresh(?:\s+the)?\s+(?:page|browser)|reload(?:\s+the)?\s+(?:page|browser))\s*$",
        re.IGNORECASE,
    )

    # Existing project modification patterns (M8 / M8.5)
    # Must NOT match new-project creation or plain educational questions.
    EXISTING_PROJECT_MOD_PATTERNS = [
        r"\b(?:modify|update|change|refactor|improve|enhance)\s+(?:my\s+|the\s+|an?\s+)?(?:existing\s+)?(?:react|python|fastapi|web|html|node|frontend|vanilla\s+)?(?P<modname>[a-zA-Z0-9_\-]+)?\s*project\b",
        r"\b(?:add|implement|integrate)\s+.+?\s+(?:to|into|in)\s+(?:my\s+|the\s+|an?\s+)?(?:existing\s+)?(?:[a-zA-Z0-9_\-]+\s+)?(?:app|project|application|site)\b",
        r"\b(?:fix|repair|resolve|debug)\s+.+?\s+(?:in|inside|within)\s+(?:my\s+|the\s+)?(?:existing\s+)?(?:[a-zA-Z0-9_\-]+\s+)?(?:app|project|application|site)\b",
        r"\b(?:modify|update|change|improve)\s+(?:my\s+|the\s+)?(?:existing\s+)(?:react|python|fastapi|web|html|node)?\s*(?:app|application|project|site)\b",
        r"\b(?:add|implement)\s+(?:dark\s+mode|login\s+page|auth(?:entication)?|dashboard|navbar|routing|api\s+endpoint|rest\s+api)\s+(?:to|into)\s+(?:my\s+|the\s+)?(?:existing\s+)?(?:react|python|fastapi|web|html)?\s*(?:app|project|application|site)\b",
        r"\bopen\s+(?:my\s+)?(?P<modname>[a-zA-Z0-9_\-]+)\s+project\s+and\s+(?:add|modify|update|fix|change)\b",
        r"\b(?:change|update|fix|improve)\s+(?:the\s+)?(?:navbar|header|footer|sidebar|theme|login|ui|design)\b",
        r"\b(?:fix\s+(?:the\s+)?build\s+errors?)\b",
        r"\b(?:improve|update)\s+(?:the\s+)?(?:existing\s+)?(?:project\s+)?ui\b",
    ]

    # Educational query patterns — must route to AI, never workflow or Git/deployment tools
    EDUCATIONAL_PATTERNS = [
        r"^(?:what\s+is\s+(?:react|vite|python|fastapi|html|css|javascript|typescript|auth|authentication|docker|git|node|tailwind|deployment|vercel|render|railway)\b)",
        r"^(?:what\s+is\s+git\s+(?:commit|push|branch|status|diff|rebase|merge|remote|pull)\b)",
        r"^(?:what\s+are\s+(?:react|vite|components|props|hooks|states|git\s+branches)\b)",
        r"^(?:how\s+does\s+(?:vite|react|auth|authentication|git|node|fastapi|deployment|vercel|render|railway)\s+work\b)",
        r"^(?:how\s+does\s+git\s+(?:commit|push|branch|merge|rebase)\s+work\b)",
        r"^(?:explain\s+(?:authentication|auth|react|vite|routing|docker|git|fastapi|deployment|vercel|render|railway)\b)",
        r"^(?:explain\s+git\s+(?:commit|push|branch|status|diff|rebase|merge)\b)",
        r"^(?:what\s+is\s+(?:an?\s+)?(?:http\s+)?health\s+check\b)",
        r"^(?:what\s+is\s+(?:an?\s+)?(?:deployment\s+)?verification\b)",
        r"^(?:how\s+does\s+(?:a\s+)?(?:health\s+check|deployment\s+verification|http\s+probe)\s+work\b)",
        r"^(?:explain\s+(?:health\s+checks?|deployment\s+verification|http\s+probes?)\b)",
        r"^(?:what\s+is\s+(?:an?\s+)?(?:autonomous\s+)?(?:development\s+)?orchestrat(?:or|ion)\b)",
        r"^(?:how\s+does\s+(?:the\s+)?(?:autonomous\s+)?orchestrat(?:or|ion)\s+work\b)",
        r"^(?:explain\s+(?:the\s+)?(?:autonomous\s+)?orchestrat(?:or|ion)\b)",
    ]

    # M14 Orchestration Patterns
    ORCHESTRATION_COMPOSITE_PATTERNS = [
        # Explicit orchestrate keyword
        r"\b(?:orchestrate|run\s+orchestrat(?:or|ion)|autonomous(?:ly)?\s+dev(?:elop)?)\b",
        # Full chain: add/modify/change/refactor/fix ... and commit/push/deploy/ship
        r"\b(?:add|implement|modify|update|change|refactor|fix)\b.+?\b(?:and\s+)?(?:commit|push|deploy|ship)\b",
        # Build/test ... and commit/push/deploy/ship
        r"\b(?:build|test)\b.+?\b(?:and\s+)?(?:commit|push|deploy|ship)\b",
        # "test it, commit it, deploy it"
        r"\b(?:test\s+it,\s*commit\s+it,\s*deploy\s+it)\b",
    ]

    ORCHESTRATION_CONTROL_PATTERNS = [
        (r"\b(?:pause\s+(?:the\s+)?orchestrat(?:or|ion)|pause\s+task)\b", "pause_orchestration"),
        (r"\b(?:resume\s+(?:the\s+)?orchestrat(?:or|ion)|resume\s+task)\b", "resume_orchestration"),
        (r"\b(?:cancel\s+(?:the\s+)?orchestrat(?:or|ion)|cancel\s+orchestrated\s+task|abort\s+orchestrat(?:or|ion))\b", "cancel_orchestration"),
        (r"\b(?:confirm\s+(?:the\s+)?orchestrat(?:or|ion)|confirm\s+orchestrated\s+step|approve\s+orchestrat(?:or|ion))\b", "confirm_orchestration"),
        (r"\b(?:(?:check|get|show)\s+(?:the\s+)?orchestrat(?:or|ion)\s+status|orchestrat(?:or|ion)\s+status)\b", "get_orchestration_status"),
    ]

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
        self._compiled_existing_mod = [
            re.compile(p, re.IGNORECASE) for p in self.EXISTING_PROJECT_MOD_PATTERNS
        ]
        self._compiled_educational = [
            re.compile(p, re.IGNORECASE) for p in self.EDUCATIONAL_PATTERNS
        ]
        self._compiled_orchestration_composite = [
            re.compile(p, re.IGNORECASE) for p in self.ORCHESTRATION_COMPOSITE_PATTERNS
        ]
        self._compiled_orchestration_control = [
            (re.compile(p, re.IGNORECASE), tool) for p, tool in self.ORCHESTRATION_CONTROL_PATTERNS
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

        # Educational queries (e.g. "What is React?", "Explain authentication", "How does Vite work?")
        # Must always route to AI, never triggering modification or creation workflows.
        for pattern in self._compiled_educational:
            if pattern.search(clean_text):
                logger.info(f"Router decision: ai (educational query) for '{raw_text[:40]}...'")
                return RouteDecision(
                    intent="ai",
                    reason="Educational query routed to AI provider",
                )

        # 0. Autonomous Development Orchestrator (M14)
        # 0.1 Direct orchestration controls (pause, resume, cancel, confirm, status)
        for pattern, tool_name in self._compiled_orchestration_control:
            if pattern.search(clean_text):
                m_task = re.search(r"\b(?:task(?:_id)?|id)[:=\s]+([a-zA-Z0-9_\-]+)\b", clean_text, re.IGNORECASE)
                task_id = m_task.group(1).strip() if m_task else ""
                args = {"task_id": task_id}
                if tool_name == "confirm_orchestration":
                    args["confirmed"] = not bool(re.search(r"\b(?:reject|deny|cancel)\b", clean_text, re.IGNORECASE))
                decision = RouteDecision(
                    intent="tool",
                    tool_name=tool_name,
                    tool_arguments=args,
                    reason=f"Matched orchestration control request for '{tool_name}'",
                )
                logger.info(f"Router decision: tool='{tool_name}' for message='{raw_text[:40]}...'")
                return decision

        # 0.2 Composite multi-stage development goal (checked BEFORE single-stage modification)
        for pattern in self._compiled_orchestration_composite:
            if pattern.search(clean_text):
                p_name = self._extract_git_project(clean_text)
                decision = RouteDecision(
                    intent="tool",
                    tool_name="orchestrate_task",
                    tool_arguments={
                        "goal": clean_text,
                        "project_name": p_name,
                        "project_path": "",
                    },
                    reason="Matched autonomous development orchestration request",
                )
                logger.info(f"Router decision: tool='orchestrate_task' for message='{raw_text[:40]}...'")
                return decision

        # 0a. Existing project modification (M8 / M8.5) — checked BEFORE new-project creation
        for pattern in self._compiled_existing_mod:
            match = pattern.search(clean_text)
            if match:
                g = match.groupdict()
                project_name_hint = g.get("modname", "") or ""
                decision = RouteDecision(
                    intent="workflow",
                    workflow_name="modify_existing_project",
                    tool_arguments={
                        "project_name_hint": project_name_hint,
                        "user_request": clean_text,
                    },
                    reason="Matched existing project modification request",
                )
                logger.info(
                    f"Router decision: workflow='modify_existing_project' for message='{raw_text[:40]}...'"
                )
                return decision

        # 0b. Check for Multi-Step Workflows (Phase 4 & 4.1)
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

        # 5.5 Composite Multi-Step Agent Goal (RYVEN 3.0 Control Plane)
        from app.agent.planner import AgentPlanner
        if AgentPlanner.is_composite_goal(clean_text) or re.search(r"\bsearch\s+(?:youtube|google)\s+(?:for\s+)?", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="agent",
                reason=f"Matched composite multi-step agent goal: '{clean_text[:50]}...'",
            )
            logger.info(f"Router decision: agent for message='{raw_text[:40]}...'")
            return decision

        # 5.6 RYVEN 3.0 Unified Internet Agent Direct Capabilities
        m_research = re.search(r"^\b(?:research|deep\s+research)\s+(?:the\s+)?(?:latest\s+)?(.+)$", clean_text, re.IGNORECASE)
        if m_research:
            topic = m_research.group(1).strip()
            topic = re.sub(r"[.?!]+$", "", topic).strip()
            decision = RouteDecision(
                intent="tool",
                tool_name="web_research",
                tool_arguments={"topic": topic, "max_sources": 3},
                reason=f"Matched web research request for '{topic}'",
            )
            logger.info(f"Router decision: tool='web_research' topic='{topic}'")
            return decision

        m_search = re.search(r"^\b(?:internet\s+search|web\s+search|search\s+(?:the\s+)?(?:web|internet)\s+for)\s+(.+)$", clean_text, re.IGNORECASE)
        if m_search:
            q = m_search.group(1).strip()
            q = re.sub(r"[.?!]+$", "", q).strip()
            decision = RouteDecision(
                intent="tool",
                tool_name="internet_search",
                tool_arguments={"query": q, "max_results": 5},
                reason=f"Matched internet search request for '{q}'",
            )
            logger.info(f"Router decision: tool='internet_search' query='{q}'")
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

        # 7.5 M14.3 Controlled Browser Automation Routing
        b_nav = self.BROWSER_NAVIGATE_PATTERN.search(clean_text)
        if b_nav:
            target_url = b_nav.group(1).strip()
            decision = RouteDecision(
                intent="tool",
                tool_name="navigate_browser",
                tool_arguments={"url": target_url},
                reason=f"Matched controlled browser navigation to '{target_url}'",
            )
            logger.info(f"Router decision: tool='navigate_browser' url='{target_url}'")
            return decision

        if self.BROWSER_READ_PATTERN.search(clean_text):
            decision = RouteDecision(
                intent="tool",
                tool_name="read_page",
                tool_arguments={},
                reason="Matched controlled browser read page request",
            )
            logger.info("Router decision: tool='read_page'")
            return decision

        if self.BROWSER_SNAPSHOT_PATTERN.search(clean_text):
            decision = RouteDecision(
                intent="tool",
                tool_name="take_browser_snapshot",
                tool_arguments={},
                reason="Matched controlled browser snapshot request",
            )
            logger.info("Router decision: tool='take_browser_snapshot'")
            return decision

        if self.BROWSER_CLOSE_PATTERN.search(clean_text):
            decision = RouteDecision(
                intent="tool",
                tool_name="close_browser",
                tool_arguments={},
                reason="Matched controlled browser close request",
            )
            logger.info("Router decision: tool='close_browser'")
            return decision

        if self.BROWSER_BACK_PATTERN.search(clean_text):
            decision = RouteDecision(
                intent="tool",
                tool_name="go_back",
                tool_arguments={},
                reason="Matched browser go back request",
            )
            logger.info("Router decision: tool='go_back'")
            return decision

        if self.BROWSER_FORWARD_PATTERN.search(clean_text):
            decision = RouteDecision(
                intent="tool",
                tool_name="go_forward",
                tool_arguments={},
                reason="Matched browser go forward request",
            )
            logger.info("Router decision: tool='go_forward'")
            return decision

        if self.BROWSER_REFRESH_PATTERN.search(clean_text):
            decision = RouteDecision(
                intent="tool",
                tool_name="refresh_page",
                tool_arguments={},
                reason="Matched browser refresh page request",
            )
            logger.info("Router decision: tool='refresh_page'")
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

        # 10b. Git Operations (Milestone 11)
        p_name = self._extract_git_project(clean_text)

        # Commit
        if re.search(r"\b(?:commit\s+(?:these\s+|the\s+|my\s+)?changes|create\s+(?:a\s+)?commit|make\s+(?:a\s+)?commit|git\s+commit)\b", clean_text, re.IGNORECASE):
            msg = self._extract_commit_message(clean_text)
            decision = RouteDecision(
                intent="tool",
                tool_name="git_commit",
                tool_arguments={"project_name": p_name, "message": msg, "confirmed": False},
                reason="Matched Git commit request",
            )
            logger.info(f"Router decision: tool='git_commit' for message='{raw_text[:40]}...'")
            return decision

        # Push
        if re.search(r"\b(?:push\s+(?:this\s+project|my\s+changes|these\s+changes|the\s+project|to\s+github|to\s+remote|to\s+origin)|git\s+push)\b", clean_text, re.IGNORECASE):
            remote = "origin"
            m_rem = re.search(r"\bto\s+(origin|upstream|[a-zA-Z0-9_\-]+)\b", clean_text, re.IGNORECASE)
            if m_rem and m_rem.group(1).lower() not in ("github", "the", "my", "remote"):
                remote = m_rem.group(1)
            decision = RouteDecision(
                intent="tool",
                tool_name="git_push",
                tool_arguments={"project_name": p_name, "remote": remote, "confirmed": False},
                reason="Matched Git push request",
            )
            logger.info(f"Router decision: tool='git_push' for message='{raw_text[:40]}...'")
            return decision

        # Diff / What changed
        if re.search(r"\b(?:show\s+(?:me\s+)?what\s+changed|what\s+changed|show\s+git\s+diff|git\s+diff|show\s+(?:the\s+)?diff)\b", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="tool",
                tool_name="git_diff",
                tool_arguments={"project_name": p_name},
                reason="Matched Git diff request",
            )
            logger.info(f"Router decision: tool='git_diff' for message='{raw_text[:40]}...'")
            return decision

        # Branch
        if re.search(r"\b(?:what\s+branch\s+am\s+i\s+on|show\s+(?:my\s+)?branch(?:es)?|show\s+git\s+branch(?:es)?|git\s+branch)\b", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="tool",
                tool_name="git_branch",
                tool_arguments={"project_name": p_name},
                reason="Matched Git branch request",
            )
            logger.info(f"Router decision: tool='git_branch' for message='{raw_text[:40]}...'")
            return decision

        # Remote
        if re.search(r"\b(?:show\s+(?:my\s+)?(?:git\s+)?remotes?|git\s+remote)\b", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="tool",
                tool_name="git_remote",
                tool_arguments={"project_name": p_name},
                reason="Matched Git remote request",
            )
            logger.info(f"Router decision: tool='git_remote' for message='{raw_text[:40]}...'")
            return decision

        # Log
        if re.search(r"\b(?:show\s+(?:my\s+)?(?:git\s+)?commits|show\s+git\s+log|git\s+log|recent\s+commits)\b", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="tool",
                tool_name="git_log",
                tool_arguments={"project_name": p_name},
                reason="Matched Git log request",
            )
            logger.info(f"Router decision: tool='git_log' for message='{raw_text[:40]}...'")
            return decision

        # Stage
        if re.search(r"\b(?:stage\s+(?:these\s+|the\s+)?files?|stage\s+changes|git\s+add|git\s+stage)\b", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="tool",
                tool_name="git_stage",
                tool_arguments={"project_name": p_name, "files": []},
                reason="Matched Git stage request",
            )
            logger.info(f"Router decision: tool='git_stage' for message='{raw_text[:40]}...'")
            return decision

        # Unstage
        if re.search(r"\b(?:unstage\s+(?:these\s+|the\s+)?files?|unstage\s+changes|git\s+unstage|git\s+restore\s+--staged)\b", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="tool",
                tool_name="git_unstage",
                tool_arguments={"project_name": p_name},
                reason="Matched Git unstage request",
            )
            logger.info(f"Router decision: tool='git_unstage' for message='{raw_text[:40]}...'")
            return decision

        # Status (Git / Project status)
        if re.search(r"\b(?:(?:show|check|what(?:\s+is|\s*'?s)?)(?:\s+me)?\s+(?:my\s+)?(?:current\s+)?(?:project|git)\s+status|git\s+status|check\s+git|project\s+status)\b", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="tool",
                tool_name="git_status",
                tool_arguments={"project_name": p_name},
                reason="Matched Git / Project status request",
            )
            logger.info(f"Router decision: tool='git_status' for message='{raw_text[:40]}...'")
            return decision

        # 10c. Deployment Operations (Milestone 12)
        if re.search(r"\b(?:deploy\s+(?:this\s+)?(?:project|app|application)?|publish\s+(?:this\s+)?(?:project|app)?|put\s+this\s+online|ship\s+(?:this|the\s+app|the\s+project))\b", clean_text, re.IGNORECASE):
            provider = "VERCEL"
            if "railway" in clean_text.lower():
                provider = "RAILWAY"
            elif "render" in clean_text.lower():
                provider = "RENDER"
            decision = RouteDecision(
                intent="workflow",
                workflow_name="deployment",
                tool_arguments={"project_name": p_name, "provider": provider},
                reason="Matched project deployment request",
            )
            logger.info(f"Router decision: workflow='deployment' for message='{raw_text[:40]}...'")
            return decision

        # Deployment preflight direct tool
        if re.search(r"\b(?:run\s+(?:deployment\s+)?preflight|check\s+deployment\s+preflight|deployment\s+preflight)\b", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="tool",
                tool_name="deployment_preflight",
                tool_arguments={"project_name": p_name},
                reason="Matched deployment preflight check request",
            )
            logger.info(f"Router decision: tool='deployment_preflight' for message='{raw_text[:40]}...'")
            return decision

        # Deployment detect direct tool
        if re.search(r"\b(?:detect\s+(?:project\s+)?framework|detect\s+deployment|check\s+framework)\b", clean_text, re.IGNORECASE):
            decision = RouteDecision(
                intent="tool",
                tool_name="deployment_detect",
                tool_arguments={"project_name": p_name},
                reason="Matched deployment framework detection request",
            )
            logger.info(f"Router decision: tool='deployment_detect' for message='{raw_text[:40]}...'")
            return decision

        # 10d. Knowledge Graph Operations (Milestone 11.5)
        # Who calls <symbol>?
        m_callers = re.search(r"\b(?:who\s+calls|what\s+calls|find\s+callers\s+of)\s+([a-zA-Z0-9_\.]+)\b", clean_text, re.IGNORECASE)
        if m_callers:
            symbol = m_callers.group(1).strip()
            return RouteDecision(
                intent="tool",
                tool_name="graph_find_callers",
                tool_arguments={"project_path": p_name or ".", "target": symbol},
                reason=f"Matched graph caller search for '{symbol}'",
            )

        # What depends on <symbol>?
        m_deps_on = re.search(r"\b(?:what\s+depends\s+on|who\s+depends\s+on|find\s+dependents\s+of)\s+([a-zA-Z0-9_\.]+)\b", clean_text, re.IGNORECASE)
        if m_deps_on:
            target = m_deps_on.group(1).strip()
            return RouteDecision(
                intent="tool",
                tool_name="graph_find_dependents",
                tool_arguments={"project_path": p_name or ".", "target": target},
                reason=f"Matched graph dependents query for '{target}'",
            )

        # What does <target> depend on?
        m_deps_of = re.search(r"\b(?:what\s+does\s+([a-zA-Z0-9_\.]+)\s+depend\s+on|dependencies\s+of\s+([a-zA-Z0-9_\.]+))\b", clean_text, re.IGNORECASE)
        if m_deps_of:
            target = (m_deps_of.group(1) or m_deps_of.group(2)).strip()
            return RouteDecision(
                intent="tool",
                tool_name="graph_find_dependencies",
                tool_arguments={"project_path": p_name or ".", "target": target},
                reason=f"Matched graph dependencies query for '{target}'",
            )

        # Where is <symbol> defined/handled?
        m_where = re.search(r"\b(?:where\s+is\s+([a-zA-Z0-9_\.]+)\s+(?:defined|handled|implemented)|locate\s+symbol\s+([a-zA-Z0-9_\.]+))\b", clean_text, re.IGNORECASE)
        if m_where:
            symbol = (m_where.group(1) or m_where.group(2)).strip()
            return RouteDecision(
                intent="tool",
                tool_name="graph_find_symbol",
                tool_arguments={"project_path": p_name or ".", "symbol": symbol},
                reason=f"Matched graph symbol search for '{symbol}'",
            )

        # Graph status
        if re.search(r"\b(?:check\s+(?:the\s+)?(?:knowledge\s+)?graph\s+status|knowledge\s+graph\s+status|is\s+(?:the\s+)?graph\s+(?:ready|stale))\b", clean_text, re.IGNORECASE):
            return RouteDecision(
                intent="tool",
                tool_name="graph_status",
                tool_arguments={"project_path": p_name or "."},
                reason="Matched knowledge graph status check request",
            )

        # Build knowledge graph
        if re.search(r"\b(?:build\s+(?:the\s+)?knowledge\s+graph|index\s+project\s+(?:structure|graph)|create\s+knowledge\s+graph)\b", clean_text, re.IGNORECASE):
            return RouteDecision(
                intent="tool",
                tool_name="graph_build",
                tool_arguments={"project_path": p_name or ".", "force": False},
                reason="Matched knowledge graph build request",
            )

        # 10e. Health & Verification Operations (Milestone 13)
        m_health_url = re.search(r"https?://[^\s'\"]+", clean_text)
        target_health_url = m_health_url.group(0) if m_health_url else ""

        # Show health history
        if re.search(r"\b(?:show\s+(?:deployment\s+)?health\s+history|health\s+history|deployment\s+health\s+history)\b", clean_text, re.IGNORECASE):
            return RouteDecision(
                intent="tool",
                tool_name="health_history",
                tool_arguments={"url": target_health_url},
                reason="Matched health check history request",
            )

        # Stop monitoring
        if re.search(r"\b(?:stop\s+(?:health\s+)?(?:monitoring|monitor)|cancel\s+(?:health\s+)?(?:monitoring|monitor))\b", clean_text, re.IGNORECASE):
            return RouteDecision(
                intent="tool",
                tool_name="health_monitor_stop",
                tool_arguments={"url": target_health_url},
                reason="Matched health monitor stop request",
            )

        # Start monitoring
        if re.search(r"\b(?:monitor\s+(?:my\s+)?(?:deployment|endpoint|site|app)|start\s+(?:continuous\s+)?(?:health\s+)?monitor(?:ing)?)\b", clean_text, re.IGNORECASE):
            return RouteDecision(
                intent="tool",
                tool_name="health_monitor_start",
                tool_arguments={"url": target_health_url},
                reason="Matched health monitoring request",
            )

        # Deployment verification
        if re.search(r"\b(?:is\s+(?:my\s+)?deployed\s+(?:app|site|project)\s+working|check\s+(?:my\s+)?deployment|verify\s+(?:the\s+)?deployment|verify\s+(?:my\s+)?deployed\s+app)\b", clean_text, re.IGNORECASE):
            return RouteDecision(
                intent="tool",
                tool_name="deployment_verify",
                tool_arguments={"project_name": p_name, "url": target_health_url},
                reason="Matched deployment verification request",
            )

        # Health check / status / latency
        if re.search(r"\b(?:is\s+(?:the\s+)?(?:site|endpoint|app|deployment)\s+healthy|check\s+(?:the\s+)?(?:endpoint\s+)?health|health\s+check|how\s+fast\s+is\s+(?:my\s+)?(?:deployment|site|app|endpoint)\s+responding|ping\s+(?:the\s+)?(?:deployment|endpoint|site))\b", clean_text, re.IGNORECASE):
            return RouteDecision(
                intent="tool",
                tool_name="health_check",
                tool_arguments={"url": target_health_url},
                reason="Matched endpoint health check request",
            )

        # 11. Default: Route to AI Provider

        decision = RouteDecision(
            intent="ai",
            tool_name=None,
            tool_arguments={},
            reason="General knowledge or conversational query delegated to AI engine",
        )

        logger.info(f"Router decision: AI engine for message='{raw_text[:40]}...'")
        return decision

    @staticmethod
    def _extract_git_project(text: str) -> str:
        """Extract project name from a Git or orchestration query if specified."""
        m = re.search(r"\b(?:in|for|of|to|project)\s+(?:my\s+|the\s+)?['\"]?([a-zA-Z0-9_\-]+)['\"]?\b", text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if candidate.lower() not in (
                "git", "github", "the", "my", "this", "these", "project", "an", "a",
                "status", "current", "repo", "repository", "files", "workspace", "app"
            ):
                return candidate
        return ""

    @staticmethod
    def _extract_commit_message(text: str) -> str:
        """Extract commit message if enclosed in quotes or after a keyword."""
        m = re.search(r"""(?:message|with\s+message|-m)\s*[:=]?\s*['"]([^'"]+)['"]""", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m2 = re.search(r""":\s*['"]([^'"]+)['"]""", text)
        if m2:
            return m2.group(1).strip()
        return "Update project files"
