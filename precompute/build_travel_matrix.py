# -*- coding: utf-8 -*-
"""
M3-後半: スポット×スポットの所要時間行列を事前計算する

入力:  data/spots.csv, data/network_tokyo.pkl
出力:  webapp/data/travel_matrix.json
        {"spot_ids": [...], "weekday": [[分]], "holiday": [[分]]}
        (行=出発スポット、列=到着スポット。-1は到達不能)

計算方法:
  各スポットの徒歩圏(1200m)の駅を入口にして、RAPTORで全駅への最早到着を計算。
  午前の複数の出発時刻(10:00/10:20/10:40)を試して所要時間の最小を採用する
  (「たまたま電車が行った直後」の偏りを避ける。norishiroと同じ考え方)。
  スポット同士が徒歩30分以内なら、徒歩の方が速い場合は徒歩の時間を採用。
"""

import csv
import json
import pickle
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

from transit_core import raptor_search

ROOT = Path(__file__).resolve().parent.parent
SPOTS_CSV = ROOT / "data" / "spots.csv"
NET_PKL = ROOT / "data" / "network_tokyo.pkl"
OUT_JSON = ROOT / "webapp" / "data" / "travel_matrix.json"

# --- パラメータ(観光客の徒歩を想定) ---
WALK_SPEED_M_PER_MIN = 75   # 観光客の歩行速度
WALK_DETOUR = 1.3
MAX_WALK_TO_STATION_M = 1200  # スポットから駅まで歩ける上限(直線)。豊洲市場→豊洲駅対応
MAX_SPOT_WALK_MIN = 30        # スポット間を直接歩く場合の上限(分)
DEPART_TIMES = [10 * 60, 10 * 60 + 20, 10 * 60 + 40]  # 10:00, 10:20, 10:40
MAX_TRANSFERS = 3


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def walk_min(dist_m):
    return dist_m * WALK_DETOUR / WALK_SPEED_M_PER_MIN


def main():
    spots = list(csv.DictReader(open(SPOTS_CSV, encoding="utf-8")))
    networks = pickle.load(open(NET_PKL, "rb"))
    stops = networks["weekday"].stops  # 駅情報はカレンダー共通

    # 各スポットの入口駅(徒歩圏)を求める
    entries = {}
    for s in spots:
        lat, lon = float(s["lat"]), float(s["lon"])
        near = []
        for sid, st in stops.items():
            d = haversine_m(lat, lon, st["lat"], st["lon"])
            if d <= MAX_WALK_TO_STATION_M:
                near.append((sid, walk_min(d)))
        entries[s["id"]] = near
        if not near:
            print(f"警告: {s['name_ja']} は徒歩{MAX_WALK_TO_STATION_M}m圏に駅がない(到達不能になる)")

    ids = [s["id"] for s in spots]
    result = {"spot_ids": ids, "generated_note":
              "5社(メトロ・都営・りんかい・東武・京王)の列車時刻表による。JR東日本・京成・ゆりかもめ非対応(2026-07-16時点の提供状況)"}
    for cal, network in networks.items():
        n = len(spots)
        matrix = [[-1] * n for _ in range(n)]
        for i, s in enumerate(spots):
            best = {}  # 到着スポットj → 最小所要分
            for t0 in DEPART_TIMES:
                initial = {sid: t0 + round(w) for sid, w in entries[s["id"]]}
                if not initial:
                    continue
                res = raptor_search(network, initial, max_transfers=MAX_TRANSFERS)
                for j, s2 in enumerate(spots):
                    if i == j:
                        continue
                    for sid, w in entries[s2["id"]]:
                        if sid in res:
                            total = res[sid]["arrival"] + round(w) - t0
                            if total < best.get(j, 10 ** 9):
                                best[j] = total
            # スポット間の直接徒歩
            for j, s2 in enumerate(spots):
                if i == j:
                    continue
                wm = walk_min(haversine_m(float(s["lat"]), float(s["lon"]),
                                          float(s2["lat"]), float(s2["lon"])))
                if wm <= MAX_SPOT_WALK_MIN and wm < best.get(j, 10 ** 9):
                    best[j] = round(wm)
            for j, v in best.items():
                matrix[i][j] = round(v)
        result[cal] = matrix
        reachable = sum(1 for row in matrix for v in row if v >= 0)
        print(f"{cal}: 到達可能ペア {reachable}/{len(spots) * (len(spots) - 1)}")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"出力: {OUT_JSON}")

    # --- 検証: 代表ペアの所要時間を表示(NAVITIME等と目視比較する) ---
    print("\n=== 代表ペアの所要時間(平日・10時台出発・徒歩込み) ===")
    checks = [("sensoji", "shibuya_crossing"), ("ueno_park", "sensoji"),
              ("meiji_jingu", "imperial_palace"), ("kabukicho", "takaosan"),
              ("skytree", "ginza"), ("tokyo_station", "odaiba")]
    wk = result["weekday"]
    name = {s["id"]: s["name_ja"] for s in spots}
    for a, b in checks:
        ia, ib = ids.index(a), ids.index(b)
        print(f"  {name[a]} → {name[b]}: {wk[ia][ib]}分")


if __name__ == "__main__":
    main()
