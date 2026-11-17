# -*- coding: utf-8 -*-
"""
X（旧Twitter）、ヤフオク、メルカリ、ジモティー スクレイピングモジュール
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def now_iso_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


async def _fetch_x_via_twikit(query: str, cookies_raw: str) -> List[Dict[str, Any]]:
    """
    twikit ライブラリと監視用サブアカウントのCookie（JSON形式）を用いて
    X（旧Twitter）の最新検索タイムラインから個人売買ポストを取得する。
    """
    results: List[Dict[str, Any]] = []
    try:
        from twikit import Client  # type: ignore
    except ImportError:
        logger.warning("twikit がインストールされていないためX検索をスキップします。")
        return results

    try:
        cookies_dict = json.loads(cookies_raw)
        # Cookieエディタ拡張機能のエクスポート形式（リスト形式）と辞書形式の両方に対応
        if isinstance(cookies_dict, list):
            cookies_map = {
                c["name"]: c["value"]
                for c in cookies_dict
                if isinstance(c, dict) and "name" in c and "value" in c
            }
        elif isinstance(cookies_dict, dict):
            cookies_map = cookies_dict
        else:
            logger.error("X_COOKIES のJSON形式が不正です。")
            return results

        client = Client("ja")
        client.set_cookies(cookies_map)

        tweets = await client.search_tweet(query, product="Latest", count=20)
        for tw in tweets:
            tweet_id = str(getattr(tw, "id", ""))
            text = str(getattr(tw, "full_text", "") or getattr(tw, "text", ""))
            if not tweet_id or not text:
                continue

            user = getattr(tw, "user", None)
            screen_name = getattr(user, "screen_name", "i/web") if user else "i/web"
            url = f"https://x.com/{screen_name}/status/{tweet_id}"

            media_list = getattr(tw, "media", []) or []
            thumb = ""
            if media_list:
                first_media = media_list[0]
                thumb = getattr(first_media, "media_url_https", "") or getattr(
                    first_media, "url", ""
                )

            first_line = text.strip().splitlines()[0][:80]
            results.append(
                {
                    "id": f"x_{tweet_id}",
                    "source": "X",
                    "title": f"【X個人売買】{first_line}",
                    "description": text,
                    "raw_price_yen": None,
                    "url": url,
                    "thumbnail": thumb,
                    "detected_at": now_iso_jst(),
                }
            )
    except Exception as exc:
        logger.warning("X (twikit) 取得中に例外が発生しました: %s", exc)

    return results


def fetch_x_posts(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    X（旧Twitter）からNDロードスター個人売買投稿を取得する。
    """
    cookies_raw = os.environ.get("X_COOKIES", "").strip()
    query = config.get("queries", {}).get(
        "x",
        '("NDロードスター" OR "ND5RC" OR "ロードスター ND") ("売ります" OR "譲ります" OR "個人売買" OR "手元" OR "降りる") -業者 -買取',
    )
    if not cookies_raw:
        logger.info("X_COOKIES が未設定のため、X巡回をスキップします。")
        return []

    return asyncio.run(_fetch_x_via_twikit(query, cookies_raw))


def fetch_yahoo_auctions(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    ヤフオク（中古車・新車 ＞ マツダ ＞ ロードスター Category: 26360）から
    「個人出品のみ（ストア除外: abatch=2）」「60万円超」のNDロードスターMT車を抽出する。
    """
    results: List[Dict[str, Any]] = []
    min_price_yen = int(float(config.get("monitoring", {}).get("min_price_man", 60)) * 10000) + 1
    query = config.get("queries", {}).get("yahoo", "ND5RC ロードスター MT")

    # abatch=2: 個人出品のみ, aucat=26360: マツダ＞ロードスター車体カテゴリ
    search_url = (
        f"https://auctions.yahoo.co.jp/search/search"
        f"?p={quote_plus(query)}&auccat=26360&abatch=2&min={min_price_yen}&istatus=0&s1=new&o1=d"
    )

    try:
        resp = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select("li.Product"):
            link_el = item.select_one("a.Product__titleLink")
            price_el = item.select_one("span.Product__priceValue")
            img_el = item.select_one("img.Product__imageData")

            if not link_el:
                continue

            title = link_el.get_text(strip=True)
            url = link_el.get("href", "")
            # オークションID抽出
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
        logger.warning("ヤフオク取得中に例外が発生しました: %s", exc)

    return results


def fetch_jmty(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    ジモティー（中古車カテゴリ）からNDロードスターMT車を抽出する。
    """
    results: List[Dict[str, Any]] = []
    min_price_yen = int(float(config.get("monitoring", {}).get("min_price_man", 60)) * 10000) + 1
    query = config.get("queries", {}).get("jmty", "NDロードスター MT")

    search_url = (
        f"https://jmty.jp/all/car-maz/g-all/a-all"
        f"?keyword={quote_plus(query)}&min={min_price_yen}"
    )

    try:
        resp = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

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
    except Exception as exc:
        logger.warning("ジモティー取得中に例外が発生しました: %s", exc)

    return results


def fetch_mercari(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    メルカリ（自動車本体カテゴリ）からNDロードスターMT車を抽出する。
    """
    results: List[Dict[str, Any]] = []
    min_price_yen = int(float(config.get("monitoring", {}).get("min_price_man", 60)) * 10000) + 1
    query = config.get("queries", {}).get("mercari", "NDロードスター MT 車体")

    # メルカリWeb検索ページ（SSR / JSON-LD または公開検索エンドポイント）の解析
    search_url = (
        f"https://jp.mercari.com/search"
        f"?keyword={quote_plus(query)}&price_min={min_price_yen}&status=on_sale&sort=created_time&order=desc"
    )

    try:
        resp = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a_el in soup.select("a[href^='/item/m']"):
                href = a_el.get("href", "")
                m_id = re.search(r"/item/(m\d+)", href)
                if not m_id:
                    continue
                item_id = m_id.group(1)
                text = a_el.get_text(" ", strip=True)
                img_el = a_el.select_one("img")
                thumb = img_el.get("src", "") if img_el else ""

                price_yen = None
                m_price = re.search(r"[¥￥]\s*([\d,]+)", text)
                if m_price:
                    price_yen = int(m_price.group(1).replace(",", ""))

                results.append(
                    {
                        "id": f"mercari_{item_id}",
                        "source": "メルカリ",
                        "title": text[:100] or f"メルカリ出品 {item_id}",
                        "description": text,
                        "raw_price_yen": price_yen,
                        "url": f"https://jp.mercari.com/item/{item_id}",
                        "thumbnail": thumb,
                        "detected_at": now_iso_jst(),
                    }
                )
    except Exception as exc:
        logger.warning("メルカリ取得中に例外が発生しました: %s", exc)

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
