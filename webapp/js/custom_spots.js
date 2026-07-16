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

/** 緯度経度→1kmメッシュ(3次メッシュ・8桁)。precompute/build_crowd_profiles.pyと同じ公式式 */
function latlonToMesh1km(lat, lon) {
  const p = Math.floor(lat * 1.5);
  const a = lat * 1.5 - p;
  const q = Math.floor(a * 8);
  const b = a * 8 - q;
  const r = Math.floor(b * 10);
  const u = Math.floor(lon - 100);
  const c = lon - 100 - u;
  const v = Math.floor(c * 8);
  const d = c * 8 - v;
  const w = Math.floor(d * 10);
  return `${String(p).padStart(2, "0")}${String(u).padStart(2, "0")}${q}${v}${r}${w}`;
}

/** 入力 {id, name, category, lat, lon} → 公式スポットと同じ形のプロファイル。
 *  meshLevels(任意)があれば人流実データからその地点の混雑レベルを引く */
function makeCustomProfile(c, meshLevels) {
  const tpl = CUSTOM_TEMPLATES[CATEGORY_TEMPLATE[c.category] || "park_day"];
  const mesh = latlonToMesh1km(c.lat, c.lon);
  const lv = meshLevels && meshLevels[mesh];
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
    level: lv ? { weekday: lv[0], holiday: lv[1] } : { weekday: 50, holiday: 50 },
    level_source: lv ? "mesh" : "default",  // mesh=人流実データ / default=中間値
    custom: true,
  };
}

/**
 * 駅テーブル(spot_station_tables.json)から カスタムC⇄公式X の実ダイヤ由来の
 * 所要時間と乗車路線チェーンを求める。
 *   C→X: min_s( C→駅s徒歩 + 駅s→X(逆方向テーブル) )
 *   X→C: min_s( X→駅s(順方向テーブル) + 駅s→C徒歩 )
 * 戻り値: {min, chain} または null(到達不能)。chainはtravel_routes.jsonと同じ形
 */
function tableLookup(tables, cal, dir, officialIdx, custom, nearStations) {
  const row = tables[cal][dir === "to_official" ? "to" : "from"][officialIdx];
  let best = null;
  for (const [stIdx, w] of nearStations) {
    const v = row[stIdx];
    if (!v) continue;
    const total = v[0] + w;
    if (!best || total < best.min) best = { min: total, chainIdx: v[1] };
  }
  if (!best) return null;
  const chain = tables.chains[best.chainIdx].map(([rw, a, b]) => ({
    line: rw,
    from: [tables.stations[a][0], tables.stations[a][1]],
    to: [tables.stations[b][0], tables.stations[b][1]],
  }));
  return { min: Math.round(best.min), chain };
}

/** カスタム地点の徒歩圏の駅一覧 [[駅Idx, 徒歩分], ...] */
function nearStationsOf(c, tables) {
  const out = [];
  for (let i = 0; i < tables.stations.length; i++) {
    const st = tables.stations[i];
    const w = walkMin(c, { lat: st[2], lon: st[3] });
    if (w <= MAX_ANCHOR_WALK_MIN) out.push([i, w]);
  }
  return out;
}

/**
 * 公式データ+カスタムスポットを合成した {profiles, matrix, customRoutes, approx} を作る。
 * aux.stationTables があれば カスタム⇄公式 は実ダイヤ由来のテーブル引き
 * (customRoutesに乗車路線チェーンも入る)。無ければ最寄りスポット経由の概算。
 * カスタム同士は常に概算で、approx[a][b]=true が立つ(UIで「(概算)」表示に使う)。
 * aux.meshLevels があれば混雑レベルを人流実データから引く。
 */
function buildExtendedData(profiles, matrix, customs, aux) {
  aux = aux || {};
  if (!customs || customs.length === 0) return { profiles, matrix, customRoutes: {}, approx: {} };
  const customProfiles = customs.map((c) => makeCustomProfile(c, aux.meshLevels));
  const allSpots = profiles.spots.concat(customProfiles);
  const officialIds = matrix.spot_ids;
  const ids = officialIds.concat(customProfiles.map((c) => c.id));
  const byId = {};
  for (const s of allSpots) byId[s.id] = s;

  const extMatrix = { spot_ids: ids };
  const customRoutes = {};  // {a: {b: {weekday: chain, holiday: chain}}}
  const approx = {};        // {a: {b: true}} 概算値のペア(UIで明示する)
  const setRoute = (a, b, cal, chain) => {
    const m1 = (customRoutes[a] = customRoutes[a] || {});
    (m1[b] = m1[b] || {})[cal] = chain;
  };
  const setApprox = (a, b) => { (approx[a] = approx[a] || {})[b] = true; };
  const tables = aux.stationTables || null;
  const nearSt = {};
  if (tables) for (const c of customProfiles) nearSt[c.id] = nearStationsOf(c, tables);
  const tSpotIdx = tables ? Object.fromEntries(tables.spots.map((id, i) => [id, i])) : null;

  for (const cal of ["weekday", "holiday"]) {
    const base = matrix[cal];
    const n = ids.length;
    const m = Array.from({ length: n }, () => Array(n).fill(-1));
    // 公式ペアはそのまま
    for (let i = 0; i < officialIds.length; i++)
      for (let j = 0; j < officialIds.length; j++) m[i][j] = base[i][j];

    // カスタムを含むペアの概算(テーブルが無いときのフォールバック)
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
        const A = byId[ids[i]], B = byId[ids[j]];
        if (!A.custom && !B.custom) continue;

        let val = null;
        const direct = walkMin(A, B);
        if (direct <= MAX_SPOT_WALK_MIN) val = { min: Math.round(direct), chain: [] };

        if (tables && A.custom !== B.custom) {
          // カスタム⇄公式: 駅テーブル引き(実ダイヤ由来の代表値+乗車路線)
          const C = A.custom ? A : B;
          const X = A.custom ? B : A;
          const oi = tSpotIdx[X.id];
          if (oi !== undefined) {
            const r = tableLookup(tables, cal, A.custom ? "to_official" : "from_official",
                                  oi, C, nearSt[C.id]);
            if (r && (!val || r.min < val.min)) val = r;
          }
        } else {
          // テーブル未取得、またはカスタム同士 → 最寄りスポット経由の概算
          const est = estimate(ids[i], ids[j]);
          if (est >= 0 && (!val || est < val.min)) {
            val = { min: est, chain: null };
            setApprox(ids[i], ids[j]);
          }
        }

        if (val) {
          m[i][j] = val.min;
          if (val.chain) setRoute(ids[i], ids[j], cal, val.chain); // []=徒歩のみ も記録
        }
      }
    }
    extMatrix[cal] = m;
  }

  return {
    profiles: { ...profiles, spots: allSpots },
    matrix: extMatrix,
    customRoutes,
    approx,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { makeCustomProfile, buildExtendedData, latlonToMesh1km,
                     CATEGORY_TEMPLATE, CUSTOM_TEMPLATES };
}
