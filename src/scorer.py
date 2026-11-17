# -*- coding: utf-8 -*-
"""
NDロードスター（FCRカップ戦仕様）マッチ度計算・除外判定・興味なし（通知ブロック）判定モジュール
"""

import re
from typing import Dict, Any, List, Optional, Tuple


# 競技・主要装備キーワードカテゴリID（150万円以上のLINE通知温存判定用）
COMPETITION_RULE_IDS = {
    "race_ready",
    "grade_nra",
    "drivetrain_lsd",
    "safety_rollbar",
    "wheel_te37",
}


def normalize_title_signature(title: str) -> str:
    """
    再出品・値下げ再投稿でも同一車両と判定できるよう、
    記号・空白・価格表現を除いた正規化シグネチャを生成する。
    """
    cleaned = re.sub(r"(?:手元\s*)?\d{2,4}(?:\.\d+)?\s*万(?:円)?", "", title or "")
    cleaned = re.sub(r"[\s　【】\[\]（）()!！?？・/／,，.。:：\-〜~]+", "", cleaned)
    return cleaned.upper()[:36]


def is_ignored_vehicle(
    item: Dict[str, Any],
    config: Dict[str, Any],
    ignored_ids: Optional[set] = None,
    ignored_signatures: Optional[set] = None,
) -> Tuple[bool, str]:
    """
    ユーザーが「興味なし（通知ミュート）」フラグを設定した車両かどうかを判定する。
    - 車両IDの完全一致
    - タイトル正規化シグネチャの一致（再出品・価格改定時の再通知もブロック）
    - 興味なし出品者・固有フレーズ（ignored_seller_keywords）の一致
    """
    if item.get("ignored") is True:
        return True, "興味なしフラグ設定済み車両"

    item_id = str(item.get("id", ""))
    cfg_ignored_ids = set(config.get("ignored_vehicle_ids", []))
    all_ignored_ids = cfg_ignored_ids | (ignored_ids or set())
    if item_id and item_id in all_ignored_ids:
        return True, f"興味なし登録ID一致 ({item_id})"

    title = item.get("title", "")
    desc = item.get("description", "")
    full_text_upper = f"{title} {desc}".upper()

    sig = normalize_title_signature(title)
    cfg_sigs = {normalize_title_signature(s) for s in config.get("ignored_title_signatures", []) if s}
    all_sigs = cfg_sigs | (ignored_signatures or set())
    if sig and len(sig) >= 8 and sig in all_sigs:
        return True, "興味なし車両の再出品シグネチャ一致"

    for seller_kw in config.get("ignored_seller_keywords", []):
        if seller_kw and seller_kw.upper() in full_text_upper:
            return True, f"興味なし指定ワード一致 ({seller_kw})"

    return False, ""


def extract_price_man(text: str, raw_price_yen: Optional[int] = None) -> Tuple[Optional[float], str]:
    """
    テキストまたは数値（円）から「万円」単位の価格と表示用文字列を抽出する。
    """
    if raw_price_yen is not None and raw_price_yen > 0:
        man = round(raw_price_yen / 10000.0, 1)
        display = f"{int(man) if man.is_integer() else man}万円"
        return man, display

    normalized = text.replace(",", "").replace("，", "")

    m_man = re.search(r"(?:手元\s*)?(\d{2,4}(?:\.\d+)?)\s*万(?:円)?", normalized)
    if m_man:
        val = float(m_man.group(1))
        is_temoto = "手元" in m_man.group(0)
        suffix = "万円（手元）" if is_temoto else "万円"
        disp_num = int(val) if val.is_integer() else val
        return val, f"{disp_num}{suffix}"

    m_yen = re.search(r"(\d{6,8})\s*円", normalized)
    if m_yen:
        val = round(int(m_yen.group(1)) / 10000.0, 1)
        disp_num = int(val) if val.is_integer() else val
        return val, f"{disp_num}万円"

    if any(k in normalized for k in ["DM", "ＤＭ", "相談", "応談", "メッセージ"]):
        return None, "価格DM・要相談"

    return None, "価格記載なし"


