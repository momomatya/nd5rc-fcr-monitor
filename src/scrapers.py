# -*- coding: utf-8 -*-
"""
X（旧Twitter）、ヤフオク、メルカリ、ジモティー スクレイピングモジュール
（Playwright ヘッドレスChromium + 海外IP/WAF回避フォールバック対応版）
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from urllib.parse import quote_plus, unquote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}


def now_iso_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _parse_cookies_for_playwright(cookies_raw: str) -> List[Dict[str, Any]]:
    """
    X_COOKIES (JSONリスト形式 または JSON辞書形式) を
    Playwright の context.add_cookies() 用のリストに変換する。
    """
    try:
        data = json.loads(cookies_raw)
    except Exception as exc:
        logger.error("X_COOKIES のJSONパースに失敗しました: %s", exc)
        return []

    cookies_map: Dict[str, str] = {}
    if isinstance(data, list):
        for c in data:
            if isinstance(c, dict) and "name" in c and "value" in c:
                cookies_map[str(c["name"])] = str(c["value"])
    elif isinstance(data, dict):
        for k, v in data.items():
            cookies_map[str(k)] = str(v)

    pw_cookies: List[Dict[str, Any]] = []
    for name, value in cookies_map.items():
        if not name or value is None:
            continue
        pw_cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }
        )
        pw_cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".twitter.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }
        )
    return pw_cookies


def _extract_tweets_from_graphql_payload(payload: Any) -> List[Dict[str, Any]]:
    """
    XのSearchTimeline GraphQLレスポンスJSONからツイート情報を再帰的に抽出する。
    """
    extracted: List[Dict[str, Any]] = []
    seen_ids = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            # Tweetオブジェクトの判定
            legacy = node.get("legacy")
            rest_id = node.get("rest_id")
            if isinstance(legacy, dict) and rest_id and "full_text" in legacy:
                tweet_id = str(rest_id)
                if tweet_id not in seen_ids:
                    seen_ids.add(tweet_id)
                    text = str(legacy.get("full_text", "")).strip()
                    # リツイート自体は除外
                    if not text.startswith("RT @"):
                        screen_name = "i/web"
                        core = node.get("core", {})
                        if isinstance(core, dict):
                            user_results = core.get("user_results", {}).get("result", {})
                            if isinstance(user_results, dict):
                                screen_name = (
                                    user_results.get("core", {}).get("screen_name")
                                    or user_results.get("legacy", {}).get("screen_name")
                                    or "i/web"
                                )

                        thumb = ""
                        entities = legacy.get("extended_entities") or legacy.get("entities") or {}
                        media_list = entities.get("media", []) if isinstance(entities, dict) else []
                        if media_list and isinstance(media_list[0], dict):
                            thumb = str(media_list[0].get("media_url_https", ""))

                        first_line = text.splitlines()[0][:80] if text else tweet_id
                        extracted.append(
                            {
                                "id": f"x_{tweet_id}",
                                "source": "X",
                                "title": f"【X個人売買】{first_line}",
                                "description": text,
                                "raw_price_yen": None,
                                "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                                "thumbnail": thumb,
                                "detected_at": now_iso_jst(),
                            }
                        )
            for val in node.values():
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return extracted


async def _fetch_x_via_playwright(query: str, cookies_raw: str) -> List[Dict[str, Any]]:
    """
    Playwright (ヘッドレスChromium) と X_COOKIES を用いて
    Xの最新検索タイムライン (f=live) を開き、GraphQL応答とDOMの両方からポストを取得する。
    （twikit の KEY_BYTE indices エラーを完全に回避）
    """
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright がインストールされていないためX検索をスキップします。")
        return results

    pw_cookies = _parse_cookies_for_playwright(cookies_raw)
    if not pw_cookies:
        logger.warning("有効な X_COOKIES が見つからないためX検索をスキップします。")
        return results

    # 検索ヒット率を高めるため、複雑なクエリに加えてシンプルな検索クエリも巡回
    queries_to_run = [
        query,
        "ND5RC (売ります OR 譲ります OR 個人売買 OR 手元 OR 車両売却) -業者",
        "ロードスター (売ります OR 個人売買 OR 車両売却) ND",
    ]
    # 重複除去
    unique_queries = list(dict.fromkeys(q.strip() for q in queries_to_run if q.strip()))

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = await browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                viewport={"width": 1280, "height": 900},
            )
            await context.add_cookies(pw_cookies)
            page = await context.new_page()

            async def handle_response(response: Any) -> None:
                try:
                    if "SearchTimeline" in response.url and response.status == 200:
                        data = await response.json()
                        for item in _extract_tweets_from_graphql_payload(data):
                            if item["id"] not in seen_ids:
                                seen_ids.add(item["id"])
                                results.append(item)
                except Exception:
                    pass

            page.on("response", handle_response)

            for q in unique_queries:
                search_url = f"https://x.com/search?q={quote_plus(q)}&src=typed_query&f=live"
                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(4500)

                    # スクロールして追加読み込み
                    await page.mouse.wheel(0, 1200)
                    await page.wait_for_timeout(2000)

                    # DOMからもフォールバック抽出
                    articles = await page.query_selector_all("article[data-testid='tweet']")
                    for art in articles:
                        link_el = await art.query_selector("a[href*='/status/']")
                        if not link_el:
                            continue
                        href = await link_el.get_attribute("href") or ""
                        m_status = re.search(r"/([^/]+)/status/(\d+)", href)
                        if not m_status:
                            continue
                        screen_name, tweet_id = m_status.group(1), m_status.group(2)
                        item_id = f"x_{tweet_id}"
                        if item_id in seen_ids:
                            continue

                        text_el = await art.query_selector("div[data-testid='tweetText']")
                        text = (await text_el.inner_text()).strip() if text_el else ""
                        if not text:
                            continue

                        img_el = await art.query_selector("div[data-testid='tweetPhoto'] img")
                        thumb = (await img_el.get_attribute("src") or "") if img_el else ""

                        seen_ids.add(item_id)
                        first_line = text.splitlines()[0][:80]
                        results.append(
                            {
                                "id": item_id,
                                "source": "X",
                                "title": f"【X個人売買】{first_line}",
                                "description": text,
                                "raw_price_yen": None,
                                "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                                "thumbnail": thumb,
                                "detected_at": now_iso_jst(),
                            }
                        )
                except Exception as q_exc:
                    logger.warning("X検索クエリ実行中に例外 (%s): %s", q, q_exc)

            await browser.close()
    except Exception as exc:
        logger.warning("X (Playwright) 取得中に例外が発生しました: %s", exc)

    logger.info("X 取得件数: %d 件", len(results))
    return results


def fetch_x_posts(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    X（旧Twitter）からNDロードスター個人売買投稿を取得する。
    """
    cookies_raw = os.environ.get("X_COOKIES", "").strip()
    query = config.get("queries", {}).get(
        "x",
        '("NDロードスター" OR "ND5RC" OR "ロードスター ND") ("売ります" OR "譲ります" OR "個人売買" OR "手元" OR "降りる" OR "車両売却") -業者 -買取',
    )
    if not cookies_raw:
        logger.info("X_COOKIES が未設定のため、X巡回をスキップします。")
        return []

    return asyncio.run(_fetch_x_via_playwright(query, cookies_raw))


