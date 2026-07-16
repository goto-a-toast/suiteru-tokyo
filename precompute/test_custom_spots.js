// カスタムスポット(ユーザー追加)の単体テスト
// 実行: node precompute/test_custom_spots.js

"use strict";
const fs = require("fs");
const path = require("path");
const { buildExtendedData, makeCustomProfile, latlonToMesh1km } = require("../webapp/js/custom_spots.js");
const { planItinerary } = require("../webapp/js/plan_itinerary.js");

const ROOT = path.join(__dirname, "..");
const profiles = JSON.parse(fs.readFileSync(path.join(ROOT, "webapp/data/crowd_profiles.json"), "utf-8"));
const matrix = JSON.parse(fs.readFileSync(path.join(ROOT, "webapp/data/travel_matrix.json"), "utf-8"));

let failed = 0;
function check(name, cond, detail) {
  console.log(`${cond ? "OK " : "NG "} ${name}${detail !== undefined ? " … " + detail : ""}`);
  if (!cond) failed += 1;
}

// 合羽橋道具街: 浅草寺から徒歩圏(約700m)の実在スポットを想定
const kappabashi = { id: "custom_1", name: "合羽橋道具街", category: "商店街", lat: 35.7139, lon: 139.7885 };
// 孤立地: どの公式スポットからも遠い(奥多摩方面)
const isolated = { id: "custom_2", name: "孤立テスト", category: "自然", lat: 35.80, lon: 139.10 };

// --- テスト1: プロファイルがテンプレートから正しく作られる ---
const p1 = makeCustomProfile(kappabashi);
check("テスト1: 商店街→shopping_afternoonの窓[10,20]", p1.visit_window[0] === 10 && p1.visit_window[1] === 20);
check("テスト1: hourly_shapeは24要素・ピーク100", p1.hourly_shape.length === 24 && Math.max(...p1.hourly_shape) === 100);
check("テスト1: customフラグ", p1.custom === true);

// --- テスト2: 行列の合成 ---
const ext = buildExtendedData(profiles, matrix, [kappabashi, isolated]);
const ids = ext.matrix.spot_ids;
const iK = ids.indexOf("custom_1"), iSen = ids.indexOf("sensoji"), iShib = ids.indexOf("shibuya_crossing");
const wk = ext.matrix.weekday;
check("テスト2: 公式ペアは元の値のまま", wk[iSen][iShib] === matrix.weekday[matrix.spot_ids.indexOf("sensoji")][matrix.spot_ids.indexOf("shibuya_crossing")]);
check("テスト2: 合羽橋→浅草寺は直接徒歩(15分以内)", wk[iK][iSen] > 0 && wk[iK][iSen] <= 15, `${wk[iK][iSen]}分`);
const viaBest = matrix.weekday[matrix.spot_ids.indexOf("sensoji")][matrix.spot_ids.indexOf("shibuya_crossing")];
check("テスト2: 合羽橋→渋谷は概算(実測45分+徒歩の範囲)", wk[iK][iShib] >= viaBest && wk[iK][iShib] <= viaBest + 45, `${wk[iK][iShib]}分`);
const iIso = ids.indexOf("custom_2");
check("テスト2: 孤立地はどこへも-1", wk[iIso].every((v) => v === -1));

// --- テスト3: そのままplanItineraryに渡せる ---
const r = planItinerary(
  { spot_ids: ["custom_1", "sensoji", "shibuya_crossing"], day_type: "weekday",
    start_time: "09:00", end_time: "20:00", month: 7 },
  { profiles: ext.profiles, matrix: ext.matrix }
);
check("テスト3: カスタム込みで計算成功", r.ok, r.reason);
if (r.ok) {
  const leg = r.legs.find((l) => l.spot_id === "custom_1");
  check("テスト3: 合羽橋の滞在が10時以降(訪問可能時間帯を尊重)", parseInt(leg.arrive) >= 10, leg.arrive);
  console.log("  行程:", r.legs.map((l) => `${l.name_ja}(${l.arrive}-${l.depart})`).join(" → "));
}

