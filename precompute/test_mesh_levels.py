# -*- coding: utf-8 -*-
"""メッシュレベル表出力の単体テスト(合成ZIP)。
実行: python3 precompute/test_mesh_levels.py"""

import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_mesh_levels as bml

failed = 0
def check(name, cond, detail=""):
    global failed
    print(f"{'OK ' if cond else 'NG '} {name}{' … ' + str(detail) if detail else ''}")
    if not cond:
        failed += 1


def make_zip(path, rows):
    """人流ZIPと同じ入れ子構造(外ZIP→月別ZIP→CSV)の合成データを作る"""
    header = "mesh1kmid,prefcode,citycode,year,month,dayflag,timezone,population\n"
    csv_body = header + "\n".join(rows)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("monthly_mdp_mesh1km.csv", csv_body)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("13/2019/01/monthly_mdp_mesh1km.csv.zip", inner.getvalue())


def main():
    tmp = Path(tempfile.mkdtemp(prefix="suiteru_mesh_test_"))
    profiles = json.load(open(bml.PROFILES_JSON, encoding="utf-8"))["spots"]
    sensoji = next(p for p in profiles if p["id"] == "sensoji")
    raw_wk = sensoji["level_raw"]["weekday"]
    raw_hd = sensoji["level_raw"]["holiday"]
    max_raw = max(p["level_raw"]["holiday"] for p in profiles)
    min_raw = min(min(p["level_raw"]["weekday"], p["level_raw"]["holiday"]) for p in profiles)

    rows = [
        # 浅草寺と同じ人口のメッシュ → 浅草寺と同じレベルになるはず
        f"99990001,13,13100,2019,01,1,0,{raw_wk}",
        f"99990001,13,13100,2019,01,0,0,{raw_hd}",
        # 最大アンカーの2倍 → 100にクランプ
        f"99990002,13,13100,2019,01,1,0,{max_raw * 2}",
        f"99990002,13,13100,2019,01,0,0,{max_raw * 2}",
        # 最小アンカーの半分 → 0にクランプ
        f"99990003,13,13100,2019,01,1,0,{max(1, min_raw // 2)}",
        f"99990003,13,13100,2019,01,0,0,{max(1, min_raw // 2)}",
        # 夜の時間帯(timezone=1)は無視される
        "99990004,13,13100,2019,01,1,1,999999",
    ]
    zpath = tmp / "jinryu.zip"
    make_zip(zpath, rows)

    bml.JINRYU_ZIP = zpath
    bml.OUT_JSON = tmp / "mesh_levels.json"
    bml.main()

    out = json.load(open(bml.OUT_JSON, encoding="utf-8"))["levels"]
    check("1: 浅草寺相当メッシュのレベル一致(±1)",
          abs(out["99990001"][0] - sensoji["level"]["weekday"]) <= 1,
          f"{out['99990001'][0]} vs {sensoji['level']['weekday']}")
    check("2: 最大超えは100にクランプ", out["99990002"] == [100, 100], out["99990002"])
    check("3: 最小未満は0にクランプ", out["99990003"][0] == 0, out["99990003"])
    check("4: 昼以外の時間帯だけのメッシュは出力されない", "99990004" not in out)

    print("\n全テスト合格" if failed == 0 else f"\n{failed}件失敗")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
