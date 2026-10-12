# AGENTS.md — 進行役の手引き（novel_studio v1.0 の作品フォルダ）

このフォルダは `engine/tools/scaffold.py`（または studio の「Fix」）が `brief.json` から作った作品フォルダです。
小説を書くのはモデルで、役は `config.json` の `llm.profiles` で分かれています。

| 役 | 既定のモデル | 受け持つ工程 |
|---|---|---|
| writer（書き手） | Claude Haiku 5.5 | CANON の設計、各章の本文、書き直し |
| reviewer（審査役） | GPT-6 Luna Pro | CANON の審査、各章の審査、章の記録（LEDGER）、最終審査 |
| polisher（磨き） | Claude Haiku 5.5 | 完成稿の文体仕上げ（任意） |
| premium（上位モデル） | Claude Opus 5.5 | **例外処理だけ**：CANON の全体修正、通らない章の手直し、全文校正 |

進行役（人、または HermesAgent / agy / omp などのエージェント）は、`tools/novelctl.py` を動かして、止まったら人に知らせるだけです。
モデルが読むのはパケット（`work/*.packet.md`）だけで、この AGENTS.md はモデルには渡りません。

---

## 0. 最上位の約束（進行役が人でもエージェントでも同じ）

1. **状態はディスクにしかない。** 自分の記憶を信用しない。迷ったら `python3 tools/novelctl.py status --json` を見て、`next` に従う。
2. **このフォルダの外に出ない。** 例外は、リポジトリ直下の `.env` を `tools/call_llm.py` が読むことだけ（進行役は `.env` を開かない・表示しない・写さない）。
3. **モデルの出力を手で直さない。** `canon/`・`chapters/`・`state/`・`work/` を書き換えない。受け箱（`work/inbox/`）に答えを置かない。自分で本文・設定・審査結果を書かない。
4. **書き換えてはいけないもの。** `AGENTS.md`・`BRIEF.md`・`brief.json`・`config.json`・`tools/`・`prompts/`・`templates/`。指紋（`guard/manifest.json`）で見張られていて、変わると novelctl が止まります。
5. **人の承認は人がする。** `approve-canon` は人が端末で（または studio のボタンで）行う。`resolve --accept`・`resolve --relock`・`resolve --done`・`reset-chapter`・`tools/guard.py seal` も人の判断で。
6. **STOP ファイルを見たら止まる。**
7. **介入は記録する。** `notes/INTERVENTIONS.md` に一行ずつ（日時・何をした・なぜ）。
8. **章の本文を丸ごと自分の文脈に読み込まない。** 読んでよいのは `status`、`work/*_feedback.md`、`work/*_verdict_*.json`、`output/final_report.md`、`logs/run.log` の末尾。

---

## 1. 動かし方

```
python3 tools/call_llm.py --ping writer      # 疎通（書き手）
python3 tools/call_llm.py --ping reviewer    # 疎通（審査役）
python3 tools/call_llm.py --ping premium     # 疎通（上位モデル。例外処理を使うときだけ）
python3 tools/novelctl.py status --json
python3 tools/novelctl.py start              # 設計 → CANON審査 → CANONロックで止まる（canon_hold）
#   人が canon/CANON.md を読む（第4節の行動原理、第6節の世界の前提、第13節の差し込み申告）
python3 tools/novelctl.py approve-canon      # 人が端末で
python3 tools/novelctl.py start              # 各章 → 記録 → 結合 → 最終審査 → DONE
python3 tools/guard.py check
```

`start` は切り離して動くので、ツールの制限時間に殺されません。進み具合は 2〜5 分おきに `status --json` で見ます。

## 2. 止まったとき（`escalation.code`）

| コード | 意味 | 進行役の対応 | 人の選択肢 |
|---|---|---|---|
| `DESIGN_EXHAUSTED` | CANON の設計が規定回数で通らない | 止まって報告 | `rescue canon`（上位モデルで一度だけ全体修正）／`redesign --feedback "..."`／自分で直して `approve-canon` |
| `CHAPTER_EXHAUSTED` | 章が3稿で合格しない | 止まって報告（`work/chNN_feedback.md` を写す） | `rescue chapter N`／`resolve --retry`／`resolve --accept` |
| `LEDGER_FAILED` | 章の記録が書式を満たさない | 止まって報告 | `work/chNN_ledger_a*.out` を見て台帳に手で足し `resolve --ledger-done` |
| `CANON_MODIFIED` | ロック後に CANON が変わった | 止まって報告 | 誰が変えたかを確かめ、正しければ `resolve --relock` |
| `GUARD_STOP` | STOP があるか、保護ファイルが変わった | 止まって報告 | 原因を確かめて元に戻す |
| `FINAL_REVIEW_FAILED` | 最終審査で重大指摘 | `output/final_report.md` の指摘を写して報告 | `reset-chapter N`／`resolve --done` |
| `BUDGET_EXCEEDED` | 費用が `budget_usd` に達した | 止まって報告 | 予算を見直す |
| `CALL_FAILED` / `CALL_TIMEOUT` | API の不調・キー・モデル名 | `--ping` で確かめ、結果を報告 | キー・モデル名を直して `resolve` |

`rescue` と `proofread` は上位モデル（premium）を一回だけ呼びます。**呼ぶかどうかは人が決めます**（費用がかかるため）。人に任されたときだけ実行してください。

## 3. 仕上げ（任意）

```
python3 tools/novelctl.py polish             # 文体仕上げ（polisher）。polished/ に保存
python3 tools/novelctl.py proofread          # 上位モデルが校正案を output/proofread_edits.json に出す（原稿は変えない）
#   人が enabled を確かめる（studio では一覧のチェックで）
python3 tools/proofread_apply.py             # 校正案を当てて output/final_proofread.md
python3 tools/novelctl.py epub --proofread   # 縦書き EPUB
```

## 4. 人への報告（進行役が notes/REPORT.md に書く）

- CANON がロックされたとき：作品の要旨、人物の行動原理の一覧、世界の前提、差し込みの一覧。
- 止まったとき：コードと、何を待っているか、人の選択肢（上の表）。
- 完成したとき：final の講評、持ち越しの差し込み、介入の数、費用（`status --json` の `usage`：役ごと）。
