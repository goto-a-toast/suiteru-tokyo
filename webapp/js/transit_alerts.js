/**
 * M6: 運行情報アラート(純粋関数)
 *
 * Workerが返す正規化済みの運行情報(またはリプレイ用の保存サンプル)を受け取り、
 *   1. 「平常運転」を除いた異常情報だけに絞る (isDisruption)
 *   2. スポットの最寄り駅路線と突き合わせる (matchAlertsToSpots)
 * DOM・fetchに依存しない。plan_itinerary.jsと同じくブラウザ/Node両対応。
 *
 * 正規化済みアラートの形:
 *   { operator: "TokyoMetro",              // odpt.Operator: プレフィクスを除いたID
 *     railway: "TokyoMetro.Ginza" | null,  // odpt.Railway: プレフィクスを除いたID
 *     status: {ja, en} | null,             // 平常時はnullの事業者が多い
 *     text:   {ja, en} | null,
 *     date:   "2026-07-16T09:00:00+09:00" }
 *
 * マッチングの方針(粗さは意図的):
 *   - 最寄り駅の表記「浅草(東京メトロ銀座線)」のように路線名まで書かれていれば路線で照合。
 *   - 「浅草(東武)」のように事業者名しか書かれていない駅は、その事業者の全アラートに反応する
 *     (取りこぼしより過剰通知を選ぶ。予報ではなくリアルタイム警告なので安全側に倒す)。
 */

"use strict";

// 事業者ID → 最寄り駅表記に現れる日本語名
const OPERATOR_JA = {
  TokyoMetro: "東京メトロ",
  Toei: "都営",
  TWR: "りんかい線",
  Tobu: "東武",
  Keio: "京王",
  Yurikamome: "ゆりかもめ",
};

// 路線ID(事業者プレフィクス込み) → 表示名。最寄り駅表記との照合にも使う
const RAILWAY_NAMES = {
  "TokyoMetro.Ginza":      { ja: "銀座線",   en: "Ginza Line" },
  "TokyoMetro.Marunouchi": { ja: "丸ノ内線", en: "Marunouchi Line" },
  "TokyoMetro.Hibiya":     { ja: "日比谷線", en: "Hibiya Line" },
  "TokyoMetro.Tozai":      { ja: "東西線",   en: "Tozai Line" },
  "TokyoMetro.Chiyoda":    { ja: "千代田線", en: "Chiyoda Line" },
  "TokyoMetro.Yurakucho":  { ja: "有楽町線", en: "Yurakucho Line" },
  "TokyoMetro.Hanzomon":   { ja: "半蔵門線", en: "Hanzomon Line" },
  "TokyoMetro.Namboku":    { ja: "南北線",   en: "Namboku Line" },
  "TokyoMetro.Fukutoshin": { ja: "副都心線", en: "Fukutoshin Line" },
  "Toei.Asakusa":          { ja: "浅草線",   en: "Toei Asakusa Line" },
  "Toei.Mita":             { ja: "三田線",   en: "Toei Mita Line" },
  "Toei.Shinjuku":         { ja: "新宿線",   en: "Toei Shinjuku Line" },
  "Toei.Oedo":             { ja: "大江戸線", en: "Toei Oedo Line" },
  "Toei.Arakawa":          { ja: "荒川線",   en: "Toden Arakawa Line" },
  "Toei.NipporiToneri":    { ja: "日暮里・舎人ライナー", en: "Nippori-Toneri Liner" },
  "TWR.Rinkai":            { ja: "りんかい線", en: "Rinkai Line" },
  "Yurikamome.Yurikamome": { ja: "ゆりかもめ", en: "Yurikamome" },
  "Tobu.TobuSkytree":      { ja: "東武スカイツリーライン", en: "Tobu Skytree Line" },
  "Tobu.Isesaki":          { ja: "伊勢崎線", en: "Tobu Isesaki Line" },
  "Tobu.Kameido":          { ja: "亀戸線",   en: "Tobu Kameido Line" },
  "Tobu.Daishi":           { ja: "大師線",   en: "Tobu Daishi Line" },
  "Tobu.Tojo":             { ja: "東上線",   en: "Tobu Tojo Line" },
  "Tobu.Nikko":            { ja: "日光線",   en: "Tobu Nikko Line" },
  "Keio.Keio":             { ja: "京王線",   en: "Keio Line" },
  "Keio.New":              { ja: "京王新線", en: "Keio New Line" },
  "Keio.Inokashira":       { ja: "井の頭線", en: "Inokashira Line" },
  "Keio.Takao":            { ja: "高尾線",   en: "Keio Takao Line" },
  "Keio.Sagamihara":       { ja: "相模原線", en: "Keio Sagamihara Line" },
};

/** アラートの表示名(未知の路線IDでも読める形にフォールバック) */
function railwayLabel(alert, lang) {
  if (alert.railway && RAILWAY_NAMES[alert.railway]) {
    return RAILWAY_NAMES[alert.railway][lang === "ja" ? "ja" : "en"];
  }
  if (alert.railway) return alert.railway; // 例: "Tobu.Ogose" のようにIDのまま
  const op = OPERATOR_JA[alert.operator];
  return lang === "ja" && op ? op : alert.operator;
}

/** 平常運転の情報を除く。statusが無い事業者は本文の「平常」で判定する */
function isDisruption(alert) {
  const statusJa = alert.status && alert.status.ja;
  const textJa = alert.text && alert.text.ja;
  if (statusJa) return !statusJa.includes("平常");
  if (textJa) return !textJa.includes("平常");
  return false; // status・本文とも無い情報は「平常」とみなす(誤報を出さない)
}

/** その事業者の路線名のどれかが駅表記に含まれるか(=路線まで特定された表記か) */
function mentionsAnyLineOf(stationText, operator) {
  for (const [rid, names] of Object.entries(RAILWAY_NAMES)) {
    if (rid.startsWith(operator + ".") && stationText.includes(names.ja)) return true;
  }
  return false;
}

/** 1つのアラートが1つのスポットに関係するか */
function alertAffectsSpot(alert, spot) {
  const stations = spot.nearest_stations || [];
  const lineJa = alert.railway && RAILWAY_NAMES[alert.railway]
    ? RAILWAY_NAMES[alert.railway].ja : null;
  const opJa = OPERATOR_JA[alert.operator];
  for (const st of stations) {
    if (lineJa && st.includes(lineJa)) return true;           // 路線名で一致
    if (opJa && st.includes(opJa) && !mentionsAnyLineOf(st, alert.operator)) {
      return true;                                            // 事業者のみの表記→粗く一致
    }
  }
  return false;
}

/**
 * 運行情報一覧をスポット一覧と突き合わせる。
 * 戻り値: 異常情報のみの配列。各要素に affected_spot_ids を付与。
 */
function matchAlertsToSpots(alerts, spots) {
  const out = [];
  for (const a of alerts || []) {
    if (!isDisruption(a)) continue;
    const affected = (spots || []).filter((s) => alertAffectsSpot(a, s)).map((s) => s.id);
    out.push({ ...a, affected_spot_ids: affected });
  }
  return out;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { isDisruption, matchAlertsToSpots, railwayLabel, RAILWAY_NAMES, OPERATOR_JA };
}
