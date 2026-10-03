"""Pydantic data models for RYVEN 3.0 Unified Internet Agent Engine.

M15.3 Phase 2 additions:
    FormFieldInfo  — per-field schema extracted from DOM
    WebFormInfo    — structured per-<form> descriptor
    WebPageState   — unified read-only snapshot for inspection

SECURITY RULE: Password input *values* MUST NEVER enter WebPageState,
FormFieldInfo, ActionEvent metadata, logs, or API responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class WebTaskStatus(str, Enum):
    """Lifecycle status of a multi-step internet task."""
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_AUTHENTICATION = "WAITING_AUTHENTICATION"
    RETRYING = "RETRYING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AuthStatus(str, Enum):
    """Status of user authentication state on a target web application."""
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    IN_PROGRESS = "IN_PROGRESS"
    RESUMING = "RESUMING"
    AUTHENTICATED = "AUTHENTICATED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class SearchResult(BaseModel):
    """A single ranked web search result."""
    title: str
    url: str
    snippet: str
    domain: str = ""
    rank: int = 1
    score: float = 1.0
    source_info: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sanitized_content: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        if not self.domain and self.url:
            import urllib.parse
            self.domain = urllib.parse.urlsplit(self.url).netloc.lower()
        if not self.source_info:
            self.source_info = f"Source: {self.domain or 'web'}"
        if not self.sanitized_content:
            self.sanitized_content = self.snippet


class WebSource(BaseModel):
    """A collected and inspected web reference source."""
    url: str
    title: str
    snippet: str
    extracted_text: str = ""
    headings: List[str] = Field(default_factory=list)
    relevance_score: float = 1.0


class WebResearchResult(BaseModel):
    """Complete multi-source research report with structured citations."""
    query: str
    topic: Optional[str] = None
    summary: str
    sources: List[WebSource] = Field(default_factory=list)
    key_findings: List[str] = Field(default_factory=list)
    comparison_notes: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def model_post_init(self, __context: Any) -> None:
        if not self.topic:
            self.topic = self.query


class WebTaskStep(BaseModel):
    """A single atomic action in a multi-step web workflow."""
    step_id: str
    order: int
    name: str
    action_type: str
    target: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    status: str = "PENDING"
    requires_confirmation: bool = False
    observation: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 2


class WebTaskPlan(BaseModel):
    """Structured plan for an internet task following Observe -> Act -> Verify."""
    plan_id: str
    goal: str
    steps: List[WebTaskStep] = Field(default_factory=list)
    target_url: Optional[str] = None
    requires_authentication: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AuthSession(BaseModel):
    """Tracks an authentication-paused browser task."""
    session_id: str
    task_id: str
    target_service: str
    login_url: str
    auth_status: AuthStatus = AuthStatus.REQUIRED
    prompt_message: str
    resume_token: str
    detected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


class InternetAgentState(BaseModel):
    """Real-time observable state of the Unified Internet Agent."""
    status: WebTaskStatus = WebTaskStatus.IDLE
    current_task_id: Optional[str] = None
    current_goal: str = ""
    current_url: str = ""
    page_title: str = ""
    active_action: Optional[str] = None
    last_search_query: Optional[str] = None
    last_research_result: Optional[WebResearchResult] = None
    active_auth_session: Optional[AuthSession] = None
    pending_confirmation_token: Optional[str] = None
    confirmation_message: Optional[str] = None
    history_urls: List[str] = Field(default_factory=list)
    task_progress_pct: int = 0
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ===========================================================================
# M15.3 Phase 2 — Unified WebPageState & Form Extraction Models
# ===========================================================================


class FormFieldInfo(BaseModel):
    """Schema of a single input field extracted from a web form.

    SECURITY: `is_password` identifies password fields so callers know to
    handle them specially.  The *value* of password fields is NEVER populated
    anywhere in this model or in any parent model.
    """

    # --- Identity -----------------------------------------------------------------
    field_id: Optional[str] = None
    """The HTML `id` attribute of the field, if present."""

    name: Optional[str] = None
    """The HTML `name` attribute of the field."""

    input_type: str = "text"
    """Normalized HTML input type (text, email, password, checkbox, select, textarea …)."""

    # --- Label association (deterministic, in priority order) ---------------------
    label_text: Optional[str] = None
    """Human-readable label resolved via <label for>, aria-label, aria-labelledby,
    placeholder, or DOM proximity — whichever fires first."""

    placeholder: Optional[str] = None
    """HTML placeholder attribute, retained separately for UX context."""

    # --- Constraints & Validation -------------------------------------------------
    is_required: bool = False
    """True when the `required` attribute is present."""

    is_password: bool = False
    """True when input type is 'password'. Value is never captured."""

    is_readonly: bool = False
    """True when `readonly` or `disabled` attribute is present."""

    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    """HTML `pattern` attribute for client-side regex validation."""

    options: List[str] = Field(default_factory=list)
    """Pre-populated choices for <select>, <datalist>, or radio group fields."""

    # --- Positioning --------------------------------------------------------------
    tab_index: Optional[int] = None
    """Declared tab order for keyboard navigation."""

    def model_post_init(self, __context: Any) -> None:
        # Auto-derive is_password from input_type so that constructing
        # FormFieldInfo(input_type="password") always sets the flag correctly,
        # regardless of whether the caller set it explicitly.
        if self.input_type == "password":
            object.__setattr__(self, "is_password", True)


class WebFormInfo(BaseModel):
    """Structured descriptor of a single <form> element on the page."""

    form_id: Optional[str] = None
    """The HTML `id` attribute of the <form> element, if present."""

    form_name: Optional[str] = None
    """The HTML `name` attribute of the <form> element."""

    action: Optional[str] = None
    """The form `action` URL (submission target).  May be relative."""

    method: str = "get"
    """HTTP method (get / post).  Normalised to lowercase."""

    fields: List[FormFieldInfo] = Field(default_factory=list)
    """Ordered list of detected input fields inside this form."""

    submit_labels: List[str] = Field(default_factory=list)
    """Text labels of all submit buttons found inside the form."""

    has_password_field: bool = False
    """Convenience flag — True when at least one field is a password input."""

    field_count: int = 0
    """Total number of input fields extracted."""

    def model_post_init(self, __context: Any) -> None:
        self.field_count = len(self.fields)
        self.has_password_field = any(f.is_password for f in self.fields)


class WebPageState(BaseModel):
    """Unified read-only snapshot of the current browser page for M15.3.

    Aggregates:
    - Core page content from BrowserSnapshot
    - Structured form data from WebFormInfo list
    - Optional PerceptionResult from M15.2 vision pipeline

    SECURITY: This model is read-only.  It MUST NOT be used to trigger
    navigation or form submission.  Password field values are never included.
    """

    # --- Origin -------------------------------------------------------------------
    url: str
    """Validated URL of the inspected page."""

    title: str = ""
    """Page <title> content."""

    captured_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    """ISO-8601 UTC timestamp of when the state was captured."""

    # --- Textual content ----------------------------------------------------------
    text_content: str = ""
    """Visible, stripped plain-text content of the page (credentials redacted)."""

    headings: List[str] = Field(default_factory=list)
    """H1–H3 headings extracted from the page."""

    links: List[Dict[str, str]] = Field(default_factory=list)
    """Anchor links found on the page: [{href, text}, …]."""

    # --- Form extraction ----------------------------------------------------------
    forms: List[WebFormInfo] = Field(default_factory=list)
    """Structured form descriptors extracted from the DOM."""

    forms_count: int = 0
    """Total number of <form> elements detected."""

    # --- Perception (optional, M15.2 integration) ---------------------------------
    perception_summary: Optional[str] = None
    """Human-readable summary from the vision/OCR perception pipeline,
    if a screenshot was available and perception was requested."""

    detected_elements_count: int = 0
    """Number of elements detected by the visual perception model."""

    # --- Status -------------------------------------------------------------------
    interactive_elements_count: int = 0
    """Total count of interactive elements (inputs, buttons, links)."""

    page_load_status: str = "loaded"
    """Descriptive load status: loaded | partial | error."""

    def model_post_init(self, __context: Any) -> None:
        self.forms_count = len(self.forms)
