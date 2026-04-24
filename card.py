import os
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.request import urlretrieve

from astrbot.api import logger

CARD_W = 900
PAD = 44
GAP = 18

PLUGIN_DIR = Path(__file__).resolve().parent
FONT_DIR = PLUGIN_DIR / "fonts"

_FONT_CACHE = {}
_FONT_FAILURE_LOGGED = False
_FONT_SUCCESS_LOGGED = False
_FONT_DOWNLOAD_ATTEMPTED = False

CJK_TEST_TEXT = "\u4e2d\u6587\u65b0\u95fb"
DOWNLOADED_FONT = FONT_DIR / "NotoSansCJKsc-Regular.otf"
FONT_DOWNLOAD_URLS = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
    "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
)
FONT_NAME_HINTS = (
    "NotoSansCJK",
    "NotoSansSC",
    "NotoSansHans",
    "SourceHanSans",
    "SourceHanSerif",
    "WenQuanYi",
    "wqy",
    "PingFang",
    "Microsoft YaHei",
    "msyh",
    "SimHei",
    "simhei",
    "SimSun",
    "simsun",
)


def _iter_font_files(root: Path):
    if not root.exists():
        return
    for pattern in ("*.otf", "*.ttf", "*.ttc"):
        yield from root.rglob(pattern)


