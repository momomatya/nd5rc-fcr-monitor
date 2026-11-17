# -*- coding: utf-8 -*-
"""
NDロードスター（FCRカップ戦仕様）メイン巡回スクリプト
（既存vehicles.json内の過去投稿・雑談ノイズ・モックデータの自動クリーンアップ機能付き）
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List

from scrapers import (
    fetch_all_sources,
    is_recent_tweet,
    is_valid_x_car_sale_post,
    MERCARI_PARTS_EXCLUDE_RE,
)
from scorer import (
    extract_price_man,
    is_excluded_item,
    score_vehicle,
    normalize_title_signature,
)
from notifier import send_line_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("monitor")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "data" / "config.json"
VEHICLES_PATH = BASE_DIR / "data" / "vehicles.json"
JST = timezone(timedelta(hours=9))

# 初期動作確認用に入れていたモックデータのID
MOCK_VEHICLE_IDS = {
    "x_18579201938471",
    "yahoo_k1149283741",
    "jmty_tokyo_car_992817",
}


def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def prune_and_reevaluate_existing_vehicles(
    vehicles: List[Dict[str, Any]],
    config: Dict[str, Any],
    ignored_ids: set,
    ignored_signatures: set,
) -> List[Dict[str, Any]]:
    """
    既存の vehicles.json に残っている
    - 初期モックデータ
    - 直近14日より前の古いX投稿
    - 日常雑談・業者宣伝・パーツ単体売り
    を自動的に掃除（パージ）し、残った本物の車体出品だけ最新ルールで再計算する。
    """
    max_age_days = int(config.get("monitoring", {}).get("max_age_days", 14))
    cleaned: List[Dict[str, Any]] = []
    removed_count = 0

    for v in vehicles:
        vid = str(v.get("id", ""))
        source = str(v.get("source", ""))
        title = str(v.get("title", ""))
        desc = str(v.get("description", ""))
        price_man = v.get("price_man")

        # 1. モックデータの自動除去
        if vid in MOCK_VEHICLE_IDS:
            removed_count += 1
            continue

        # 2. Xの過去投稿（14日超）および雑談ノイズの自動除去
        if source == "X" or vid.startswith("x_"):
            raw_tid = vid.replace("x_", "")
            if not is_recent_tweet(raw_tid, max_age_days=max_age_days):
                removed_count += 1
                continue
            if not is_valid_x_car_sale_post(desc or title):
                removed_count += 1
                continue

        # 3. メルカリ等の小物・パーツ単体・価格不明の除去
        if source == "メルカリ" and MERCARI_PARTS_EXCLUDE_RE.search(f"{title} {desc}"):
            removed_count += 1
            continue

        excluded, _ = is_excluded_item(title, desc, price_man, source, config)
        if excluded:
            removed_count += 1
            continue

        notified_already = v.get("notified_line", False)
        rescored = score_vehicle(
            v, config, ignored_ids=ignored_ids, ignored_signatures=ignored_signatures
        )
        rescored.pop("should_notify_line", None)
        rescored["notified_line"] = notified_already
        cleaned.append(rescored)

    if removed_count > 0:
        logger.info("既存データ自動クリーンアップ: 古い投稿・雑談・モック等 %d 件を削除しました", removed_count)

    return cleaned


def main() -> None:
    config = load_json(CONFIG_PATH, {})
    if not config.get("monitoring", {}).get("enabled", True):
        logger.info("config.json で監視が一時停止 (enabled=false) に設定されています。終了します。")
        return

    store = load_json(
        VEHICLES_PATH,
        {
            "last_updated": "",
            "seen_ids": [],
            "ignored_ids": [],
            "ignored_signatures": [],
            "vehicles": [],
        },
    )
    seen_ids = set(store.get("seen_ids", []))
    ignored_ids = set(store.get("ignored_ids", [])) | set(
        config.get("ignored_vehicle_ids", [])
    )
    ignored_signatures = set(store.get("ignored_signatures", [])) | {
        normalize_title_signature(s)
        for s in config.get("ignored_title_signatures", [])
        if s
    }
    vehicles: List[Dict[str, Any]] = store.get("vehicles", [])

    for v in vehicles:
        if v.get("ignored"):
            if v.get("id"):
                ignored_ids.add(v["id"])
            if v.get("title"):
                ignored_signatures.add(normalize_title_signature(v["title"]))

    # 既存データのクリーンアップ＆再評価
    vehicles = prune_and_reevaluate_existing_vehicles(
        vehicles, config, ignored_ids, ignored_signatures
    )
    existing_ids = {v.get("id") for v in vehicles if v.get("id")}

    fetched_items = fetch_all_sources(config)
    logger.info("巡回取得件数: %d 件", len(fetched_items))

    new_count = 0
    notified_count = 0

    for raw_item in fetched_items:
        item_id = raw_item.get("id")
        if not item_id or item_id in existing_ids:
            continue

        title = raw_item.get("title", "")
        desc = raw_item.get("description", "")
        source = raw_item.get("source", "不明")
        price_man, price_disp = extract_price_man(
            f"{title} {desc}", raw_price_yen=raw_item.get("raw_price_yen")
        )
        raw_item["price_man"] = price_man
        raw_item["price_display"] = price_disp

        excluded, reason = is_excluded_item(title, desc, price_man, source, config)
        if excluded:
            seen_ids.add(item_id)
            logger.info("除外 [%s] %s (%s)", source, title[:40], reason)
            continue

        scored = score_vehicle(
            raw_item,
            config,
            ignored_ids=ignored_ids,
            ignored_signatures=ignored_signatures,
        )
        should_notify = scored.pop("should_notify_line", False)

        notified_ok = False
        # 過去に一度もLINE通知していない新規IDのみLINE送信
        if should_notify and item_id not in seen_ids:
            notified_ok = send_line_notification(scored)
            if notified_ok:
                notified_count += 1
        else:
            logger.info(
                "LINE通知スキップ [%s] %s (%s)",
                scored.get("rank"),
                title[:40],
                scored.get("skip_reason") or "通知済み",
            )

        seen_ids.add(item_id)
        existing_ids.add(item_id)
        scored["notified_line"] = notified_ok
        vehicles.insert(0, scored)
        new_count += 1

    vehicles = vehicles[:300]
    seen_list = list(seen_ids)[-1000:]

    store_out = {
        "last_updated": datetime.now(JST).isoformat(timespec="seconds"),
        "seen_ids": seen_list,
        "ignored_ids": sorted(list(ignored_ids)),
        "ignored_signatures": sorted(list(ignored_signatures)),
        "vehicles": vehicles,
    }
    save_json(VEHICLES_PATH, store_out)
    logger.info(
        "巡回完了: 新規追加=%d件, LINE通知送信=%d件, 保存総数=%d件",
        new_count,
        notified_count,
        len(vehicles),
    )


if __name__ == "__main__":
    main()
