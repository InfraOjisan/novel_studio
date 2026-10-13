# TOOLS.md — ハーネスに渡す道具の一覧

novel_studio v1.0 の道具は、すべて「JSON を入れて JSON を出す」CLI です。ハーネスの種類に依存しません。
ハーネスの権限設定（許可／確認／禁止）は、下の「権限」の列をそのまま写してください。

## 1. 道具

### studio/cli.py（企画と執筆の窓口。リポジトリ直下で実行）

| コマンド | 入力 | 出力（JSON の主な鍵） | 副作用 | モデル呼び出し | 権限 |
|---|---|---|---|---|---|
| `works` | — | `works[]` | なし | なし | 許可 |
| `new <slug> --theme F` | theme.json | 状態の要約 | `works/<slug>/` を作る | なし | 許可 |
| `show <slug> [--brief]` | — | `stage` `proposals` `classify` `briefs` `fixed` `costs` | なし | なし | 許可 |
| `theme <slug> --theme F` | theme.json | 状態の要約 | 提案・分類・BRIEF を history に退避して最初から | なし | **確認** |
| `propose <slug> --models a,b --n 2 [--mode new/resubmit/rolling] [--instruction T]` | — | `round` `proposals[]` `errors{}` | 案を足す | 各モデル1〜2回 | 許可 |
| `edit <slug> <pid> --fields F` | 欄の JSON | `pid`（新しい案） | 案を足す | なし | 確認 |
| `revise <slug> <pid> --model m --instruction T` | — | `pid` | 案を足す | 1〜2回 | 許可 |
| `classify <slug> [--method tool/ai/both] [--model m]` | — | `labels` `clusters` `mean_similarity` `top_pairs` | 分類を保存 | 1〜2回（骨格の抽出） | 許可 |
| `reclassify <slug> --threshold x` | — | `labels` `clusters` | 分類を保存 | なし | 許可 |
| `select <slug> <pid>` | — | `selected` | 選択を保存 | なし | 許可 |
| `brief <slug> [--model m] [--instruction T] [--base V]` | — | `v` | BRIEF の版を足す | 1〜2回 | 許可 |
| `brief-put <slug> <V> --file F` | brief.json | `v` `lint` | BRIEF の版を足す（自分で書いた BRIEF） | なし | 確認（L3-B のみ） |
| `brief-show <slug> <V> [--markdown]` | — | `lint` `markdown` | なし（終了コード2＝lint 指摘あり） | なし | 許可 |
| `fix <slug> <V> [--roles F] [--budget N]` | roles.json | `fixed` | 作品フォルダに engine を展開。以後 BRIEF は変えられない | なし | **確認** |
| `engine <slug> start` | — | `status` | 執筆エンジンを切り離して起動 | 多数（安いモデル） | 許可 |
| `engine <slug> step` | — | `status` | 1段だけ進める（同期） | 1〜2回 | 許可 |
| `engine <slug> redesign --feedback T` | — | `status` | 人の指示で CANON を再設計 | — | **確認** |
| `engine <slug> rescue_canon` | — | `status` | 上位モデルが CANON 全体を一度だけ直す | 1回（上位・高い） | **確認** |
| `engine <slug> rescue_chapter --ch N` | — | `status` | 上位モデルが第N章の指摘箇所を直す | 1回（上位・高い） | **確認** |
| `engine <slug> resolve --flags retry/accept/done/ledger-done/relock` | — | `status` | 止まりを解く | なし | **確認**（accept/done/relock は人だけ） |
| `engine <slug> reset_chapter --ch N` | — | `status` | 第N章以降を書き直しに戻す | なし | 禁止（人だけ） |
| `engine <slug> polish` | — | `status` | 文体の磨き（任意） | 章の数 | 確認 |
| `engine <slug> proofread [--chapters 1,2]` | — | `status` | 上位モデルの校正案を `output/proofread_edits.json` に | 章の数（上位） | **確認** |
| `engine <slug> apply_proofread` | — | 出力 | 採った校正案を当てて校正版を作る | なし | 確認 |
| `engine <slug> cover [--model M] [--style S] [--motif T] [--title-dir D] [--font F] [--author A] [--reuse-art]` | — | 出力 | 表紙を作る（images/cover.png・cover.json） | 1回（画像生成。`--reuse-art` と `--model none` はなし） | **確認** |
| `image-models` | — | `models[]` | なし | なし | 許可 |
| `engine <slug> epub` | — | 出力 | 縦書き EPUB | なし | 許可 |
| `status <slug>` | — | `phase` `current` `chapters[]` `slots` `escalation` `next` `usage` `running` | なし | なし | 許可 |
| `costs <slug>` | — | `studio{}` `engine{}` `total_usd` | なし | なし | 許可 |
| `approve` | — | 常に拒否 | — | — | — |

