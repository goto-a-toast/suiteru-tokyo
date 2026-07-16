# -*- coding: utf-8 -*-
"""
M2: 混雑プロファイル事前計算

入力:
  data/spots.csv                     … M1で整備したスポットマスタ(22箇所)
  data/monthly_mdp_mesh1km_13.zip    … 人流オープンデータ(東京都・1kmメッシュ滞在人口)
  precompute/curve_templates.json    … 観光地タイプ別の時間帯カーブ(形)

出力:
  webapp/data/crowd_profiles.json    … スポット×平休日×時間帯(1時間刻み)の混雑プロファイル

考え方(ハイブリッド方式):
  レベル(その場所がどれだけ混む場所か) = 人流データの「昼(11-14時台)の滞在人口」(2019年・平日/休日別)
  形(1日の中でいつ混むか)            = カーブテンプレート(タイプ別・出典つき)
  ※人流データに1時間刻みの実測はないため、この2つを掛け合わせて使う
"""

import csv
import io
import json
import math
import zipfile
from pathlib import Path

# --- パスの設定(このファイルの場所を基準にする) ---
ROOT = Path(__file__).resolve().parent.parent
SPOTS_CSV = ROOT / "data" / "spots.csv"
JINRYU_ZIP = ROOT / "data" / "monthly_mdp_mesh1km_13.zip"
TEMPLATES_JSON = ROOT / "precompute" / "curve_templates.json"
OUT_JSON = ROOT / "webapp" / "data" / "crowd_profiles.json"

# --- パラメータ ---
BASE_YEAR = "2019"   # コロナ前の2019年を基準にする(README・応募書類に明記済みの限界)
TIMEZONE_NOON = "0"  # 人流データの時間帯コード: 0=昼(11-14時台の平均)
DAYFLAG_HOLIDAY = "0"  # 0=休日
DAYFLAG_WEEKDAY = "1"  # 1=平日
DAYFLAG_ALL = "2"      # 2=全日


def latlon_to_mesh1km(lat: float, lon: float) -> str:
    """緯度経度から1kmメッシュ(3次メッシュ・8桁)コードを計算する。

    標準地域メッシュの公式の計算式そのまま。
    検算: 山形駅(38.2484, 140.3278) → "57402296"(norishiroプロジェクトの検証済みテストケース)
    """
    p = int(lat * 1.5)          # 1次メッシュ(緯度側)
    a = lat * 1.5 - p
    q = int(a * 8)              # 2次メッシュ(緯度側)
    b = a * 8 - q
    r = int(b * 10)             # 3次メッシュ(緯度側)
    u = int(lon - 100)          # 1次メッシュ(経度側)
    c = lon - 100 - u
    v = int(c * 8)              # 2次メッシュ(経度側)
    d = c * 8 - v
    w = int(d * 10)             # 3次メッシュ(経度側)
    return f"{p:02d}{u:02d}{q}{v}{r}{w}"


