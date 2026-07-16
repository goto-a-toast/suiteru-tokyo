# -*- coding: utf-8 -*-
"""
M3-中盤: ODPT JSONの列車時刻表を、RAPTORエンジン(transit_core.py)が扱える
Network形式に変換して保存する

入力:  data/odpt/{事業者}_stations.json / _train_timetables.json
出力:  data/network_tokyo.pkl  … {"weekday": Network, "holiday": Network}

変換の考え方:
  odpt:TrainTimetable 1件 = 列車1本 → Trip
  停車駅の並びが同じ列車のグループ → Pattern (RAPTORの探索単位)
  平日(Weekday)と土休日(SaturdayHoliday/Holiday)で別のNetworkを作る

既知の簡略化(READMEにも書く):
  - 直通運転(odpt:nextTrainTimetable)は連結しない。直通でも「同駅乗換3分」として
    扱われるため、実際より少し長めに出る(安全側の誤差)
  - JR東日本・京成は時刻表データが提供されていないため含まれない(2026-07-16確認)
"""

import json
import pickle
from collections import defaultdict
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

from transit_core import Network, Pattern, Trip

ROOT = Path(__file__).resolve().parent.parent
ODPT_DIR = ROOT / "data" / "odpt"
OUT_PKL = ROOT / "data" / "network_tokyo.pkl"

OPERATORS = ["TokyoMetro", "Toei", "TWR", "Tobu", "Keio"]

# 徒歩乗換のパラメータ
TRANSFER_MAX_M = 500        # 直線距離でこの範囲の駅同士は徒歩乗換できる(大手町-東京など)
WALK_SPEED_M_PER_MIN = 60   # 乗換徒歩の速度(構内・階段込みの控えめな値)
WALK_DETOUR = 1.3           # 直線距離→実際の道のりの割増係数

# カレンダーの正規化: 平日/土休日の2種類にまとめる
CALENDAR_MAP = {
    "odpt.Calendar:Weekday": "weekday",
    "odpt.Calendar:SaturdayHoliday": "holiday",
    "odpt.Calendar:Holiday": "holiday",
    "odpt.Calendar:Saturday": "holiday",
}


def haversine_m(lat1, lon1, lat2, lon2):
    """2点間の直線距離(メートル)"""
    R = 6371000
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def to_minutes(hhmm: str) -> int:
    """"10:05" → 605分。ODPTは深夜も"00:15"等で表すことがあるが、
    ここでは変換せず、列車内の並びで日跨ぎを補正する(build_trip参照)"""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def build_trip(tt: dict) -> tuple:
    """odpt:TrainTimetable 1件 → (停車駅タプル, Trip)。壊れたデータはNoneを返す"""
    stops, arrs, deps = [], [], []
    prev = -1
    for obj in tt.get("odpt:trainTimetableObject", []):
        station = obj.get("odpt:departureStation") or obj.get("odpt:arrivalStation")
        t_dep = obj.get("odpt:departureTime") or obj.get("odpt:arrivalTime")
        t_arr = obj.get("odpt:arrivalTime") or obj.get("odpt:departureTime")
        if station is None or t_dep is None:
            continue
        arr, dep = to_minutes(t_arr), to_minutes(t_dep)
        # 日跨ぎ補正: 前の停車より小さい時刻が来たら翌日扱い(+24時間)
        if arr < prev - 720:
            arr += 1440
        if dep < arr:
            dep += 1440 if dep < arr - 720 else 0
        dep = max(dep, arr)
        if arr < prev:  # 並び順がおかしいデータは捨てる
            return None, None
        stops.append(station)
        arrs.append(arr)
        deps.append(dep)
        prev = arr
    if len(stops) < 2:
        return None, None
    route_name = tt.get("odpt:railway", "?").split(":")[-1]
    return tuple(stops), Trip(trip_id=tt.get("owl:sameAs", "?"),
                              route_name=route_name, arrivals=arrs, departures=deps)


def main():
    # --- 駅情報(全社まとめて) ---
    stops = {}
    for op in OPERATORS:
        for s in json.load(open(ODPT_DIR / f"{op}_stations.json", encoding="utf-8")):
            if "geo:lat" not in s:
                continue
            title = s.get("odpt:stationTitle", {})
            stops[s["owl:sameAs"]] = {
                "name": s.get("dc:title", "?"),
                "name_en": title.get("en", ""),
                "lat": s["geo:lat"],
                "lon": s["geo:long"],
                "operator": op,
            }
    print(f"駅(座標あり): {len(stops)}")

    # --- 列車 → カレンダー別にPattern化 ---
    patterns_by_cal = {"weekday": defaultdict(list), "holiday": defaultdict(list)}
    n_trips = {"weekday": 0, "holiday": 0}
    skipped = 0
    for op in OPERATORS:
        for tt in json.load(open(ODPT_DIR / f"{op}_train_timetables.json", encoding="utf-8")):
            cal = CALENDAR_MAP.get(tt.get("odpt:calendar"))
            if cal is None:
                skipped += 1
                continue
            stop_ids, trip = build_trip(tt)
            if trip is None:
                skipped += 1
                continue
            patterns_by_cal[cal][stop_ids].append(trip)
            n_trips[cal] += 1
    print(f"列車: 平日{n_trips['weekday']}本 / 土休日{n_trips['holiday']}本 / 除外{skipped}件")

    # --- 徒歩乗換ペア(格子で近傍だけ比較して高速化。norishiroの手法) ---
    grid = defaultdict(list)
    for sid, s in stops.items():
        grid[(int(s["lat"] / 0.005), int(s["lon"] / 0.005))].append(sid)
    footpaths = defaultdict(list)
    seen = set()
    for (gy, gx), ids in grid.items():
        neighbors = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                neighbors += grid.get((gy + dy, gx + dx), [])
        for a in ids:
            for b in neighbors:
                if a >= b or (a, b) in seen:
                    continue
                seen.add((a, b))
                d = haversine_m(stops[a]["lat"], stops[a]["lon"], stops[b]["lat"], stops[b]["lon"])
                if d <= TRANSFER_MAX_M:
                    walk_min = d * WALK_DETOUR / WALK_SPEED_M_PER_MIN
                    footpaths[a].append((b, walk_min))
                    footpaths[b].append((a, walk_min))
    n_pairs = sum(len(v) for v in footpaths.values()) // 2
    print(f"徒歩乗換ペア({TRANSFER_MAX_M}m以内): {n_pairs}")

    # --- Network組み立て(カレンダー別) ---
    networks = {}
    for cal, pat_dict in patterns_by_cal.items():
        patterns, stop_routes = [], defaultdict(list)
        for stop_ids, trips in pat_dict.items():
            trips.sort(key=lambda t: t.departures[0])
            idx = len(patterns)
            patterns.append(Pattern(stop_ids=stop_ids, trips=trips))
            for pos, sid in enumerate(stop_ids):
                stop_routes[sid].append((idx, pos))
        networks[cal] = Network(patterns=patterns, stop_routes=dict(stop_routes),
                                stops=stops, footpaths=dict(footpaths))
        print(f"{cal}: {len(patterns)}パターン")

    with open(OUT_PKL, "wb") as f:
        pickle.dump(networks, f)
    print(f"出力: {OUT_PKL}")


if __name__ == "__main__":
    main()
