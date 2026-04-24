"""新闻条目数据结构。"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class NewsItem:
    """统一的新闻数据结构"""
    title: str
    url: str
    source: str                          # 来源名称
    source_id: str                       # 来源标识
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