def _font_candidates() -> list[str]:
    candidates: list[str] = []

    if os.environ.get("AI_NEWS_FONT_PATH"):
        candidates.append(os.environ["AI_NEWS_FONT_PATH"])

    preferred_names = (
        "NotoSansCJKsc-Regular.otf",
        "SourceHanSansSC-Regular.otf",
        "SourceHanSansCN-Regular.otf",
        "msyh.ttc",
        "simhei.ttf",
    )
    candidates.extend(str(FONT_DIR / name) for name in preferred_names)

    for path in _iter_font_files(FONT_DIR) or []:
        candidates.append(str(path))

    common_dirs = [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path("/usr/share/fonts/opentype"),
        Path("/usr/share/fonts/truetype"),
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path("C:/Windows/Fonts"),
    ]
    for root in common_dirs:
        for path in _iter_font_files(root) or []:
            name = path.name
            if any(hint.lower() in name.lower() for hint in FONT_NAME_HINTS):
                candidates.append(str(path))

    candidates.extend(
        [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
        ]
    )

    seen = set()
    result = []
    for path in candidates:
        norm = os.path.abspath(os.path.expanduser(path))
        if norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def _download_default_font() -> bool:
    global _FONT_DOWNLOAD_ATTEMPTED
    if _FONT_DOWNLOAD_ATTEMPTED:
        return False
    _FONT_DOWNLOAD_ATTEMPTED = True
    FONT_DIR.mkdir(parents=True, exist_ok=True)

    for url in FONT_DOWNLOAD_URLS:
        tmp_path = DOWNLOADED_FONT.with_suffix(".download")
        try:
            logger.info(f"[AI News] downloading CJK font: {url}")
            if tmp_path.exists():
                tmp_path.unlink()
            urlretrieve(url, tmp_path)
            tmp_path.replace(DOWNLOADED_FONT)
            logger.info(f"[AI News] downloaded CJK font to: {DOWNLOADED_FONT}")
            return True
        except Exception as exc:
            logger.warning(f"[AI News] font download failed from {url}: {exc}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
    return False


def _font_supports_cjk(font) -> bool:
    try:
        bbox = font.getbbox(CJK_TEST_TEXT)
        return bool(bbox and bbox[2] > bbox[0] and bbox[3] > bbox[1])
    except Exception:
        try:
            return bool(font.getmask(CJK_TEST_TEXT).getbbox())
        except Exception:
            return False


def _load_font(size: int, bold: bool = False):
    global _FONT_FAILURE_LOGGED, _FONT_SUCCESS_LOGGED
    from PIL import ImageFont

    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    font, failures = _try_load_font(size)
    if font is None and _download_default_font():
        font, more_failures = _try_load_font(size)
        failures.extend(more_failures)

    if font is not None:
        _FONT_CACHE[key] = font
        return font

    if not _FONT_FAILURE_LOGGED:
        detail = "; ".join(failures[:5]) if failures else "no candidate font files found"
        logger.warning(f"[AI News] no usable CJK font found, skip image card. {detail}")
        _FONT_FAILURE_LOGGED = True
    return None


def _try_load_font(size: int):
    global _FONT_SUCCESS_LOGGED
    from PIL import ImageFont

    failures = []
    for path in _font_candidates():
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
            if not _font_supports_cjk(font):
                failures.append(f"{path}: no CJK glyph")
                continue
            if not _FONT_SUCCESS_LOGGED:
                logger.info(f"[AI News] loaded CJK font: {path}")
                _FONT_SUCCESS_LOGGED = True
            return font, failures
        except Exception as exc:
            failures.append(f"{path}: {exc}")
    return None, failures


def _wrap(text: str, font, max_width: int, draw) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return [""]
    lines = []
    current = ""
    for char in text:
        test = current + char
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def _topic_of(item) -> str:
    tags = getattr(item, "tags", None) or []
    return str(tags[0]) if tags else "\u7efc\u5408"


def generate_news_card(news_list: list, ai_summary: str = "", today: str = "") -> str | None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("[AI News] Pillow is not installed, fallback to text")
        return None

    if not news_list:
        return None

    today = today or datetime.now().strftime("%Y-%m-%d")
    title_font = _load_font(34, True)
    date_font = _load_font(18)
    summary_font = _load_font(18)
    num_font = _load_font(26, True)
    body_font = _load_font(22, True)
    desc_font = _load_font(17)
    meta_font = _load_font(15)
    footer_font = _load_font(14)

    if not all([title_font, date_font, summary_font, num_font, body_font, desc_font, meta_font, footer_font]):
        return None

    temp = Image.new("RGB", (CARD_W, 100), "white")
    draw = ImageDraw.Draw(temp)
    inner_w = CARD_W - PAD * 2
    text_w = inner_w - 74

    summary_lines = _wrap(ai_summary, summary_font, inner_w, draw)[:2] if ai_summary else []
    item_blocks = []
    for item in news_list:
        title_lines = _wrap(item.title, body_font, text_w, draw)[:2]
        desc_lines = _wrap(item.description or "", desc_font, text_w, draw)[:2]
        height = 26 + len(title_lines) * 30 + len(desc_lines) * 24 + 24 + GAP
        item_blocks.append((title_lines, desc_lines, max(112, height)))

    header_h = 150 + len(summary_lines) * 28
    total_h = header_h + sum(block[2] for block in item_blocks) + PAD + 48
    img = Image.new("RGB", (CARD_W, total_h), (247, 248, 244))
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, CARD_W, header_h], fill=(20, 35, 32))
    draw.ellipse([-120, -160, 260, 220], fill=(54, 94, 73))
    draw.ellipse([CARD_W - 160, 20, CARD_W + 90, 270], fill=(181, 115, 73))

    title = "\u0041\u0049 \u65b0\u95fb\u6574\u5408"
    draw.text(((CARD_W - draw.textlength(title, font=title_font)) / 2, 38), title, font=title_font, fill=(255, 249, 232))
    draw.text(((CARD_W - draw.textlength(today, font=date_font)) / 2, 86), today, font=date_font, fill=(223, 231, 217))

    sy = 116
    for line in summary_lines:
        draw.text(((CARD_W - draw.textlength(line, font=summary_font)) / 2, sy), line, font=summary_font, fill=(238, 220, 184))
        sy += 28

    y = header_h + 26
    for idx, (item, block) in enumerate(zip(news_list, item_blocks), start=1):
        title_lines, desc_lines, block_h = block
        x0 = PAD
        y0 = y
        x1 = CARD_W - PAD
        y1 = y + block_h - GAP
        draw.rounded_rectangle([x0, y0, x1, y1], radius=22, fill=(255, 253, 247), outline=(224, 221, 210), width=1)
        draw.rounded_rectangle([x0 + 22, y0 + 24, x0 + 58, y0 + 60], radius=10, fill=(221, 116, 79))
        number = str(idx)
        draw.text((x0 + 40 - draw.textlength(number, font=num_font) / 2, y0 + 24), number, font=num_font, fill=(255, 248, 236))

        tx = x0 + 78
        ty = y0 + 22
        for line in title_lines:
            draw.text((tx, ty), line, font=body_font, fill=(24, 33, 29))
            ty += 30
        for line in desc_lines:
            draw.text((tx, ty + 4), line, font=desc_font, fill=(85, 88, 82))
            ty += 24

        meta = f"{_topic_of(item)} | {item.source}"
        draw.text((tx, y1 - 28), meta, font=meta_font, fill=(130, 105, 77))
        y += block_h

    footer = "/ainews \u9886\u57df1,\u9886\u57df2    /ainews_sub \u8ba2\u9605"
    draw.text(((CARD_W - draw.textlength(footer, font=footer_font)) / 2, total_h - 34), footer, font=footer_font, fill=(118, 123, 116))

    out_path = os.path.join(tempfile.gettempdir(), "ainews_card.png")
    img.save(out_path, "PNG", optimize=True)
    return out_path
