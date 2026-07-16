# -*- coding: utf-8 -*-
"""スポット⇔駅テーブル出力の単体テスト(合成ネットワーク)。
実行: python3 precompute/test_spot_station_tables.py

合成の地理(test_travel_routes.pyと同じ):
  spotA -- 駅S1 ==[LineX]== 駅S2 ==[LineY]== 駅S3 -- spotB
  列車はS1→S3方向のみ(逆方向テーブルの検証がしやすい)
"""

import csv
import json
import pickle
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_spot_station_tables as est
from transit_core import Network, Pattern, Trip

failed = 0
def check(name, cond, detail=""):
    global failed
    print(f"{'OK ' if cond else 'NG '} {name}{' … ' + str(detail) if detail else ''}")
    if not cond:
        failed += 1


def make_network():
    stops = {
        "S1": {"name": "エス1", "name_en": "S-One",   "lat": 35.00, "lon": 139.00, "operator": "T"},
        "S2": {"name": "エス2", "name_en": "S-Two",   "lat": 35.00, "lon": 139.05, "operator": "T"},
        "S3": {"name": "エス3", "name_en": "S-Three", "lat": 35.00, "lon": 139.10, "operator": "T"},
    }
    def trips(line, dep0, ride_min):
        return [Trip(trip_id=f"{line}.{k}", route_name=line,
                     arrivals=[dep0 + 20 * k, dep0 + 20 * k + ride_min],
                     departures=[dep0 + 20 * k, dep0 + 20 * k + ride_min])
                for k in range(24)]  # 10時台〜のかなり後まで運行(締切逆算用に多めに)
    patterns = [
        Pattern(stop_ids=("S1", "S2"), trips=trips("T.LineX", 9 * 60 + 5, 10)),
        Pattern(stop_ids=("S2", "S3"), trips=trips("T.LineY", 9 * 60 + 0, 8)),
    ]
    stop_routes = {}
    for idx, p in enumerate(patterns):
        for pos, sid in enumerate(p.stop_ids):
            stop_routes.setdefault(sid, []).append((idx, pos))
    net = Network(patterns=patterns, stop_routes=stop_routes, stops=stops, footpaths={})
    return {"weekday": net, "holiday": net}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="suiteru_sst_test_"))
    spots = [
        {"id": "spotA", "name_ja": "スポットA", "name_en": "Spot A", "lat": 35.001, "lon": 139.000},
        {"id": "spotB", "name_ja": "スポットB", "name_en": "Spot B", "lat": 35.001, "lon": 139.100},
    ]
    csv_path = tmp / "spots.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "name_ja", "name_en", "lat", "lon"])
        w.writeheader()
        w.writerows(spots)
    pkl_path = tmp / "net.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(make_network(), f)

    est.SPOTS_CSV = csv_path
    est.NET_PKL = pkl_path
    est.OUT_JSON = tmp / "spot_station_tables.json"
    est.main()

    d = json.load(open(est.OUT_JSON, encoding="utf-8"))
    st_names = [s[0] for s in d["stations"]]
    iS1, iS3 = st_names.index("エス1"), st_names.index("エス3")
    iA, iB = d["spots"].index("spotA"), d["spots"].index("spotB")
    wk = d["weekday"]

    # --- 1. スポット→駅(順方向): A→S3 は 徒歩+待ち+乗換込みでおよそ20〜45分 ---
    vA_S3 = wk["from"][iA][iS3]
    check("1: A→S3が到達可能", bool(vA_S3), vA_S3)
    if vA_S3:
        check("1: 所要が妥当(20〜45分)", 20 <= vA_S3[0] <= 45, f"{vA_S3[0]}分")
        ch = d["chains"][vA_S3[1]]
        check("1: チェーンがLineX→LineY", [c[0] for c in ch] == ["T.LineX", "T.LineY"], ch)
        check("1: チェーンの向き S1→S2, S2→S3",
              st_names[ch[0][1]] == "エス1" and st_names[ch[1][2]] == "エス3", ch)

    # --- 2. 駅→スポット(逆方向): S1→B が到達可能で、チェーンは実世界の向き ---
    vS1_B = wk["to"][iB][iS1]
    check("2: S1→Bが到達可能", bool(vS1_B), vS1_B)
    if vS1_B:
        check("2: 所要が妥当(20〜60分)", 20 <= vS1_B[0] <= 60, f"{vS1_B[0]}分")
        ch = d["chains"][vS1_B[1]]
        check("2: チェーンがLineX→LineY(実世界の向き)",
              [c[0] for c in ch] == ["T.LineX", "T.LineY"], ch)
        check("2: 乗車 S1発・S3着",
              st_names[ch[0][1]] == "エス1" and st_names[ch[-1][2]] == "エス3", ch)

    # --- 3. 列車が無い向き: S3→A(逆走)は徒歩圏外なら到達不能(0) ---
    vS3_A = wk["to"][iA][iS3]
    check("3: S3→A(逆走)は到達不能", vS3_A == 0, vS3_A)

    # --- 4. 対称性の目安: A→S3(順) と S1→B(逆) の乗車部分は同じ路線構成 ---
    check("4: chains辞書が重複排除されている", len(d["chains"]) <= 6, f"{len(d['chains'])}種")

    print("\n全テスト合格" if failed == 0 else f"\n{failed}件失敗")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
