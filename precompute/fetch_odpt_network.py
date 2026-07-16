# -*- coding: utf-8 -*-
"""
M3-前半: ODPT APIから鉄道ネットワークの素材(駅・路線・列車時刻表)を取得する

使い方:
  1. ODPT開発者サイトで発行されたAPIキー(アクセストークン)を
     data/odpt_apikey.txt に1行だけ貼り付けて保存する(.gitignore対象なので公開されない)
  2. まず可用性チェック:  python precompute/fetch_odpt_network.py --check
     → 必要な各社のデータが自分のキーで取れるかの一覧が出る
  3. 本取得:              python precompute/fetch_odpt_network.py
     → data/odpt/ にJSONが保存される(以降のbuild_network_tokyo.pyが読む)

なぜGTFSでなくODPT JSONか(2026-07-16調査):
  ODPTセンターでは都営・メトロ等の一部だけがGTFS提供で、JR東日本や私鉄の多くは
  ODPT独自JSON(odpt:Station / odpt:TrainTimetable)での提供のため、
  全社を同じ形式で扱えるJSONに統一する。
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "data" / "odpt_apikey.txt"
OUT_DIR = ROOT / "data" / "odpt"
API_BASE = "https://api.odpt.org/api/v4/"

# 取得対象の事業者と路線(スポット22箇所の最寄り駅をカバーする範囲)
# 路線IDはodpt:Railwayの命名規則(odpt.Railway:事業者.路線名)
OPERATORS = [
    "JR-East",       # 山手線・中央線・京浜東北線など
    "TokyoMetro",    # 銀座線・日比谷線・千代田線・丸ノ内線・半蔵門線など
    "Toei",          # 浅草線・大江戸線・新宿線・三田線
    "Yurikamome",    # 豊洲市場・チームラボ・お台場
    "TWR",           # りんかい線(お台場)
    "Tobu",          # スカイツリーライン
    "Keio",          # 高尾山
    "Keisei",        # 柴又(金町線)
    "Seibu",         # 西武新宿(歌舞伎町)
]


def read_key() -> str:
    if not KEY_FILE.exists():
        print(f"エラー: APIキーのファイルがありません: {KEY_FILE}")
        print("ODPT開発者サイトのアクセストークンを、このファイルに1行だけ貼り付けてください。")
        sys.exit(1)
    return KEY_FILE.read_text(encoding="utf-8").strip()


def fetch(data_type: str, params: dict, key: str):
    """ODPT APIを1回呼ぶ。例: fetch("odpt:Station", {"odpt:operator": "odpt.Operator:JR-East"})"""
    query = dict(params)
    query["acl:consumerKey"] = key
    url = API_BASE + data_type + "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": "suiteru-tokyo/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(key: str):
    """各事業者の駅・列車時刻表が取れるかを確認して表にする"""
    print(f"{'事業者':<12} {'駅数':>6} {'列車時刻表(件)':>10}  判定")
    ng = []
    for op in OPERATORS:
        op_id = f"odpt.Operator:{op}"
        try:
            stations = fetch("odpt:Station", {"odpt:operator": op_id}, key)
            n_sta = len(stations)
        except Exception as e:
            n_sta = f"エラー({e})"
        try:
            # 件数確認だけなので路線を絞らず事業者単位で取る
            tt = fetch("odpt:TrainTimetable", {"odpt:operator": op_id}, key)
            n_tt = len(tt)
        except Exception as e:
            n_tt = f"エラー({e})"
        ok = isinstance(n_sta, int) and n_sta > 0 and isinstance(n_tt, int) and n_tt > 0
        if not ok:
            ng.append(op)
        print(f"{op:<12} {str(n_sta):>6} {str(n_tt):>10}  {'OK' if ok else 'NG'}")
        time.sleep(1)  # 連続アクセスの行儀(レートリミット対策)
    if ng:
        print(f"\nNGの事業者: {ng}")
        print("→ build_network_tokyo.py 側で代替(近隣駅から徒歩など)を検討する")
    else:
        print("\n全事業者OK。本取得(--checkなし)に進んでください。")


def download_all(key: str):
    """駅・路線・列車時刻表を事業者ごとに保存する"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for op in OPERATORS:
        op_id = f"odpt.Operator:{op}"
        for data_type, fname in [("odpt:Station", "stations"),
                                 ("odpt:Railway", "railways"),
                                 ("odpt:TrainTimetable", "train_timetables")]:
            out = OUT_DIR / f"{op}_{fname}.json"
            try:
                data = fetch(data_type, {"odpt:operator": op_id}, key)
            except Exception as e:
                print(f"{op} {data_type}: 取得失敗 ({e})")
                continue
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            print(f"{op} {data_type}: {len(data)}件 → {out.name}")
            time.sleep(1)


if __name__ == "__main__":
    key = read_key()
    if "--check" in sys.argv:
        check(key)
    else:
        download_all(key)