def extract_vehicle_specs(text: str, existing_specs: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    タイトル・本文からお気に入り比較画面用の主要スペック項目を自動抽出する。
    """
    if existing_specs:
        return existing_specs

    upper = text.upper()
    year_str = "不明（要確認）"
    m_year = re.search(r"(201[5-9]|202[0-5])年|H(2[7-9]|30|31)年?", text)
    if m_year:
        year_str = m_year.group(0)

    grade_str = "6MT（グレード要確認）"
    if "NR-A" in upper or "NRA" in upper:
        grade_str = "NR-A (6MT)"
    elif "RS" in upper:
        grade_str = "RS (6MT)"
    elif "Sスペシャル" in text or "S SPECIAL" in upper:
        grade_str = "S Special Package (6MT)"
    elif "Sレザー" in text:
        grade_str = "S Leather Package (6MT)"

    mileage_str = "記載なし"
    m_km = re.search(r"(\d{1,2}(?:\.\d)?)\s*万\s*(?:km|キロ)|(\d{1,3},\d{3})\s*(?:km|キロ)", text, re.IGNORECASE)
    if m_km:
        mileage_str = m_km.group(0)

    lsd_str = "未記載 / 純正"
    for kw in ["クスコ", "CUSCO", "OS技研", "機械式LSD", "2WAY", "1.5WAY"]:
        if kw.upper() in upper:
            lsd_str = f"社外・機械式 ({kw})"
            break

    rollbar_str = "未装着 / 未記載"
    for kw in ["サイトウロールケージ", "オクヤマ", "6点式", "ロールバー", "ロールケージ"]:
        if kw.upper() in upper:
            rollbar_str = f"装着済 ({kw})"
            break

    wheel_str = "純正 / その他"
    if "TE37" in upper or "SONIC" in upper:
        wheel_str = "RAYS TE37 SONIC (16×7J)"
    elif "16インチ" in text:
        wheel_str = "16インチアルミ"

    return {
        "year": year_str,
        "grade": grade_str,
        "mileage": mileage_str,
        "shaken": "本文参照",
        "lsd": lsd_str,
        "rollbar": rollbar_str,
        "wheel": wheel_str,
        "location": "出品ページ参照",
    }


def is_excluded_item(
    title: str,
    description: str,
    price_man: Optional[float],
    source: str,
    config: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    共通必須フィルタに基づく除外判定を行う。
    """
    full_text = f"{title} {description}"
    full_text_upper = full_text.upper()

    if "NDERC" in full_text_upper or "RF" in title.upper().split():
        return True, "RF（2.0L NDERC）のため除外"

    exclude_keywords: List[str] = config.get("exclude_keywords", [])
    for kw in exclude_keywords:
        if kw.upper() in full_text_upper:
            if kw.upper() in ("AT", "オートマ") and any(
                mt_kw in full_text_upper for mt_kw in ["6MT", "6速MT", "マニュアル車", "MT車"]
            ):
                continue
            return True, f"除外ワード検出: {kw}"

    part_patterns = [
        r"(?:純正|社外)?(?:マフラー|ホイール|バンパー|シート|車高調|ステアリング|幌|ハードトップ|デフ|LSD)\s*(?:のみ|単体|出品|売ります)",
        r"部品取り車",
        r"不動車",
        r"書類なし",
    ]
    for pat in part_patterns:
        if re.search(pat, full_text, re.IGNORECASE):
            return True, f"パーツ単体・不動車パターン一致: {pat}"

    min_price_man = float(config.get("monitoring", {}).get("min_price_man", 60))
    if price_man is not None:
        if price_man <= min_price_man:
            return True, f"下限価格以下（{price_man}万円 <= {min_price_man}万円）"
    else:
        if source != "X":
            return True, "X以外で価格不明のため除外"

    return False, ""


def score_vehicle(
    item: Dict[str, Any],
    config: Dict[str, Any],
    ignored_ids: Optional[set] = None,
    ignored_signatures: Optional[set] = None,
) -> Dict[str, Any]:
    """
    車両情報を解析してマッチ度スコア（最大100pt）、ランク（S/A/B/C）、
    獲得バッジ配列、スペック要約、興味なしフラグ判定、およびLINE通知可否を算出する。
    """
    title = item.get("title", "")
    description = item.get("description", "")
    full_text = f"{title} {description}"
    full_text_upper = full_text.upper()

    price_man, price_display = extract_price_man(
        full_text, raw_price_yen=item.get("raw_price_yen")
    )
    if item.get("price_man") is not None:
        price_man = float(item["price_man"])
    if item.get("price_display"):
        price_display = item["price_display"]

    raw_score = 0
    badges: List[Dict[str, Any]] = []
    matched_rule_ids = set()

    for rule in config.get("scoring_rules", []):
        rule_id = rule.get("id", "")
        category = rule.get("category", "")
        points = int(rule.get("points", 0))
        keywords = rule.get("keywords", [])
        neg_keywords = rule.get("negative_keywords", [])

        if neg_keywords and any(nk.upper() in full_text_upper for nk in neg_keywords):
            if not any(
                pos_k in full_text_upper
                for pos_k in ["機械式", "クスコ", "CUSCO", "OS技研", "1.5WAY", "2WAY", "カーツ", "KAAZ", "マツダスピード"]
            ):
                continue

        matched_kws = [kw for kw in keywords if kw.upper() in full_text_upper]
        if matched_kws:
            raw_score += points
            matched_rule_ids.add(rule_id)
            label_str = "/".join(matched_kws[:2])
            badges.append(
                {
                    "id": rule_id,
                    "label": label_str,
                    "points": points,
                    "category": category,
                }
            )

    price_bonus_cfg = config.get("price_bonus", {})
    pb_min = float(price_bonus_cfg.get("min_exclusive_man", 60))
    pb_max = float(price_bonus_cfg.get("max_inclusive_man", 120))
    pb_pts = int(price_bonus_cfg.get("points", 10))

    if price_man is not None and (pb_min < price_man <= pb_max):
        raw_score += pb_pts
        badges.append(
            {
                "id": "price_bonus",
                "label": f"{int(pb_min)}〜{int(pb_max)}万円圏内",
                "points": pb_pts,
                "category": price_bonus_cfg.get("category", "価格ボーナス"),
            }
        )

    final_score = min(100, raw_score)

    if final_score >= 70:
        rank = "S"
    elif final_score >= 40:
        rank = "A"
    elif final_score >= 15:
        rank = "B"
    else:
        rank = "C"

    # 興味なし（通知ブロック）判定
    ignored_flag, ignore_reason = is_ignored_vehicle(
        item, config, ignored_ids=ignored_ids, ignored_signatures=ignored_signatures
    )

    skip_over_man = float(
        config.get("monitoring", {}).get("skip_line_normal_over_man", 150)
    )
    has_competition_feature = bool(matched_rule_ids & COMPETITION_RULE_IDS)

    should_notify = rank in ("S", "A", "B")
    skip_reason = None

    if ignored_flag:
        should_notify = False
        skip_reason = f"🚫 興味なしフラグ設定中のためLINE通知をブロック（{ignore_reason}）"
    elif rank == "C":
        should_notify = False
        skip_reason = "Cランク（0〜14pt：ノーマル・要確認）のためダッシュボード記録のみ"
    elif (
        price_man is not None
        and price_man >= skip_over_man
        and not has_competition_feature
    ):
        should_notify = False
        skip_reason = (
            f"{int(skip_over_man)}万円以上かつ競技系加点キーワード"
            "（NR-A/LSD/ロールバー/TE37/レース仕様）なしのためLINE通知を温存スキップ"
        )

    default_photo = "assets/car_s.jpg" if rank == "S" else ("assets/car_a.jpg" if rank == "A" else "assets/car_b.jpg")
    thumbnail = item.get("thumbnail") or default_photo

    result = dict(item)
    result.update(
        {
            "price_man": price_man,
            "price_display": price_display,
            "thumbnail": thumbnail,
            "favorite": False if ignored_flag else bool(item.get("favorite", False)),
            "ignored": ignored_flag,
            "title_signature": normalize_title_signature(title),
            "specs": extract_vehicle_specs(full_text, item.get("specs")),
            "score": final_score,
            "raw_score": raw_score,
            "rank": rank,
            "badges": badges,
            "should_notify_line": should_notify,
            "skip_reason": skip_reason,
        }
    )
    return result
