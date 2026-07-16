# -*- coding: utf-8 -*-
"""
M3-補足: ゆりかもめの「駅時刻表」から「列車時刻表」を復元する

ゆりかもめは列車単位の時刻表(odpt:TrainTimetable)が提供されておらず、
駅ごとの発車時刻表(odpt:StationTimetable)のみ提供されている(2026-07-16確認)。
ゆりかもめは全列車各駅停車・追い越しなしの単一路線なので、
進行方向に沿って「次の駅の、直近の未使用の発車」を順につなげば列車を復元できる。

入力:  data/odpt/Yurikamome_{stations,railways,station_timetables}.json
出力:  data/odpt/Yurikamome_train_timetables.json
        (odpt:TrainTimetableと同じ形式。build_network_tokyo.pyがそのまま読める)
"""

import json
from collections import defaultdict
from pathlib import Path

ODPT_DIR = Path(__file__).resolve().parent.parent / "data" / "odpt"

CHAIN_MIN, CHAIN_MAX = 1, 8   # 次駅の発車を探す時間窓(分)。駅間は概ね2分
CAL_MAP = {"odpt.Calendar:Weekday": "odpt.Calendar:Weekday",
           "odpt.Calendar:SaturdayHoliday": "odpt.Calendar:SaturdayHoliday"}


def to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes):
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


def main():
    railways = json.load(open(ODPT_DIR / "Yurikamome_railways.json", encoding="utf-8"))
    stt = json.load(open(ODPT_DIR / "Yurikamome_station_timetables.json", encoding="utf-8"))

    # 駅の並び(新橋→豊洲の昇順)
    order = sorted(railways[0]["odpt:stationOrder"], key=lambda x: x["odpt:index"])
    stations_asc = [o["odpt:station"] for o in order]
    railway_id = railways[0]["owl:sameAs"]

    # 方向×カレンダーごとに、駅→発車時刻リスト(昇順)を作る
    # 方向の意味は destinationStation から判定する(Inbound=新橋行き等の名前に依存しない)
    deps = defaultdict(lambda: defaultdict(list))  # (cal, direction) -> station -> [分,...]
    dest_of = {}
    for st in stt:
        cal = CAL_MAP.get(st.get("odpt:calendar"))
        if cal is None:
            continue
        key = (cal, st.get("odpt:railDirection"))
        for obj in st["odpt:stationTimetableObject"]:
            deps[key][st["odpt:station"]].append(to_minutes(obj["odpt:departureTime"]))
            dest_of[key] = obj.get("odpt:destinationStation", [None])[0]
        deps[key][st["odpt:station"]].sort()

    out = []
    for (cal, direction), by_station in deps.items():
        # 進行方向の駅順: 行き先が並びの末尾側なら昇順、先頭側なら降順
        dest = dest_of.get((cal, direction))
        seq = stations_asc if dest == stations_asc[-1] else list(reversed(stations_asc))
        used = {s: 0 for s in seq}   # 各駅の発車リストの「ここまで使った」ポインタ(追い越しなし前提)

        for start_idx, start_station in enumerate(seq[:-1]):
            times = by_station.get(start_station, [])
            while used[start_station] < len(times):
                t0 = times[used[start_station]]
                used[start_station] += 1
                stops = [(start_station, t0)]
                t_prev = t0
                for nxt in seq[start_idx + 1:]:
                    cand = by_station.get(nxt, [])
                    i = used[nxt]
                    while i < len(cand) and cand[i] < t_prev + CHAIN_MIN:
                        i += 1
                    if i >= len(cand) or cand[i] > t_prev + CHAIN_MAX:
                        break   # つながる発車がない = この駅が終点(またはデータ端)
                    used[nxt] = i + 1
                    stops.append((nxt, cand[i]))
                    t_prev = cand[i]
                if len(stops) < 2:
                    continue
                # 終点の到着 = 最後の発車+2分(駅間の平均所要で近似)
                tt_obj = [{"odpt:departureStation": s, "odpt:departureTime": to_hhmm(t)}
                          for s, t in stops]
                last_station_after = seq[seq.index(stops[-1][0]) + 1] if stops[-1][0] != seq[-1] else None
                if last_station_after:
                    tt_obj.append({"odpt:arrivalStation": last_station_after,
                                   "odpt:arrivalTime": to_hhmm(stops[-1][1] + 2)})
                out.append({
                    "owl:sameAs": f"odpt.TrainTimetable:Yurikamome.reconstructed.{cal.split(':')[-1]}.{direction.split(':')[-1] if direction else '?'}.{to_hhmm(t0)}.{start_idx}",
                    "odpt:railway": railway_id,
                    "odpt:calendar": cal,
                    "odpt:trainTimetableObject": tt_obj,
                })

    with open(ODPT_DIR / "Yurikamome_train_timetables.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    # 検証表示
    from collections import Counter
    print("復元した列車:", len(out), "本")
    print("カレンダー別:", Counter(t["odpt:calendar"] for t in out))
    lens = Counter(len(t["odpt:trainTimetableObject"]) for t in out)
    print("停車数の分布(16駅通し運転が多数派のはず):", dict(sorted(lens.items())))


if __name__ == "__main__":
    main()
