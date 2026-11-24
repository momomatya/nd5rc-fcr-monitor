# -*- coding: utf-8 -*-
"""
X（旧Twitter）、ヤフオク、メルカリ、ジモティー スクレイピングモジュール
（Playwright + 画像内スペック記載のX個人売買対応 + キーワードなしカテゴリ全件網羅 + 海外IPブロック回避版）
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Twitter/X Snowflake Epoch (2010-11-04 01:42:54.657 UTC)
TWITTER_EPOCH_MS = 1288834974657

# X投稿が「NDロードスター車体の個人売買（売り）」であるかを判定するパターン
X_VEHICLE_MODEL_RE = re.compile(
    r"(?:ND5RC|ND\s*ロードスター|ロードスター\s*ND|NR-A|"
    r"ロードスター[^。]{0,40}(?:平成2[789]年|平成3[01]年|H2[789]|H3[01]|令和\s*[1-8]年|R[1-8]年|201[5-9]年|202[0-6]年|1\.5|S\s*レザー|S\s*スペシャル|990S))",
    re.IGNORECASE,
)

# 強い売却意思シグナル
X_STRONG_SALE_RE = re.compile(
    r"(?:車両売却|車売ります|#車売ります|#売ります|#個人売買|個人売買|売りたし|売ります|譲ります|引き取って|降りる|降りま|売却|手元\s*\d|欲しい人いますか|買う人いますか)"
)

# 画像内にスペック・価格を書く個人売買ポスト特有のシグナル（写真付きなら本文スペック省略でも通過）
X_IMAGE_SPEC_SALE_RE = re.compile(
    r"(?:#売ります|#車売ります|#個人売買|詳細はDM|DM下さい|DMください|DMにて|DMまで|欲しい人いますか|買う人いますか)",
    re.IGNORECASE,
)

# 車両スペック記載シグナル
X_CAR_SPEC_SIGNAL_RE = re.compile(
    r"(?:走行|距離|\d+(?:\.\d+)?\s*万\s*(?:km|キロ)|[\d,]+\s*km|車検|年式|平成2[789]年|平成3[01]年|H2[789]|H30|令和|R[1-8]|201[5-9]\s*年?|202[0-6]\s*年?|6MT|6速|MT|手元\s*\d+|\d+\s*万円|NR-A|LSD|ロールバー|ロールケージ|Leather\s*Package|レザーパッケージ|S\s*Special|RS|#車売ります|#個人売買)",
    re.IGNORECASE,
)

# 確実に弾くノイズ（業者入庫・買取・雑談・リプライ・パーツ単体・グッズ）
X_NOISE_EXCLUDE_RE = re.compile(
    r"(?:"
    r"買取させていただきました|ご成約|新規入庫|入庫車|入庫しました|下取り|カババ|見積もり|ディーラーで|"
    r"売りたい人探してます|買います|探しています|探してます|募集|繋がろう|"
    r"(?:純正|社外|RS)?\s*(?:レカロ|RECARO|シート|マフラー|ホイール|車高調|バンパー|ステアリング|幌|ハードトップ|フライホイール|ナンバーステー|エンジン|ミッション|デフ|LSD|ブレーキ|キャリパー|パッド|ロールバー|ボス|スイッチ|リア周り|フロント周り|一式)[^。、\n]{0,18}(?:売ります|譲ります|引き取って|出品)|"
    r"部品取り|廃車|二束三文|PS5|ゲオ|セリカ|SWのターボ"
    r")",
    re.IGNORECASE,
)

# 旧型ロードスター（NA/NB/NC・平成1年〜26年・1989〜2014年）や2.0L RF（NDERC）を弾いてND型（2015/H27〜）のみ残す判定
OLD_OR_RF_ROADSTER_RE = re.compile(
    r"(?:NA6CE|NA8C|NB6C|NB8C|NCEC|NDERC|\bNA\b|\bNB\b|\bNC\b|\bNC[123]\b|ユーノス|"
    r"平成\s*(?:[1-9]|1\d|2[0-6])\s*年|H(?:[1-9]|1\d|2[0-6])年|199\d年|200\d年|201[0-4]年)",
    re.IGNORECASE,
)

ND_GENERATION_POSITIVE_RE = re.compile(
    r"(?:ND5RC|\bND\b|ND型|ND系|NR-A|990S|1\.5|"
    r"平成2[789]年|平成3[01]年|H2[789]|H3[01]|令和\s*[1-8]年|R[1-8]年|R[1-8]\.|201[5-9]年|202[0-6]年|"
    r"S\s*スペシャル|S\s*Special|S\s*レザー|S\s*Leather|セラミックメタリック)",
    re.IGNORECASE,
)

# メルカリ等のタイトルで「明らかに車体ではなく小物・パーツ」のものを弾くパターン
MERCARI_PARTS_EXCLUDE_RE = re.compile(
    r"(?:フィルター|カバー|ネット|追加画像|一式|マフラー|ホイール|シート|レカロ|車高調|ステアリング|バンパー|幌|ライト|テール|エンブレム|ミニカー|トミカ|カタログ|キーホルダー|マット|シフトノブ|ペダル|ナビ単体|モニター|ドラレコ|キャリア|スペーサー|ナット|パッド|ローター|アーム|スタビ|タワーバー)",
    re.IGNORECASE,
)


def now_iso_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def is_nd_roadster_candidate(text: str) -> bool:
    if not text or ("ロードスター" not in text and "ND5RC" not in text.upper()):
        return False
    if OLD_OR_RF_ROADSTER_RE.search(text):
        return False
    if ND_GENERATION_POSITIVE_RE.search(text):
        return True
    return False


def tweet_id_to_datetime_jst(tweet_id: str) -> Optional[datetime]:
    if not tweet_id or not tweet_id.isdigit():
        return None
    try:
        tid = int(tweet_id)
        if tid < 100000000000000:
            return None
        timestamp_ms = (tid >> 22) + TWITTER_EPOCH_MS
        return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=JST)
    except Exception:
        return None


def is_recent_tweet(tweet_id: str, max_age_days: int = 14) -> bool:
    if not tweet_id or not tweet_id.isdigit():
        return False
    if len(tweet_id) < 18:
        return False
    dt = tweet_id_to_datetime_jst(tweet_id)
    if not dt:
        return False
    age = datetime.now(JST) - dt
    return age <= timedelta(days=max_age_days)


def is_valid_x_car_sale_post(
    text: str, is_reply: bool = False, has_media: bool = False
) -> bool:
    """
    Xのポスト本文が「NDロードスター車体の個人売買（売り）」であり、
    リプライ・日常雑談・買取業者宣伝・パーツ単体売りではないことを判定する。
    ★写真付き＋「#売ります」「詳細はDM」「欲しい人いますか」等の投稿は、
      価格・スペックが画像内に書かれているケースが多いため本文スペック必須チェックを緩和して通過させる。
    """
    if not text or is_reply:
        return False
    stripped = text.strip()
    if stripped.startswith("RT @") or stripped.startswith("@"):
        return False
    if len(stripped) < 18:
        return False
    if not X_VEHICLE_MODEL_RE.search(stripped):
        return False
    if not X_STRONG_SALE_RE.search(stripped):
        return False
    if X_NOISE_EXCLUDE_RE.search(stripped):
        return False

    # 写真付き＆DM誘導・売却ハッシュタグ付きなら画像内スペック記載とみなして通過
    if has_media and X_IMAGE_SPEC_SALE_RE.search(stripped):
        return True

    # テキストのみの場合は車両スペック記載を必須にして雑談を除外
    if not X_CAR_SPEC_SIGNAL_RE.search(stripped):
        return False
    return True


def _parse_cookies_for_playwright(cookies_raw: str) -> List[Dict[str, Any]]:
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
        for domain in (".x.com", ".twitter.com"):
            pw_cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                }
            )
    return pw_cookies


def _extract_tweets_from_graphql_payload(payload: Any, max_age_days: int = 14) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []
    seen_ids = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            legacy = node.get("legacy")
            rest_id = node.get("rest_id")
            if isinstance(legacy, dict) and rest_id and "full_text" in legacy:
                tweet_id = str(rest_id)
                if tweet_id not in seen_ids:
                    seen_ids.add(tweet_id)
                    if not is_recent_tweet(tweet_id, max_age_days=max_age_days):
                        return

                    text = str(legacy.get("full_text", "")).strip()
                    is_reply = bool(
                        legacy.get("in_reply_to_status_id_str")
                        or legacy.get("in_reply_to_user_id_str")
                    )

                    thumb = ""
                    entities = legacy.get("extended_entities") or legacy.get("entities") or {}
                    media_list = entities.get("media", []) if isinstance(entities, dict) else []
                    if media_list and isinstance(media_list[0], dict):
                        thumb = str(media_list[0].get("media_url_https", ""))

                    if is_valid_x_car_sale_post(text, is_reply=is_reply, has_media=bool(thumb)):
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

                        post_dt = tweet_id_to_datetime_jst(tweet_id)
                        detected_iso = post_dt.isoformat(timespec="seconds") if post_dt else now_iso_jst()

                        first_line = text.splitlines()[0][:80] if text else tweet_id
                        extracted.append(
                            {
                                "id": f"x_{tweet_id}",
                                "source": "X",
                                "title": f"【X個人売買】{first_line}",
                                "description": text,
                                "raw_price_yen": _extract_price_yen_from_text(text),
                                "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                                "thumbnail": thumb,
                                "detected_at": detected_iso,
                            }
                        )
            for val in node.values():
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return extracted


async def _fetch_x_via_playwright(
    query: str, cookies_raw: str, max_age_days: int = 14
) -> List[Dict[str, Any]]:
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

    search_targets = [
        ("#NDロードスター #売ります", "live"),
        ("NDロードスター 売ります", "live"),
        ("ロードスター 売り", "live"),
        ("ロードスター 売り", "top"),
        ("ロードスター 欲しい人", "live"),
        ("ND5RC 車両売却", "live"),
        ("ND5RC 個人売買", "live"),
        ("ND5RC 売ります", "live"),
        ("NDロードスター 個人売買", "live"),
        ("#NDロードスター #車売ります", "live"),
    ]

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
                        for item in _extract_tweets_from_graphql_payload(data, max_age_days=max_age_days):
                            if item["id"] not in seen_ids:
                                seen_ids.add(item["id"])
                                results.append(item)
                except Exception:
                    pass

            page.on("response", handle_response)

            for q, tab_mode in search_targets:
                if tab_mode == "live":
                    search_url = f"https://x.com/search?q={quote_plus(q)}&src=typed_query&f=live"
                else:
                    search_url = f"https://x.com/search?q={quote_plus(q)}&src=typed_query"

                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(3000)
                    await page.mouse.wheel(0, 1000)
                    await page.wait_for_timeout(1200)

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

                        if not is_recent_tweet(tweet_id, max_age_days=max_age_days):
                            continue

                        text_el = await art.query_selector("div[data-testid='tweetText']")
                        text = (await text_el.inner_text()).strip() if text_el else ""

                        img_el = await art.query_selector("div[data-testid='tweetPhoto'] img")
                        thumb = (await img_el.get_attribute("src") or "") if img_el else ""

                        if not is_valid_x_car_sale_post(text, has_media=bool(thumb)):
                            continue

                        post_dt = tweet_id_to_datetime_jst(tweet_id)
                        detected_iso = post_dt.isoformat(timespec="seconds") if post_dt else now_iso_jst()

                        seen_ids.add(item_id)
                        first_line = text.splitlines()[0][:80]
                        results.append(
                            {
                                "id": item_id,
                                "source": "X",
                                "title": f"【X個人売買】{first_line}",
                                "description": text,
                                "raw_price_yen": _extract_price_yen_from_text(text),
                                "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                                "thumbnail": thumb,
                                "detected_at": detected_iso,
                            }
                        )
                except Exception as q_exc:
                    logger.warning("X検索クエリ実行中に例外 (%s): %s", q, q_exc)

            await browser.close()
    except Exception as exc:
        logger.warning("X (Playwright) 取得中に例外が発生しました: %s", exc)

    logger.info("X 厳選取得件数 (直近%d日以内): %d 件", max_age_days, len(results))
    return results


def fetch_x_posts(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    cookies_raw = os.environ.get("X_COOKIES", "").strip()
    query = config.get("queries", {}).get("x", "")
    max_age_days = int(config.get("monitoring", {}).get("max_age_days", 14))
    if not cookies_raw:
        logger.info("X_COOKIES が未設定のため、X巡回をスキップします。")
        return []

    return asyncio.run(_fetch_x_via_playwright(query, cookies_raw, max_age_days=max_age_days))


def _extract_price_yen_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    m_man = re.search(r"(\d{2,3}(?:\.\d+)?)\s*万\s*円?", text)
    if m_man:
        val = int(float(m_man.group(1)) * 10000)
        if 300000 <= val <= 10000000:
            return val
    for m_num in re.finditer(r"(?:[¥￥]\s*)?(\d{1,2},\d{3},\d{3}|\d{3},\d{3}|\d{6,7})(?:\s*円)?", text):
        raw = m_num.group(1).replace(",", "")
        if raw.isdigit():
            val = int(raw)
            if 300000 <= val <= 10000000:
                return val
    return None


async def _fetch_mercari_via_playwright(queries: List[str], min_price_yen: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen_ids = set()

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
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()

            async def handle_mercari_response(response: Any) -> None:
                try:
                    if ("entities:search" in response.url or "search_index" in response.url) and response.status == 200:
                        data = await response.json()
                        items_list = data.get("items", []) if isinstance(data, dict) else []
                        for it in items_list:
                            if not isinstance(it, dict):
                                continue
                            item_id = str(it.get("id", ""))
                            name = str(it.get("name", ""))
                            price_raw = it.get("price")
                            if not item_id.startswith("m") or item_id in seen_ids:
                                continue
                            if not is_nd_roadster_candidate(name):
                                continue
                            if MERCARI_PARTS_EXCLUDE_RE.search(name):
                                continue
                            price_yen = int(price_raw) if str(price_raw).isdigit() else _extract_price_yen_from_text(name)
                            if price_yen is None or price_yen < min_price_yen:
                                continue
                            thumbs = it.get("thumbnails", [])
                            thumb = thumbs[0] if isinstance(thumbs, list) and thumbs else ""
                            seen_ids.add(item_id)
                            results.append(
                                {
                                    "id": f"mercari_{item_id}",
                                    "source": "メルカリ",
                                    "title": name[:100],
                                    "description": name,
                                    "raw_price_yen": price_yen,
                                    "url": f"https://jp.mercari.com/item/{item_id}",
                                    "thumbnail": str(thumb),
                                    "detected_at": now_iso_jst(),
                                }
                            )
                except Exception:
                    pass

            page.on("response", handle_mercari_response)

            for q in queries:
                search_url = (
                    f"https://jp.mercari.com/search"
                    f"?keyword={quote_plus(q)}&price_min={min_price_yen}&status=on_sale&sort=created_time&order=desc"
                )
                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        await page.wait_for_selector("a[href*='/item/m']", timeout=8000)
                    except Exception:
                        await page.wait_for_timeout(4000)

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
                        if img_el:
                            alt_text = (await img_el.get_attribute("alt") or "").strip()
                            if alt_text and alt_text not in combined_text:
                                combined_text = f"{alt_text} {combined_text}".strip()

                        if not is_nd_roadster_candidate(combined_text):
                            continue
                        if MERCARI_PARTS_EXCLUDE_RE.search(combined_text):
                            continue

                        price_yen = _extract_price_yen_from_text(combined_text)
                        clean_title = re.sub(r"\s+", " ", combined_text)
                        clean_title = re.sub(r"のサムネイル.*$", "", clean_title).strip()[:100]
                        if not clean_title:
                            clean_title = f"メルカリ出品 {item_id}"

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
                except Exception as q_exc:
                    logger.warning("メルカリ検索クエリ実行中に例外 (%s): %s", q, q_exc)

            for item in results[:5]:
                if item.get("raw_price_yen") is None:
                    try:
                        await page.goto(item["url"], wait_until="domcontentloaded", timeout=20000)
                        await page.wait_for_timeout(2500)
                        body_text = await page.inner_text("body")
                        extracted_price = _extract_price_yen_from_text(body_text)
                        if extracted_price:
                            item["raw_price_yen"] = extracted_price
                        item["description"] = f"{item['title']}\n{body_text[:1500]}"
                    except Exception:
                        pass

            await browser.close()
    except Exception as exc:
        logger.warning("メルカリ (Playwright) 取得中に例外が発生しました: %s", exc)

    filtered_results = [
        r for r in results
        if r.get("raw_price_yen") is not None and r["raw_price_yen"] >= min_price_yen
    ]

    logger.info("メルカリ 車体厳選取得件数: %d 件", len(filtered_results))
    return filtered_results


def fetch_mercari(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    min_price_yen = int(float(config.get("monitoring", {}).get("min_price_man", 60)) * 10000) + 1
    queries = ["ND5RC", "NDロードスター 車体", "マツダ ロードスター 車体", "ロードスター NR-A"]
    return asyncio.run(_fetch_mercari_via_playwright(queries, min_price_yen))


def _parse_yahoo_html_or_markdown(content: str, min_price_yen: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    soup = BeautifulSoup(content, "html.parser")
    for item in soup.select("li.Product"):
        link_el = item.select_one("a.Product__titleLink")
        price_el = item.select_one("span.Product__priceValue")
        img_el = item.select_one("img.Product__imageData")
        if not link_el:
            continue
        title = link_el.get_text(strip=True)
        url = link_el.get("href", "")
        m_id = re.search(r"/auction/([a-zA-Z0-9]+)", url)
        if not m_id:
            continue
        auc_id = m_id.group(1)
        if auc_id in seen_ids or not is_nd_roadster_candidate(title):
            continue
        price_yen = None
        if price_el:
            digits = re.sub(r"[^\d]", "", price_el.get_text())
            if digits:
                price_yen = int(digits)
        if price_yen is not None and price_yen < min_price_yen:
            continue
        thumb = img_el.get("src", "") if img_el else ""
        seen_ids.add(auc_id)
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

    for m_md in re.finditer(
        r"\[([^\]]{10,160})\]\((https?://auctions\.yahoo\.co\.jp/(?:jp/)?auction/([a-zA-Z0-9]+))\)([^\n]{0,120})",
        content,
    ):
        title = m_md.group(1).strip()
        url = m_md.group(2).strip()
        auc_id = m_md.group(3).strip()
        tail = m_md.group(4).strip()
        if auc_id in seen_ids:
            continue
        if not is_nd_roadster_candidate(f"{title} {tail}"):
            continue
        price_yen = _extract_price_yen_from_text(f"{title} {tail}") or (min_price_yen + 100000)
        if price_yen < min_price_yen:
            continue
        seen_ids.add(auc_id)
        results.append(
            {
                "id": f"yahoo_{auc_id}",
                "source": "ヤフオク",
                "title": title,
                "description": f"{title} {tail}".strip(),
                "raw_price_yen": price_yen,
                "url": url,
                "thumbnail": "",
                "detected_at": now_iso_jst(),
            }
        )

    return results


def _fallback_search_multi_engine(
    site_domain: str,
    keywords: List[str],
    source_label: str,
    id_prefix: str,
    min_price_yen: int = 600001,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen_urls = set()

    for kw in keywords:
        q = f"site:{site_domain} {kw}"
        bing_rss_url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&setlang=ja-JP&cc=JP"
        try:
            resp = requests.get(bing_rss_url, headers=DEFAULT_HEADERS, timeout=15)
            if resp.status_code == 200 and "<rss" in resp.text:
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item"):
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    desc = (item.findtext("description") or "").strip()
                    if not link or site_domain not in link or link in seen_urls:
                        continue
                    combined = f"{title} {desc}"
                    if not is_nd_roadster_candidate(combined):
                        continue
                    if MERCARI_PARTS_EXCLUDE_RE.search(title):
                        continue
                    seen_urls.add(link)
                    price_yen = _extract_price_yen_from_text(combined) or (min_price_yen + 100000)
                    if price_yen < min_price_yen:
                        continue
                    m_auc = re.search(r"/auction/([a-zA-Z0-9]+)", link)
                    m_jmty = re.search(r"article-([a-zA-Z0-9]+)", link)
                    item_key = (
                        m_auc.group(1)
                        if m_auc
                        else (m_jmty.group(1) if m_jmty else re.sub(r"\W+", "", link[-20:]))
                    )
                    results.append(
                        {
                            "id": f"{id_prefix}_{item_key}",
                            "source": source_label,
                            "title": title[:100],
                            "description": combined,
                            "raw_price_yen": price_yen,
                            "url": link,
                            "thumbnail": "",
                            "detected_at": now_iso_jst(),
                        }
                    )
        except Exception as exc:
            logger.debug("%s Bing RSS フォールバック例外 (%s): %s", source_label, kw, exc)

    return results


def fetch_yahoo_auctions(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    min_price_yen = int(float(config.get("monitoring", {}).get("min_price_man", 60)) * 10000) + 1
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    target_urls = [
        f"https://auctions.yahoo.co.jp/category/leaf/26360/?min={min_price_yen}&abatch=2&istatus=0&s1=new&o1=d",
        f"https://auctions.yahoo.co.jp/search/search?auccat=26360&min={min_price_yen}&abatch=2&istatus=0&s1=new&o1=d",
        f"https://auctions.yahoo.co.jp/search/search?p={quote_plus('ロードスター')}&auccat=26360&min={min_price_yen}&s1=new&o1=d",
    ]

    for url in target_urls:
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=12)
            if resp.status_code == 200 and "Product" in resp.text:
                for it in _parse_yahoo_html_or_markdown(resp.text, min_price_yen):
                    if it["id"] not in seen_ids:
                        seen_ids.add(it["id"])
                        results.append(it)
        except Exception:
            pass

        if results:
            break

        try:
            jina_url = f"https://r.jina.ai/{url}"
            resp_jina = requests.get(
                jina_url,
                headers={**DEFAULT_HEADERS, "X-Return-Format": "html"},
                timeout=20,
            )
            if resp_jina.status_code == 200 and len(resp_jina.text) > 500:
                for it in _parse_yahoo_html_or_markdown(resp_jina.text, min_price_yen):
                    if it["id"] not in seen_ids:
                        seen_ids.add(it["id"])
                        results.append(it)
        except Exception:
            pass

        if results:
            break

    if not results:
        fallback_kws = [
            "ND5RC ロードスター",
            "ロードスター 平成27年",
            "ロードスター 平成28年",
            "ロードスター 平成29年",
            "ロードスター NR-A",
            "ロードスター 1.5 レザーパッケージ",
            "ロードスター Sスペシャルパッケージ",
        ]
        results = _fallback_search_multi_engine(
            "auctions.yahoo.co.jp/jp/auction",
            fallback_kws,
            "ヤフオク",
            "yahoo",
            min_price_yen=min_price_yen,
        )

    logger.info("ヤフオク 取得件数: %d 件", len(results))
    return results


def _parse_jmty_html_or_markdown(content: str, min_price_yen: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    soup = BeautifulSoup(content, "html.parser")
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
        if item_id in seen_ids:
            continue

        desc = detail_el.get_text(" ", strip=True) if detail_el else title
        if not is_nd_roadster_candidate(f"{title} {desc}"):
            continue

        price_yen = None
        if price_el:
            digits = re.sub(r"[^\d]", "", price_el.get_text())
            if digits:
                price_yen = int(digits)
        if price_yen is not None and price_yen < min_price_yen:
            continue

        thumb = ""
        if img_el:
            thumb = img_el.get("data-original") or img_el.get("src") or ""

        seen_ids.add(item_id)
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

    return results


def fetch_jmty(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    min_price_yen = int(float(config.get("monitoring", {}).get("min_price_man", 60)) * 10000) + 1
    search_url = (
        f"https://jmty.jp/all/car-maz/g-all/a-all"
        f"?keyword={quote_plus('ロードスター')}&min={min_price_yen}"
    )

    results: List[Dict[str, Any]] = []

    try:
        jina_url = f"https://r.jina.ai/{search_url}"
        resp_jina = requests.get(
            jina_url,
            headers={**DEFAULT_HEADERS, "X-Return-Format": "html"},
            timeout=20,
        )
        if resp_jina.status_code == 200 and "p-articles-list-item" in resp_jina.text:
            results = _parse_jmty_html_or_markdown(resp_jina.text, min_price_yen)
    except Exception:
        pass

    if not results:
        fallback_kws = [
            "ND5RC ロードスター",
            "ロードスター 平成27年",
            "ロードスター 平成28年",
            "ロードスター NR-A",
            "ND ロードスター 万円",
        ]
        results = _fallback_search_multi_engine(
            "jmty.jp",
            fallback_kws,
            "ジモティー",
            "jmty",
            min_price_yen=min_price_yen,
        )

    logger.info("ジモティー 取得件数: %d 件", len(results))
    return results


def fetch_all_sources(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    items.extend(fetch_x_posts(config))
    items.extend(fetch_yahoo_auctions(config))
    items.extend(fetch_mercari(config))
    items.extend(fetch_jmty(config))
    return items
