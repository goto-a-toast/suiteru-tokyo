/**
 * M4: 行程最適化エンジン(ルールベース・純粋関数)
 *
 * 設計ルール(docs/plan_suiteru.md §2):
 *   - 入出力はJSONのみ。DOM・fetch・グローバル状態に依存しない純粋関数。
 *     Stage 1では画面のフォームが、Stage 2ではLLMコンシェルジュが、同じ関数を呼ぶ。
 *   - ブラウザとNode.jsの両方で動く(テストはNodeで実行する)。
 *
 * 使い方:
 *   const result = planItinerary(request, data);
 *   request = {
 *     spot_ids: ["tsukiji", "sensoji", "shibuya_crossing"],  // 行きたい場所(2〜6箇所)
 *     day_type: "weekday" | "holiday",
 *     start_time: "09:00",     // 行動開始(この時刻に最初のスポットへ出発できる)
 *     end_time: "20:00",       // この時刻までに最後のスポットを出る
 *     month: 7,                // 1-12(省略可。月別係数に使う)
 *   }
 *   data = { profiles: crowd_profiles.jsonの中身, matrix: travel_matrix.jsonの中身 }
 *
 * 戻り値(成功時):
 *   {
 *     ok: true,
 *     legs: [{spot_id, name_ja, name_en, arrive: "09:12", depart: "10:12",
 *             travel_min: 12, crowd_pct: 23}],   // crowd_pct=滞在中の混雑(その場所のピーク=100)
 *     total_crowd: 87.3,          // 行程全体の混雑コスト(比較用)
 *     baseline: {legs: [...], total_crowd: 152.1},  // 入力順のまま回った場合
 *     reduction_pct: 43,          // ベースライン比でどれだけ混雑を減らせたか
 *     warnings: ["..."]
 *   }
 * 失敗時: { ok: false, reason: "...", unreachable: [...] }
 */

"use strict";

// カテゴリ別の標準滞在時間(分)。リクエストで dwell_min を渡せば上書きできる
const DEFAULT_DWELL_MIN = {
  "寺社": 60,
  "市場": 75,
  "公園庭園": 90,
  "商店街": 60,
  "繁華街": 60,
  "展望施設": 75,
  "アトラクション": 120,
  "建築観光": 30,
  "自然": 180,
};

const START_OFFSETS = [0, 30, 60, 90]; // 「時間ずらし」候補: 開始を最大90分遅らせて試す

