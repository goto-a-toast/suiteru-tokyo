# -*- coding: utf-8 -*-
"""
ユーザー追加スポットの実データ化(段階1): 全メッシュの混雑レベル表を出力する

入力:  data/monthly_mdp_mesh1km_13.zip  … 人流オープンデータ(東京都・1kmメッシュ)
       webapp/data/crowd_profiles.json  … 正規化の基準(公式22箇所のlevel_raw)
出力:  webapp/data/mesh_levels.json     … {"メッシュコード": [平日レベル, 休日レベル]}

レベルは公式スポットと同じ物差し(22箇所のlevel_rawの最小〜最大を対数スケールで
0-100に写像)で正規化する。範囲外は0/100にクランプ。
これにより、地図上のどの地点を追加しても「その場所の2019年実測ベースの混雑レベル」が引ける。
"""

import csv
import io
import json
import math
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JINRYU_ZIP = ROOT / "data" / "monthly_mdp_mesh1km_13.zip"
PROFILES_JSON = ROOT / "webapp" / "data" / "crowd_profiles.json"
OUT_JSON = ROOT / "webapp" / "data" / "mesh_levels.json"

BASE_YEAR = "2019"
TIMEZONE_NOON = "0"
DAYFLAG_HOLIDAY = "0"
DAYFLAG_WEEKDAY = "1"


def main():
    # 正規化アンカー(公式22箇所と同じ物差しにする)
    profiles = json.load(open(PROFILES_JSON, encoding="utf-8"))["spots"]
    max_raw = max(p["level_raw"]["holiday"] for p in profiles)
    min_raw = min(min(p["level_raw"]["weekday"], p["level_raw"]["holiday"]) for p in profiles)
    log_min, log_max = math.log(min_raw), math.log(max_raw)

    def level(v):
        if v <= 0:
            return 0
        score = 100 * (math.log(v) - log_min) / (log_max - log_min)
        return max(0, min(100, round(score)))

    # 全メッシュ×月×平休日の昼人口を集計
    sums = defaultdict(float)
    counts = defaultdict(int)
    with zipfile.ZipFile(JINRYU_ZIP) as z:
        inner_names = [n for n in z.namelist()
                       if f"/{BASE_YEAR}/" in n and n.endswith("monthly_mdp_mesh1km.csv.zip")]
        print(f"{BASE_YEAR}年の月別ファイル: {len(inner_names)}個")
        for name in sorted(inner_names):
            with zipfile.ZipFile(io.BytesIO(z.read(name))) as inner:
                csv_name = [n for n in inner.namelist() if n.endswith(".csv")][0]
                with inner.open(csv_name) as f:
                    for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                        if row["timezone"] != TIMEZONE_NOON:
                            continue
                        flag = row["dayflag"]
                        if flag not in (DAYFLAG_WEEKDAY, DAYFLAG_HOLIDAY):
                            continue
                        key = (row["mesh1kmid"], flag)
                        sums[key] += float(row["population"])
                        counts[key] += 1

    meshes = {m for m, _ in sums}
    out = {}
    for m in meshes:
        wk_key, hd_key = (m, DAYFLAG_WEEKDAY), (m, DAYFLAG_HOLIDAY)
        if counts[wk_key] == 0 or counts[hd_key] == 0:
            continue
        wk = sums[wk_key] / counts[wk_key]
        hd = sums[hd_key] / counts[hd_key]
        out[m] = [level(wk), level(hd)]

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"generated_note":
                   f"全国の人流オープンデータ(国交省) {BASE_YEAR}年・1kmメッシュ・昼(11-14時台)の"
                   "年平均を、公式22スポットと同じ対数スケールで0-100に正規化",
                   "levels": out}, f, ensure_ascii=False)
    size = OUT_JSON.stat().st_size
    print(f"出力: {OUT_JSON} ({len(out)}メッシュ, {size / 1000:.0f}KB)")

    # 検算: 公式スポットのメッシュはlevelとほぼ一致するはず(丸め差のみ)
    diffs = []
    for p in profiles:
        got = out.get(p["mesh1kmid"])
        if got:
            diffs.append(abs(got[0] - p["level"]["weekday"]))
    if diffs:
        print(f"検算: 公式22箇所とのレベル差 最大{max(diffs)} (丸め差の範囲なら正常)")


if __name__ == "__main__":
    main()
