# すいてる東京 (Suiteru Tokyo)

公共交通オープンデータチャレンジ2026 応募作品(開発中)

> 「いつ行くか」を変えるだけで、東京はすいてる。

訪日外国人観光客に、混雑予報に基づいて行程の時間と順序を組み替えた観光プランを
提案するWebサービス。テーマはオーバーツーリズム対策。

- 設計書: [docs/plan_suiteru.md](docs/plan_suiteru.md)(マイルストーンM0〜M8)
- データ調査記録: [docs/data_survey.md](docs/data_survey.md)
- **応募締切: 2027年1月11日(月・祝) 23:59 JST**(応募期間は2026年10月1日〜)

## 現在の状況

- [x] 計画策定・プロジェクト骨格(2026-07-16)
- [x] M0: データ実在確認 — ハイブリッド方式で確定、両登録完了(2026-07-16)。
      ※人流CSV(メッシュ5339)のDLはM2着手までに
- [x] M1: スポットマスタ(data/spots.csv・22箇所) — 座標は概値。M3の最寄り駅計算で検証する
- [x] M2: 混雑プロファイル(webapp/data/crowd_profiles.json・22箇所) — レベルは人流2019年実データ、
      時間カーブはv0テンプレート(出典裏付けは応募前にv1化する)
- [x] M3: スポット間所要時間行列(webapp/data/travel_matrix.json) — 6社(メトロ・都営・りんかい・
      東武・京王・ゆりかもめ)のダイヤでRAPTOR計算。380/462ペア到達可能。
      代表ペア検証OK(浅草→渋谷45分、スカイツリー→銀座25分、東京駅→豊洲市場34分など)。
      ※JR東日本・京成は時刻表データ未提供(2026-07時点)のため高尾山・柴又は経路対象外、
        ゆりかもめは駅時刻表から列車を復元(reconstruct_yurikamome.py)
- [x] M4: 行程最適化エンジン(webapp/js/plan_itinerary.js・純粋関数) — 混雑ピークを
      べき乗2.5の非線形ペナルティで回避、訪問可能時間帯を考慮、順列×開始時刻ずらし探索。
      単体テスト4件合格(築地は朝に配置され混雑32%削減、など)
- [x] M5: 静的Webフロント(日英) — webapp/index.html。スポット別「すいてる時間」ヒートマップ+
      行程ビルダー。GitHub Actionsで自動デプロイ(https://goto-a-toast.github.io/suiteru-tokyo/)
- **Stage 1(応募可能な最小構成)ここまで完成。以下はStage 2(余力があれば)**
- [x] M6: Worker+運行情報リアルタイムアラート — コード完成・リプレイ検証済み(2026-07-16)。
      worker/にODPT中継Worker(キー秘匿・60秒キャッシュ)、フロントは行程の利用路線に
      異常があれば警告(transit_alerts.js・単体テスト11件合格)。Worker未設定/不達なら
      表示ごと消えるだけでStage 1は無傷。`?demo=alerts`で保存サンプルをリプレイ確認できる。
      ※Cloudflareへの実デプロイは未実施(手順: worker/README.md)
- [x] 行程マップ+路線案内(2026-07-16) — 行程を地図表示(Leaflet同梱・訪問順マーカーを混雑度で
      色分け)。移動区間の乗車路線(例: 銀座線 浅草→渋谷)を travel_routes.json から表示する。
      実データ生成済み(380/462ペア・15路線、travel_matrix.jsonは再生成前と完全一致で再現性確認)。
      再生成手順: APIキーを data/odpt_apikey.txt(+data/odpt_challenge_apikey.txt) に置き、
      `fetch_odpt_network.py → reconstruct_yurikamome.py → build_network_tokyo.py →
      build_travel_matrix.py` の順に実行(ゆりかもめの駅時刻表取得もfetchスクリプトに組込み済み。
      生成ロジックは合成ネットワークの単体テストでも検証: test_travel_routes.py)
- [ ] M7: LLMコンシェルジュ
- [ ] M8: 応募材料の最終化(カーブv1化・デモ動画・応募文面)

## 開発のはじめ方(どの端末からでも)

このリポジトリだけで開発を再開できる。追加のツールはPython 3とNode.js(テスト用)のみ。

```bash
git clone https://github.com/goto-a-toast/suiteru-tokyo.git
cd suiteru-tokyo

# 1. アプリをローカルで動かす(ビルド不要・依存なし)
python3 -m http.server 8000 --directory webapp
# → http://localhost:8000 (運行情報デモは http://localhost:8000/?demo=alerts )

# 2. テストを回す
node precompute/test_plan_itinerary.js      # M4 行程最適化
node precompute/test_transit_alerts.js      # M6 運行情報アラート
python3 precompute/test_travel_routes.py    # 路線案内の生成ロジック
```

- **データ再生成は普段の開発では不要**(生成済みJSONがwebapp/data/にコミット済み)。
  ダイヤ改正時などに再生成する場合のみODPTのAPIキーが要る(手順は上のM3/路線案内の項)
- **APIキーの置き場所**: `data/odpt_apikey.txt` / `data/odpt_challenge_apikey.txt`。
  `data/*` は.gitignore済みなのでコミットされない。キー自体はパスワードマネージャ等で
  端末間共有すること(リポジトリには絶対に入れない)
- **ODPTの生データ(data/odpt/)はコミットしない**。チャレンジ限定データは再配布できないため、
  各端末でAPIキーから取得する方針
- Claude Code(Web版)のセッションはクラウド側にあるため、claude.ai のアカウントがあれば
  ブラウザ・スマホアプリのどれからでも同じセッションの続きができる

## 正直さについて(方針)

本作品の混雑情報は**統計と公表資料に基づく「予報」であり、実測のリアルタイム値ではない**。
リアルタイムなのは運行情報(遅延・運休)のみ。この区別をUI上でも明示する。
