# -*- coding: utf-8 -*-
"""
ユーザー追加スポットの実データ化: 「公式スポット⇔全駅」所要時間+経路表を出力する

入力:  data/spots.csv, data/network_tokyo.pkl
出力:  webapp/data/spot_station_tables.json
        {
          "stations": [[name_ja, name_en, lat, lon], ...],      # 添字=駅ID
          "chains":   [[[railway, fromStIdx, toStIdx], ...], ...] # 乗車路線チェーン(重複排除)
          "spots":    ["sensoji", ...],
          "weekday": {"from": [spot][station] = [分, chainIdx] | 0,   # スポット→駅
                      "to":   [spot][station] = [分, chainIdx] | 0},  # 駅→スポット
          "holiday": {...}
        }
        0 = 到達不能。"from"の分にはスポット→入口駅の徒歩を含む(降車駅から先の徒歩は含まない)。
        "to"の分には駅からの乗車〜スポットまでの徒歩を含む(乗車駅までの徒歩は含まない)。

設計メモ:
  - 時刻表そのものは配らない(チャレンジ限定データの再配布を避ける)。この表は
    travel_matrix.jsonと同じ「代表値の集計」であり、ライセンス上安全な派生物
  - スポット→駅は通常のRAPTOR。駅→スポットは時刻を反転したネットワークで
    「到着締切から逆算」するRAPTOR(締切11:30/11:50/12:10の最遅出発)で代表値を取る
"""

import csv
import json
import pickle
from pathlib import Path

from build_travel_matrix import (DEPART_TIMES, MAX_TRANSFERS, MAX_WALK_TO_STATION_M,
                                 haversine_m, walk_min)
from transit_core import Leg, Network, Pattern, Trip, raptor_search, reconstruct_path

ROOT = Path(__file__).resolve().parent.parent
SPOTS_CSV = ROOT / "data" / "spots.csv"
NET_PKL = ROOT / "data" / "network_tokyo.pkl"
OUT_JSON = ROOT / "webapp" / "data" / "spot_station_tables.json"

# 逆方向(駅→スポット)の代表値に使う到着締切。DEPART_TIMES(10時台出発)に
# 平均的な乗車時間を足した水準に合わせる
ARRIVE_DEADLINES = [11 * 60 + 30, 11 * 60 + 50, 12 * 60 + 10]


def reverse_network(net: Network) -> Network:
    """時刻を反転したネットワーク(逆RAPTOR用)。
    実世界の s→X の移動は、反転世界では X→s の移動になる"""
    patterns = []
    for p in net.patterns:
        trips = []
        for t in p.trips:
            trips.append(Trip(
                trip_id=t.trip_id, route_name=t.route_name,
                arrivals=[-d for d in reversed(t.departures)],
                departures=[-a for a in reversed(t.arrivals)],
            ))
        trips.sort(key=lambda t: t.departures[0])
        patterns.append(Pattern(stop_ids=tuple(reversed(p.stop_ids)), trips=trips))
    stop_routes = {}
    for idx, p in enumerate(patterns):
        for pos, sid in enumerate(p.stop_ids):
            stop_routes.setdefault(sid, []).append((idx, pos))
    return Network(patterns=patterns, stop_routes=stop_routes,
                   stops=net.stops, footpaths=net.footpaths)


def chain_of(legs, reverse=False):
    """Leg列 → [(railway, from_sid, to_sid), ...]。同一路線の連続乗車は結合。
    reverse=True は反転世界のLeg列を実世界の向きに直す"""
    rides = [l for l in legs if l.kind == "ride"]
    if reverse:
        rides = [Leg(kind="ride", from_stop=l.to_stop, to_stop=l.from_stop,
                     depart=-l.arrive, arrive=-l.depart, trip_id=l.trip_id,
                     route_name=l.route_name) for l in reversed(rides)]
    out = []
    for l in rides:
        if out and out[-1][0] == l.route_name and out[-1][2] == l.from_stop:
            out[-1] = (out[-1][0], out[-1][1], l.to_stop)
        else:
            out.append((l.route_name, l.from_stop, l.to_stop))
    return out


