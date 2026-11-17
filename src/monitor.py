# -*- coding: utf-8 -*-
"""
NDロードスター（FCRカップ戦仕様）メイン巡回スクリプト
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List

from scrapers import fetch_all_sources
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


def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def reevaluate_existing_vehicles(
    vehicles: List[Dict[str, Any]],
    config: Dict[str, Any],
    ignored_ids: set,
    ignored_signatures: set,
) -> List[Dict[str, Any]]:
    """
    ダッシュボードから config.json や興味なしフラグが変更された際、
    既存の保存車両データのスコア・ランク・興味なし状態も最新ルールで再計算して同期する。
    """
    updated: List[Dict[str, Any]] = []
    for v in vehicles:
        notified_already = v.get("notified_line", False)
        rescored = score_vehicle(
            v, config, ignored_ids=ignored_ids, ignored_signatures=ignored_signatures
        )
        rescored.pop("should_notify_line", None)
        rescored["notified_line"] = notified_already
        updated.append(rescored)
    return updated


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

    # 既存車両で ignored=True のもののID・シグネチャも統合
    for v in vehicles:
        if v.get("ignored"):
            if v.get("id"):
                ignored_ids.add(v["id"])
            if v.get("title"):
                ignored_signatures.add(normalize_title_signature(v["title"]))

    vehicles = reevaluate_existing_vehicles(
        vehicles, config, ignored_ids, ignored_signatures
    )

    fetched_items = fetch_all_sources(config)
    logger.info("巡回取得件数: %d 件", len(fetched_items))

    new_count = 0
    notified_count = 0

    for raw_item in fetched_items:
        item_id = raw_item.get("id")
        if not item_id or item_id in seen_ids:
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
        seen_ids.add(item_id)
        if excluded:
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
        if should_notify:
            notified_ok = send_line_notification(scored)
            if notified_ok:
                notified_count += 1
        else:
            logger.info(
                "LINE通知スキップ [%s] %s (%s)",
                scored.get("rank"),
                title[:40],
                scored.get("skip_reason"),
            )

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
