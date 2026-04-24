import asyncio
import json
import re
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import urlparse

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.tool import ToolSet

from .card import generate_news_card
from .sources import NewsItem

DEFAULT_SEARCH_TOOLS = ["web_search_tavily", "web_search_bocha", "web_search_brave", "web_search_baidu"]
NOT_CONFIGURED_MARKERS = ("api key is not configured", "not configured", "\u672a\u914d\u7f6e")


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
    "\u4f7f\u7528 AstrBot \u641c\u7d22\u5de5\u5177\u548c\u6307\u5b9a AI \u6a21\u578b\u6574\u5408\u6307\u5b9a\u9886\u57df\u65b0\u95fb\u3002",
    "4.0.2",
    "https://github.com/yourname/astrbot_plugin_ai_news",
)
class AINewsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._apply_config()
        self._task = asyncio.create_task(self._schedule_daily_push())
        logger.info(
            f"[AI News] loaded provider={self.ai_provider_id or 'current'} "
            f"topics={','.join(self.news_topics)} tools={','.join(self.enabled_search_tools)}"
        )

    def _apply_config(self):
        timing = self.config.get("timing", {}) or {}
        content = self.config.get("content_settings", {}) or {}
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
        self.news_topics = _split_csv(g(content, "news_topics", "\u4eba\u5de5\u667a\u80fd"), ["\u4eba\u5de5\u667a\u80fd"])
        self.ai_provider_id = str(g(content, "ai_provider_id", "") or "").strip()
        self.render_card = _to_bool(g(content, "render_card", True), True)

        self.enabled_search_tools = _split_csv(
            g(search, "enabled_search_tools", ",".join(DEFAULT_SEARCH_TOOLS)),
            DEFAULT_SEARCH_TOOLS,
        )
        self.stop_on_search_tool_error = _to_bool(g(search, "stop_on_search_tool_error", False), False)

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
                    f"\u641c\u7d22\u5de5\u5177\uff1a{', '.join(self.enabled_search_tools)}",
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
            f"resolved_tools={[getattr(tool, 'name', '?') for tool in tools]}",
            f"stop_on_search_tool_error={self.stop_on_search_tool_error}",
            "Search API keys are managed by AstrBot search tools, not this plugin.",
        ]
        for tool in tools:
            attrs = []
            for attr in ("name", "active", "description"):
                if hasattr(tool, attr):
                    attrs.append(f"{attr}={getattr(tool, attr)}")
            lines.append("; ".join(attrs))
        yield event.plain_result("\n".join(lines))

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
            return "\u672a\u80fd\u83b7\u53d6\u5230\u53ef\u7528\u65b0\u95fb\u3002\u8bf7\u68c0\u67e5\u641c\u7d22\u5de5\u5177 API \u662f\u5426\u5728 AstrBot \u5bf9\u5e94\u5de5\u5177\u4e2d\u751f\u6548\u3002"
        news_list = await self._translate_news_to_chinese(news_list, event)

        if self.render_card:
            img_path = generate_news_card(
                news_list,
                ai_summary=f"\u9886\u57df\uff1a{', '.join(topics)}\uff1b\u6bcf\u4e2a\u9886\u57df\u6700\u591a {self.news_count} \u6761",
            )
            if img_path:
                return (img_path, "")

        return self._format_fallback(news_list, topics)

    async def _generate_integrated_news(self, event: AstrMessageEvent | None, topics: list[str]) -> list[NewsItem]:
        all_items: list[NewsItem] = []
        for topic in topics:
            items = await self._run_search_agent_with_failover(event, self._build_prompt(topic))
            for item in items[: self.news_count]:
                item.tags = list(dict.fromkeys([topic, *getattr(item, "tags", [])]))
                all_items.append(item)
        return self._deduplicate(all_items)

    def _build_prompt(self, topic: str) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return (
            f"\u8bf7\u641c\u7d22\u5e76\u6574\u5408 {today} \u524d\u540e\u6700\u65b0\u7684\u201c{topic}\u201d\u9886\u57df\u65b0\u95fb\u3002"
            f"\u8fd4\u56de {self.news_count * 2} \u6761\u5019\u9009\uff0c\u4f18\u5148\u9009\u62e9\u6709\u660e\u786e\u6765\u6e90\u548c\u53ef\u8bbf\u95ee\u94fe\u63a5\u7684\u65b0\u95fb\u3002"
            "\u53ea\u8f93\u51fa JSON\uff0c\u4e0d\u8981\u8f93\u51fa Markdown \u6216\u89e3\u91ca\u3002"
            '{"news":[{"title":"news title","description":"summary","source":"source","url":"https://..."}]}'
        )

    async def _run_search_agent_with_failover(self, event: AstrMessageEvent | None, prompt: str) -> list[NewsItem]:
        provider_id = await self._resolve_provider_id(event)
        search_tools = self._get_web_search_tools()

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
                        max_steps=2,
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

    async def _llm_only(self, prompt: str, provider_id: str) -> list[NewsItem]:
        try:
            if provider_id:
                resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            else:
                resp = await self.context.get_using_provider().text_chat(prompt=prompt, session_id="ai_news_integrated")
        except Exception:
            resp = await self.context.get_using_provider().text_chat(prompt=prompt, session_id="ai_news_integrated")
        return await self._parse_and_verify(resp.completion_text if resp else "")

    async def _translate_news_to_chinese(self, news_list: list[NewsItem], event: AstrMessageEvent | None) -> list[NewsItem]:
        if not news_list:
            return news_list
        payload = [
            {
                "idx": idx,
                "title": item.title,
                "description": item.description,
                "source": item.source,
                "url": item.url,
            }
            for idx, item in enumerate(news_list)
        ]
        prompt = (
            "Translate or rewrite every news title and description into natural Simplified Chinese. "
            "Keep source and url unchanged. Return strict JSON only, with this schema: "
            '{"news":[{"idx":0,"title":"中文标题","description":"中文摘要","source":"source","url":"https://..."}]}. '
            "Input JSON: "
            + json.dumps({"news": payload}, ensure_ascii=False)
        )
        prompt = (
            "Translate or rewrite every news title and description into natural Simplified Chinese. "
            "Keep source and url unchanged. Return strict JSON only, with this schema: "
            '{"news":[{"idx":0,"title":"\u4e2d\u6587\u6807\u9898","description":"\u4e2d\u6587\u6458\u8981","source":"source","url":"https://..."}]}. '
            "Input JSON: "
            + json.dumps({"news": payload}, ensure_ascii=False)
        )
        try:
            provider_id = await self._resolve_provider_id(event)
            if provider_id:
                resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            else:
                resp = await self.context.get_using_provider().text_chat(prompt=prompt, session_id="ai_news_translate_zh")
            translated = self._parse_translation_json(resp.completion_text if resp else "")
        except Exception as exc:
            logger.warning(f"[AI News] translate to Chinese failed: {exc}")
            return news_list

        by_idx = {item["idx"]: item for item in translated if isinstance(item.get("idx"), int)}
        for idx, item in enumerate(news_list):
            data = by_idx.get(idx)
            if not data:
                continue
            title = _safe_text(data.get("title", ""), 28)
            desc = _safe_text(data.get("description", ""), 60)
            if title:
                item.title = title
            if desc:
                item.description = desc
        return news_list

    def _parse_translation_json(self, text: str) -> list[dict]:
        try:
            data = json.loads(self._extract_json_payload(text))
        except Exception:
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
        verified = await self._verify_ai_news(candidates)
        return verified or candidates

    def _parse_news_json(self, text: str) -> list[NewsItem]:
        try:
            payload = self._extract_json_payload(text)
            data = json.loads(payload)
            raw_items = data.get("news", []) if isinstance(data, dict) else data
        except Exception as exc:
            logger.warning(f"[AI News] parse JSON failed: {exc}")
            return []

        result: list[NewsItem] = []
        for item in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(item, dict):
                continue
            title = _safe_text(item.get("title", ""), 28)
            desc = _safe_text(item.get("description", ""), 60)
            source = _safe_text(item.get("source", ""), 40)
            url = str(item.get("url") or "").strip()
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
                )
            )
        return self._deduplicate(result)

    async def _verify_ai_news(self, news_list: list[NewsItem]) -> list[NewsItem]:
        try:
            import aiohttp
        except ImportError:
            return []

        headers = {"User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/124 Safari/537.36"}
        connector = aiohttp.TCPConnector(ssl=False)
        timeout = aiohttp.ClientTimeout(total=self.fetch_timeout)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            results = await asyncio.gather(*(self._verify_single_ai_news(session, item) for item in news_list), return_exceptions=True)
        return [item for item, ok in zip(news_list, results) if ok is True]

    async def _verify_single_ai_news(self, session, item: NewsItem) -> bool:
        try:
            async with session.get(item.url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    return False
                html = await resp.text(errors="ignore")
        except Exception:
            return False
        return bool(self._extract_page_title(html)) or True

    def _topics_from_event(self, event: AstrMessageEvent) -> list[str]:
        text = (event.message_str or "").strip()
        text = re.sub(r"^/?ainews(@\S+)?", "", text, flags=re.IGNORECASE).strip()
        if not text:
            return []
        return _split_csv(text, [])

    def _save_runtime_config(self):
        self.config["push_targets"] = ",".join(self.push_targets)
        self.config.save_config()

    @staticmethod
    def _has_not_configured_error(text: str) -> bool:
        lowered = (text or "").lower()
        return any(marker.lower() in lowered for marker in NOT_CONFIGURED_MARKERS)

    @staticmethod
    def _deduplicate(items: list[NewsItem]) -> list[NewsItem]:
        seen = set()
        result = []
        for item in items:
            key = item.url.lower().rstrip("/") or item.title
            if key not in seen:
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