def main():
    spots = list(csv.DictReader(open(SPOTS_CSV, encoding="utf-8")))
    networks = pickle.load(open(NET_PKL, "rb"))
    stops = networks["weekday"].stops

    station_ids = sorted(stops.keys())
    st_index = {sid: i for i, sid in enumerate(station_ids)}
    stations = [[stops[s]["name"], stops[s].get("name_en", ""),
                 round(stops[s]["lat"], 5), round(stops[s]["lon"], 5)] for s in station_ids]

    # スポットの入口駅(build_travel_matrix.pyと同じ徒歩圏)
    entries = {}
    for s in spots:
        lat, lon = float(s["lat"]), float(s["lon"])
        near = [(sid, walk_min(haversine_m(lat, lon, st["lat"], st["lon"])))
                for sid, st in stops.items()
                if haversine_m(lat, lon, st["lat"], st["lon"]) <= MAX_WALK_TO_STATION_M]
        entries[s["id"]] = near

    chains = []          # 重複排除したチェーン
    chain_index = {}

    def chain_id(ch):
        key = tuple(ch)
        if key not in chain_index:
            chain_index[key] = len(chains)
            chains.append([[rw, st_index[a], st_index[b]] for rw, a, b in ch])
        return chain_index[key]

    out = {"spots": [s["id"] for s in spots]}
    for cal, network in networks.items():
        rev = reverse_network(network)
        table = {"from": [], "to": []}
        for s in spots:
            ent = entries[s["id"]]
            # --- スポット→駅(通常RAPTOR、出発時刻3通りの最小) ---
            row_from = [0] * len(station_ids)
            best = {}
            for t0 in DEPART_TIMES:
                initial = {sid: t0 + round(w) for sid, w in ent}
                if not initial:
                    continue
                res = raptor_search(network, initial, max_transfers=MAX_TRANSFERS)
                for sid, e in res.items():
                    dur = e["arrival"] - t0
                    if sid in st_index and dur < best.get(sid, (10 ** 9,))[0]:
                        best[sid] = (dur, chain_of(reconstruct_path(res, sid)))
            for sid, (dur, ch) in best.items():
                row_from[st_index[sid]] = [round(dur), chain_id(ch)]
            table["from"].append(row_from)

            # --- 駅→スポット(反転RAPTOR、到着締切3通りの最小) ---
            row_to = [0] * len(station_ids)
            best = {}
            for T in ARRIVE_DEADLINES:
                initial = {sid: -T + round(w) for sid, w in ent}
                if not initial:
                    continue
                res = raptor_search(rev, initial, max_transfers=MAX_TRANSFERS)
                for sid, e in res.items():
                    dur = T + e["arrival"]  # e["arrival"] = -(最遅出発時刻)
                    if sid in st_index and dur < best.get(sid, (10 ** 9,))[0]:
                        best[sid] = (dur, chain_of(reconstruct_path(res, sid), reverse=True))
            for sid, (dur, ch) in best.items():
                row_to[st_index[sid]] = [round(dur), chain_id(ch)]
            table["to"].append(row_to)
        out[cal] = table
        n_from = sum(1 for r in table["from"] for v in r if v)
        n_to = sum(1 for r in table["to"] for v in r if v)
        print(f"{cal}: スポット→駅 {n_from}エントリ / 駅→スポット {n_to}エントリ")

    out["stations"] = stations
    out["chains"] = chains
    out["generated_note"] = ("公式スポット⇔全駅の代表所要時間(10時台基準)+乗車路線。"
                             "時刻表の再配布ではなく集計済み派生物")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    size = OUT_JSON.stat().st_size
    print(f"出力: {OUT_JSON} ({size / 1e6:.1f}MB, チェーン{len(chains)}種)")


if __name__ == "__main__":
    main()
