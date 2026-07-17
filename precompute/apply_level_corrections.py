# -*- coding: utf-8 -*-
"""
混雑レベルの補正適用: level_corrections.json を webapp/data/crowd_profiles.json に反映する

2019年人流データ基準のレベルのうち、2019年以降の構造変化が公的資料で確認できた
スポットだけを出典つきで補正する(検証記録: docs/level_validation_2026.md)。
絶対値で上書きするので何度実行しても同じ結果になる(冪等)。
補正したスポットには level_note を付け、補正済みであることをデータ側にも残す。

実行: python3 precompute/apply_level_corrections.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORRECTIONS_JSON = ROOT / "precompute" / "level_corrections.json"
PROFILES_JSON = ROOT / "webapp" / "data" / "crowd_profiles.json"


def main():
    corrections = json.load(open(CORRECTIONS_JSON, encoding="utf-8"))["corrections"]
    data = json.load(open(PROFILES_JSON, encoding="utf-8"))

    applied = []
    for spot in data["spots"]:
        c = corrections.get(spot["id"])
        if not c:
            continue
        before = dict(spot["level"])
        spot["level"]["weekday"] = c["weekday"]
        spot["level"]["holiday"] = c["holiday"]
        spot["level_note"] = c["note_ja"]
        applied.append((spot["id"], before, dict(spot["level"])))

    with open(PROFILES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    for sid, before, after in applied:
        print(f"{sid}: {before} → {after}")
    missing = set(corrections) - {s["id"] for s in data["spots"]}
    if missing:
        print(f"警告: 補正対象がスポットに見つからない: {missing}")
    print(f"適用 {len(applied)}件 → {PROFILES_JSON}")


if __name__ == "__main__":
    main()
