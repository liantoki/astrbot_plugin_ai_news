from .base import BaseNewsSource, NewsItem

__all__ = ["BaseNewsSource", "NewsItem", "SOURCE_REGISTRY", "get_source"]

SOURCE_REGISTRY: dict[str, type[BaseNewsSource]] = {}


def get_source(source_id: str, timeout: int = 15) -> BaseNewsSource | None:
    return None
