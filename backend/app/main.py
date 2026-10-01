"""FastAPI application entrypoint for RYVEN backend."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.core.assistant import Assistant
from app.core.config import settings
from app.core.logging_config import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context for initialization and cleanup."""
    logger.info("Initializing RYVEN backend core subsystems...")
    app.state.assistant = Assistant()
    logger.info(
        f"Subsystems active. Registered tools: {app.state.assistant.registry.list_tools()}"
    )
    yield
    logger.info("Shutting down RYVEN backend...")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Modular AI Assistant Backend for RYVEN",
        lifespan=lifespan,
    )

    # Configure CORS for local development with frontend
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    application.include_router(router)

    @application.get("/", tags=["root"])
    async def root() -> dict:
        return {
            "app": settings.app_name,
            "version": settings.version,
            "status": "online",
            "docs": "/docs",
        }

    return application


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
