// M4の単体テスト(Node.jsで実行: node precompute/test_plan_itinerary.js)
// 計画書の完成条件: 「浅草+渋谷+築地の1日プランで築地が朝に来る」こと

"use strict";
const fs = require("fs");
const path = require("path");
const { planItinerary } = require("../webapp/js/plan_itinerary.js");

const ROOT = path.join(__dirname, "..");
const data = {
  profiles: JSON.parse(fs.readFileSync(path.join(ROOT, "webapp/data/crowd_profiles.json"), "utf-8")),
  matrix: JSON.parse(fs.readFileSync(path.join(ROOT, "webapp/data/travel_matrix.json"), "utf-8")),
};

let failed = 0;
function check(name, cond, detail) {
  console.log(`${cond ? "OK " : "NG "} ${name}${detail ? " … " + detail : ""}`);
  if (!cond) failed += 1;
}

// --- テスト1: 築地は朝に来る(市場の訪問可能時間帯と混雑回避の両方から) ---
const r1 = planItinerary(
  { spot_ids: ["sensoji", "shibuya_crossing", "tsukiji"], day_type: "weekday",
    start_time: "08:00", end_time: "20:00", month: 7 },
  data
);
check("テスト1: 計算成功", r1.ok, r1.reason);
if (r1.ok) {
  const order = r1.legs.map((l) => l.spot_id);
  const tsukijiLeg = r1.legs.find((l) => l.spot_id === "tsukiji");
  check("テスト1: 築地が最初", order[0] === "tsukiji", `順序=${order.join("→")}`);
  check("テスト1: 築地到着が午前", parseInt(tsukijiLeg.arrive) < 12,
        `到着=${tsukijiLeg.arrive}`);
  check("テスト1: ベースラインより混雑減", r1.reduction_pct > 0,
        `削減率=${r1.reduction_pct}%`);
  console.log("  行程:", r1.legs.map((l) => `${l.name_ja}(${l.arrive}-${l.depart}, 混雑${l.crowd_pct})`).join(" → "));
}

// --- テスト2: 経路対象外スポット(高尾山)は明確なエラーになる ---
const r2 = planItinerary(
  { spot_ids: ["sensoji", "takaosan"], day_type: "weekday" }, data
);
check("テスト2: 高尾山は到達不能と報告", !r2.ok && r2.unreachable && r2.unreachable.includes("takaosan"),
      JSON.stringify(r2.unreachable));

// --- テスト3: 展望施設(スカイツリー)は夕方ピークを避けて早い時間に置かれる ---
const r3 = planItinerary(
  { spot_ids: ["skytree", "ueno_park", "ginza"], day_type: "holiday",
    start_time: "09:00", end_time: "21:00", month: 7 },
  data
);
check("テスト3: 計算成功", r3.ok, r3.reason);
if (r3.ok) {
  const sky = r3.legs.find((l) => l.spot_id === "skytree");
  check("テスト3: スカイツリーは18時前に滞在", parseInt(sky.depart) <= 18, `滞在=${sky.arrive}-${sky.depart}`);
  console.log("  行程:", r3.legs.map((l) => `${l.name_ja}(${l.arrive}-${l.depart}, 混雑${l.crowd_pct})`).join(" → "));
}

// --- テスト4: 時間が足りない場合は正直に失敗する ---
const r4 = planItinerary(
  { spot_ids: ["sensoji", "shibuya_crossing", "tsukiji", "odaiba", "ueno_park", "ginza"],
    day_type: "weekday", start_time: "17:00", end_time: "19:00" },
  data
);
check("テスト4: 不可能な行程は失敗を返す", !r4.ok, r4.reason);

process.exit(failed > 0 ? 1 : 0);
