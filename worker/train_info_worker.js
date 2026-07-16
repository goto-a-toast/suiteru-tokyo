/**
 * M6: ODPT運行情報の中継Worker (Cloudflare Workers)
 *
 * 役割:
 *   - ODPTのodpt:TrainInformationを6事業者ぶん取得してマージし、正規化して返す。
 *   - APIキーはWorkerのシークレット(環境変数)に置き、クライアントには一切出さない。
 *   - 60秒キャッシュでODPT側への負荷とレイテンシを抑える。
 *   - このWorkerが落ちてもフロントは黙って機能を無効化する(Stage 1は無傷)設計。
 *
 * エンドポイント:
 *   GET /v1/train-info
 *   → { fetched_at, alerts: [{operator, railway, status:{ja,en}|null, text:{ja,en}|null, date}],
 *       errors: ["Tobu: 403", ...] }   errorsは取得に失敗した事業者(部分的成功を許す)
 *
 * デプロイ手順は worker/README.md を参照。
 */

// どの事業者をどちらのエンドポイントから取るか(センター/チャレンジの提供状況はfetch_odpt_network.py調べと同じ)
const SOURCES = [
  { endpoint: "https://api.odpt.org/api/v4/", tokenVar: "ODPT_TOKEN",
    operators: ["TokyoMetro", "Toei", "TWR"] },
  { endpoint: "https://api-challenge.odpt.org/api/v4/", tokenVar: "ODPT_CHALLENGE_TOKEN",
    operators: ["Tobu", "Keio", "Yurikamome"] },
];

const CACHE_SECONDS = 60;

function corsHeaders() {
  // 読み取り専用の公開データのみを返すのでオリジンは制限しない。
  // (M7でチャットを載せるときはレート制限とオリジン制限を入れる)
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
  };
}

function strip(id, prefix) {
  return typeof id === "string" && id.startsWith(prefix) ? id.slice(prefix.length) : id || null;
}

function normalize(raw) {
  return {
    operator: strip(raw["odpt:operator"], "odpt.Operator:"),
    railway: strip(raw["odpt:railway"], "odpt.Railway:"),
    status: raw["odpt:trainInformationStatus"] || null,
    text: raw["odpt:trainInformationText"] || null,
    date: raw["dc:date"] || null,
  };
}

async function fetchOperator(endpoint, token, op) {
  const url = `${endpoint}odpt:TrainInformation?odpt:operator=odpt.Operator:${op}` +
    `&acl:consumerKey=${encodeURIComponent(token)}`;
  const res = await fetch(url, { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`${op}: HTTP ${res.status}`);
  const items = await res.json();
  return items.map(normalize);
}

async function buildPayload(env) {
  const alerts = [];
  const errors = [];
  const jobs = [];
  for (const src of SOURCES) {
    const token = env[src.tokenVar];
    for (const op of src.operators) {
      if (!token) { errors.push(`${op}: token ${src.tokenVar} not set`); continue; }
      jobs.push(fetchOperator(src.endpoint, token, op).then(
        (items) => alerts.push(...items),
        (e) => errors.push(String(e.message || e)),
      ));
    }
  }
  await Promise.all(jobs);
  return { fetched_at: new Date().toISOString(), alerts, errors };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: corsHeaders() });
    const url = new URL(request.url);
    if (request.method !== "GET" || url.pathname !== "/v1/train-info") {
      return new Response("Not found", { status: 404, headers: corsHeaders() });
    }

    // キャッシュキーはパスのみ(クエリ差でキャッシュが割れないように)
    const cacheKey = new Request(`${url.origin}/v1/train-info`);
    const cache = caches.default;
    const hit = await cache.match(cacheKey);
    if (hit) {
      const res = new Response(hit.body, hit);
      res.headers.set("X-Cache", "HIT");
      return res;
    }

    const payload = await buildPayload(env);
    const res = new Response(JSON.stringify(payload), {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": `public, s-maxage=${CACHE_SECONDS}, max-age=${CACHE_SECONDS}`,
        ...corsHeaders(),
      },
    });
    ctx.waitUntil(cache.put(cacheKey, res.clone()));
    return res;
  },
};