async def _fetch_mercari_via_playwright(search_url: str) -> List[Dict[str, Any]]:
    """
    Playwrightでメルカリの検索画面をレンダリングし、動的DOMから出品情報を抽出する。
    """
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return results

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
            )
            page = await context.new_page()
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(4500)

            anchors = await page.query_selector_all("a[href*='/item/m']")
            for a_el in anchors:
                href = await a_el.get_attribute("href") or ""
                m_id = re.search(r"/item/(m\d+)", href)
                if not m_id:
                    continue
                item_id = m_id.group(1)
                if item_id in seen_ids:
                    continue

                text = (await a_el.inner_text()).strip()
                aria = (await a_el.get_attribute("aria-label") or "").strip()
                combined_text = f"{aria} {text}".strip()

                img_el = await a_el.query_selector("img")
                thumb = (await img_el.get_attribute("src") or "") if img_el else ""
                if not aria and img_el:
                    alt_text = (await img_el.get_attribute("alt") or "").strip()
                    combined_text = f"{alt_text} {combined_text}".strip()

                price_yen: Optional[int] = None
                m_price = re.search(r"(?:[¥￥]|([\d,]+)\s*円)\s*([\d,]*)", combined_text)
                if m_price:
                    digits = re.sub(r"[^\d]", "", m_price.group(0))
                    if digits:
                        price_yen = int(digits)

                clean_title = re.sub(r"\s+", " ", combined_text)[:100] or f"メルカリ出品 {item_id}"
                seen_ids.add(item_id)
                results.append(
                    {
                        "id": f"mercari_{item_id}",
                        "source": "メルカリ",
                        "title": clean_title,
                        "description": combined_text or clean_title,
                        "raw_price_yen": price_yen,
                        "url": f"https://jp.mercari.com/item/{item_id}",
                        "thumbnail": thumb,
                        "detected_at": now_iso_jst(),
                    }
                )
            await browser.close()
    except Exception as exc:
        logger.warning("メルカリ (Playwright) 取得中に例外が発生しました: %s", exc)

    logger.info("メルカリ 取得件数: %d 件", len(results))
    return results


