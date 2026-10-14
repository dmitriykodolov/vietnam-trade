"""Vietnam Customs publication catalogue ingestion."""

from .client import VietnamCustomsClient
from .discovery import DiscoveryResult, discover_publications
from .registry import build_registry, filter_target_series

__all__ = [
    "DiscoveryResult",
    "VietnamCustomsClient",
    "build_registry",
    "discover_publications",
    "filter_target_series",
]