function toMin(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function toHHMM(min) {
  const h = Math.floor(min / 60) % 24;
  const m = min % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** 滞在時間帯 [arrive, depart) の平均混雑(そのスポットのピーク=100) */
function crowdDuringVisit(profile, arriveMin, departMin) {
  let sum = 0;
  let n = 0;
  for (let t = arriveMin; t < departMin; t += 15) { // 15分刻みでサンプリング
    const h = Math.floor(t / 60) % 24;
    sum += profile.hourly_shape[h];
    n += 1;
  }
  return n > 0 ? sum / n : 0;
}

/**
 * 混雑コスト。設計意図:
 *   - べき乗2.5の非線形: 「どこか1箇所でピーク(90超)を踏む」ことを、
 *     複数箇所のそこそこの混雑よりも重いペナルティにする。
 *     線形の合計だと「築地のピーク直撃を、他の空きで相殺」する行程が選ばれてしまう
 *   - レベルは0.5〜1.0の弱い重み: 混雑の絶対量が大きい場所をやや優先しつつ、
 *     小さなスポットのピーク回避も無視しない
 */
function crowdCost(profile, arriveMin, departMin, dayType, month) {
  const shape = crowdDuringVisit(profile, arriveMin, departMin);
  const levelWeight = 0.5 + 0.5 * (profile.level[dayType] / 100);
  const mf = month ? profile.month_factor[month - 1] : 1.0;
  return Math.pow(shape / 100, 2.5) * 100 * levelWeight * mf;
}

/** 順列を列挙する(スポット数<=6なので全列挙で問題ない) */
function permutations(arr) {
  if (arr.length <= 1) return [arr];
  const out = [];
  for (let i = 0; i < arr.length; i++) {
    const rest = arr.slice(0, i).concat(arr.slice(i + 1));
    for (const p of permutations(rest)) out.push([arr[i]].concat(p));
  }
  return out;
}

/**
 * 1つの訪問順・開始時刻で行程をシミュレーションする。
 * 訪問可能時間帯(visit_window)前に着いたら開くまで待つ。
 * 収まらなければ null を返す。
 */
function simulate(order, startMin, endMin, ctx) {
  const { profilesById, matrixOf, dayType, month, dwellOf } = ctx;
  const legs = [];
  let t = startMin;
  let total = 0;
  let prev = null;
  for (const id of order) {
    const p = profilesById[id];
    const travel = prev === null ? 0 : matrixOf(prev, id);
    if (travel < 0) return null; // 経路データなし(到達不能ペア)
    let arrive = t + travel;
    const [openH, closeH] = p.visit_window;
    if (arrive < openH * 60) arrive = openH * 60;   // 開くまで待つ
    const depart = arrive + dwellOf(id);
    if (depart > closeH * 60) return null;           // 閉まる前に滞在を終えられない
    if (depart > endMin) return null;                // 1日の終了時刻に収まらない
    const cost = crowdCost(p, arrive, depart, dayType, month);
    legs.push({
      spot_id: id,
      name_ja: p.name_ja,
      name_en: p.name_en,
      travel_min: travel,
      arrive: toHHMM(arrive),
      depart: toHHMM(depart),
      crowd_pct: Math.round(crowdDuringVisit(p, arrive, depart)),
    });
    total += cost;
    t = depart;
    prev = id;
  }
  return { legs, total_crowd: Math.round(total * 10) / 10 };
}

function planItinerary(request, data) {
  const ids = request.spot_ids || [];
  if (ids.length < 2 || ids.length > 6) {
    return { ok: false, reason: "スポットは2〜6箇所で指定してください / Choose 2-6 spots" };
  }
  const profilesById = {};
  for (const s of data.profiles.spots) profilesById[s.id] = s;
  for (const id of ids) {
    if (!profilesById[id]) return { ok: false, reason: `未知のスポット: ${id}` };
  }

  const dayType = request.day_type === "holiday" ? "holiday" : "weekday";
  const mIds = data.matrix.spot_ids;
  const mat = data.matrix[dayType];
  const matrixOf = (a, b) => mat[mIds.indexOf(a)][mIds.indexOf(b)];
  const dwellOf = (id) =>
    (request.dwell_min && request.dwell_min[id]) ||
    DEFAULT_DWELL_MIN[profilesById[id].category] || 60;

  // 経路データ対象外のスポット(高尾山・柴又など)を先に検出して明確に伝える
  const unreachable = ids.filter((a) => ids.every((b) => a === b || matrixOf(a, b) < 0));
  if (unreachable.length > 0) {
    return {
      ok: false,
      reason: "経路データ未提供のスポットが含まれています / No route data for some spots",
      unreachable,
    };
  }

  const ctx = { profilesById, matrixOf, dayType, month: request.month, dwellOf };
  const startMin = toMin(request.start_time || "09:00");
  const endMin = toMin(request.end_time || "20:00");

  let best = null;
  for (const order of permutations(ids)) {
    for (const offset of START_OFFSETS) {
      const sim = simulate(order, startMin + offset, endMin, ctx);
      if (sim && (!best || sim.total_crowd < best.total_crowd)) {
        best = { ...sim, start_offset: offset };
      }
    }
  }
  if (!best) {
    return { ok: false, reason: "指定の時間内に収まる行程が見つかりません / No feasible itinerary in the given time window" };
  }

  // ベースライン: 入力順のまま・開始時刻そのままで回った場合(比較のため)
  const baseline = simulate(ids, startMin, endMin, ctx);
  const warnings = [];
  let reduction = null;
  if (baseline && baseline.total_crowd > 0) {
    reduction = Math.round((1 - best.total_crowd / baseline.total_crowd) * 100);
  } else if (!baseline) {
    warnings.push("入力順では時間内に収まらないため、ベースライン比較はありません");
  }

  return {
    ok: true,
    day_type: dayType,
    legs: best.legs,
    total_crowd: best.total_crowd,
    start_offset: best.start_offset,
    baseline: baseline ? { legs: baseline.legs, total_crowd: baseline.total_crowd } : null,
    reduction_pct: reduction,
    warnings,
  };
}

// ブラウザ(グローバル関数)とNode.js(require)の両方で使えるようにする
if (typeof module !== "undefined" && module.exports) {
  module.exports = { planItinerary };
}