def fetch_mercari(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    メルカリ（自動車本体カテゴリ）からNDロードスターMT車を抽出する。
    """
    min_price_yen = int(float(config.get("monitoring", {}).get("min_price_man", 60)) * 10000) + 1
    query = config.get("queries", {}).get("mercari", "NDロードスター MT 車体")

    search_url = (
        f"https://jp.mercari.com/search"
        f"?keyword={quote_plus(query)}&price_min={min_price_yen}&status=on_sale&sort=created_time&order=desc"
    )
    return asyncio.run(_fetch_mercari_via_playwright(search_url))


def _fallback_search_via_ddg(site_domain: str, keyword: str, source_label: str, id_prefix: str) -> List[Dict[str, Any]]:
    """
    GitHub Actionsの海外IPからヤフオクやジモティーがジオブロック/403される場合の
    検索インデックス経由フォールバック取得。
    """
    results: List[Dict[str, Any]] = []
    q = f"site:{site_domain} {keyword}"
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(q)}"
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if resp.status_code != 200:
            return results
        soup = BeautifulSoup(resp.text, "html.parser")
        for row in soup.select(".result"):
            a_el = row.select_one("a.result__a")
            snip_el = row.select_one(".result__snippet")
            if not a_el:
                continue
            title = a_el.get_text(" ", strip=True)
            raw_href = a_el.get("href", "")
            m_uddg = re.search(r"uddg=([^&]+)", raw_href)
            real_url = unquote(m_uddg.group(1)) if m_uddg else raw_href
            if site_domain not in real_url:
                continue

            desc = snip_el.get_text(" ", strip=True) if snip_el else title
            combined = f"{title} {desc}"

            price_yen: Optional[int] = None
            m_man = re.search(r"(\d+(?:\.\d+)?)\s*万\s*円", combined)
            m_yen = re.search(r"([\d,]{6,})\s*円", combined)
            if m_man:
                price_yen = int(float(m_man.group(1)) * 10000)
            elif m_yen:
                price_yen = int(m_yen.group(1).replace(",", ""))

            item_key = re.sub(r"\W+", "", real_url[-20:])
            results.append(
                {
                    "id": f"{id_prefix}_{item_key}",
                    "source": source_label,
                    "title": title,
                    "description": combined,
                    "raw_price_yen": price_yen,
                    "url": real_url,
                    "thumbnail": "",
                    "detected_at": now_iso_jst(),
                }
            )
    except Exception as exc:
        logger.warning("%s フォールバック検索中に例外: %s", source_label, exc)
    return results


def fetch_yahoo_auctions(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    ヤフオク（中古車・新車 ＞ マツダ ＞ ロードスター Category: 26360）から
    「個人出品のみ（ストア除外: abatch=2）」「60万円超」のNDロードスターMT車を抽出する。
    """
    results: List[Dict[str, Any]] = []
    min_price_yen = int(float(config.get("monitoring", {}).get("min_price_man", 60)) * 10000) + 1
    query = config.get("queries", {}).get("yahoo", "ND5RC ロードスター MT")

    search_url = (
        f"https://auctions.yahoo.co.jp/search/search"
        f"?p={quote_plus(query)}&auccat=26360&abatch=2&min={min_price_yen}&istatus=0&s1=new&o1=d"
    )

    try:
        resp = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for item in soup.select("li.Product"):
                link_el = item.select_one("a.Product__titleLink")
                price_el = item.select_one("span.Product__priceValue")
                img_el = item.select_one("img.Product__imageData")

                if not link_el:
                    continue

                title = link_el.get_text(strip=True)
                url = link_el.get("href", "")
                m_id = re.search(r"/auction/([a-zA-Z0-9]+)", url)
                auc_id = m_id.group(1) if m_id else re.sub(r"\W+", "", url[-16:])

                price_yen = None
                if price_el:
                    digits = re.sub(r"[^\d]", "", price_el.get_text())
                    if digits:
                        price_yen = int(digits)

                thumb = img_el.get("src", "") if img_el else ""

                results.append(
                    {
                        "id": f"yahoo_{auc_id}",
                        "source": "ヤフオク",
                        "title": title,
                        "description": title,
                        "raw_price_yen": price_yen,
                        "url": url,
                        "thumbnail": thumb,
                        "detected_at": now_iso_jst(),
                    }
                )
    except Exception as exc:
        logger.warning("ヤフオク直接取得中に例外が発生しました (海外IPブロック等): %s", exc)

    if not results:
        results = _fallback_search_via_ddg(
            "auctions.yahoo.co.jp/jp/auction",
            "ND5RC ロードスター MT",
            "ヤフオク",
            "yahoo",
        )

    logger.info("ヤフオク 取得件数: %d 件", len(results))
    return results


async def _fetch_jmty_via_playwright(search_url: str) -> List[Dict[str, Any]]:
    """
    Playwrightでジモティーを開いて抽出する。
    """
    results: List[Dict[str, Any]] = []
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return results

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = await browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
            )
            page = await context.new_page()
            resp = await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
            if resp and resp.status == 200:
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                for row in soup.select("li.p-articles-list-item"):
                    title_el = row.select_one(".p-item-title a")
                    price_el = row.select_one(".p-item-price")
                    detail_el = row.select_one(".p-item-detail")
                    img_el = row.select_one("img")

                    if not title_el:
                        continue

                    title = title_el.get_text(strip=True)
                    href = title_el.get("href", "")
                    url = href if href.startswith("http") else f"https://jmty.jp{href}"
                    m_id = re.search(r"article-([a-zA-Z0-9]+)", url)
                    item_id = m_id.group(1) if m_id else re.sub(r"\W+", "", url[-16:])

                    price_yen = None
                    if price_el:
                        digits = re.sub(r"[^\d]", "", price_el.get_text())
                        if digits:
                            price_yen = int(digits)

                    desc = detail_el.get_text(" ", strip=True) if detail_el else title
                    thumb = ""
                    if img_el:
                        thumb = img_el.get("data-original") or img_el.get("src") or ""

                    results.append(
                        {
                            "id": f"jmty_{item_id}",
                            "source": "ジモティー",
                            "title": title,
                            "description": desc,
                            "raw_price_yen": price_yen,
                            "url": url,
                            "thumbnail": thumb,
                            "detected_at": now_iso_jst(),
                        }
                    )
            await browser.close()
    except Exception as exc:
        logger.warning("ジモティー (Playwright) 取得中に例外: %s", exc)

    return results


def fetch_jmty(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    ジモティー（中古車カテゴリ）からNDロードスターMT車を抽出する。
    """
    min_price_yen = int(float(config.get("monitoring", {}).get("min_price_man", 60)) * 10000) + 1
    query = config.get("queries", {}).get("jmty", "NDロードスター MT")

    search_url = (
        f"https://jmty.jp/all/car-maz/g-all/a-all"
        f"?keyword={quote_plus(query)}&min={min_price_yen}"
    )

    results = asyncio.run(_fetch_jmty_via_playwright(search_url))
    if not results:
        results = _fallback_search_via_ddg(
            "jmty.jp",
            "ND5RC ロードスター MT",
            "ジモティー",
            "jmty",
        )

    logger.info("ジモティー 取得件数: %d 件", len(results))
    return results


def fetch_all_sources(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    4サイトすべてから出品リストを収集して結合する（Xを最優先で先頭に配置）。
    """
    items: List[Dict[str, Any]] = []
    items.extend(fetch_x_posts(config))
    items.extend(fetch_yahoo_auctions(config))
    items.extend(fetch_mercari(config))
    items.extend(fetch_jmty(config))
    return items
