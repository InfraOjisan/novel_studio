# issue の草稿

`issues.json` が元データです。`python3 scripts/create_issues.py`（確認のみ）→ `--apply`（作成）で GitHub に起票します。

| # | タイトル | ラベル |
|---|---|---|
| 1 | 本物のモデルで studio を端から端まで一本通す | studio, engine, v1.1 |
| 2 | 上位モデル（premium）の OpenRouter のモデル名を確かめる | engine, studio, bug |
| 3 | 上位モデルの役を agy（定額枠）にもつなげる：枠と従量 API の配車 | engine, v1.1 |
| 4 | 事実の表と章カードを分ける（同じ事実を二か所に書かない構造） | engine, v1.1 |
| 5 | 差分審査：直った指摘は再審査しない（収束が保証されるループ） | engine, v1.1 |
| 6 | 台詞の指紋（キャラ崩れ検知） | engine, v1.1 |
| 7 | LEDGER を本文から再生成してから最終審査する | engine |
| 8 | ルールの適用範囲（スコープ）タグ | engine |
| 9 | 数値の照合を機械で（金額・里程・刻・人数の合計） | engine |
| 10 | 設計の二段化（骨組み → 章カード） | engine |
| 11 | 学習するテンプレート：止まるたびに足した前提を次作の CANON ひな形へ | engine, v1.1 |
| 12 | engine をパッケージ化する（作品ごとに tools を複製しない） | engine |
| 13 | 「指摘を消す修正」と「問題を解く修正」の判別 | engine, experiment |
| 14 | 審査と校正に「想定読者」の軸を入れる（ペルソナ別採点） | engine, studio |
| 15 | ミステリ：引きと真相を分け、人は引きだけで選ぶ（設計者が自分で騙される） | studio, experiment |
| 16 | 骨格台帳を作品をまたいで持つ | studio |
| 17 | ジャンル規範リンター（ノックスの十戒・ハードSFの考証） | studio, engine |
| 18 | 天才枠：投票や分類に左右されない暴走案を一つ通す | studio, experiment |
| 19 | 提案の相互採点と甘辛補正を studio に取り込む | studio |
| 20 | 1つの CANON から読者層別に3版を書く（書き分けの検証） | engine, experiment |
| 21 | studio を自分の Mac 以外で使うときの認証 | studio |
| 22 | studio から公開（なろう・Romancer）へつなぐ | studio, publish |
| 23 | Hermes Agent で試験 L1・L2 を回す | harness, experiment |
| 24 | agy（Opus 5.5）で試験 L3B・L4 を回す | harness, experiment |
| 25 | omp（安いモデル）で試験 L1・L2 を回す | harness, experiment |
| 26 | 試験 L5：20章の長編を人の介入最小で完走 | harness |
| 27 | CLI を MCP で包む汎用ラッパー | harness, idea |
| 28 | グラウヴァルを v1.0 エンジンへ移す | series, engine |
| 29 | ミステリのローリング版コンペ | experiment, studio |
| 30 | 上位モデル依存度の診断（rescues を KPI に） | idea |
| 31 | 企画の分類を社内の未来計画に（model router につなぐ） | idea, studio |
| 32 | 外れ値の予言台帳 | idea, studio |
