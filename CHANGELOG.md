# CHANGELOG

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
