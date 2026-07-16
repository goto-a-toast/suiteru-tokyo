# worker/ — 運行情報中継Worker (M6)

ODPTの運行情報(`odpt:TrainInformation`)を6事業者ぶんまとめて返すCloudflare Worker。
APIキーを隠すこととキャッシュ(60秒)が目的。**このWorkerが無くても・落ちても、
webapp本体(Stage 1)は運行情報表示が消えるだけで全機能が動く。**

## デプロイ手順(無料枠でOK)

```bash
cd worker
npm install -g wrangler          # 初回のみ
wrangler login                   # ブラウザでCloudflareにログイン
wrangler secret put ODPT_TOKEN             # センター(api.odpt.org)のトークンを貼る
wrangler secret put ODPT_CHALLENGE_TOKEN   # チャレンジ2026トークンを貼る(無ければスキップ可)
wrangler deploy
```

デプロイすると `https://suiteru-train-info.<アカウント名>.workers.dev` が発行される。

## フロントへの接続

`webapp/index.html` 冒頭の設定を書き換える:

```js
const TRAIN_INFO_URL = "https://suiteru-train-info.<アカウント名>.workers.dev/v1/train-info";
```

デプロイ前でも次の方法で動作確認できる:

- `?worker=<WorkerのURL>/v1/train-info` … URLパラメータで一時的に接続先を指定
- `?demo=alerts` … 保存済みサンプル(`webapp/data/sample_train_info.json`)をリプレイ表示。
  Workerなしでアラート表示を確認できる(M6の完成条件の検証用)

## 応答の形

```json
{
  "fetched_at": "2026-07-16T09:00:00.000Z",
  "alerts": [
    { "operator": "TokyoMetro", "railway": "TokyoMetro.Ginza",
      "status": {"ja": "遅延", "en": "Delay"},
      "text": {"ja": "…", "en": "…"}, "date": "…" }
  ],
  "errors": ["Yurikamome: HTTP 404"]
}
```

- 平常運転の項目も含めてそのまま返す(絞り込みはクライアント側 `transit_alerts.js`)
- `errors` は取得に失敗した事業者。部分的に取れていれば `alerts` は返す
- 運行情報を提供していない事業者(例: ゆりかもめは2026-07時点で未確認)は errors に出るだけで害はない