def read_spots():
    """スポットマスタを読み込み、各スポットの1kmメッシュコードを付ける"""
    spots = []
    with open(SPOTS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["mesh1kmid"] = latlon_to_mesh1km(float(row["lat"]), float(row["lon"]))
            spots.append(row)
    return spots


def read_jinryu_for_meshes(target_meshes: set):
    """人流ZIPから、対象メッシュの2019年データだけを取り出す。

    ZIPの中に「13/2019/01/monthly_mdp_mesh1km.csv.zip」のように月別のZIPが
    入れ子になっているので、2019年の12ヶ月分を順に開いて読む。
    戻り値: {(mesh, month, dayflag): population} の辞書(時間帯は昼のみ)
    """
    result = {}
    with zipfile.ZipFile(JINRYU_ZIP) as outer:
        names = [n for n in outer.namelist()
                 if f"/{BASE_YEAR}/" in n and n.endswith("monthly_mdp_mesh1km.csv.zip")]
        if len(names) != 12:
            print(f"警告: {BASE_YEAR}年の月別ファイルが12個でなく{len(names)}個です")
        for name in sorted(names):
            inner_bytes = outer.read(name)
            with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                csv_name = [n for n in inner.namelist() if n.endswith(".csv")][0]
                with inner.open(csv_name) as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                    for row in reader:
                        if row["mesh1kmid"] not in target_meshes:
                            continue
                        if row["timezone"] != TIMEZONE_NOON:
                            continue  # 「昼」だけ使う(1時間刻みの形はテンプレート側の担当)
                        key = (row["mesh1kmid"], row["month"], row["dayflag"])
                        result[key] = int(row["population"])
    return result


def mean(values):
    """平均(データが無い月があっても落ちないように)"""
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def main():
    spots = read_spots()
    meshes = {s["mesh1kmid"] for s in spots}
    print(f"スポット{len(spots)}箇所 → 対象メッシュ{len(meshes)}個")

    jinryu = read_jinryu_for_meshes(meshes)
    print(f"人流データ({BASE_YEAR}年・昼)の該当行: {len(jinryu)}件")

    with open(TEMPLATES_JSON, encoding="utf-8") as f:
        templates = json.load(f)["templates"]

    months = [f"{m:02d}" for m in range(1, 13)]
    profiles = []
    missing = []
    for s in spots:
        m = s["mesh1kmid"]
        wk = mean([jinryu.get((m, mo, DAYFLAG_WEEKDAY)) for mo in months])   # 平日昼の年平均
        hd = mean([jinryu.get((m, mo, DAYFLAG_HOLIDAY)) for mo in months])   # 休日昼の年平均
        if wk is None or hd is None:
            missing.append(s["id"])
            continue
        # 月別係数: その月の全日昼人口 ÷ 年平均(桜期・紅葉期などの季節変動を表す)
        all_by_month = [jinryu.get((m, mo, DAYFLAG_ALL)) for mo in months]
        all_mean = mean(all_by_month)
        month_factor = [round(v / all_mean, 2) if (v and all_mean) else 1.0 for v in all_by_month]

        curve = templates[s["curve_type"]]
        profiles.append({
            "id": s["id"],
            "name_ja": s["name_ja"],
            "name_en": s["name_en"],
            "lat": float(s["lat"]),
            "lon": float(s["lon"]),
            "category": s["category"],
            "curve_type": s["curve_type"],
            "nearest_stations": s["nearest_stations"].split(";"),
            "open_hours": s["open_hours"],
            "mesh1kmid": m,
            "level_raw": {"weekday": round(wk), "holiday": round(hd)},
            "month_factor": month_factor,
            "hourly_shape": curve["hourly"],  # 0時〜23時の相対混雑(ピーク=100)
        })

    if missing:
        print(f"警告: 人流データが見つからないスポット: {missing}")

    # レベルの正規化: 対数スケールで0-100に(渋谷と柴又は桁が違うため、
    # そのまま比例させると小さいスポットが全部0になってしまう)
    max_raw = max(p["level_raw"]["holiday"] for p in profiles)
    min_raw = min(min(p["level_raw"]["weekday"], p["level_raw"]["holiday"]) for p in profiles)
    for p in profiles:
        for k in ("weekday", "holiday"):
            v = p["level_raw"][k]
            score = 100 * (math.log(v) - math.log(min_raw)) / (math.log(max_raw) - math.log(min_raw))
            p.setdefault("level", {})[k] = round(score)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_from": f"全国の人流オープンデータ(国交省) {BASE_YEAR}年・東京都・1kmメッシュ・昼(11-14時台)",
        "note": "混雑スコアは統計と公表資料に基づく予報であり実測ではない。hourly_shapeはカーブテンプレート(curve_templates.json参照)",
        "spots": profiles,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"出力: {OUT_JSON}")

    # --- 検証用の表示: レベル順ランキングと直感チェック ---
    print("\n=== 休日昼の滞在人口ランキング(レベルの妥当性チェック) ===")
    for p in sorted(profiles, key=lambda x: -x["level_raw"]["holiday"]):
        print(f"  {p['level']['holiday']:3d}点 {p['level_raw']['holiday']:8,}人 {p['name_ja']}")


if __name__ == "__main__":
    main()
