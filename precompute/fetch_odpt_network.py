# -*- coding: utf-8 -*-
"""
M3-前半: ODPT APIから鉄道ネットワークの素材(駅・路線・列車時刻表)を取得する

使い方:
  1. APIキーを以下に置く(.gitignore対象なので公開されない):
       data/odpt_apikey.txt            … ODPT開発者サイト(センター)のトークン
       data/odpt_challenge_apikey.txt  … チャレンジ2026専用トークン(エントリー後に発行。無くても動く)
  2. 可用性チェック:  python precompute/fetch_odpt_network.py --check
  3. 本取得:          python precompute/fetch_odpt_network.py
     → data/odpt/ に事業者ごとのJSONが保存される

2026-07-16調査メモ:
  - センター(api.odpt.org)で列車時刻表が取れるのは TokyoMetro / Toei / TWR
  - JR東日本・ゆりかもめ・京王・京成・東武・西武などのチャレンジ限定データは
    api-challenge.odpt.org + チャレンジ専用トークンが必要(センターのトークンでは403)
  - 列車時刻表は1回の応答が1000件で頭打ちになるため、路線ごとに分割して取得する
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "data" / "odpt_apikey.txt"
CHALLENGE_KEY_FILE = ROOT / "data" / "odpt_challenge_apikey.txt"
OUT_DIR = ROOT / "data" / "odpt"

ENDPOINTS = {
    "center": "https://api.odpt.org/api/v4/",
    "challenge": "https://api-challenge.odpt.org/api/v4/",
}

# 取得対象の事業者(スポット22箇所の最寄り駅をカバーする範囲)。
# source: どちらのエンドポイントを先に試すか
OPERATORS = [
    ("TokyoMetro", "center"),
    ("Toei", "center"),
    ("TWR", "center"),        # りんかい線(お台場)
    ("JR-East", "challenge"),  # 山手線・中央線など(チャレンジ限定)
    ("Yurikamome", "challenge"),
    ("Tobu", "challenge"),     # スカイツリーライン
    ("Keio", "challenge"),     # 高尾山
    ("Keisei", "challenge"),   # 柴又
    ("Seibu", "challenge"),    # 西武新宿
]


def read_keys() -> dict:
    keys = {}
    if KEY_FILE.exists():
        keys["center"] = KEY_FILE.read_text(encoding="utf-8").strip()
    if CHALLENGE_KEY_FILE.exists():
        keys["challenge"] = CHALLENGE_KEY_FILE.read_text(encoding="utf-8").strip()
    if "center" not in keys:
        print(f"エラー: {KEY_FILE} がありません")
        sys.exit(1)
    if "challenge" not in keys:
        print("注意: チャレンジ専用トークン(data/odpt_challenge_apikey.txt)が未配置。"
              "JR東日本などチャレンジ限定データはスキップされます。\n")
    return keys


def fetch(source: str, keys: dict, data_type: str, params: dict):
    """ODPT APIを1回呼ぶ。sourceは 'center' か 'challenge'"""
    if source not in keys:
        raise RuntimeError(f"{source}のトークンが未配置")
    query = dict(params)
    query["acl:consumerKey"] = keys[source]
    url = ENDPOINTS[source] + data_type + "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": "suiteru-tokyo/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_operator(op: str, source: str, keys: dict):
    """1事業者分の駅・路線・列車時刻表を取得する。
    列車時刻表は1000件上限を避けるため路線ごとに分けて取る。
    戻り値: (stations, railways, timetables) いずれもlist。取得不能ならNone"""
    op_id = f"odpt.Operator:{op}"
    try:
        stations = fetch(source, keys, "odpt:Station", {"odpt:operator": op_id})
        railways = fetch(source, keys, "odpt:Railway", {"odpt:operator": op_id})
    except (urllib.error.HTTPError, RuntimeError) as e:
        return None, None, None, f"駅・路線の取得失敗({e})"

    timetables = []
    for rw in railways:
        rw_id = rw["owl:sameAs"]
        try:
            tt = fetch(source, keys, "odpt:TrainTimetable", {"odpt:railway": rw_id})
        except (urllib.error.HTTPError, RuntimeError) as e:
            return None, None, None, f"{rw_id}の時刻表取得失敗({e})"
        if len(tt) >= 1000:
            # それでも上限に当たる場合は方面別に分ける
            tt = []
            for direction in rw.get("odpt:ascendingRailDirection"), rw.get("odpt:descendingRailDirection"):
                if direction:
                    tt += fetch(source, keys, "odpt:TrainTimetable",
                                {"odpt:railway": rw_id, "odpt:railDirection": direction})
            if len(tt) >= 2000:
                print(f"  警告: {rw_id} は方面別でも1000件上限に達している可能性")
        timetables += tt
        time.sleep(0.6)
    return stations, railways, timetables, None


def main():
    keys = read_keys()
    check_only = "--check" in sys.argv
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"{'事業者':<12} {'取得元':<10} {'駅':>5} {'路線':>4} {'列車時刻表':>8}  結果")
    for op, source in OPERATORS:
        if source not in keys:
            print(f"{op:<12} {source:<10} {'-':>5} {'-':>4} {'-':>8}  スキップ(トークン未配置)")
            continue
        stations, railways, timetables, err = fetch_operator(op, source, keys)
        if err:
            print(f"{op:<12} {source:<10} {'-':>5} {'-':>4} {'-':>8}  NG: {err}")
            continue
        ok = len(timetables) > 0
        print(f"{op:<12} {source:<10} {len(stations):>5} {len(railways):>4} {len(timetables):>8}  {'OK' if ok else 'NG(時刻表なし)'}")
        if ok and not check_only:
            for data, fname in [(stations, "stations"), (railways, "railways"),
                                (timetables, "train_timetables")]:
                with open(OUT_DIR / f"{op}_{fname}.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
        time.sleep(0.6)

    if not check_only:
        print(f"\n保存先: {OUT_DIR}")


if __name__ == "__main__":
    main()
