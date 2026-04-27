import asyncio
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.tool import ToolSet

from .card import generate_news_card
from .sources import NewsItem

DEFAULT_SEARCH_TOOLS = ["web_search_tavily", "web_search_bocha", "web_search_brave", "web_search_baidu"]
NOT_CONFIGURED_MARKERS = ("api key is not configured", "not configured", "\u672a\u914d\u7f6e")
DEFAULT_FIXED_NEWS_SOURCES = [
    {
        "name": "Google新闻搜索",
        "url": "https://news.google.com/rss/search?q={topic}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "topics": "",
        "weight": 2,
    },
    {
        "name": "IT之家",
        "url": "https://www.ithome.com/rss/",
        "topics": "人工智能,半导体,科技,数码",
        "weight": 8,
    },
    {
        "name": "36氪",
        "url": "https://36kr.com/feed",
        "topics": "科技,创业,互联网,人工智能,新能源,商业",
        "weight": 7,
    },
    {
        "name": "少数派",
        "url": "https://sspai.com/feed",
        "topics": "科技,数码,软件,效率工具",
        "weight": 5,
    },
    {
        "name": "Solidot",
        "url": "https://www.solidot.org/index.rss",
        "topics": "科技,开源,软件,安全,人工智能",
        "weight": 5,
    },
    {
        "name": "联合早报即时",
        "url": "https://www.zaobao.com.sg/realtime/rss.xml",
        "topics": "国际,中国,财经,社会,政治",
        "weight": 6,
    },
    {
        "name": "BBC中文",
        "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "topics": "国际,中国,政治,经济,社会",
        "weight": 6,
    },
    {
        "name": "财新",
        "url": "https://rss.caixin.com/caixin_finance.xml",
        "topics": "财经,金融,经济,商业",
        "weight": 6,
    },
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "topics": "人工智能,科技,创业,互联网",
        "weight": 7,
    },
    {
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "topics": "人工智能,科技,互联网,数码",
        "weight": 6,
    },
    {
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "topics": "科技,软件,安全,人工智能,硬件",
        "weight": 5,
    },
    {
        "name": "Hacker News",
        "url": "https://hnrss.org/frontpage",
        "topics": "科技,软件,创业,人工智能,开源",
        "weight": 4,
    },
]
DEFAULT_FIXED_NEWS_SOURCE_LINES = [
    f"{item['name']}|{item['url']}|{item.get('topics', '')}|{item.get('weight', 5)}"
    for item in DEFAULT_FIXED_NEWS_SOURCES
]
LOW_VALUE_NEWS_PATTERNS = (
    "一周",
    "周报",
    "日报",
    "月报",
    "盘点",
    "总结",
    "汇总",
    "合集",
    "榜单",
    "十大",
    "8大",
    "趋势",
    "展望",
    "前瞻",
    "论坛",
    "峰会",
    "研讨会",
    "培训",
    "讲座",
    "课程",
    "方案",
    "白皮书",
    "报告",
    "倡议",
    "共建",
    "签约",
    "合作",
    "落地",
    "赋能",
)
DEFAULT_FIXED_NEWS_SOURCE_CONFIGS = [
    {
        "__template_key": "rss_source",
        "name": item["name"],
        "url": item["url"],
        "topics": item.get("topics", ""),
        "weight": item.get("weight", 5),
    }
    for item in DEFAULT_FIXED_NEWS_SOURCES
]
TOPIC_ALIASES = {
    "人工智能": {"ai", "a.i.", "aigc", "大模型", "llm", "机器学习", "深度学习", "智能体", "生成式ai", "生成式人工智能"},
    "半导体": {"芯片", "集成电路", "gpu", "算力", "晶圆", "先进制程"},
    "科技": {"技术", "tech", "technology", "互联网", "数码", "硬件", "软件"},
    "财经": {"金融", "经济", "商业", "股票", "股市", "投资", "市场"},
    "国际": {"全球", "世界", "海外", "外交"},
    "中国": {"国内", "大陆", "内地"},
    "政治": {"政务", "政策", "监管"},
    "社会": {"民生", "公共事件"},
    "新能源": {"电动车", "电动汽车", "ev", "储能", "光伏", "锂电"},
    "创业": {"创投", "融资", "初创", "startup", "venture"},
    "开源": {"opensource", "open source"},
    "安全": {"网络安全", "信息安全", "漏洞", "security"},
}
DEFAULT_TOPIC_ALIAS_LINES = [
    f"{canonical}|{','.join(sorted(aliases))}"
    for canonical, aliases in TOPIC_ALIASES.items()
]


def _split_csv(value, default=None) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").replace("\uff0c", ",").replace("\u3001", ",").split(",")
    result = [str(item).strip() for item in items if str(item).strip()]
    return result or list(default or [])


def _to_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _safe_text(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:limit].rstrip()


