# -*- coding: utf-8 -*-
"""
LINE Messaging API 通知送信モジュール
"""

import os
import logging
from typing import Dict, Any
import requests

logger = logging.getLogger(__name__)

LINE_PUSH_ENDPOINT = "https://api.line.me/v2/bot/message/push"

RANK_TITLES = {
    "S": "🚨【★即決・最優先 Sランク】",
    "A": "🔥【有力候補 Aランク】",
    "B": "🚙【ベース車候補 Bランク】",
    "C": "📋【要確認 Cランク】",
}


def format_line_message(vehicle: Dict[str, Any]) -> str:
    """
    検出した車両情報からLINE通知用のテキストメッセージを生成する。
    """
    rank = vehicle.get("rank", "C")
    header = RANK_TITLES.get(rank, "📋【車両検出】")
    score = vehicle.get("score", 0)
    source = vehicle.get("source", "不明")
    price_display = vehicle.get("price_display", "価格不明")
    title = vehicle.get("title", "タイトルなし")
    url = vehicle.get("url", "")

    badges = vehicle.get("badges", [])
    if badges:
        badge_lines = " / ".join(
            [f"[{b['label']} +{b['points']}]" for b in badges]
        )
    else:
        badge_lines = "加点なし"

    message = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━\n"
        f"■ マッチ度: {score} pt ({rank}ランク)\n"
        f"■ 出品元: {source}\n"
        f"■ 価格: {price_display}\n"
        f"■ タイトル:\n{title}\n"
        f"■ 判定内訳:\n{badge_lines}\n"
        f"━━━━━━━━━━━━━━\n"
        f"▼ 出品ページ直リンク\n{url}"
    )
    return message


def send_line_notification(vehicle: Dict[str, Any]) -> bool:
    """
    LINE Messaging API の Push Message エンドポイントを用いて通知を送信する。
    環境変数 LINE_CHANNEL_ACCESS_TOKEN, LINE_USER_ID が未設定の場合はログ出力のみ行う。
    """
    channel_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("LINE_USER_ID", "").strip()

    text_body = format_line_message(vehicle)

    if not channel_token or not user_id:
        logger.warning(
            "LINE_CHANNEL_ACCESS_TOKEN または LINE_USER_ID が未設定のため、ドライラン出力します:\n%s",
            text_body,
        )
        return False

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {channel_token}",
    }
    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": text_body,
            }
        ],
    }

    try:
        resp = requests.post(
            LINE_PUSH_ENDPOINT, headers=headers, json=payload, timeout=15
        )
        if resp.status_code == 200:
            logger.info("LINE通知送信成功: %s (%spt)", vehicle.get("id"), vehicle.get("score"))
            return True
        logger.error(
            "LINE通知送信エラー (status=%s): %s", resp.status_code, resp.text
        )
        return False
    except requests.RequestException as exc:
        logger.error("LINE Messaging API 通信例外: %s", exc)
        return False
