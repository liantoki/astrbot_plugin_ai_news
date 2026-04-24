from datetime import datetime

from .sources import NewsItem


def format_daily_news(news_list: list[NewsItem], ai_summary: str = "") -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"AI 新闻整合（{today}）", ""]
    if ai_summary:
        lines.extend([ai_summary, ""])
    for idx, item in enumerate(news_list, start=1):
        lines.append(f"{idx}. [{item.title}]({item.url})")
        if item.description:
            lines.append(f"   {item.description}")
        lines.append(f"   来源：{item.source}")
        lines.append("")
    return "\n".join(lines).strip()
