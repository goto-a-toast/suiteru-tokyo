// M6の単体テスト(Node.jsで実行: node precompute/test_transit_alerts.js)
// 完成条件: 保存レスポンスのリプレイで、行程に関係する路線の異常だけが警告になること

"use strict";
const fs = require("fs");
const path = require("path");
const { isDisruption, matchAlertsToSpots, railwayLabel } =
  require("../webapp/js/transit_alerts.js");

const ROOT = path.join(__dirname, "..");
const profiles = JSON.parse(fs.readFileSync(path.join(ROOT, "webapp/data/crowd_profiles.json"), "utf-8"));
const sample = JSON.parse(fs.readFileSync(path.join(ROOT, "webapp/data/sample_train_info.json"), "utf-8"));
const spotById = {};
for (const s of profiles.spots) spotById[s.id] = s;

let failed = 0;
function check(name, cond, detail) {
  console.log(`${cond ? "OK " : "NG "} ${name}${detail ? " … " + detail : ""}`);
  if (!cond) failed += 1;
}

// --- テスト1: 平常運転は異常扱いしない ---
const tozai = sample.alerts.find((a) => a.railway === "TokyoMetro.Tozai");
check("テスト1: 平常運転(東西線)はisDisruption=false", !isDisruption(tozai));
check("テスト1: 遅延(銀座線)はisDisruption=true",
      isDisruption(sample.alerts.find((a) => a.railway === "TokyoMetro.Ginza")));

// --- テスト2: 銀座線の遅延は浅草寺(浅草=銀座線)に届き、豊洲市場(ゆりかもめ)には届かない ---
const m2 = matchAlertsToSpots(sample.alerts, [spotById.sensoji, spotById.toyosu_market]);
check("テスト2: 異常のみ3件(平常の東西線は除外)", m2.length === 3, `件数=${m2.length}`);
const ginza2 = m2.find((a) => a.railway === "TokyoMetro.Ginza");
check("テスト2: 銀座線遅延→浅草寺に影響", ginza2.affected_spot_ids.includes("sensoji"),
      `affected=${ginza2.affected_spot_ids.join(",")}`);
check("テスト2: 銀座線遅延→豊洲市場は無関係", !ginza2.affected_spot_ids.includes("toyosu_market"));

// --- テスト3: 事業者名だけの表記は粗くマッチする(浅草(東武)→スカイツリーライン遅延) ---
const m3 = matchAlertsToSpots(sample.alerts, [spotById.sensoji]);
const tobu3 = m3.find((a) => a.railway === "Tobu.TobuSkytree");
check("テスト3: 東武の遅延→浅草寺(浅草(東武))に影響", tobu3.affected_spot_ids.includes("sensoji"));

// --- テスト4: 路線名まで書かれた表記は他路線のアラートに反応しない ---
//   築地: 築地(東京メトロ日比谷線);築地市場(都営大江戸線)
//   → 銀座線遅延には反応せず、大江戸線見合わせには反応する
const m4 = matchAlertsToSpots(sample.alerts, [spotById.tsukiji]);
const ginza4 = m4.find((a) => a.railway === "TokyoMetro.Ginza");
const oedo4 = m4.find((a) => a.railway === "Toei.Oedo");
check("テスト4: 銀座線遅延→築地は無関係", !ginza4.affected_spot_ids.includes("tsukiji"));
check("テスト4: 大江戸線見合わせ→築地に影響", oedo4.affected_spot_ids.includes("tsukiji"),
      `affected=${oedo4.affected_spot_ids.join(",")}`);

// --- テスト5: 表示名(既知路線は日英名、未知IDはそのまま読める形) ---
check("テスト5: 銀座線の日本語表示", railwayLabel(ginza4, "ja") === "銀座線");
check("テスト5: 銀座線の英語表示", railwayLabel(ginza4, "en") === "Ginza Line");
check("テスト5: 未知路線IDはIDのまま",
      railwayLabel({ operator: "Tobu", railway: "Tobu.Ogose" }, "ja") === "Tobu.Ogose");

console.log(failed === 0 ? "\n全テスト合格" : `\n${failed}件失敗`);
process.exit(failed === 0 ? 0 : 1);