// --- テスト4: カスタム同士(合羽橋⇄孤立地は-1、近接カスタム同士は徒歩) ---
const near = { id: "custom_3", name: "近接テスト", category: "寺社", lat: 35.7150, lon: 139.7900 };
const ext2 = buildExtendedData(profiles, matrix, [kappabashi, near]);
const ids2 = ext2.matrix.spot_ids;
const v = ext2.matrix.weekday[ids2.indexOf("custom_1")][ids2.indexOf("custom_3")];
check("テスト4: 近接カスタム同士は徒歩数分", v > 0 && v <= 10, `${v}分`);

// --- テスト5: メッシュコード計算がPython実装(公式22箇所の既知値)と一致 ---
let meshOk = 0;
for (const s of profiles.spots) {
  if (latlonToMesh1km(s.lat, s.lon) === s.mesh1kmid) meshOk += 1;
}
check("テスト5: 22箇所全部のメッシュコード一致", meshOk === profiles.spots.length, `${meshOk}/22`);

// --- テスト6: メッシュ実レベルの適用 ---
const kMesh = latlonToMesh1km(kappabashi.lat, kappabashi.lon);
const p6 = makeCustomProfile(kappabashi, { [kMesh]: [72, 81] });
check("テスト6: 人流実データのレベルが入る", p6.level.weekday === 72 && p6.level.holiday === 81);
check("テスト6: level_source=mesh", p6.level_source === "mesh");
check("テスト6: データ無し地点はdefault(50)",
      makeCustomProfile(isolated, { [kMesh]: [72, 81] }).level_source === "default");

// --- テスト7: 駅テーブルによる実ダイヤ由来の所要時間+乗車路線 ---
// 浅草駅・渋谷駅の2駅だけの合成テーブル。合羽橋→浅草駅は徒歩約13分
const stAsakusa = 0, stShibuya = 1;
const nSpots = matrix.spot_ids.length;
const iShibOff = matrix.spot_ids.indexOf("shibuya_crossing");
const mkRows = () => Array.from({ length: nSpots }, () => [0, 0]);
const tables = {
  stations: [["浅草", "Asakusa", 35.7118, 139.7966], ["渋谷", "Shibuya", 35.6580, 139.7016]],
  chains: [[["TokyoMetro.Ginza", stAsakusa, stShibuya]]],
  spots: matrix.spot_ids,
  weekday: { from: mkRows(), to: mkRows() },
  holiday: { from: mkRows(), to: mkRows() },
};
for (const cal of ["weekday", "holiday"]) {
  tables[cal].from[iShibOff] = [[45, 0], [5, 0]];  // 渋谷スポット→浅草駅45分/渋谷駅5分
  tables[cal].to[iShibOff] = [[44, 0], [5, 0]];    // 浅草駅→渋谷スポット44分/渋谷駅→5分
}
const ext7 = buildExtendedData(profiles, matrix, [kappabashi, isolated], { stationTables: tables });
const ids7 = ext7.matrix.spot_ids;
const iK7 = ids7.indexOf("custom_1"), iShib7 = ids7.indexOf("shibuya_crossing");
const cToX = ext7.matrix.weekday[iK7][iShib7];
const xToC = ext7.matrix.weekday[iShib7][iK7];
check("テスト7: 合羽橋→渋谷=徒歩13+テーブル44≒57分", cToX >= 55 && cToX <= 59, `${cToX}分`);
check("テスト7: 渋谷→合羽橋=テーブル45+徒歩13≒58分", xToC >= 56 && xToC <= 60, `${xToC}分`);
const ch7 = ext7.customRoutes["custom_1"] && ext7.customRoutes["custom_1"]["shibuya_crossing"];
check("テスト7: 乗車路線チェーンあり(銀座線 浅草→渋谷)",
      !!ch7 && ch7.weekday[0].line === "TokyoMetro.Ginza" && ch7.weekday[0].from[0] === "浅草",
      ch7 && JSON.stringify(ch7.weekday));
check("テスト7: テーブル経由ペアは概算フラグなし",
      !(ext7.approx["custom_1"] && ext7.approx["custom_1"]["shibuya_crossing"]));
check("テスト7: カスタム同士は概算フラグあり",
      !!(ext7.approx["custom_1"] && ext7.approx["custom_1"]["custom_2"]) ||
      ext7.matrix.weekday[iK7][ids7.indexOf("custom_2")] === -1);

console.log(failed === 0 ? "\n全テスト合格" : `\n${failed}件失敗`);
process.exit(failed === 0 ? 0 : 1);
