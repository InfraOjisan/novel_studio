# CHANGELOG

## 未リリース

- **並列の提案で落ちることがあった不具合を直した。** 提案はモデルごとのスレッドで並列に走り、それぞれが費用の計算でモデル一覧のキャッシュ（`studio/.cache/models_*.json`）を読む。1日の期限が切れた直後などに、一つのスレッドが書き直している途中の中身を別のスレッドが読むと JSONDecodeError で落ちていた。
  - キャッシュは別名に書いてから差し替える（書きかけを読ませない）。画像生成モデルの一覧も同じ。
  - 壊れたキャッシュ（古い版が書きかけで残したもの）は読み捨てて、取り直す。
  - 回帰テストに、壊れたキャッシュからの立ち直りと、12スレッドで60回の同時読み書きを追加（19 件）。直す前のコードでは両方とも落ちることを確かめた。
  - plan_studio（novel_studio の Issue #3 から分けたリポジトリ）で見つけたもの。

## v1.1.1（2026-10-13）

- **OpenCode（Go・Zen）のモデルで動くようにした。** OpenCode Go で HTTP 403「error code: 1010」になっていた件。
  - 原因1：studio の呼び出しが User-Agent を名乗っておらず、Python の既定（`Python-urllib/3.x`）のまま送っていた。OpenCode の前の Cloudflare はこれを弾く（2026-10-13 に実際に確かめた：同じ GET /models が Python-urllib だと 403・1010、道具の名前だと 200）。
  - 原因2：OpenCode Go は `x-opencode-session` ヘッダが必須になっていた（ないと HTTP 400・MissingSessionID）。作品ごとに同じ値を送る（プロンプトのキャッシュに効く）。
  - 窓口の使い分け：OpenCode では Claude・MiniMax・Qwen は `/messages`、GPT・Grok・Muse は `/responses`、ほかは `/chat/completions`。モデル名と base_url から自動で決め、合わなければ別の窓口で一度ずつ出し直す。`NOVEL_LLM_PROTOCOLS="接頭辞:窓口,..."` か config.json の `llm.protocols` で指定もできる。
  - 費用：OpenCode は応答に費用を入れないので、公開価格の表（`engine/tools/llm_wire.py` の PRICES）で見積もる。表にないモデルは `NOVEL_LLM_PRICES="gpt-6-luna=1.25/10"`（1M トークンあたりのドル、入力/出力）で足せる。画面の費用は、見積もりを含むと「≈」、価格の分からない呼び出しがあると「+?」がつく。
  - 新しい共通部品 `engine/tools/llm_wire.py`（studio・engine・表紙の三か所が同じヘッダと窓口を使う）。
  - 作りかけの作品は、執筆の画面の「道具を最新にする」を押すと直る（tools/ だけを入れ替え。BRIEF・CANON・本文は変わらない）。
- **テーマを直して、提案を頼み直せるようにした（一 テーマの画面）。** 新しい作品フォルダの名前を必ず変える方式。
  - 元の作品には一切触れない（提案・分類・BRIEF・本文・ログもそのまま）。同じ名前・すでにある名前・中身のあるフォルダには作らない＝上書きが起きない。
  - 新しい作品には「どの作品の、どの版のテーマから、どの欄を変えたか」が残り、元の作品の画面には派生した作品の一覧が出る。確定済みの作品からも派生できる。
  - CLI：`python3 studio/cli.py derive <元> <新しい名前> [--theme theme.json]`。
  - これまでの「この作品フォルダのまま、テーマを変えて最初からやり直す」は畳んで残した。
- 偽モデルに OpenCode の真似（`/zen/go/v1`：User-Agent と session の門、`/responses`・`/messages`、費用なしの応答）を足した。回帰テストに窓口の判定・組み立て・読み取り・見積もりと、偽 OpenCode での studio と engine の通し、派生を追加（17 件）。

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
