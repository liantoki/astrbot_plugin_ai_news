# astrbot_plugin_ai_news

AstrBot AI 新闻整合插件。插件调用 AstrBot 已启用的网页搜索工具获取新闻，再交给指定 AI 模型整理为新闻卡片或文字消息。

## 功能

- 支持配置默认新闻领域，也支持 `/ainews 人工智能,半导体` 临时指定多个领域。
- 每个领域会按配置数量生成新闻条目，最终输出对应数量的卡片内容。
- 支持选择用于新闻整合的 AstrBot AI 模型。
- 搜索 API Key 在 AstrBot 对应搜索工具中配置。
- 如果图片卡片生成失败，会自动降级为文字新闻，并使用可点击的 Markdown 新闻标题保留链接。
- 英文标题和摘要会在输出前交给 AI 转写为简体中文。

## 依赖

```bash
pip install -r requirements.txt
```

`requirements.txt` 当前包含：

- `Pillow`：生成图片新闻卡片。
- `aiohttp`：验证新闻链接可访问性。

字体下载使用 Python 标准库 `urllib`，不额外依赖 `requests`。

## 字体策略

插件首次生成卡片时会按以下顺序寻找可用中文字体：

1. 环境变量 `AI_NEWS_FONT_PATH` 指定的字体文件。
2. 插件目录 `fonts/` 下的 `.otf`、`.ttf`、`.ttc` 字体。
3. Linux、macOS、Windows 常见系统字体目录中的 CJK 字体。
4. 如果仍然找不到，会自动下载 `NotoSansCJKsc-Regular.otf` 到插件目录 `fonts/`。

如果 Docker 容器没有网络，或插件目录不可写，自动下载可能失败。此时插件会跳过图片卡片，降级发送文字新闻，避免发出没有中文字体的坏图。

如果你希望完全离线发布，可以提前把可用中文字体放到插件目录：

```text
astrbot_plugin_ai_news/fonts/NotoSansCJKsc-Regular.otf
```

## Docker 注意事项

如果 AstrBot 插件管理器不会自动安装 `requirements.txt`，需要进入容器执行：

```bash
pip install -r /AstrBot/data/plugins/astrbot_plugin_ai_news/requirements.txt
```

如果容器禁止访问 GitHub/CDN，可以选择安装系统字体：

```bash
apt update && apt install -y fonts-noto-cjk
```

或者手动把 `NotoSansCJKsc-Regular.otf` 放进插件 `fonts/` 目录。

## 配置

主要配置项：

- `content_settings.ai_provider_id`：用于整合新闻的 AI 模型 ID，留空则使用当前会话默认模型。
- `content_settings.news_topics`：默认新闻领域，支持逗号分隔。
- `content_settings.news_count`：每个领域最多输出几条新闻。
- `content_settings.render_card`：是否生成图片卡片。
- `search_settings.enabled_search_tools`：启用的 AstrBot 搜索工具名称列表。
- `search_settings.stop_on_search_tool_error`：某个搜索工具失败后是否停止继续尝试。
- `push_settings.push_targets`：订阅推送目标，通常由 `/ainews_sub` 自动维护。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/ainews` | 按默认领域生成新闻 |
| `/ainews 人工智能,半导体` | 按指定领域生成新闻 |
| `/ainews_sub` | 订阅每日推送 |
| `/ainews_unsub` | 取消订阅 |
| `/ainews_status` | 查看插件状态 |
| `/ainews_diag` | 查看搜索工具解析情况 |
