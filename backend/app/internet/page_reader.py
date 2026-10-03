"""RYVEN 3.0 — Page Reader Service.

Extracts readable text, hierarchical headings, links, and structured elements from web pages
while stripping noise, scripts, and redacting credentials.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional
from app.browser.models import BrowserSnapshot, ElementInfo
from app.internet.security import InternetSecurityPolicy


class PageReader:
    """Intelligent DOM and page content extractor for the Internet Agent."""

    @classmethod
    def extract_page_data(cls, body_html: str, target_url: str) -> BrowserSnapshot:
        """Parse raw HTML into a structured, noise-free BrowserSnapshot."""
        if not body_html:
            return BrowserSnapshot(
                url=target_url,
                title="Empty Page",
                text_content="",
                headings=[],
                links=[],
                interactive_elements_count=0,
            )

        # 1. Page Title
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body_html, re.IGNORECASE | re.DOTALL)
        page_title = html.unescape(title_match.group(1).strip()) if title_match else target_url

        # 2. Extract Headings (h1 - h3)
        headings: List[str] = []
        for h in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", body_html, re.IGNORECASE | re.DOTALL):
            clean_h = html.unescape(re.sub(r"<[^>]+>", "", h).strip())
            if clean_h and clean_h not in headings:
                headings.append(clean_h)
            if len(headings) >= 15:
                break

        # 3. Strip script, style, noscript, svg, and iframe noise
        cleaned_html = re.sub(
            r"<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>",
            " ",
            body_html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # 4. Extract Main Content Text
        # Prefer <main>, <article>, or role="main" if present
        main_match = re.search(r"<(?:main|article)[^>]*>(.*?)</(?:main|article)>", cleaned_html, re.IGNORECASE | re.DOTALL)
        raw_text_source = main_match.group(1) if main_match else cleaned_html

        no_tags = re.sub(r"<[^>]+>", " ", raw_text_source)
        clean_text = html.unescape(re.sub(r"\s+", " ", no_tags).strip())

        # Redact any credentials or secrets
        sanitized_text = InternetSecurityPolicy.redact_secrets(clean_text)

        # 5. Extract Interactive Links
        links: List[Dict[str, str]] = []
        for href, text in re.findall(r'<a[^>]+href=["\'](.*?)["\'][^>]*>(.*?)</a>', body_html, re.IGNORECASE | re.DOTALL):
            clean_link_text = re.sub(r"<[^>]+>", "", text).strip()
            clean_href = href.strip()
            if (
                clean_link_text
                and not clean_href.startswith(("#", "javascript:", "mailto:", "tel:"))
                and not any(l["href"] == clean_href for l in links)
            ):
                links.append({"href": clean_href, "text": clean_link_text})
            if len(links) >= 25:
                break

        return BrowserSnapshot(
            url=target_url,
            title=page_title,
            text_content=sanitized_text[:10000],
            headings=headings,
            links=links,
            interactive_elements_count=len(links),
            raw_html_truncated=body_html[:8000],
        )

    @classmethod
    def format_as_markdown(cls, snapshot: BrowserSnapshot) -> str:
        """Convert a BrowserSnapshot into clean markdown for research reports."""
        md = [f"# {snapshot.title}", f"**URL:** {snapshot.url}\n"]
        if snapshot.headings:
            md.append("## Key Sections")
            for h in snapshot.headings[:8]:
                md.append(f"- {h}")
            md.append("")

        md.append("## Content Preview")
        preview = snapshot.text_content[:2000]
        md.append(preview + ("..." if len(snapshot.text_content) > 2000 else ""))

        if snapshot.links:
            md.append("\n## Reference Links")
            for link in snapshot.links[:8]:
                md.append(f"- [{link['text']}]({link['href']})")

        return "\n".join(md)