### 作品フォルダの中（works/<slug>/）

| コマンド | 用途 | 権限 |
|---|---|---|
| `python3 tools/novelctl.py status --json` | studio/cli.py の `status` と同じ | 許可 |
| `python3 tools/call_llm.py --ping writer\|reviewer\|premium` | 疎通の確認（数十トークン） | 許可 |
| `python3 tools/guard.py check` | 見張り番：STOP・保護ファイル・越えてはならない線・引き写し | 許可 |
| `python3 tools/novelctl.py approve-canon` | CANON の承認 | **禁止**（人が端末で） |
| `python3 tools/guard.py seal` | 保護ファイルの指紋を取り直す | **禁止**（人だけ） |

### 検査・試験

| コマンド | 用途 | 権限 |
|---|---|---|
| `python3 engine/tools/scaffold.py lint brief.json` | BRIEF の機械検査（JSON） | 許可 |
| `python3 -m unittest discover -s engine/tests` | 回帰テスト（偽モデルで端から端まで。費用なし） | 許可 |
| `python3 studio/devtools/mock_openrouter.py --port 8765` | 偽モデル（試験の前に人が起動） | 人が起動 |
| `python3 harness/exams/check.py <level> <sandbox>` | 試験の採点 | 人が実行 |

## 2. 権限設定の例

### OpenCode 系（omp など、opencode.json の形の設定を読むもの）

```json
{
  "permission": {
    "edit": "deny",
    "bash": {
      "*": "ask",
      "python3 studio/cli.py works*": "allow",
      "python3 studio/cli.py show *": "allow",
      "python3 studio/cli.py new *": "allow",
      "python3 studio/cli.py propose *": "allow",
      "python3 studio/cli.py revise *": "allow",
      "python3 studio/cli.py classify *": "allow",
      "python3 studio/cli.py reclassify *": "allow",
      "python3 studio/cli.py select *": "allow",
      "python3 studio/cli.py brief *": "allow",
      "python3 studio/cli.py brief-show *": "allow",
      "python3 studio/cli.py status *": "allow",
      "python3 studio/cli.py costs *": "allow",
      "python3 studio/cli.py engine * start": "allow",
      "python3 studio/cli.py engine * step": "allow",
      "python3 studio/cli.py engine * epub": "allow",
      "cat works/*/notes/*": "allow",
      "cat works/*/work/*_feedback.md": "allow",
      "cat works/*/output/final_report.md": "allow",
      "tail *": "allow",
      "*approve-canon*": "deny",
      "*guard.py seal*": "deny",
      "*.env*": "deny"
    }
  }
}
```

`notes/REPORT.md` と `notes/INTERVENTIONS.md` への追記は、`edit: deny` のままだと書けません。ハーネスがファイル編集の許可を道ごとに分けられるなら `works/*/notes/**` だけ許可、分けられないなら `ask` にしてください。

### Hermes Agent・agy

細かい許可表を持たないハーネスでは、AGENTS.md 第0節と第4節が唯一の歯止めになります。代わりに、道具の側に機械の歯止めがあります。
- `approve-canon` は端末でなければ拒否し、試みたことを `logs/run.log` に残す。
- 保護ファイル（BRIEF・config・tools・prompts）の指紋を毎段で確かめ、変わっていれば止まる（GUARD_STOP）。
- `budget_usd` を超えると止まる（BUDGET_EXCEEDED）。
- 上位モデルの呼び出しは `logs/usage.jsonl` に役 `premium` として残るので、許可なく呼んだかどうかを後から数えられる。

## 3. 入力ファイルの形

theme.json
```json
{"genre": "SF", "theme": "氷の衛星で記録を残し続ける人", "chapters": 8, "chapter_target": 3000,
 "audience": "一般読者", "ending": "ビターエンド", "must": "", "must_not": "AI を全能の神として描かない", "mood": ""}
```

roles.json（役ごとのモデル。文字列か {"model": ..., "fallback_models": [...]}）
```json
{"writer": "anthropic/claude-haiku-5.5", "reviewer": "openai/gpt-6-luna-pro",
 "polisher": "anthropic/claude-haiku-5.5", "premium": "anthropic/claude-opus-5.5"}
```

brief.json の形は `engine/tools/scaffold.py` の冒頭に書いてあります。
