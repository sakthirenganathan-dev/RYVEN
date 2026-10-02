"""Central Assistant core orchestrating routing, tool execution, safety guards, workflows, and local AI reasoning."""

from typing import Any, Dict, Optional
from app.ai.ollama import OllamaProvider, OllamaUnavailableError
from app.ai.provider import AIProvider
from app.core.context import ConversationManager
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard, safety_guard
from app.core.prompts import get_system_prompt
from app.core.router import IntentRouter
from app.schemas.messages import ChatResponse
from app.tools.app_tool import OpenApplicationTool
from app.tools.clipboard_tool import GetClipboardTool, SetClipboardTool
from app.tools.file_tool import OpenFileTool
from app.tools.folder_tool import OpenFolderTool
from app.tools.registry import ToolRegistry
from app.tools.search_tool import SearchFilesTool
from app.tools.system_info_tool import SystemInfoTool
from app.tools.system_tool import SystemStatusTool
from app.tools.time_tool import TimeTool
from app.tools.website_tool import OpenWebsiteTool
from app.workflows.engine import WorkflowEngine


class Assistant:
    """Central intelligence core coordinating message understanding, tools, workflows, and AI generation."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        ai_provider: Optional[AIProvider] = None,
        router: Optional[IntentRouter] = None,
        context_manager: Optional[ConversationManager] = None,
        system_prompt: Optional[str] = None,
        safety: Optional[SafetyGuard] = None,
        workflow_engine: Optional[WorkflowEngine] = None,
    ) -> None:
        # 1. Safe Tool Registry with all registered tools
        if registry is None:
            from app.tools.registry import create_default_registry
            self.registry = create_default_registry()
        else:
            self.registry = registry

        # 2. AI Provider (Ollama / Local Qwen)
        self.ai_provider = ai_provider or OllamaProvider()

        # 3. Intent Router
        self.router = router or IntentRouter()

        # 4. Short-term in-memory conversation context
        self.context_manager = context_manager or ConversationManager()

        # 5. RYVEN Persona & System Identity
        self.system_prompt = system_prompt or get_system_prompt()

        # 6. Safety & Permission Guard
        self.safety_guard = safety or safety_guard

        # 7. Workflow Engine (Phase 4)
        self.workflow_engine = workflow_engine or WorkflowEngine(
            registry=self.registry,
            guard=self.safety_guard,
        )

        logger.info(
            f"Assistant online. AI Provider: {self.ai_provider.provider_name} | "
            f"Registered Tools: {self.registry.list_tools()}"
        )

    async def process(
        self,
        message: str,
        session_id: str = "default",
        auto_confirm: bool = True,
    ) -> ChatResponse:
        """Process incoming user message through security validation, intent classification, workflows, tools, or AI with context."""
        clean_text = message.strip()
        logger.info(f"Incoming message received (session='{session_id}', length={len(clean_text)})")

        if not clean_text:
            return ChatResponse(
                success=False,
                type="error",
                message="Message cannot be empty.",
                tool=None,
                metadata={"error_code": "EMPTY_MESSAGE"},
            )

        # 0. Global Security & Prompt Injection Check
        blocked_reason = self.safety_guard.is_blocked_instruction(clean_text)
        if blocked_reason:
            logger.warning(f"Security Alert: Blocked instruction detected: {blocked_reason}")
            return ChatResponse(
                success=False,
                type="error",
                message=f"Action blocked by RYVEN security policy: {blocked_reason}",
                tool=None,
                metadata={"security_status": "BLOCKED", "risk_level": "blocked"},
            )

        # 1. Evaluate intent
        decision = self.router.route(clean_text)

        # 2. Workflow Execution Path (Phase 4 & 4.1)
        if decision.intent == "workflow":
            logger.info(f"Executing workflow for intent: '{decision.workflow_name}'")
            wf_result = await self.workflow_engine.run_from_query(
                clean_text, auto_confirm=auto_confirm
            )
            if wf_result:
                display_message = wf_result.message
                self.context_manager.add_user_message(session_id, clean_text)
                self.context_manager.add_assistant_message(session_id, display_message)

                return ChatResponse(
                    success=wf_result.success,
                    type="workflow",
                    message=display_message,
                    tool=None,
                    metadata={
                        "workflow_id": wf_result.workflow_id,
                        "name": wf_result.name,
                        "status": wf_result.status.value,
                        "steps_total": wf_result.steps_total,
                        "steps_completed": wf_result.steps_completed,
                        "steps_failed": wf_result.steps_failed,
                        "steps": wf_result.step_details,
                        "error": wf_result.error,
                        **wf_result.metadata,
                    },
                )

            logger.warning("Workflow planning returned None. Falling back to AI.")

        # 3. Tool Execution Path (Phase 3)
        if decision.intent == "tool" and decision.tool_name:
            # Permission check before executing tool
            perm = self.safety_guard.validate_action(
                tool_name=decision.tool_name,
                arguments=decision.tool_arguments,
                raw_query=clean_text,
            )

            if not perm.allowed:
                logger.warning(f"Permission denied for tool '{decision.tool_name}': {perm.reason}")
                return ChatResponse(
                    success=False,
                    type="error",
                    message=f"Permission denied: {perm.reason}",
                    tool=decision.tool_name,
                    metadata={"security_status": "BLOCKED", "risk_level": perm.risk_level},
                )

            tool = self.registry.get(decision.tool_name)
            if tool:
                logger.info(f"Executing registered tool: '{tool.name}' with args {decision.tool_arguments}")
                try:
                    tool_output = await self.registry.execute_tool(
                        name=tool.name,
                        arguments=decision.tool_arguments,
                    )
                    display_message = tool_output.get(
                        "message", f"Tool {tool.name} executed successfully."
                    )
                    is_success = tool_output.get("success", True)

                    # Update session history with the tool interaction
                    self.context_manager.add_user_message(session_id, clean_text)
                    self.context_manager.add_assistant_message(session_id, display_message)

                    return ChatResponse(
                        success=is_success,
                        type="tool",
                        message=display_message,
                        tool=tool.name,
                        metadata=tool_output,
                    )
                except Exception as exc:
                    logger.error(f"Error executing tool '{tool.name}': {exc}", exc_info=True)
                    return ChatResponse(
                        success=False,
                        type="error",
                        message=f"An error occurred while executing tool '{tool.name}'.",
                        tool=tool.name,
                        metadata={"error_code": "TOOL_EXECUTION_ERROR"},
                    )

            logger.warning(
                f"Tool '{decision.tool_name}' determined by router is not registered. Falling back to AI."
            )

        # 4. AI Generation Path (Local Ollama / Qwen)
        # Retrieve recent conversation history for this session
        history = self.context_manager.get_history(session_id)

        logger.info(
            f"Dispatching AI completion to {self.ai_provider.provider_name} with {len(history)} prior message(s)"
        )
        try:
            ai_response = await self.ai_provider.generate(
                prompt=clean_text,
                system_prompt=self.system_prompt,
                messages=history,
            )

            # Record turn in conversation history
            self.context_manager.add_user_message(session_id, clean_text)
            self.context_manager.add_assistant_message(session_id, ai_response.content)

            return ChatResponse(
                success=True,
                type="ai",
                message=ai_response.content,
                tool=None,
                metadata={
                    "model": ai_response.model,
                    "provider": ai_response.provider,
                    **ai_response.metadata,
                },
            )

        except OllamaUnavailableError as exc:
            logger.error(f"AI Provider error: {exc}")
            return ChatResponse(
                success=False,
                type="error",
                message=str(exc),
                tool=None,
                metadata={
                    "error_type": "AI_PROVIDER_UNAVAILABLE",
                    "provider": self.ai_provider.provider_name,
                },
            )

        except Exception as exc:
            logger.error(f"Unexpected error during AI generation: {exc}", exc_info=True)
            return ChatResponse(
                success=False,
                type="error",
                message="RYVEN encountered an unexpected internal error during response generation.",
                tool=None,
                metadata={"error_type": "AI_EXECUTION_FAILURE"},
            )
