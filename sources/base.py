"""
新闻源基类 - 所有新闻源均继承此类
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class NewsItem:
    """统一的新闻数据结构"""
    title: str
    url: str
    source: str                          # 来源名称，如 "Hacker News"
    source_id: str                       # 来源标识，如 "hackernews"
    language: str = "en"                 # "en" 或 "zh"
    description: str = ""               # 摘要/正文片段
    score: int = 0                       # 热度分数（用于排序）
    published_at: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "source_id": self.source_id,
            "language": self.language,
            "description": self.description,
            "score": self.score,
            "published_at": self.published_at.isoformat() if self.published_at else "",
            "tags": self.tags,
        }


class BaseNewsSource(ABC):
    """新闻源抽象基类"""

    source_id: str = ""       # 唯一标识
    source_name: str = ""     # 显示名称
    language: str = "en"      # 语言
    description: str = ""     # 来源说明

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    @abstractmethod
    async def fetch(self, count: int = 10) -> list[NewsItem]:
        """抓取新闻，返回 NewsItem 列表"""
        ...

    def __repr__(self):
        return f"<{self.__class__.__name__} id={self.source_id}>"