@register(
    "astrbot_plugin_ai_news",
    "\u0041\u0049 \u65b0\u95fb\u6574\u5408",
    "\u56fa\u5b9a RSS/Atom/JSON Feed \u4fe1\u606f\u6e90\u4f18\u5148\uff0c\u652f\u6301\u641c\u7d22\u8865\u5145\u3001\u53bb\u91cd\u8bc4\u5206\u548c\u5206\u6279 AI \u6539\u5199\u7684\u591a\u9886\u57df\u65b0\u95fb\u6574\u5408\u63d2\u4ef6\u3002",
    "1.1.1",
    "https://github.com/liantoki/astrbot_plugin_ai_news",
)
class AINewsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._api_key_cache: dict[str, tuple[str, str]] = {}
        self._apply_config()
        self._task = asyncio.create_task(self._schedule_daily_push())
        logger.info(
            f"[AI News] loaded provider={self.ai_provider_id or 'current'} "
            f"topics={','.join(self.news_topics)} tools={','.join(self.enabled_search_tools)}"
        )

    def _apply_config(self):
        timing = self.config.get("timing", {}) or {}
        content = self.config.get("content_settings", {}) or {}
        sources = self.config.get("source_settings", {}) or {}
        search = self.config.get("search_settings", {}) or {}
        push = self.config.get("push_settings", {}) or {}

        def g(group, key, default):
            value = group.get(key)
            if value is None:
                value = self.config.get(key)
            return default if value is None else value

        self.push_hour = int(g(timing, "push_hour", 8))
        self.push_minute = int(g(timing, "push_minute", 0))
        self.news_count = int(g(content, "news_count", 6))
        self.candidate_pool_multiplier = max(1, int(g(content, "candidate_pool_multiplier", 3)))
        self.news_topics = _split_csv(g(content, "news_topics", "\u4eba\u5de5\u667a\u80fd"), ["\u4eba\u5de5\u667a\u80fd"])
        self.ai_provider_id = str(g(content, "ai_provider_id", "") or "").strip()
        self.render_card = _to_bool(g(content, "render_card", True), True)

        self.use_fixed_sources = _to_bool(g(sources, "use_fixed_sources", True), True)
        raw_fixed_sources = sources.get("fixed_sources")
        if raw_fixed_sources is None:
            raw_fixed_sources = self.config.get("fixed_sources")
        raw_fixed_sources = self._migrate_default_fixed_sources(raw_fixed_sources, sources)
        self.fixed_sources = self._parse_fixed_news_sources(raw_fixed_sources)
        raw_topic_aliases = sources.get("topic_aliases")
        if raw_topic_aliases is None:
            raw_topic_aliases = self.config.get("topic_aliases")
        raw_topic_aliases = self._migrate_default_topic_aliases(raw_topic_aliases, sources)
        self.topic_aliases = self._parse_topic_aliases(raw_topic_aliases)
        self.search_supplement_enabled = _to_bool(g(sources, "search_supplement_enabled", True), True)
        self.fixed_source_concurrency = max(1, int(g(sources, "fixed_source_concurrency", 6)))
        self.min_fixed_source_candidates = max(0, int(g(sources, "min_fixed_source_candidates", 0)))
        self.search_supplement_threshold = max(1, int(g(sources, "search_supplement_threshold", self.news_count)))
        self.rewrite_extra_candidates = max(0, int(g(sources, "rewrite_extra_candidates", 2)))
        self.rewrite_batch_size = max(1, int(g(sources, "rewrite_batch_size", self.news_count + self.rewrite_extra_candidates)))
        self.rewrite_concurrency = max(1, int(g(sources, "rewrite_concurrency", 1)))

        self.enabled_search_tools = _split_csv(
            g(search, "enabled_search_tools", ",".join(DEFAULT_SEARCH_TOOLS)),
            DEFAULT_SEARCH_TOOLS,
        )
        self.stop_on_search_tool_error = _to_bool(g(search, "stop_on_search_tool_error", False), False)
        self.max_search_rounds = max(1, int(g(search, "max_search_rounds", 2)))
        self.max_news_age_days = max(1, int(g(search, "max_news_age_days", 1)))
        self.search_round_concurrency = max(1, int(g(search, "search_round_concurrency", 3)))
        self.direct_search_providers = _split_csv(g(search, "direct_search_providers", "tavily,bocha,baidu"), ["tavily", "bocha", "baidu"])
        self.tavily_api_key = str(g(search, "tavily_api_key", "") or "").strip()
        self.bocha_api_key = str(g(search, "bocha_api_key", "") or "").strip()
        self.baidu_api_key = str(g(search, "baidu_api_key", "") or "").strip()
        self.debug_log_full_api_key = _to_bool(g(search, "debug_log_full_api_key", False), False)
        self._api_key_cache.clear()
        self.astrbot_config_paths = _split_csv(
            g(
                search,
                "astrbot_config_paths",
                "/AstrBot/data/config",
            ),
            ["/AstrBot/data/config"],
        )
        self.astrbot_config_paths = [path for path in self.astrbot_config_paths if path]
        if not self.astrbot_config_paths:
            self.astrbot_config_paths = ["/AstrBot/data/config"]
        old_default_paths = ["/AstrBot/data/config", "/AstrBot/data/plugins", "./data/config", "./data/plugins"]
        if self.astrbot_config_paths == old_default_paths:
            self.astrbot_config_paths = ["/AstrBot/data/config"]
        self.use_astrbot_search_tools = _to_bool(g(search, "use_astrbot_search_tools", True), True)

        self.fetch_timeout = int(g(push, "fetch_timeout", 20))
        self.push_targets = _split_csv(g(push, "push_targets", ""), [])

    @filter.command("ainews")
    async def cmd_ainews(self, event: AstrMessageEvent):
        topics = self._topics_from_event(event) or self.news_topics
        yield event.plain_result(f"\u6b63\u5728\u6574\u5408\u65b0\u95fb\uff1a{', '.join(topics)}")
        result = await self._build_message(event=event, topics=topics)
        if isinstance(result, str):
            yield event.plain_result(result)
        else:
            yield event.chain_result([Image.fromFileSystem(result[0])])

    @filter.command("ainews_sub")
    async def cmd_subscribe(self, event: AstrMessageEvent):
        target = event.unified_msg_origin
        if target not in self.push_targets:
            self.push_targets.append(target)
            self._save_runtime_config()
        yield event.plain_result(f"\u5df2\u8ba2\u9605 AI \u65b0\u95fb\u6574\u5408\uff0c\u6bcf\u5929 {self.push_hour:02d}:{self.push_minute:02d} \u63a8\u9001\u3002")

    @filter.command("ainews_unsub")
    async def cmd_unsubscribe(self, event: AstrMessageEvent):
        target = event.unified_msg_origin
        if target in self.push_targets:
            self.push_targets.remove(target)
            self._save_runtime_config()
        yield event.plain_result("\u5df2\u53d6\u6d88 AI \u65b0\u95fb\u6574\u5408\u8ba2\u9605\u3002")

    @filter.command("ainews_status")
    async def cmd_status(self, event: AstrMessageEvent):
        subscribed = event.unified_msg_origin in self.push_targets
        subscribed_text = "\u5df2\u8ba2\u9605" if subscribed else "\u672a\u8ba2\u9605"
        provider_text = self.ai_provider_id or "\u5f53\u524d\u4f1a\u8bdd\u9ed8\u8ba4\u6a21\u578b"
        yield event.plain_result(
            "\n".join(
                [
                    "AI \u65b0\u95fb\u6574\u5408\u72b6\u6001",
                    f"\u8ba2\u9605\u72b6\u6001\uff1a{subscribed_text}",
                    f"\u63a8\u9001\u65f6\u95f4\uff1a{self.push_hour:02d}:{self.push_minute:02d}",
                    f"\u9ed8\u8ba4\u9886\u57df\uff1a{', '.join(self.news_topics)}",
                    f"\u6bcf\u4e2a\u9886\u57df\u6570\u91cf\uff1a{self.news_count}",
                    f"AI \u6a21\u578b\uff1a{provider_text}",
                    f"\u56fa\u5b9a\u4fe1\u606f\u6e90\uff1a{len(self.fixed_sources)} \u4e2a\uff0c\u542f\u7528\uff1a{self.use_fixed_sources}",
                    f"\u641c\u7d22\u8865\u5145\uff1a{self.search_supplement_enabled}",
                    f"\u76f4\u8fde\u641c\u7d22\uff1a{', '.join(self.direct_search_providers)}",
                    f"\u641c\u7d22\u5de5\u5177\uff1a{', '.join(self.enabled_search_tools)}",
                    f"\u4f7f\u7528 AstrBot \u641c\u7d22\u5de5\u5177\uff1a{self.use_astrbot_search_tools}",
                    f"\u6700\u5927\u641c\u7d22\u8f6e\u6b21\uff1a{self.max_search_rounds}",
                    f"\u5de5\u5177\u5931\u8d25\u540e\u505c\u6b62\uff1a{self.stop_on_search_tool_error}",
                ]
            )
        )

    @filter.command("ainews_diag")
    async def cmd_diag(self, event: AstrMessageEvent):
        tools = self._get_web_search_tools()
        lines = [
            "AI News diagnostics",
            f"enabled_search_tools={self.enabled_search_tools}",
            f"direct_search_providers={self.direct_search_providers}",
            f"tavily_api_key={self._format_api_key_diag('tavily')}",
            f"bocha_api_key={self._format_api_key_diag('bocha')}",
            f"baidu_api_key={self._format_api_key_diag('baidu')}",
            f"astrbot_config_paths={self.astrbot_config_paths}",
            f"use_astrbot_search_tools={self.use_astrbot_search_tools}",
            f"use_fixed_sources={self.use_fixed_sources}",
            f"fixed_sources_count={len(self.fixed_sources)}",
            f"search_supplement_enabled={self.search_supplement_enabled}",
            f"search_supplement_threshold={self.search_supplement_threshold}",
            f"fixed_source_concurrency={self.fixed_source_concurrency}",
            f"resolved_tools={[getattr(tool, 'name', '?') for tool in tools]}",
            f"max_search_rounds={self.max_search_rounds}",
            f"max_news_age_days={self.max_news_age_days}",
            f"search_round_concurrency={self.search_round_concurrency}",
            f"stop_on_search_tool_error={self.stop_on_search_tool_error}",
            f"debug_log_full_api_key={self.debug_log_full_api_key}",
            "Direct search API keys are read from this plugin config or AstrBot config files.",
            "AstrBot web_search_* tool keys are only used when use_astrbot_search_tools=True.",
        ]
        lines.extend(self._format_astrbot_config_scan_diag())
        for tool in tools:
            attrs = []
            for attr in ("name", "active", "description"):
                if hasattr(tool, attr):
                    attrs.append(f"{attr}={getattr(tool, attr)}")
            lines.append("; ".join(attrs))
        for source in self.fixed_sources:
            lines.append(
                f"fixed_source name={source.get('name')} url={source.get('url')} "
                f"topics={','.join(source.get('topics') or [])} weight={source.get('weight')}"
            )
        yield event.plain_result("\n".join(lines))

    @filter.command("ainews_reset_sources")
    async def cmd_reset_sources(self, event: AstrMessageEvent):
        self._write_default_fixed_sources()
        self._apply_config()
        yield event.plain_result(f"已恢复默认固定信息源，共 {len(self.fixed_sources)} 个。请重新打开插件配置查看列表。")

    @filter.command("ainews_reset_aliases")
    async def cmd_reset_aliases(self, event: AstrMessageEvent):
        self._write_default_topic_aliases()
        self._apply_config()
        yield event.plain_result(f"已恢复默认领域别名，共 {len(self.topic_aliases)} 组。请重新打开插件配置查看列表。")

    async def _schedule_daily_push(self):
        while True:
            try:
                now = datetime.now()
                target = now.replace(hour=self.push_hour, minute=self.push_minute, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())
                await self._do_push()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[AI News] schedule failed: {exc}")
                await asyncio.sleep(60)

    async def _do_push(self):
        if not self.push_targets:
            return
        result = await self._build_message(event=None, topics=self.news_topics)
        for target in list(self.push_targets):
            try:
                from astrbot.api.event import MessageChain

                chain = MessageChain().message(result) if isinstance(result, str) else MessageChain().file_image(result[0])
                await self.context.send_message(target, chain)
            except Exception as exc:
                logger.error(f"[AI News] push failed target={target} error={exc}")

    async def _build_message(self, event: AstrMessageEvent | None, topics: list[str]):
        news_list = await self._generate_integrated_news(event=event, topics=topics)
        if not news_list:
            return "\u672a\u80fd\u83b7\u53d6\u5230\u8db3\u591f\u65b0\u7684\u53ef\u7528\u65b0\u95fb\u3002\u8bf7\u653e\u5bbd\u65b0\u95fb\u65f6\u6548\u3001\u589e\u52a0\u641c\u7d22\u8f6e\u6b21\u6216\u66f4\u6362\u641c\u7d22\u6e90\u3002"
        before_rewrite_count = len(news_list)
        target_count = max(1, self.news_count * max(1, len(topics)))
        news_list = await self._rewrite_candidates_until_enough(news_list, event, topics, target_count)
        logger.info(f"[AI News] card candidates before_rewrite={before_rewrite_count} after_rewrite={len(news_list)}")
        news_list = news_list[:target_count]
        if not news_list:
            return "\u672a\u80fd\u83b7\u53d6\u5230\u8db3\u591f\u65b0\u7684\u53ef\u7528\u65b0\u95fb\u3002\u8bf7\u653e\u5bbd\u65b0\u95fb\u65f6\u6548\u6216\u66f4\u6362\u641c\u7d22\u8bcd\u3002"

        if self.render_card:
            img_path = generate_news_card(
                news_list,
                ai_summary=f"\u9886\u57df\uff1a{', '.join(topics)}\uff1b\u6bcf\u4e2a\u9886\u57df\u6700\u591a {self.news_count} \u6761",
            )
            if img_path:
                return (img_path, "")

        return self._format_fallback(news_list, topics)

    async def _rewrite_candidates_until_enough(
        self,
        news_list: list[NewsItem],
        event: AstrMessageEvent | None,
        topics: list[str],
        target_count: int,
    ) -> list[NewsItem]:
        if not news_list:
            return news_list
        candidates = self._rank_news_items(self._deduplicate(news_list), ",".join(topics))
        batches = [
            candidates[idx : idx + self.rewrite_batch_size]
            for idx in range(0, len(candidates), self.rewrite_batch_size)
        ]
        kept: list[NewsItem] = []
        batch_index = 0
        while batch_index < len(batches) and len(kept) < target_count:
            wave = batches[batch_index : batch_index + self.rewrite_concurrency]
            wave_start = batch_index
            batch_index += len(wave)
            rewritten_batches = await asyncio.gather(
                *(
                    self._rewrite_and_filter_batch(batch, event, topics, wave_start + offset)
                    for offset, batch in enumerate(wave)
                ),
                return_exceptions=True,
            )
            for result in rewritten_batches:
                if isinstance(result, Exception):
                    logger.warning(f"[AI News] rewrite batch failed: {_safe_text(result, 200)}")
                    continue
                kept = self._deduplicate([*kept, *result])
            kept = self._rank_news_items(kept, ",".join(topics))
        logger.info(
            f"[AI News] rewrite batches candidates={len(candidates)} batch_size={self.rewrite_batch_size} "
            f"concurrency={self.rewrite_concurrency} kept={len(kept)} target={target_count}"
        )
        return kept

    async def _rewrite_and_filter_batch(
        self,
        batch: list[NewsItem],
        event: AstrMessageEvent | None,
        topics: list[str],
        batch_id: int = 0,
    ) -> list[NewsItem]:
        rewritten = await self._translate_news_to_chinese(list(batch), event, batch_id=batch_id)
        rewritten = self._filter_relevant_after_rewrite(rewritten, topics)
        return self._deduplicate(self._filter_recent_news(rewritten))

    async def _generate_integrated_news(self, event: AstrMessageEvent | None, topics: list[str]) -> list[NewsItem]:
        async def collect_topic(topic: str) -> list[NewsItem]:
            pool_size = max(self.news_count, self.news_count * self.candidate_pool_multiplier)
            fixed_items = await self._run_fixed_source_rounds(topic)
            topic_items = list(fixed_items)
            supplement_threshold = self.min_fixed_source_candidates or self.search_supplement_threshold
            if self.search_supplement_enabled and len(topic_items) < supplement_threshold:
                supplement_items = await self._run_direct_search_rounds(topic)
                if not supplement_items:
                    supplement_items = await self._run_fallback_search_rounds(event, topic)
                topic_items.extend(supplement_items)
            topic_items = self._rank_news_items(self._deduplicate(topic_items), topic)
            logger.info(
                f"[AI News] topic={topic} fixed={len(fixed_items)} "
                f"combined={len(topic_items)} pool={pool_size}"
            )
            selected = []
            for item in topic_items[:pool_size]:
                item.tags = list(dict.fromkeys([topic, *getattr(item, "tags", [])]))
                selected.append(item)
            return selected

        topic_results = await asyncio.gather(
            *(collect_topic(topic) for topic in topics),
            return_exceptions=True,
        )
        all_items: list[NewsItem] = []
        for topic, result in zip(topics, topic_results):
            if isinstance(result, Exception):
                logger.warning(f"[AI News] topic collection failed topic={topic} error={_safe_text(result, 200)}")
                continue
            all_items.extend(result)
        return self._rank_news_items(self._deduplicate(all_items), ",".join(topics))

    def _filter_relevant_after_rewrite(self, news_list: list[NewsItem], topics: list[str]) -> list[NewsItem]:
        kept = []
        dropped = 0
        for item in news_list:
            topic = (getattr(item, "tags", None) or topics or [""])[0]
            if self._is_topic_relevant(item, topic):
                kept.append(item)
            else:
                dropped += 1
        if dropped:
            logger.info(f"[AI News] dropped {dropped} off-topic rewritten candidates")
        return kept

    def _migrate_default_fixed_sources(self, raw_sources, source_settings: dict):
        initialized = False
        if isinstance(source_settings, dict):
            initialized = _to_bool(
                source_settings.get("fixed_sources_initialized", source_settings.get("_fixed_sources_initialized", False)),
                False,
            )
        if isinstance(raw_sources, list) and raw_sources and any(isinstance(item, str) for item in raw_sources):
            converted = self._fixed_source_template_configs_from_legacy(raw_sources)
            if converted:
                self._write_fixed_source_configs(converted)
                return converted
        if isinstance(raw_sources, list) and (raw_sources or initialized):
            return raw_sources
        if raw_sources is not None and str(raw_sources).strip():
            return raw_sources
        return self._write_default_fixed_sources()

    def _write_default_fixed_sources(self):
        return self._write_fixed_source_configs([dict(item) for item in DEFAULT_FIXED_NEWS_SOURCE_CONFIGS])

    def _write_fixed_source_configs(self, source_configs: list[dict]):
        try:
            settings = self.config.get("source_settings", {}) or {}
            if not isinstance(settings, dict):
                settings = {}
            settings["fixed_sources"] = source_configs
            settings["fixed_sources_initialized"] = True
            self.config["source_settings"] = settings
            self.config.save_config()
            logger.info("[AI News] initialized default fixed source list in plugin config")
        except Exception as exc:
            logger.warning(f"[AI News] failed to initialize default fixed source list: {_safe_text(exc, 200)}")
        return source_configs

    def _fixed_source_template_configs_from_legacy(self, raw_sources: list) -> list[dict]:
        configs = []
        for raw in raw_sources:
            if isinstance(raw, dict):
                config = dict(raw)
            else:
                parsed = self._parse_fixed_source_lines(str(raw or ""))
                if not parsed:
                    continue
                config = parsed[0]
            if not config.get("url"):
                continue
            configs.append(
                {
                    "__template_key": "rss_source",
                    "name": config.get("name") or urlparse(str(config.get("url"))).netloc,
                    "url": config.get("url"),
                    "topics": ",".join(config.get("topics") or []) if isinstance(config.get("topics"), list) else config.get("topics", ""),
                    "weight": int(config.get("weight", 5) or 5),
                }
            )
        return configs

    def _migrate_default_topic_aliases(self, raw_aliases, source_settings: dict):
        initialized = False
        if isinstance(source_settings, dict):
            initialized = _to_bool(
                source_settings.get("topic_aliases_initialized", source_settings.get("_topic_aliases_initialized", False)),
                False,
            )
        if isinstance(raw_aliases, list) and (raw_aliases or initialized):
            return raw_aliases
        if raw_aliases is not None and str(raw_aliases).strip():
            return raw_aliases
        return self._write_default_topic_aliases()

    def _write_default_topic_aliases(self):
        try:
            settings = self.config.get("source_settings", {}) or {}
            if not isinstance(settings, dict):
                settings = {}
            settings["topic_aliases"] = list(DEFAULT_TOPIC_ALIAS_LINES)
            settings["topic_aliases_initialized"] = True
            self.config["source_settings"] = settings
            self.config["topic_aliases"] = list(DEFAULT_TOPIC_ALIAS_LINES)
            self.config.save_config()
            logger.info("[AI News] initialized default topic alias list in plugin config")
        except Exception as exc:
            logger.warning(f"[AI News] failed to initialize default topic alias list: {_safe_text(exc, 200)}")
        return list(DEFAULT_TOPIC_ALIAS_LINES)

    def _parse_topic_aliases(self, value) -> dict[str, set[str]]:
        aliases: dict[str, set[str]] = {key: set(items) for key, items in TOPIC_ALIASES.items()}
        if value is None:
            return aliases
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return aliases
            try:
                value = json.loads(text)
            except Exception:
                value = [line.strip() for line in text.splitlines() if line.strip()]
        if isinstance(value, dict):
            iterable = [f"{key}|{','.join(items if isinstance(items, list) else _split_csv(items, []))}" for key, items in value.items()]
        elif isinstance(value, list):
            iterable = value
        else:
            return aliases
        for raw in iterable:
            if isinstance(raw, dict):
                canonical = str(raw.get("name") or raw.get("canonical") or raw.get("topic") or "").strip()
                words = raw.get("aliases") or raw.get("words") or raw.get("values") or ""
                alias_items = _split_csv(words, [])
            else:
                parts = str(raw or "").split("|", 1)
                canonical = parts[0].strip()
                alias_items = _split_csv(parts[1] if len(parts) > 1 else "", [])
            if not canonical:
                continue
            current = aliases.setdefault(canonical, set())
            current.update(alias_items)
        return aliases

    def _parse_fixed_news_sources(self, value) -> list[dict]:
        raw_sources = value
        if raw_sources is None:
            raw_sources = DEFAULT_FIXED_NEWS_SOURCES
        elif isinstance(raw_sources, str):
            text = raw_sources.strip()
            if not text:
                raw_sources = DEFAULT_FIXED_NEWS_SOURCE_LINES
            else:
                try:
                    raw_sources = json.loads(text)
                except Exception:
                    raw_sources = self._parse_fixed_source_lines(text)
        if isinstance(raw_sources, dict):
            raw_sources = raw_sources.get("sources") or raw_sources.get("feeds") or []
        if not isinstance(raw_sources, list):
            raw_sources = []

        sources = []
        for idx, item in enumerate(raw_sources):
            if isinstance(item, str):
                parsed_lines = self._parse_fixed_source_lines(item)
                item = parsed_lines[0] if parsed_lines else {"url": item}
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("feed") or item.get("rss") or "").strip()
            if not self._is_valid_http_url(url):
                continue
            name = _safe_text(item.get("name") or item.get("source") or urlparse(url).netloc, 40)
            topics = _split_csv(item.get("topics") or item.get("tags") or "", [])
            try:
                weight = int(item.get("weight", 5))
            except Exception:
                weight = 5
            sources.append(
                {
                    "name": name,
                    "url": url,
                    "topics": topics,
                    "weight": max(0, min(20, weight)),
                    "source_id": str(item.get("source_id") or f"fixed_{idx}_{urlparse(url).netloc}"),
                }
            )
        return sources

    def _parse_fixed_source_lines(self, text: str) -> list[dict]:
        sources = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) == 1:
                sources.append({"url": parts[0]})
            elif len(parts) == 2:
                sources.append({"name": parts[0], "url": parts[1]})
            else:
                sources.append({"name": parts[0], "url": parts[1], "topics": parts[2], "weight": parts[3] if len(parts) > 3 else 5})
        return sources

    async def _run_fixed_source_rounds(self, topic: str) -> list[NewsItem]:
        if not self.use_fixed_sources or not self.fixed_sources:
            return []
        try:
            import aiohttp
        except ImportError:
            return []

        matched_sources = [source for source in self.fixed_sources if self._source_matches_topic(source, topic)]
        if not matched_sources:
            matched_sources = [source for source in self.fixed_sources if not source.get("topics")]
        if not matched_sources:
            return []

        semaphore = asyncio.Semaphore(self.fixed_source_concurrency)
        timeout = aiohttp.ClientTimeout(total=self.fetch_timeout)
        headers = {"User-Agent": "AstrBot-AINews/1.0 (+RSS/Atom)"}
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector, trust_env=True) as session:
            results = await asyncio.gather(
                *(self._fetch_fixed_source(session, semaphore, self._render_source_for_topic(source, topic)) for source in matched_sources),
                return_exceptions=True,
            )

        items: list[NewsItem] = []
        source_counts: dict[str, int] = {}
        for source, result in zip(matched_sources, results):
            if isinstance(result, Exception):
                logger.warning(f"[AI News] fixed source failed source={source.get('name')} error={_safe_text(result, 200)}")
                continue
            source_counts[str(source.get("name") or source.get("url"))] = len(result)
            items.extend(result)
        before = len(items)
        items = self._filter_topic_relevant_news(self._filter_recent_news(items), topic)
        items = self._rank_news_items(self._deduplicate(items), topic)
        kept_counts: dict[str, int] = {}
        for item in items:
            kept_counts[item.source] = kept_counts.get(item.source, 0) + 1
        logger.info(
            f"[AI News] fixed sources topic={topic} raw={before} kept={len(items)} "
            f"sources={len(matched_sources)} raw_by_source={source_counts} kept_by_source={kept_counts}"
        )
        return items

    def _render_source_for_topic(self, source: dict, topic: str) -> dict:
        rendered = dict(source)
        url = str(rendered.get("url") or "")
        topic_text = str(topic or "").strip()
        replacements = {
            "{topic}": quote_plus(topic_text),
            "{topic_raw}": topic_text,
            "{days}": str(max(1, self.max_news_age_days)),
            "{hours}": str(max(1, self.max_news_age_days * 24)),
        }
        for placeholder, value in replacements.items():
            url = url.replace(placeholder, value)
        rendered["url"] = url
        return rendered

    async def _fetch_fixed_source(self, session, semaphore: asyncio.Semaphore, source: dict) -> list[NewsItem]:
        async with semaphore:
            try:
                async with session.get(source["url"], allow_redirects=True) as resp:
                    if resp.status >= 400:
                        logger.warning(f"[AI News] fixed source HTTP {resp.status}: {source.get('url')}")
                        return []
                    text = await resp.text(errors="ignore")
                    content_type = (resp.headers.get("content-type") or "").lower()
            except Exception as exc:
                logger.warning(f"[AI News] fixed source request failed source={source.get('name')} error={_safe_text(exc, 200)}")
                return []
        if "json" in content_type or text.lstrip().startswith("{"):
            return self._parse_json_feed(text, source)
        return self._parse_xml_feed(text, source)

    def _parse_json_feed(self, text: str, source: dict) -> list[NewsItem]:
        try:
            data = json.loads(text)
        except Exception:
            return []
        raw_items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(raw_items, list):
            return []
        items = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or raw.get("external_url") or raw.get("id") or "").strip()
            title = _safe_text(raw.get("title") or "", 80)
            desc = self._clean_html(raw.get("summary") or raw.get("content_text") or raw.get("content_html") or "")
            published_at = self._parse_published_at(raw.get("date_published") or raw.get("date_modified"))
            item = self._build_fixed_news_item(title, url, desc, published_at, source)
            if item:
                items.append(item)
        return items

    def _parse_xml_feed(self, text: str, source: dict) -> list[NewsItem]:
        try:
            root = ET.fromstring(text.encode("utf-8"))
        except Exception:
            return []
        entries = list(root.findall(".//item")) or list(root.findall(".//{*}entry"))
        items = []
        for entry in entries:
            title = _safe_text(self._xml_child_text(entry, "title"), 80)
            url = self._xml_child_text(entry, "link")
            if not url:
                link_node = entry.find("{*}link")
                if link_node is not None:
                    url = str(link_node.attrib.get("href") or "").strip()
            desc = self._clean_html(
                self._xml_child_text(entry, "description")
                or self._xml_child_text(entry, "summary")
                or self._xml_child_text(entry, "content")
                or self._xml_child_text(entry, "encoded")
            )
            published_at = self._parse_feed_datetime(
                self._xml_child_text(entry, "pubDate")
                or self._xml_child_text(entry, "published")
                or self._xml_child_text(entry, "updated")
                or self._xml_child_text(entry, "date")
            )
            item = self._build_fixed_news_item(title, url, desc, published_at, source)
            if item:
                items.append(item)
        return items

    def _build_fixed_news_item(self, title: str, url: str, desc: str, published_at: datetime | None, source: dict) -> NewsItem | None:
        if not title or not self._is_valid_http_url(url):
            return None
        source_name = _safe_text(source.get("name") or urlparse(url).netloc, 40)
        score = 100 + int(source.get("weight") or 0)
        if self._is_google_news_source(source):
            score -= 70
            if self._is_low_value_news_text(f"{title} {desc}"):
                return None
        elif self._is_low_value_news_text(f"{title} {desc}"):
            score -= 35
        return NewsItem(
            title=title,
            url=url,
            source=source_name,
            source_id=str(source.get("source_id") or "fixed_source"),
            language="zh" if self._looks_chinese(f"{title} {desc}") else "en",
            description=_safe_text(desc or title, 120),
            score=score,
            published_at=published_at,
            tags=list(source.get("topics") or []),
        )

    @staticmethod
    def _is_google_news_source(source: dict) -> bool:
        name = str(source.get("name") or "").lower()
        url = str(source.get("url") or "").lower()
        return "google" in name or "news.google.com" in url

    @staticmethod
    def _is_low_value_news_text(text: str) -> bool:
        normalized = str(text or "").lower()
        return any(pattern.lower() in normalized for pattern in LOW_VALUE_NEWS_PATTERNS)

    @staticmethod
    def _xml_child_text(node, local_name: str) -> str:
        for child in list(node):
            tag = str(child.tag).split("}", 1)[-1]
            if tag == local_name:
                return (child.text or "").strip()
        return ""

    def _parse_feed_datetime(self, value) -> datetime | None:
        parsed = self._parse_published_at(value)
        if parsed is not None:
            return parsed
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return parsedate_to_datetime(text).replace(tzinfo=None)
        except Exception:
            return None

    @staticmethod
    def _clean_html(value: str) -> str:
        text = unescape(str(value or ""))
        text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        return _safe_text(text, 180)

    @staticmethod
    def _looks_chinese(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))

    def _source_matches_topic(self, source: dict, topic: str) -> bool:
        topics = source.get("topics") or []
        if not topics:
            return True
        requested_terms = self._expand_topic_terms(topic)
        for item in topics:
            source_terms = self._expand_topic_terms(item)
            if requested_terms & source_terms:
                return True
            for requested in requested_terms:
                for source_topic in source_terms:
                    if requested in source_topic or source_topic in requested:
                        return True
        return False

    @staticmethod
    def _normalize_topic_text(value: str) -> str:
        return re.sub(r"[\s,，、/|_\-]+", "", str(value or "").strip().lower())

    def _expand_topic_terms(self, topic: str) -> set[str]:
        terms = {
            self._normalize_topic_text(part)
            for part in re.split(r"[\s,，、/|]+", str(topic or ""))
            if str(part).strip()
        }
        normalized = self._normalize_topic_text(topic)
        if normalized:
            terms.add(normalized)
        changed = True
        while changed:
            changed = False
            for canonical, aliases in self.topic_aliases.items():
                group = {self._normalize_topic_text(canonical), *(self._normalize_topic_text(alias) for alias in aliases)}
                if terms & group and not group <= terms:
                    terms.update(group)
                    changed = True
        return {term for term in terms if term}

    def _rank_news_items(self, items: list[NewsItem], topic: str) -> list[NewsItem]:
        now = datetime.now()
        for item in items:
            score = int(getattr(item, "score", 0) or 0)
            if self._is_low_value_news_text(f"{item.title} {item.description}"):
                score -= 25
            if item.published_at:
                age_hours = max(0.0, (now - item.published_at).total_seconds() / 3600)
                if age_hours <= 6:
                    score += 30
                elif age_hours <= 24:
                    score += 20
                elif age_hours <= 72:
                    score += 8
            if self._is_topic_relevant(item, topic):
                score += 20
            if item.description and item.description != item.title:
                score += 5
            item.score = score
        return sorted(
            items,
            key=lambda item: (
                int(getattr(item, "score", 0) or 0),
                item.published_at or datetime.min,
            ),
            reverse=True,
        )

    async def _run_direct_search_rounds(self, topic: str) -> list[NewsItem]:
        semaphore = asyncio.Semaphore(self.search_round_concurrency)

        async def run_round(round_idx: int) -> list[dict]:
            async with semaphore:
                return await self._run_direct_search_apis_raw(topic, round_idx)

        round_results = await asyncio.gather(
            *(run_round(round_idx) for round_idx in range(1, self.max_search_rounds + 1)),
            return_exceptions=True,
        )
        raw_items: list[dict] = []
        for result in round_results:
            if isinstance(result, Exception):
                logger.warning(f"[AI News] direct search round failed topic={topic} error={_safe_text(result, 200)}")
                continue
            raw_items.extend(result)
        raw_items = self._deduplicate_raw_items(raw_items)
        if not raw_items:
            return []
        logger.info(f"[AI News] direct search collected {len(raw_items)} unique raw candidates topic={topic}")
        items = await self._parse_and_verify(json.dumps({"results": raw_items}, ensure_ascii=False))
        items = self._filter_topic_relevant_news(items, topic)
        logger.info(f"[AI News] direct search kept {len(items)} final candidates topic={topic}")
        return self._deduplicate(items)

    async def _run_fallback_search_rounds(self, event: AstrMessageEvent | None, topic: str) -> list[NewsItem]:
        topic_items: list[NewsItem] = []
        semaphore = asyncio.Semaphore(self.search_round_concurrency)

        async def run_round(round_idx: int) -> list[NewsItem]:
            async with semaphore:
                items = await self._run_search_agent_with_failover(
                    event,
                    topic,
                    self._build_prompt(topic, round_idx),
                    round_idx,
                    use_direct=False,
                )
                return self._filter_topic_relevant_news(items, topic)

        round_results = await asyncio.gather(
            *(run_round(round_idx) for round_idx in range(1, self.max_search_rounds + 1)),
            return_exceptions=True,
        )
        for result in round_results:
            if isinstance(result, Exception):
                logger.warning(f"[AI News] fallback search round failed topic={topic} error={_safe_text(result, 200)}")
                continue
            topic_items = self._deduplicate([*topic_items, *result])
        return topic_items

    def _build_prompt(self, topic: str, round_idx: int = 1) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        retry_hint = "" if round_idx <= 1 else f"\u8fd9\u662f\u7b2c {round_idx} \u8f6e\u8865\u5145\u641c\u7d22\uff0c\u5c3d\u91cf\u907f\u514d\u8fd4\u56de\u5df2\u91cd\u590d\u7684\u65b0\u95fb\u3002"
        return (
            f"\u8bf7\u641c\u7d22\u5e76\u6574\u5408 {today} \u5f53\u5929\u6216\u8fd1 {self.max_news_age_days} \u5929\u5185\u7684\u201c{topic}\u201d\u9886\u57df\u65b0\u95fb\u3002"
            f"\u8fd4\u56de {self.news_count * 3} \u6761\u5019\u9009\uff0c\u5fc5\u987b\u4f18\u5148\u9009\u62e9\u6709\u660e\u786e\u53d1\u5e03\u65f6\u95f4\u3001\u660e\u786e\u6765\u6e90\u548c\u53ef\u8bbf\u95ee\u94fe\u63a5\u7684\u65b0\u95fb\u3002"
            "\u4e0d\u8981\u8fd4\u56de\u5bfc\u8bfb\u3001\u5468\u62a5\u3001\u6708\u62a5\u3001\u6280\u672f\u56de\u987e\u3001\u8d44\u6599\u6c47\u603b\u6216\u65e7\u95fb\u7ffb\u65b0\u3002"
            "\u6807\u9898\u8981\u6539\u5199\u4e3a\u77ed\u53e5\u65b0\u95fb\u70b9\uff0c\u4e0d\u8981\u7167\u6284\u7f51\u9875\u6807\u9898\u3001\u7ad9\u70b9\u540d\u3001\u680f\u76ee\u540d\u6216\u65e5\u671f\u6bb5\u3002"
            f"{retry_hint}"
            "\u53ea\u8f93\u51fa JSON\uff0c\u4e0d\u8981\u8f93\u51fa Markdown \u6216\u89e3\u91ca\u3002"
            '{"news":[{"title":"短新闻点","description":"一句话说明最新进展","source":"source","url":"https://...","published_at":"2026-04-25T12:00:00+08:00"}]}'
        )

    async def _run_search_agent_with_failover(
        self,
        event: AstrMessageEvent | None,
        topic: str,
        prompt: str,
        round_idx: int = 1,
        use_direct: bool = True,
    ) -> list[NewsItem]:
        provider_id = await self._resolve_provider_id(event)
        search_tools = self._get_web_search_tools() if self.use_astrbot_search_tools else []

        if use_direct:
            direct_items = await self._run_direct_search_apis(topic, round_idx)
            if direct_items:
                return direct_items

        if event is not None and search_tools:
            system_prompt = (
                "\u4f60\u662f\u65b0\u95fb\u641c\u7d22\u4e0e\u6838\u9a8c\u52a9\u624b\u3002"
                "\u5bf9\u6bcf\u4e2a\u641c\u7d22\u5de5\u5177\u6700\u591a\u8c03\u7528 1 \u6b21\u3002"
                "\u5982\u679c\u5de5\u5177\u8fd4\u56de API key is not configured\uff0c\u7acb\u5373\u505c\u6b62\u5e76\u8bf4\u660e\u9519\u8bef\u3002"
                "\u6210\u529f\u641c\u7d22\u540e\u53ea\u8f93\u51fa\u4e25\u683c JSON\u3002"
            )
            for tool in search_tools:
                tools = ToolSet()
                tools.add_tool(tool)
                try:
                    resp = await self.context.tool_loop_agent(
                        event=event,
                        chat_provider_id=provider_id,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        tools=tools,
                        max_steps=5,
                        tool_call_timeout=max(30, self.fetch_timeout),
                    )
                except Exception as exc:
                    logger.warning(f"[AI News] search tool failed tool={tool.name} error={exc}")
                    if self.stop_on_search_tool_error:
                        return []
                    continue

                text = resp.completion_text if resp else ""
                if self._has_not_configured_error(text):
                    logger.warning(f"[AI News] search tool not configured: {tool.name}")
                    if self.stop_on_search_tool_error:
                        return []
                    continue

                items = await self._parse_and_verify(text)
                if items:
                    return items
                if self.stop_on_search_tool_error:
                    logger.warning(f"[AI News] search tool produced no JSON, stop failover: {tool.name}")
                    return []

        return await self._llm_only(prompt, provider_id)

    async def _run_direct_search_apis(self, topic: str, round_idx: int) -> list[NewsItem]:
        raw_items = await self._run_direct_search_apis_raw(topic, round_idx)
        if not raw_items:
            return []
        return await self._parse_and_verify(json.dumps({"results": self._deduplicate_raw_items(raw_items)}, ensure_ascii=False))

    async def _run_direct_search_apis_raw(self, topic: str, round_idx: int) -> list[dict]:
        providers = [provider.lower() for provider in self.direct_search_providers]
        if not providers:
            return []
        try:
            import aiohttp
        except ImportError:
            return []

        params = self._build_search_tool_params(topic, round_idx)
        timeout = aiohttp.ClientTimeout(total=self.fetch_timeout)
        headers = {"User-Agent": "AstrBot-AINews/1.0"}

        async def run_provider(provider: str, session) -> list[dict]:
            collected: list[dict] = []
            for api_key, api_key_source in self._get_direct_search_api_key_candidates(provider):
                logger.warning(
                    f"[AI News] direct search api key provider={provider} "
                    f"{self._format_api_key_debug(provider, api_key, api_key_source)}"
                )
                if self.use_astrbot_search_tools:
                    items = await self._call_astrbot_builtin_search(provider, params, api_key)
                    self._log_raw_search_items(provider, "astrbot_builtin", round_idx, items)
                    collected.extend(items)
                if provider == "tavily" and api_key:
                    items = await self._call_tavily_search(session, params, api_key)
                elif provider == "bocha" and api_key:
                    items = await self._call_bocha_search(session, params, api_key)
                elif provider == "baidu" and api_key:
                    items = await self._call_baidu_search(session, params, api_key)
                else:
                    continue
                self._log_raw_search_items(provider, "direct_api", round_idx, items)
                collected.extend(items)
            return collected

        async with aiohttp.ClientSession(timeout=timeout, headers=headers, trust_env=True) as session:
            provider_results = await asyncio.gather(
                *(run_provider(provider, session) for provider in providers),
                return_exceptions=True,
            )

        collected: list[dict] = []
        for provider, result in zip(providers, provider_results):
            if isinstance(result, Exception):
                logger.warning(f"[AI News] direct search provider failed provider={provider} error={_safe_text(result, 200)}")
                continue
            collected.extend(result)
        return collected

    def _log_raw_search_items(self, provider: str, source: str, round_idx: int, items: list[dict]):
        count = len(items) if items else 0
        logger.info(f"[AI News] {provider} {source} round={round_idx} returned {count} raw candidates")

    async def _call_astrbot_builtin_search(self, provider: str, params: dict, api_key: str) -> list[dict]:
        if not api_key:
            return []
        try:
            from astrbot.core.tools import web_search_tools as builtin_search
        except Exception:
            return []

        try:
            if provider == "tavily":
                payload = {
                    "query": params["query"],
                    "max_results": params["max_results"],
                    "include_favicon": True,
                    "search_depth": "basic",
                    "topic": params["topic"],
                    "days": params["days"],
                }
                results = await builtin_search._tavily_search({"websearch_tavily_key": [api_key]}, payload)
            elif provider == "bocha":
                payload = {
                    "query": params["query"],
                    "count": params["max_results"],
                    "summary": True,
                }
                results = await builtin_search._bocha_search({"websearch_bocha_key": [api_key]}, payload)
            elif provider == "baidu":
                payload = {
                    "messages": [{"role": "user", "content": params["query"][:72]}],
                    "search_source": "baidu_search_v2",
                    "resource_type_filter": [{"type": "web", "top_k": params["max_results"]}],
                    "search_recency_filter": params["recency_filter"],
                }
                results = await builtin_search._baidu_search({"websearch_baidu_app_builder_key": api_key}, payload)
            else:
                return []
        except Exception as exc:
            logger.warning(f"[AI News] astrbot builtin {provider} search failed error={_safe_text(exc, 300)}")
            return []

        normalized = []
        for item in results:
            normalized.append(
                {
                    "title": getattr(item, "title", ""),
                    "url": getattr(item, "url", ""),
                    "description": getattr(item, "snippet", ""),
                    "source": "",
                }
            )
        return normalized

    async def _call_tavily_search(self, session, params: dict, api_key: str) -> list[dict]:
        payload = {
            "query": params["query"],
            "topic": params["topic"],
            "max_results": params["max_results"],
            "include_favicon": True,
            "days": params["days"],
            "search_depth": "basic",
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with session.post("https://api.tavily.com/search", json=payload, headers=headers) as resp:
                if resp.status == 401:
                    return await self._call_tavily_search_legacy(session, payload, api_key)
                if resp.status >= 400:
                    reason = await self._safe_response_text(resp)
                    logger.warning(f"[AI News] tavily direct search failed status={resp.status} reason={reason}")
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[AI News] tavily direct search failed error={exc}")
            return []
        return self._normalize_search_results(data)

    async def _call_tavily_search_legacy(self, session, payload: dict, api_key: str) -> list[dict]:
        legacy_payload = dict(payload)
        legacy_payload["api_key"] = api_key
        try:
            async with session.post("https://api.tavily.com/search", json=legacy_payload) as resp:
                if resp.status >= 400:
                    reason = await self._safe_response_text(resp)
                    logger.warning(f"[AI News] tavily direct search failed status={resp.status} reason={reason}")
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[AI News] tavily direct search failed error={exc}")
            return []
        return self._normalize_search_results(data)

    async def _call_baidu_search(self, session, params: dict, api_key: str) -> list[dict]:
        payload = {
            "messages": [{"role": "user", "content": params["query"][:72]}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": params["max_results"]}],
            "search_recency_filter": params["recency_filter"],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Appbuilder-Authorization": f"Bearer {api_key}",
        }
        try:
            async with session.post("https://qianfan.baidubce.com/v2/ai_search/web_search", json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    reason = await self._safe_response_text(resp)
                    logger.warning(f"[AI News] baidu direct search failed status={resp.status} reason={reason}")
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[AI News] baidu direct search failed error={exc}")
            return []
        return self._normalize_search_results(data)

    async def _call_bocha_search(self, session, params: dict, api_key: str) -> list[dict]:
        payload = {
            "query": params["query"],
            "summary": True,
            "count": params["max_results"],
            "freshness": "oneDay" if self.max_news_age_days <= 1 else "oneWeek",
        }
        headers = {"Authorization": f"Bearer {api_key}", "Accept-Encoding": "gzip, deflate"}
        try:
            async with session.post("https://api.bochaai.com/v1/web-search", json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    reason = await self._safe_response_text(resp)
                    logger.warning(f"[AI News] bocha direct search failed status={resp.status} reason={reason}")
                    return []
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[AI News] bocha direct search failed error={exc}")
            return []
        return self._normalize_search_results(data)

    def _normalize_search_results(self, data) -> list[dict]:
        raw_items = self._extract_raw_news_items(data)
        normalized = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "title": item.get("title") or item.get("name") or "",
                    "url": item.get("url") or item.get("link") or "",
                    "description": item.get("description") or item.get("snippet") or item.get("summary") or item.get("content") or "",
                    "source": item.get("source") or item.get("siteName") or item.get("website") or "",
                    "published_at": (
                        item.get("published_at")
                        or item.get("publishedDate")
                        or item.get("published_date")
                        or item.get("date")
                        or item.get("time")
                    ),
                }
            )
        return normalized

    @staticmethod
    async def _safe_response_text(resp, limit: int = 300) -> str:
        try:
            text = await resp.text(errors="ignore")
        except Exception:
            return ""
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]

    def _format_api_key_diag(self, provider: str) -> str:
        key, source = self._get_direct_search_api_key_with_source(provider)
        enabled = provider in [item.lower() for item in self.direct_search_providers]
        state = "set" if key else "empty"
        detail = f" {self._api_key_fingerprint(key)}" if key else ""
        return f"{state} source={source} enabled={enabled}{detail}"

    def _format_api_key_debug(self, provider: str, key: str, source: str) -> str:
        enabled = provider in [item.lower() for item in self.direct_search_providers]
        state = "set" if key else "empty"
        if self.debug_log_full_api_key and key:
            return f"{state} source={source} enabled={enabled} key={key!r}"
        detail = f" {self._api_key_fingerprint(key)}" if key else ""
        return f"{state} source={source} enabled={enabled}{detail}"

    def _get_direct_search_api_key_with_source(self, provider: str) -> tuple[str, str]:
        candidates = self._get_direct_search_api_key_candidates(provider)
        if candidates:
            return candidates[0]
        return "", "none"

    def _get_direct_search_api_key_candidates(self, provider: str) -> list[tuple[str, str]]:
        provider = (provider or "").lower()
        configured = {
            "tavily": self.tavily_api_key,
            "bocha": self.bocha_api_key,
            "baidu": self.baidu_api_key,
        }.get(provider, "")
        candidates: list[tuple[str, str]] = []
        if configured:
            candidates.append((self._normalize_api_key(configured), "plugin_config"))

        cached = self._api_key_cache.get(provider)
        if cached and cached[0]:
            candidates.append(cached)
        else:
            config_key = self._discover_api_key_from_astrbot_config(provider)
            if config_key:
                result = (self._normalize_api_key(config_key), "astrbot_config_file")
                self._api_key_cache[provider] = result
                candidates.append(result)

        discovered = self._discover_api_key_from_astrbot_tool(provider)
        if discovered:
            candidates.append((self._normalize_api_key(discovered), "astrbot_tool_object"))

        result = []
        seen = set()
        for key, source in candidates:
            if not key or key in seen or not self._looks_like_provider_api_key(key, provider):
                if key and self.debug_log_full_api_key:
                    logger.warning(
                        f"[AI News] ignore invalid {provider} api key candidate "
                        f"source={source} {self._api_key_debug_value(key)}"
                    )
                continue
            seen.add(key)
            result.append((key, source))
        return result

    def _discover_api_key_from_astrbot_config(self, provider: str) -> str:
        for path in self._iter_astrbot_config_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as exc:
                self._log_config_scan_debug(provider, f"read_failed path={path} error={_safe_text(exc, 200)}")
                continue
            self._log_config_scan_debug(provider, f"scan_file path={path} size={len(text)}")
            data = self._load_config_text(text, path.suffix.lower())
            if data is not None:
                found = self._find_api_key_in_config_data(data, provider)
                if found:
                    self._log_config_scan_debug(provider, f"found_in_json path={path} {self._api_key_debug_value(found)}")
                    return found
                self._log_config_scan_debug(provider, f"not_found_in_json path={path}")
                text_found = self._find_api_key_in_text(text, provider)
                if text_found:
                    self._log_config_scan_debug(provider, f"found_in_json_text_fallback path={path} {self._api_key_debug_value(text_found)}")
                    return text_found
                continue
            found = self._find_api_key_in_text(text, provider)
            if found:
                self._log_config_scan_debug(provider, f"found_in_text path={path} {self._api_key_debug_value(found)}")
                return found
            self._log_config_scan_debug(provider, f"not_found_in_text path={path}")
        return ""

    def _iter_astrbot_config_files(self):
        suffixes = {".json", ".yaml", ".yml", ".toml", ".ini", ".conf"}
        yielded = 0
        for raw_path in self.astrbot_config_paths:
            try:
                path = Path(raw_path).expanduser()
                if not path.is_absolute():
                    path = Path.cwd() / path
                if path.is_file() and path.suffix.lower() in suffixes:
                    if path.stat().st_size <= 2 * 1024 * 1024:
                        yielded += 1
                        yield path
                    continue
                if not path.is_dir():
                    continue
                files = []
                for pattern in ("abconf*.json", "*.json", "*.yaml", "*.yml", "*.toml", "*.ini", "*.conf"):
                    files.extend(path.rglob(pattern))
                seen_paths = set()
                for file_path in files:
                    if yielded >= 200:
                        return
                    if file_path in seen_paths:
                        continue
                    seen_paths.add(file_path)
                    if not file_path.is_file() or file_path.suffix.lower() not in suffixes:
                        continue
                    try:
                        if file_path.stat().st_size > 2 * 1024 * 1024:
                            continue
                    except Exception:
                        continue
                    yielded += 1
                    yield file_path
            except Exception:
                continue

    def _format_astrbot_config_scan_diag(self) -> list[str]:
        lines = ["AstrBot config scan"]
        files = list(self._iter_astrbot_config_files())
        for raw_path in self.astrbot_config_paths:
            try:
                path = Path(raw_path).expanduser()
                if not path.is_absolute():
                    path = Path.cwd() / path
                lines.append(
                    f"path={raw_path!r}; resolved={str(path)!r}; "
                    f"exists={path.exists()}; is_dir={path.is_dir()}; is_file={path.is_file()}"
                )
            except Exception as exc:
                lines.append(f"path={raw_path!r}; error={_safe_text(exc, 200)}")
        lines.append(f"candidate_files_count={len(files)}")
        for file_path in files[:20]:
            lines.append(f"candidate_file={str(file_path)!r}")
        for provider in ("tavily", "bocha", "baidu"):
            key, file_path, mode = self._discover_api_key_from_astrbot_config_with_trace(provider)
            state = "set" if key else "empty"
            detail = self._api_key_debug_value(key) if key else ""
            lines.append(f"{provider}_config_scan={state} mode={mode} file={file_path!r} {detail}".rstrip())
        return lines

    def _discover_api_key_from_astrbot_config_with_trace(self, provider: str) -> tuple[str, str, str]:
        for path in self._iter_astrbot_config_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            data = self._load_config_text(text, path.suffix.lower())
            if data is not None:
                found = self._find_api_key_in_config_data(data, provider)
                if found:
                    return found, str(path), "json"
                text_found = self._find_api_key_in_text(text, provider)
                if text_found:
                    return text_found, str(path), "json_text_fallback"
                continue
            found = self._find_api_key_in_text(text, provider)
            if found:
                return found, str(path), "text"
        return "", "", "none"

    def _log_config_scan_debug(self, provider: str, message: str):
        if self.debug_log_full_api_key:
            logger.warning(f"[AI News] config key scan provider={provider} {message}")

    @staticmethod
    def _load_config_text(text: str, suffix: str):
        try:
            if suffix == ".json":
                return json.loads(text)
            if suffix in {".yaml", ".yml"}:
                try:
                    import yaml
                except ImportError:
                    return None
                return yaml.safe_load(text)
        except Exception:
            return None
        return None

    def _find_api_key_in_text(self, text: str, provider: str) -> str:
        provider = provider.lower()
        exact = self._find_exact_api_key_in_text(text, provider)
        if exact:
            return exact
        provider_aliases = {
            "tavily": ("tavily",),
            "bocha": ("bocha",),
            "baidu": ("baidu", "appbuilder", "qianfan"),
        }.get(provider, (provider,))
        key_pattern = r"(?:api[_-]?key|apikey|token|access[_-]?token|authorization)"
        value_pattern = r"[A-Za-z0-9_\-./+=]{16,}"
        for alias in provider_aliases:
            patterns = [
                rf"{alias}[\w\W]{{0,120}}?{key_pattern}[\w\W]{{0,40}}?[\"']?({value_pattern})[\"']?",
                rf"{key_pattern}[\w\W]{{0,80}}?{alias}[\w\W]{{0,40}}?[\"']?({value_pattern})[\"']?",
            ]
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match:
                    value = match.group(1).strip()
                    if self._looks_like_api_key(value):
                        return value
        return ""

    def _find_exact_api_key_in_text(self, text: str, provider: str) -> str:
        exact_fields = self._exact_api_key_fields(provider)
        if not exact_fields:
            return ""
        value_pattern = r"([A-Za-z0-9_\-./+=]{16,})"
        for field in exact_fields:
            patterns = [
                rf"[\"']{re.escape(field)}[\"']\s*:\s*\[\s*[\"']?{value_pattern}[\"']?",
                rf"[\"']{re.escape(field)}[\"']\s*:\s*[\"']?{value_pattern}[\"']?",
                rf"\b{re.escape(field)}\b\s*[:=]\s*\[\s*[\"']?{value_pattern}[\"']?",
                rf"\b{re.escape(field)}\b\s*[:=]\s*[\"']?{value_pattern}[\"']?",
            ]
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if not match:
                    continue
                value = self._normalize_api_key(match.group(1))
                if self._looks_like_api_key(value):
                    return value
        return ""

    def _find_api_key_in_config_data(self, data, provider: str) -> str:
        exact = self._find_exact_config_api_key(data, provider)
        if exact:
            return exact
        return self._find_api_key_in_provider_scope(data, provider, False, set())

    def _find_exact_config_api_key(self, data, provider: str) -> str:
        exact_fields = self._exact_api_key_fields(provider)
        if not exact_fields:
            return ""
        return self._find_value_by_exact_keys(data, exact_fields, set())

    @staticmethod
    def _exact_api_key_fields(provider: str) -> tuple[str, ...]:
        return {
            "tavily": ("websearch_tavily_key", "tavily_api_key"),
            "bocha": ("websearch_bocha_key", "bocha_api_key"),
            "baidu": ("websearch_baidu_app_builder_key", "baidu_api_key"),
        }.get((provider or "").lower(), ())

    def _find_value_by_exact_keys(self, obj, exact_fields: tuple[str, ...], seen: set[int]) -> str:
        if obj is None:
            return ""
        obj_id = id(obj)
        if obj_id in seen:
            return ""
        seen.add(obj_id)
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key).lower() in exact_fields:
                    found = self._find_api_key_in_object(value, "")
                    if found:
                        return found
            for value in obj.values():
                found = self._find_value_by_exact_keys(value, exact_fields, seen)
                if found:
                    return found
        elif isinstance(obj, (list, tuple, set)):
            for value in obj:
                found = self._find_value_by_exact_keys(value, exact_fields, seen)
                if found:
                    return found
        return ""

    def _find_api_key_in_provider_scope(self, obj, provider: str, in_scope: bool, seen: set[int]) -> str:
        if obj is None:
            return ""
        obj_id = id(obj)
        if obj_id in seen:
            return ""
        seen.add(obj_id)

        if isinstance(obj, dict):
            for key, value in obj.items():
                key_text = str(key).lower()
                child_scope = in_scope or self._field_mentions_provider(key_text, provider)
                if child_scope and self._is_key_value_field(key_text):
                    found = self._find_api_key_in_object(value, provider)
                    if found:
                        return found
                found = self._find_api_key_in_provider_scope(value, provider, child_scope, seen)
                if found:
                    return found
            return ""

        if isinstance(obj, (list, tuple, set)):
            for value in obj:
                found = self._find_api_key_in_provider_scope(value, provider, in_scope, seen)
                if found:
                    return found
        return ""

    @staticmethod
    def _field_mentions_provider(field: str, provider: str) -> bool:
        aliases = {
            "tavily": ("tavily", "web_search_tavily"),
            "bocha": ("bocha", "web_search_bocha"),
            "baidu": ("baidu", "appbuilder", "qianfan", "web_search_baidu"),
        }.get(provider, (provider,))
        return any(alias in field for alias in aliases)

    @staticmethod
    def _is_key_value_field(field: str) -> bool:
        field = field.lower()
        return any(marker in field for marker in ("api_key", "apikey", "token", "access_token", "authorization", "secret"))

    def _discover_api_key_from_astrbot_tool(self, provider: str) -> str:
        tool_names = {
            "tavily": ("web_search_tavily", "tavily"),
            "bocha": ("web_search_bocha", "bocha"),
            "baidu": ("web_search_baidu", "baidu", "appbuilder"),
        }.get(provider, ())
        if not tool_names:
            return ""
        for tool in self._get_web_search_tools():
            name = str(getattr(tool, "name", "") or "").lower()
            if not any(marker in name for marker in tool_names):
                continue
            key = self._find_api_key_in_object(tool, provider)
            if key and self._looks_like_provider_api_key(key, provider):
                return key
        return ""

    def _find_api_key_in_object(self, obj, provider: str, depth: int = 0, seen: set[int] | None = None) -> str:
        if obj is None or depth > 4:
            return ""
        if seen is None:
            seen = set()
        obj_id = id(obj)
        if obj_id in seen:
            return ""
        seen.add(obj_id)

        if isinstance(obj, str):
            value = obj.strip()
            if not self._looks_like_api_key(value):
                return ""
            if provider and not self._looks_like_provider_api_key(value, provider):
                return ""
            return value
        if isinstance(obj, (list, tuple, set)):
            for value in obj:
                found = self._find_api_key_in_object(value, provider, depth + 1, seen)
                if found:
                    return found
            return ""
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_text = str(key).lower()
                if self._is_api_key_field(key_text, provider):
                    found = self._find_api_key_in_object(value, provider, depth + 1, seen)
                    if found:
                        return found
            for value in obj.values():
                found = self._find_api_key_in_object(value, provider, depth + 1, seen)
                if found:
                    return found
            return ""

        attrs = getattr(obj, "__dict__", None)
        if isinstance(attrs, dict):
            found = self._find_api_key_in_object(attrs, provider, depth + 1, seen)
            if found:
                return found

        for attr in ("api_key", "apikey", "token", "access_token", "authorization", "config", "conf", "settings", "client"):
            if not hasattr(obj, attr):
                continue
            try:
                value = getattr(obj, attr)
            except Exception:
                continue
            if self._is_api_key_field(attr, provider):
                found = self._find_api_key_in_object(value, provider, depth + 1, seen)
                if found:
                    return found
            elif attr in {"config", "conf", "settings", "client"}:
                found = self._find_api_key_in_object(value, provider, depth + 1, seen)
                if found:
                    return found
        return ""

    @staticmethod
    def _is_api_key_field(field: str, provider: str) -> bool:
        field = field.lower()
        provider = provider.lower()
        if "key" not in field and "token" not in field and "authorization" not in field:
            return False
        return provider in field or "api" in field or "appbuilder" in field

    @staticmethod
    def _looks_like_api_key(value: str) -> bool:
        value = (value or "").strip()
        lowered = value.lower()
        if not value or len(value) < 16 or any(ch.isspace() for ch in value):
            return False
        if "*" in value or lowered in {"none", "null", "undefined", "apikey", "api_key", "your_api_key", "your-api-key"}:
            return False
        if any(marker in lowered for marker in ("example", "placeholder", "please_input", "请输入", "填入")):
            return False
        return True

    @staticmethod
    def _looks_like_provider_api_key(value: str, provider: str) -> bool:
        value = (value or "").strip()
        provider = (provider or "").lower()
        if not AINewsPlugin._looks_like_api_key(value):
            return False
        lowered = value.lower()
        if provider == "tavily":
            return lowered.startswith("tvly-")
        if provider == "bocha":
            return lowered.startswith("sk-") and len(value) >= 24
        if provider == "baidu":
            return lowered.startswith("bce-v3/") or len(value) >= 32
        return True

    @staticmethod
    def _normalize_api_key(value: str) -> str:
        value = (value or "").strip().strip('"').strip("'").strip()
        value = re.sub(r"[\u200b-\u200f\ufeff]", "", value)
        value = "".join(ch for ch in value if ch.isprintable())
        lowered = value.lower()
        if lowered.startswith("bearer "):
            value = value[7:].strip()
        return value

    @staticmethod
    def _api_key_fingerprint(value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        prefix = value[:6]
        suffix = value[-4:] if len(value) >= 4 else value
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return f"len={len(value)} prefix={prefix!r} suffix={suffix!r} sha256={digest}"

    def _api_key_debug_value(self, value: str) -> str:
        value = self._normalize_api_key(value)
        if not value:
            return ""
        if self.debug_log_full_api_key:
            return f"key={value!r}"
        return self._api_key_fingerprint(value)

    def _build_search_tool_params(self, topic: str, round_idx: int = 1) -> dict:
        today = datetime.now()
        today_text = today.strftime("%Y-%m-%d")
        variants = [
            f"{topic} 最新新闻 {today_text}",
            f"{topic} 今日动态 {today.year}年{today.month}月{today.day}日",
            f"{topic} 过去24小时 重大进展 {today_text}",
            f"{topic} 公司 融资 产品 发布 {today_text}",
            f"{topic} 政策 监管 产业 应用 {today_text}",
            f"{topic} latest AI news last 24 hours {today_text}",
        ]
        return {
            "query": variants[(round_idx - 1) % len(variants)],
            "topic": "news",
            "max_results": max(self.news_count * 4, self.news_count),
            "days": self.max_news_age_days,
            "recency_filter": "day" if self.max_news_age_days <= 1 else "week",
        }

    async def _llm_only(self, prompt: str, provider_id: str) -> list[NewsItem]:
        try:
            if provider_id:
                resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            else:
                resp = await self.context.get_using_provider().text_chat(prompt=prompt, session_id="ai_news_integrated")
        except Exception:
            resp = await self.context.get_using_provider().text_chat(prompt=prompt, session_id="ai_news_integrated")
        return await self._parse_and_verify(resp.completion_text if resp else "")

    async def _translate_news_to_chinese(
        self,
        news_list: list[NewsItem],
        event: AstrMessageEvent | None,
        batch_id: int = 0,
    ) -> list[NewsItem]:
        if not news_list:
            return news_list
        payload = [
            {
                "idx": idx,
                "title": item.title,
                "description": item.description,
                "source": item.source,
                "url": item.url,
                "published_at": item.published_at.isoformat() if item.published_at else "",
            }
            for idx, item in enumerate(news_list)
        ]
        prompt = (
            f"今天是 {datetime.now().strftime('%Y-%m-%d')}。请把候选搜索结果整理成真正适合新闻卡片的简体中文内容。"
            "不要直译或照抄网页标题；title 必须改写成 10-22 个汉字的新闻点，去掉站点名、栏目名、日期段、导读、周报、月报等字样。"
            "description 写成 25-55 字的一句话，说明最新进展本身，不要复述“今日新闻/早间新闻/欢迎收看”。"
            f"只保留今天或近 {self.max_news_age_days} 天内发生/发布的新闻；旧闻、综述、导读、预测盘点、教程资料、无法看出时效的内容 keep=false。"
            "Future event previews are not valid unless the article is about a new announcement made today. "
            "Remove duplicate or near-duplicate stories that describe the same event, company action, funding, ranking, or policy. "
            "Remove off-topic items even if the search result was returned for the topic. "
            "The input has already passed hard freshness, link, and topic filters. Prefer keep=true for all candidates unless a candidate is clearly invalid. "
            f"Try to keep at least {min(self.news_count, len(news_list))} candidates when possible. "
            "Keep source and url unchanged. Return strict JSON only, with this schema: "
            '{"news":[{"idx":0,"keep":true,"title":"短新闻点","description":"一句话最新进展","source":"source","url":"https://..."}]}. '
            "Input JSON: "
            + json.dumps({"news": payload}, ensure_ascii=False)
        )
        try:
            provider_id = await self._resolve_provider_id(event)
            if provider_id:
                resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            else:
                session_id = f"ai_news_rewrite_card_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{batch_id}"
                resp = await self.context.get_using_provider().text_chat(prompt=prompt, session_id=session_id)
            translated = self._parse_translation_json(resp.completion_text if resp else "")
        except Exception as exc:
            logger.warning(f"[AI News] rewrite news failed: {exc}")
            return news_list

        by_idx = {item["idx"]: item for item in translated if isinstance(item.get("idx"), int)}
        kept = []
        target_count = min(self.news_count, len(news_list))
        for idx, item in enumerate(news_list):
            data = by_idx.get(idx)
            if data and data.get("keep") is False:
                continue
            if data:
                title = _safe_text(data.get("title", ""), 28)
                desc = _safe_text(data.get("description", ""), 60)
                if title:
                    item.title = title
                if desc:
                    item.description = desc
            kept.append(item)
        if len(kept) < target_count:
            kept_ids = {id(item) for item in kept}
            for item in news_list:
                if id(item) in kept_ids:
                    continue
                kept.append(item)
                kept_ids.add(id(item))
                if len(kept) >= target_count:
                    break
        if len(kept) != len(news_list):
            logger.info(f"[AI News] rewrite kept {len(kept)} of {len(news_list)} candidates")
        return kept

    def _parse_translation_json(self, text: str) -> list[dict]:
        data = self._load_first_json_payload(text, ("news",))
        if data is None:
            return []
        raw_items = data.get("news", []) if isinstance(data, dict) else data
        return raw_items if isinstance(raw_items, list) else []

    async def _resolve_provider_id(self, event: AstrMessageEvent | None) -> str:
        if self.ai_provider_id:
            return self.ai_provider_id
        if event is not None:
            try:
                return await self.context.get_current_chat_provider_id(event.unified_msg_origin)
            except Exception:
                pass
        return ""

    def _get_web_search_tools(self) -> list:
        try:
            tool_manager = self.context.get_llm_tool_manager()
        except Exception:
            return []

        tools = []
        seen = set()
        for name in self.enabled_search_tools:
            try:
                tool = tool_manager.get_func(name)
            except Exception:
                tool = None
            if tool is not None and getattr(tool, "active", True) and tool.name not in seen:
                tools.append(tool)
                seen.add(tool.name)
        return tools

    async def _parse_and_verify(self, text: str) -> list[NewsItem]:
        candidates = self._parse_news_json(text)
        if not candidates:
            return []
        parsed_count = len(candidates)
        candidates = self._filter_recent_news(candidates)
        if not candidates:
            return []
        recent_count = len(candidates)
        verified = await self._verify_ai_news(candidates)
        logger.info(
            f"[AI News] unified filters parsed={parsed_count} recent={recent_count} verified={len(verified)}"
        )
        return verified

    def _parse_news_json(self, text: str) -> list[NewsItem]:
        data = self._load_first_json_payload(text, ("news", "results"))
        if data is None:
            logger.warning("[AI News] parse JSON failed: no usable news/results payload")
            return []

        raw_items = self._extract_raw_news_items(data)
        result: list[NewsItem] = []
        for item in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(item, dict):
                continue
            title = _safe_text(item.get("title", ""), 28)
            desc = _safe_text(
                item.get("description", "")
                or item.get("snippet", "")
                or item.get("content", "")
                or item.get("summary", ""),
                60,
            )
            source = _safe_text(item.get("source", ""), 40)
            url = str(item.get("url") or "").strip()
            published_at = self._parse_published_at(
                item.get("published_at")
                or item.get("publishedDate")
                or item.get("published_date")
                or item.get("date")
                or item.get("time")
            )
            if published_at is None:
                published_at = self._extract_date_from_text(" ".join([title, desc]))
            if not title or not url or not self._is_valid_http_url(url):
                continue
            result.append(
                NewsItem(
                    title=title,
                    url=url,
                    source=source or urlparse(url).netloc,
                    source_id="ai_integrated",
                    language="zh",
                    description=desc or title,
                    published_at=published_at,
                )
            )
        return self._deduplicate(result)

    @staticmethod
    def _extract_raw_news_items(data) -> list:
        if isinstance(data, dict):
            if isinstance(data.get("news"), list):
                return data["news"]
            if isinstance(data.get("results"), list):
                return data["results"]
            if isinstance(data.get("references"), list):
                return data["references"]
            if isinstance(data.get("search_results"), list):
                return data["search_results"]
            if isinstance(data.get("value"), list):
                return data["value"]
            if isinstance(data.get("items"), list):
                return data["items"]
            for value in data.values():
                if isinstance(value, dict):
                    items = AINewsPlugin._extract_raw_news_items(value)
                    if items:
                        return items
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value
        return data if isinstance(data, list) else []

    def _filter_recent_news(self, news_list: list[NewsItem]) -> list[NewsItem]:
        kept = []
        dropped = 0
        for item in news_list:
            if self._is_recent_news_item(item):
                kept.append(item)
            else:
                dropped += 1
        if dropped:
            logger.info(f"[AI News] dropped {dropped} stale news candidates")
        return kept

    def _is_recent_news_item(self, item: NewsItem) -> bool:
        if item.published_at is None:
            return not self._text_has_stale_date(f"{item.title} {item.description}")
        now = datetime.now(item.published_at.tzinfo) if item.published_at.tzinfo else datetime.now()
        cutoff = now - timedelta(days=self.max_news_age_days)
        future_limit = now + timedelta(hours=6)
        return cutoff <= item.published_at <= future_limit

    def _text_has_stale_date(self, text: str) -> bool:
        found = self._extract_date_from_text(text)
        if found is None:
            return False
        item = NewsItem(title="", url="", source="", source_id="", published_at=found)
        return not self._is_recent_news_item(item)

    def _extract_date_from_text(self, text: str) -> datetime | None:
        text = str(text or "")
        now = datetime.now()
        patterns = [
            r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*(\d{1,2})[:时点]",
            r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2})[:时点]",
            r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?",
            r"(\d{1,2})月(\d{1,2})日",
            r"(20\d{2})年(\d{1,2})月",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            try:
                if len(match.groups()) == 4:
                    return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)))
                if len(match.groups()) == 3 and len(match.group(1)) != 4:
                    return datetime(now.year, int(match.group(1)), int(match.group(2)), int(match.group(3)))
                if len(match.groups()) == 3 and len(match.group(1)) == 4:
                    return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                if len(match.groups()) == 2 and len(match.group(1)) == 4:
                    return datetime(int(match.group(1)), int(match.group(2)), 1)
                if len(match.groups()) == 2:
                    return datetime(now.year, int(match.group(1)), int(match.group(2)))
            except ValueError:
                continue
        return None

    def _parse_published_at(self, value) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                timestamp = float(value)
                if timestamp > 10_000_000_000:
                    timestamp /= 1000
                return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
            except Exception:
                return None
        text = str(value or "").strip()
        if not text:
            return None
        lowered = text.lower()
        now = datetime.now()
        if lowered in {"today", "今天", "今日"}:
            return now
        if lowered in {"yesterday", "昨天", "昨日"}:
            return now - timedelta(days=1)
        relative = re.search(r"(\d+)\s*(分钟|小時|小时|hour|hours|day|days|天)\s*(前|ago)?", lowered)
        if relative:
            amount = int(relative.group(1))
            unit = relative.group(2)
            if unit in {"分钟"}:
                return now - timedelta(minutes=amount)
            if unit in {"小時", "小时", "hour", "hours"}:
                return now - timedelta(hours=amount)
            return now - timedelta(days=amount)
        normalized = text.replace("Z", "+00:00")
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
            try:
                parsed = datetime.strptime(normalized, fmt)
                return parsed.replace(tzinfo=None)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.replace(tzinfo=None)
        except ValueError:
            return self._extract_date_from_text(text)

    def _filter_topic_relevant_news(self, news_list: list[NewsItem], topic: str) -> list[NewsItem]:
        kept = []
        dropped = 0
        for item in news_list:
            if self._is_topic_relevant(item, topic):
                kept.append(item)
            else:
                dropped += 1
        if dropped:
            logger.info(f"[AI News] dropped {dropped} off-topic news candidates topic={topic}")
        return kept

    def _is_topic_relevant(self, item: NewsItem, topic: str) -> bool:
        topic_text = str(topic or "").strip().lower()
        haystack = f"{item.title} {item.description} {item.source}".lower()
        if not topic_text:
            return True
        ai_topics = {"人工智能", "ai", "aigc", "大模型", "机器学习", "深度学习"}
        if topic_text in ai_topics or any(marker in topic_text for marker in ai_topics):
            ai_markers = (
                "人工智能",
                "智能体",
                " ai ",
                "ai-",
                "-ai",
                "ai ",
                " ai",
                "aigc",
                "大模型",
                "模型",
                "llm",
                "openai",
                "anthropic",
                "deepseek",
                "chatgpt",
                "gemini",
                "claude",
                "算力",
                "gpu",
                "芯片",
                "机器人",
                "自动驾驶",
                "机器学习",
                "深度学习",
            )
            padded = f" {haystack} "
            return any(marker in padded for marker in ai_markers)
        keywords = [part.lower() for part in re.split(r"[\s,，、/|]+", topic_text) if len(part.strip()) >= 2]
        return not keywords or any(keyword in haystack for keyword in keywords)

    async def _verify_ai_news(self, news_list: list[NewsItem]) -> list[NewsItem]:
        try:
            import aiohttp
        except ImportError:
            return news_list

        headers = {"User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/124 Safari/537.36"}
        connector = aiohttp.TCPConnector(ssl=False)
        timeout = aiohttp.ClientTimeout(total=self.fetch_timeout)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            results = await asyncio.gather(*(self._verify_single_ai_news(session, item) for item in news_list), return_exceptions=True)
        kept = []
        dropped = 0
        for item, status in zip(news_list, results):
            if isinstance(status, Exception):
                status = "unknown"
            if status == "invalid":
                dropped += 1
                continue
            kept.append(item)
        if dropped:
            logger.info(f"[AI News] dropped {dropped} definitely invalid news links")
        return kept or news_list

    async def _verify_single_ai_news(self, session, item: NewsItem) -> str:
        try:
            async with session.head(item.url, allow_redirects=True) as resp:
                if resp.status in {404, 410}:
                    return "invalid"
                if resp.status < 400 or resp.status in {401, 403, 405, 429}:
                    return "valid"
        except Exception:
            pass

        try:
            async with session.get(item.url, allow_redirects=True) as resp:
                if resp.status in {404, 410}:
                    return "invalid"
                if resp.status >= 400:
                    return "unknown"
                html = await resp.text(errors="ignore")
        except Exception:
            return "unknown"
        return "valid" if self._extract_page_title(html) else "unknown"

    def _topics_from_event(self, event: AstrMessageEvent) -> list[str]:
        text = (event.message_str or "").strip()
        text = re.sub(r"^/?ainews(@\S+)?", "", text, flags=re.IGNORECASE).strip()
        if not text:
            return []
        return _split_csv(text, [])

    def _save_runtime_config(self):
        push_settings = self.config.get("push_settings", {}) or {}
        if not isinstance(push_settings, dict):
            push_settings = {}
        push_settings["push_targets"] = ",".join(self.push_targets)
        self.config["push_settings"] = push_settings
        self.config.save_config()

    @staticmethod
    def _has_not_configured_error(text: str) -> bool:
        lowered = (text or "").lower()
        return any(marker.lower() in lowered for marker in NOT_CONFIGURED_MARKERS)

    @staticmethod
    def _deduplicate(items: list[NewsItem]) -> list[NewsItem]:
        seen = set()
        seen_titles = []
        result = []
        for item in items:
            key = item.url.lower().rstrip("/") or item.title
            title_key = AINewsPlugin._normalize_title_for_dedupe(item.title)
            if key in seen or AINewsPlugin._is_similar_title(title_key, seen_titles):
                continue
            seen.add(key)
            if title_key:
                seen_titles.append(title_key)
            result.append(item)
        return result

    @staticmethod
    def _normalize_title_for_dedupe(title: str) -> str:
        text = str(title or "").lower()
        text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
        text = re.sub(r"(20\d{2}年?\d{0,2}月?\d{0,2}日?|\d{1,2}月\d{1,2}日|\d{1,2}时)", "", text)
        text = re.sub(r"(新闻|消息|报道|快讯|新浪|腾讯|网易|搜狐|百度|央视|中新网)", "", text)
        return text

    @staticmethod
    def _is_similar_title(title: str, seen_titles: list[str]) -> bool:
        if not title:
            return False
        title_chars = set(title)
        for seen in seen_titles:
            if not seen:
                continue
            if title in seen or seen in title:
                return True
            seen_chars = set(seen)
            overlap = len(title_chars & seen_chars) / max(1, min(len(title_chars), len(seen_chars)))
            if overlap >= 0.72:
                return True
        return False

    @staticmethod
    def _deduplicate_raw_items(items: list[dict]) -> list[dict]:
        seen = set()
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("link") or "").lower().rstrip("/")
            title = str(item.get("title") or item.get("name") or "").strip().lower()
            key = url or title
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    @staticmethod
    def _extract_json_payload(text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        first_obj = text.find("{")
        first_arr = text.find("[")
        starts = [idx for idx in (first_obj, first_arr) if idx >= 0]
        if not starts:
            return text
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        return text[start : end + 1] if end >= start else text[start:]

    @staticmethod
    def _load_first_json_payload(text: str, preferred_keys: tuple[str, ...] = ()):
        text = (text or "").strip()
        if not text:
            return None

        candidates = [AINewsPlugin._extract_json_payload(text)]
        decoder = json.JSONDecoder()
        for idx, char in enumerate(text):
            if char not in "{[":
                continue
            try:
                value, _ = decoder.raw_decode(text[idx:])
            except Exception:
                continue
            candidates.append(value)

        fallback = None
        for candidate in candidates:
            try:
                data = json.loads(candidate) if isinstance(candidate, str) else candidate
            except Exception:
                continue
            if fallback is None:
                fallback = data
            if AINewsPlugin._json_payload_has_keys(data, preferred_keys):
                return data
        return fallback

    @staticmethod
    def _json_payload_has_keys(data, keys: tuple[str, ...]) -> bool:
        if not keys:
            return True
        if isinstance(data, dict):
            if any(isinstance(data.get(key), list) for key in keys):
                return True
            return any(AINewsPlugin._json_payload_has_keys(value, keys) for value in data.values())
        return isinstance(data, list)

    @staticmethod
    def _is_valid_http_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _extract_page_title(html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.IGNORECASE | re.DOTALL)
        return unescape(re.sub(r"\s+", " ", match.group(1))).strip() if match else ""

    @staticmethod
    def _format_fallback(news_list: list[NewsItem], topics: list[str]) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        lines = [f"AI \u65b0\u95fb\u6574\u5408\uff08{today}\uff09", f"\u9886\u57df\uff1a{', '.join(topics)}", ""]
        for idx, item in enumerate(news_list, start=1):
            topic = item.tags[0] if getattr(item, "tags", None) else "\u65b0\u95fb"
            lines.append(f"{idx}. [{item.title}]({item.url})")
            lines.append(f"   \u9886\u57df\uff1a{topic}\uff5c\u6765\u6e90\uff1a{item.source}")
            if item.description:
                lines.append(f"   {item.description}")
            lines.append("")
        lines.append("\u5361\u7247\u751f\u6210\u5931\u8d25\u65f6\u5df2\u4fdd\u7559\u53ef\u70b9\u51fb\u65b0\u95fb\u6807\u9898\u94fe\u63a5\u3002")
        return "\n".join(lines)

    async def terminate(self):
        if getattr(self, "_task", None):
            self._task.cancel()
        logger.info("[AI News] terminated")
