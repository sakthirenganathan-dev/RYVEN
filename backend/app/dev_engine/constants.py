"""Constants and security boundaries for RYVEN DEV ENGINE v1."""

# Maximum size per individual generated file (bytes) -> 500 KB
MAX_FILE_SIZE_BYTES: int = 500_000

# Maximum number of files permitted in a single project scaffolding
MAX_FILES_PER_PROJECT: int = 50

# Maximum total generated project size (bytes) -> 5 MB
MAX_TOTAL_PROJECT_SIZE_BYTES: int = 5_000_000

# Maximum allowed project name length
MAX_PROJECT_NAME_LENGTH: int = 64

# Strict regex pattern for safe project directory names
# Disallows spaces, quotes, slashes, backslashes, dots, and shell meta-characters
PROJECT_NAME_REGEX: str = r"^[a-zA-Z0-9_\-]+$"

# Supported generation strategies / project types
SUPPORTED_PROJECT_TYPES = {
    "react_ts": "React + Vite + TypeScript",
    "react": "React + Vite + TypeScript",
    "web": "Vanilla HTML5 / CSS3 / JavaScript",
    "vanilla": "Vanilla HTML5 / CSS3 / JavaScript",
    "python": "Python Application / CLI",
    "python_api": "Python FastAPI Service",
}

# Maximum iterations in controlled fix loop
MAX_FIX_ITERATIONS: int = 10

