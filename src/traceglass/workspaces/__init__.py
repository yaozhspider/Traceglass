"""Product workspaces composed with native AgentScope sessions and backends."""

from .manager import ProductDockerWorkspaceManager, ProductLocalWorkspaceManager
from .service import ProductWorkspaceService

__all__ = ["ProductDockerWorkspaceManager", "ProductLocalWorkspaceManager", "ProductWorkspaceService"]
