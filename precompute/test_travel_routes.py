# -*- coding: utf-8 -*-
"""路線案内(travel_routes.json)生成の単体テスト。
実行: python3 precompute/test_travel_routes.py

data/network_tokyo.pkl はAPIキーが要る再生成物なのでリポジトリに無い。
そこで小さな合成ネットワーク(2路線・乗換1回)を作り、
build_travel_matrix.main() を丸ごと実行して出力を検証する。

合成の地理(1度≈111kmを利用して座標を配置):
  spotA -- 駅S1 ==[LineX]== 駅S2(乗換) ==[LineY]== 駅S3 -- spotB
  spotA と spotC は徒歩圏(約500m)
"""

import csv
import json
import pickle
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_travel_matrix as btm
from transit_core import Network, Pattern, Trip

failed = 0
def check(name, cond, detail=""):
    global failed
    print(f"{'OK ' if cond else 'NG '} {name}{' … ' + str(detail) if detail else ''}")
    if not cond:
        failed += 1


def make_network():
    stops = {
        "S1": {"name": "エス1", "name_en": "S-One",   "lat": 35.00, "lon": 139.00, "operator": "TestOp"},
        "S2": {"name": "エス2", "name_en": "S-Two",   "lat": 35.00, "lon": 139.05, "operator": "TestOp"},
        "S3": {"name": "エス3", "name_en": "S-Three", "lat": 35.00, "lon": 139.10, "operator": "TestOp"},
    }
    # LineX: S1→S2、LineY: S2→S3。10時台に20分間隔で運行
    def trips(line, dep0, ride_min):
        return [Trip(trip_id=f"{line}.{k}", route_name=line,
                     arrivals=[dep0 + 20 * k, dep0 + 20 * k + ride_min],
                     departures=[dep0 + 20 * k, dep0 + 20 * k + ride_min])
                for k in range(6)]
    patterns = [
        Pattern(stop_ids=("S1", "S2"), trips=trips("TestOp.LineX", 10 * 60 + 5, 10)),
        Pattern(stop_ids=("S2", "S3"), trips=trips("TestOp.LineY", 10 * 60 + 0, 8)),
    ]
    stop_routes = {}
    for idx, p in enumerate(patterns):
        for pos, sid in enumerate(p.stop_ids):
            stop_routes.setdefault(sid, []).append((idx, pos))
    net = Network(patterns=patterns, stop_routes=stop_routes, stops=stops, footpaths={})
    return {"weekday": net, "holiday": net}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="suiteru_routes_test_"))
    # spotA=S1のそば、spotB=S3のそば、spotC=spotAから徒歩500m(駅からは遠い)
    spots = [
        {"id": "spotA", "name_ja": "スポットA", "name_en": "Spot A", "lat": 35.001, "lon": 139.000},
        {"id": "spotB", "name_ja": "スポットB", "name_en": "Spot B", "lat": 35.001, "lon": 139.100},
        {"id": "spotC", "name_ja": "スポットC", "name_en": "Spot C", "lat": 35.0055, "lon": 139.000},
    ]
    csv_path = tmp / "spots.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "name_ja", "name_en", "lat", "lon"])
        w.writeheader()
        w.writerows(spots)
    pkl_path = tmp / "network.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(make_network(), f)

    btm.SPOTS_CSV = csv_path
    btm.NET_PKL = pkl_path
    btm.OUT_JSON = tmp / "travel_matrix.json"
    btm.OUT_ROUTES_JSON = tmp / "travel_routes.json"
    btm.main()

    matrix = json.load(open(btm.OUT_JSON, encoding="utf-8"))
    routes = json.load(open(btm.OUT_ROUTES_JSON, encoding="utf-8"))
    ids = routes["spot_ids"]
    ia, ib, ic = ids.index("spotA"), ids.index("spotB"), ids.index("spotC")
    wk = routes["weekday"]

    # --- 1. 乗換1回の経路: LineX→LineY の2区間が乗車順に出る ---
    r_ab = wk[ia][ib]
    check("A→Bは2区間", isinstance(r_ab, list) and len(r_ab) == 2, r_ab)
    if isinstance(r_ab, list) and len(r_ab) == 2:
        check("1本目はLineX(エス1→エス2)",
              r_ab[0]["line"] == "TestOp.LineX" and r_ab[0]["from"][0] == "エス1"
              and r_ab[0]["to"][0] == "エス2", r_ab[0])
        check("2本目はLineY(エス2→エス3)",
              r_ab[1]["line"] == "TestOp.LineY" and r_ab[1]["to"][0] == "エス3", r_ab[1])
        check("英語駅名も出る", r_ab[0]["from"][1] == "S-One", r_ab[0]["from"])

    # --- 2. 行列との整合: 経路がある向きは所要分も正 ---
    check("A→Bの所要分が正", matrix["weekday"][ia][ib] > 0, matrix["weekday"][ia][ib])

    # --- 3. 徒歩のみのペアは [] ---
    r_ac = wk[ia][ic]
    check("A→C(徒歩500m)は[]", r_ac == [], r_ac)

    # --- 4. 到達不能はnull(B→Cは逆向きの列車が無い) ---
    r_bc = wk[ib][ic]
    check("B→C(逆向き列車なし・徒歩圏外)はnull", r_bc is None, r_bc)

    print("\n全テスト合格" if failed == 0 else f"\n{failed}件失敗")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
