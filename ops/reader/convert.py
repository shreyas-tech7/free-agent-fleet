"""HTML to Markdown.

Crawl4AI does the work when it is installed (it is, in the reader venv). It only gets HTML that
the safe fetcher already fetched. It never fetches anything, never starts a browser, and its
stealth and fake user agent features are not used.
A small standard-library converter is the fallback, so the reader still works without Crawl4AI.
"""
import asyncio
import html
import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "form", "head", "nav", "footer"}
_BLOCK = {"p", "div", "section", "article", "li", "tr", "br", "ul", "ol", "table", "blockquote", "pre", "main", "header"}


def decode(body, content_type_header=""):
    match = re.search(r"charset=([\w\-]+)", content_type_header or "", re.I)
    for enc in ([match.group(1)] if match else []) + ["utf-8"]:
        try:
            return body.decode(enc, "replace")
        except LookupError:
            continue
    return body.decode("utf-8", "replace")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self.in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in _SKIP and tag != "head":
            self.skip += 1
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in _BLOCK:
            self.out.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href", "")
            if href.startswith(("http://", "https://")):
                self.out.append("[")
                self._href = href

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in _SKIP and tag != "head" and self.skip:
            self.skip -= 1
        elif tag == "a" and getattr(self, "_href", None):
            self.out.append("](%s)" % self._href)
            self._href = None
        elif tag in ("h1", "h2", "h3", "h4", "p"):
            self.out.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def fallback_convert(text):
    parser = _Text()
    parser.feed(text)
    body = html.unescape("".join(parser.out))
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n\s*\n+", "\n\n", body).strip()
    return parser.title.strip(), body


async def _crawl4ai_markdown(page_html, url):
    from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig
    try:
        from crawl4ai import AsyncHTTPCrawlerStrategy
        strategy = AsyncHTTPCrawlerStrategy()
    except ImportError:
        from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
        strategy = AsyncHTTPCrawlerStrategy()
    async with AsyncWebCrawler(crawler_strategy=strategy) as crawler:
        result = await crawler.arun(url="raw:" + page_html, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS))
    md = result.markdown
    text = getattr(md, "raw_markdown", None) or str(md or "")
    meta = getattr(result, "metadata", None) or {}
    return (meta.get("title") or "").strip(), text.strip()


def convert(body, content_type, url, content_type_header="", prefer_crawl4ai=True):
    """Return (title, markdown, converter_name)."""
    page = decode(body, content_type_header)
    if content_type in ("text/plain", "text/markdown"):
        return "", page.strip(), "plain"
    if prefer_crawl4ai:
        try:
            title, md = asyncio.run(_crawl4ai_markdown(page, url))
            if md:
                return title, md, "crawl4ai"
        except Exception:
            pass
    title, md = fallback_convert(page)
    return title, md, "fallback"
