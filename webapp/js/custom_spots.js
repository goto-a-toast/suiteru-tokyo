/**
 * ユーザー追加スポット(カスタムスポット)のロジック(純粋関数)
 *
 * 公式22スポットは人流実データ+事前計算済みの所要時間を持つが、
 * ユーザーが追加する場所にはどちらも無い。そこで:
 *   - 混雑カーブ: カテゴリ別テンプレート(precompute/curve_templates.json v0と同じもの)を適用。
 *     混雑レベルは中間値(50)固定。※「テンプレートによる予報」であることをUIで明示する
 *   - 所要時間: 「最寄りの公式スポットまでの実測所要時間 + 徒歩」の最小値で概算。
 *     近距離は直接徒歩。※「概算」であることをUIで明示する
 * plan_itinerary.js / index.html からは公式スポットと同じ形で見えるようにする。
 */

"use strict";

// precompute/curve_templates.json (v0) から転記した時間帯カーブ。
// テンプレートを更新(v1化)したらここも同期すること
const CUSTOM_TEMPLATES = {
  market_early: {
    hourly: [0,0,0,0,5,15,25,35,45,70,95,100,95,75,40,15,5,0,0,0,0,0,0,0],
    visit_window: [6, 14],
  },
  shrine_morning: {
    hourly: [0,0,0,0,0,5,10,15,30,50,75,90,95,100,95,85,65,40,20,10,5,0,0,0],
    visit_window: [6, 17],
  },
  park_day: {
    hourly: [0,0,0,0,0,0,5,10,25,45,70,85,95,100,95,85,65,35,15,5,0,0,0,0],
    visit_window: [9, 17],
  },
  shopping_afternoon: {
    hourly: [0,0,0,0,0,0,0,5,10,25,45,65,80,90,100,95,90,80,65,45,25,10,5,0],
    visit_window: [10, 20],
  },
  downtown_evening: {
    hourly: [5,5,0,0,0,0,5,10,15,25,35,50,60,65,70,75,80,90,100,100,95,80,55,25],
    visit_window: [10, 23],
  },
  observation_evening: {
    hourly: [0,0,0,0,0,0,0,0,5,20,35,50,60,65,70,75,85,95,100,95,80,55,25,5],
    visit_window: [10, 22],
  },
};

// カテゴリ→テンプレート(公式スポットのspots.csvと同じ対応)
const CATEGORY_TEMPLATE = {
  "寺社": "shrine_morning",
  "市場": "market_early",
  "公園庭園": "park_day",
  "商店街": "shopping_afternoon",
  "繁華街": "downtown_evening",
  "展望施設": "observation_evening",
  "アトラクション": "shopping_afternoon",
  "建築観光": "observation_evening",
  "自然": "park_day",
};

// 徒歩パラメータ(precompute/build_travel_matrix.pyと同じ値)
const WALK_SPEED_M_PER_MIN = 75;
const WALK_DETOUR = 1.3;
const MAX_SPOT_WALK_MIN = 30;   // 直接徒歩を採用する上限
const MAX_ANCHOR_WALK_MIN = 45; // 「最寄りスポット経由」で歩ける上限(これを超える孤立地は到達不能扱い)

function haversineM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const rad = (d) => (d * Math.PI) / 180;
  const dlat = rad(lat2 - lat1), dlon = rad(lon2 - lon1);
  const a = Math.sin(dlat / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dlon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function walkMin(a, b) {
  return (haversineM(a.lat, a.lon, b.lat, b.lon) * WALK_DETOUR) / WALK_SPEED_M_PER_MIN;
}

/** 入力 {id, name, category, lat, lon} → 公式スポットと同じ形のプロファイル */
function makeCustomProfile(c) {
  const tpl = CUSTOM_TEMPLATES[CATEGORY_TEMPLATE[c.category] || "park_day"];
  return {
    id: c.id,
    name_ja: c.name,
    name_en: c.name,       // 追加時は1つの名前を日英共用
    lat: c.lat,
    lon: c.lon,
    category: c.category,
    nearest_stations: [],  // 不明(運行情報アラートの対象外になる)
    month_factor: Array(12).fill(1.0),
    hourly_shape: tpl.hourly,
    visit_window: tpl.visit_window,
    level: { weekday: 50, holiday: 50 }, // 実データが無いので中間値
    custom: true,
  };
}

/**
 * 公式データ+カスタムスポットを合成した {profiles, matrix} を作る。
 * カスタム同士・カスタム⇄公式の所要時間は
 *   min(直接徒歩, min_S(公式Sまで徒歩 + Sからの実測所要時間))
 * で概算する(-1=到達不能)。既存の公式ペアの値はそのまま。
 */
function buildExtendedData(profiles, matrix, customs) {
  if (!customs || customs.length === 0) return { profiles, matrix };
  const customProfiles = customs.map(makeCustomProfile);
  const allSpots = profiles.spots.concat(customProfiles);
  const officialIds = matrix.spot_ids;
  const ids = officialIds.concat(customProfiles.map((c) => c.id));
  const byId = {};
  for (const s of allSpots) byId[s.id] = s;

  const extMatrix = { spot_ids: ids };
  for (const cal of ["weekday", "holiday"]) {
    const base = matrix[cal];
    const n = ids.length;
    const m = Array.from({ length: n }, () => Array(n).fill(-1));
    // 公式ペアはそのまま
    for (let i = 0; i < officialIds.length; i++)
      for (let j = 0; j < officialIds.length; j++) m[i][j] = base[i][j];

    // カスタムを含むペアを概算
    const estimate = (a, b) => {
      let best = Infinity;
      const direct = walkMin(byId[a], byId[b]);
      if (direct <= MAX_SPOT_WALK_MIN) best = direct;
      for (let s = 0; s < officialIds.length; s++) {
        const S = byId[officialIds[s]];
        // a側がカスタムなら「a→S徒歩 + S→b」、b側がカスタムなら「a→S + S→b徒歩」
        const wa = byId[a].custom ? walkMin(byId[a], S) : null;
        const wb = byId[b].custom ? walkMin(S, byId[b]) : null;
        if (byId[a].custom && !byId[b].custom) {
          const t = base[s][officialIds.indexOf(b)];
          if (wa <= MAX_ANCHOR_WALK_MIN && t >= 0) best = Math.min(best, wa + t);
        } else if (!byId[a].custom && byId[b].custom) {
          const t = base[officialIds.indexOf(a)][s];
          if (wb <= MAX_ANCHOR_WALK_MIN && t >= 0) best = Math.min(best, t + wb);
        } else {
          // 両方カスタム: a→S徒歩 + S→S'実測 + S'→b徒歩
          for (let s2 = 0; s2 < officialIds.length; s2++) {
            const t = s === s2 ? 0 : base[s][s2];
            const wb2 = walkMin(byId[officialIds[s2]], byId[b]);
            if (wa <= MAX_ANCHOR_WALK_MIN && wb2 <= MAX_ANCHOR_WALK_MIN && t >= 0)
              best = Math.min(best, wa + t + wb2);
          }
        }
      }
      return best === Infinity ? -1 : Math.round(best);
    };

    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if (i === j) continue;
        if (byId[ids[i]].custom || byId[ids[j]].custom) m[i][j] = estimate(ids[i], ids[j]);
      }
    }
    extMatrix[cal] = m;
  }

  return {
    profiles: { ...profiles, spots: allSpots },
    matrix: extMatrix,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { makeCustomProfile, buildExtendedData, CATEGORY_TEMPLATE, CUSTOM_TEMPLATES };
}
