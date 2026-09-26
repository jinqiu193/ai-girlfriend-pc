"""AI 每日新闻阅读系统。

每日 AI 会通过 Tavily API 搜索十条重要新闻，涵盖：
- 国内新闻
- 用户职业相关新闻
- 用户爱好相关新闻
- AI 角色职业相关新闻

搜索结果由 LLM 总结成一段新闻摘要，存入 news_digests 表，
同时写入 memU 长期记忆，让 AI 在对话中能自然引用时事。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import httpx

from llm import call_once

log = logging.getLogger("ai-girlfriend.news")

TAVILY_URL = "https://api.tavily.com/search"


def _build_queries(
    *,
    user_profession: str,
    user_hobbies: str,
    character_card: dict[str, Any],
) -> list[tuple[str, str]]:
    """Build (category, query) pairs for Tavily search."""
    queries: list[tuple[str, str]] = [
        ("国内新闻", "中国今日重要新闻"),
    ]

    if user_profession:
        queries.append(("工作新闻", f"{user_profession}行业最新动态"))

    if user_hobbies:
        queries.append(("爱好新闻", f"{user_hobbies}最新资讯"))

    char_bg = str(character_card.get("background", "") or "")
    char_info = str(character_card.get("basic_info", "") or "")
    char_text = f"{char_info} {char_bg}".strip()
    if char_text:
        char_name = character_card.get("name", "")
        queries.append(("对方职业新闻", f"{char_name} {char_text[:50]} 相关新闻"))

    return queries


async def _tavily_search(
    *,
    api_key: str,
    query: str,
    max_results: int = 3,
) -> list[dict[str, Any]]:
    """Call Tavily search API, return list of {title, url, content}."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                TAVILY_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json={
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "topic": "news",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("tavily search failed for '%s': %s", query, exc)
        return []


async def fetch_daily_digest(
    *,
    tavily_api_key: str,
    user_profession: str = "",
    user_hobbies: str = "",
    character_card: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Fetch news via Tavily and generate an LLM-summarised digest.

    Returns: {"summary": "...", "raw_titles": "..."} or empty dict on failure.
    """
    if not tavily_api_key or tavily_api_key == "tvly-replace-me":
        log.info("tavily api key not configured, skipping news digest")
        return {}

    character_card = character_card or {}
    queries = _build_queries(
        user_profession=user_profession,
        user_hobbies=user_hobbies,
        character_card=character_card,
    )

    all_headlines: list[str] = []
    category_results: list[str] = []

    for category, query in queries:
        results = await _tavily_search(api_key=tavily_api_key, query=query)
        if not results:
            continue
        titles = [r.get("title", "") for r in results if r.get("title")]
        all_headlines.extend(titles)
        snippets = []
        for r in results[:3]:
            title = r.get("title", "")
            content = (r.get("content") or "")[:150]
            snippets.append(f"- {title}: {content}")
        category_results.append(f"【{category}】\n" + "\n".join(snippets))

    if not all_headlines:
        log.info("no news results returned, skipping digest")
        return {}

    raw_titles = "\n".join(all_headlines[:10])
    news_block = "\n\n".join(category_results)

    char_name = character_card.get("nickname") or character_card.get("name") or "角色"
    now = datetime.now()

    prompt = (
        f"你是{char_name}。现在是{now.strftime('%Y年%m月%d日')}。\n"
        f"你今天看了以下新闻：\n\n{news_block}\n\n"
        "请用你的语气和性格，把这些新闻总结成一段100-200字的摘要。\n"
        "- 像跟朋友聊天一样自然地讲述，不要罗列标题\n"
        "- 可以加入你的感受和评论\n"
        "- 保留关键信息（什么事、谁、什么影响）\n"
        "- 纯文本，不要 markdown\n"
    )

    try:
        summary = await call_once(
            [{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        summary = summary.strip()[:500]
        if summary:
            return {"summary": summary, "raw_titles": raw_titles}
    except Exception as exc:  # noqa: BLE001
        log.warning("news digest LLM summarisation failed: %s", exc)

    return {}