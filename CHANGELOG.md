# CHANGELOG

## v1.1.0（2026-10-13）

- 表紙：EPUB を作る前に、画像生成モデルを選んで表紙を作れるようにした（仕上げの画面、`novelctl cover`、`cli.py engine <slug> cover`）。
  - 選べるもの：画像生成モデル（OpenRouter の一覧、または使わない）、スタイル（シンプル／イラスト／写実）、モチーフ（自由入力）、題名（縦書き／横書き）、書体（明朝／ゴシック／カジュアル）、著者名（自由入力）。
  - 絵は文字なしで描かせ、題名と著者名はこちらで組んで重ねる（ヘッドレス Chrome で 1600×2560 の PNG。Python のパッケージは不要）。
  - 「絵はそのまま、文字だけ組み直す」（費用なし）。
  - 著者名は EPUB の著者（dc:creator）にも入る。費用は役 `cover` として記録。
- 上位モデルの例外処理で、モデルを選べるようにした（`rescue chapter N --model M --note 指示`、`rescue canon --model M`、画面では手直しのたびにモデルと指示を選ぶ）。
  - 指定した上位モデルで直らなかったとき、別のモデルでもう一度直せる。試したモデルと結果（合格／審査で不合格）は章ごとに残り、画面に出る。
  - 最終審査で落ちた章も、最終審査の指摘を修正指示にして上位モデルで直せる（これまでは巻き戻すか、そのまま完成にするしかなかった）。
  - 予備のモデルへの自動切り替えはしない（人が選んだモデルだけを呼ぶ）。
- BRIEF の機械検査に「伏線の詰め込み」の警告を足した（最終章の回収が3件以上、1章あたりの伏線の操作が字数に比べて多い）。BRIEF の依頼文にも同じ目安を入れた。短編で最終章が直しきれなかった実例から。
- 偽モデルに画像生成（/images と /images/models）、最終審査の不合格（MOCK_FAIL_FINAL）、特定モデルの手直しが通らない（MOCK_RESCUE_FAIL_MODEL）を足した。回帰テストに表紙と、別モデルでの手直しを追加。

## v1.0.0（2026-10-13）

[novel-tools](https://github.com/InfraOjisan/novel-tools) から道具だけを分けて公開した最初の版。

- `studio/`：テーマ → 提案（n 案・費用つき）→ 分類（収束／バリエーション／外れ値）→ BRIEF → 確定 → CANON → 執筆 → 校正 → EPUB の Web アプリと、同じ工程の CLI（JSON 入出力）。
- `engine/`：グラウヴァル第6回で使った novelctl を汎用化した執筆エンジン。
  - 上位モデルによる例外処理（`rescue canon` / `rescue chapter N` / `proofread`）、人の指示での再設計（`redesign`）。
  - `status --json`、費用の記録（`logs/usage.jsonl`）、予算の上限（`budget_usd`）。
  - CANON テンプレートに「世界の前提（効く範囲つき）」の節。
  - `scaffold.py`：brief.json から BRIEF.md・config.json・CANON テンプレートを作り、lint する。
  - 回帰テスト（偽モデルで端から端まで）。
- `harness/`：Claude Code 以外のハーネス向けの AGENTS.md・開始プロンプト・道具の一覧・試験 L1〜L4 と採点。
- `studio/devtools/mock_openrouter.py`：費用をかけずに試す偽モデル。
