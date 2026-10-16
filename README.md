# novel_studio

生成 AI に小説を一から書かせるための道具です。テーマを入れると、複数のモデルが企画を出し、似た案と外れた案を見分け、選んだ案から執筆指示書（BRIEF）を作り、設定書（CANON）・各章の執筆・審査・校正・縦書き EPUB まで、一つの画面で進みます。

- Python 3.9 以上の標準ライブラリだけで動きます（pip install は不要）。
- モデルは OpenRouter か OpenCode（Go・Zen）。OpenAI 互換の窓口なら差し替え可。
- 自分の Mac（127.0.0.1）で使う前提です。

```
git clone https://github.com/InfraOjisan/novel_studio && cd novel_studio
echo "OPENROUTER_API_KEY=sk-or-..." > .env
python3 studio/server.py          # → http://127.0.0.1:8780
```

OpenCode Go を使うときは `.env` を次のようにします（モデル名は `claude-haiku-5-5`・`gpt-6-luna`・`deepseek-v4.1-flash` のように OpenCode の名前で選びます）。

```
NOVEL_LLM_BASE_URL=https://opencode.ai/zen/go/v1
NOVEL_PROVIDER_APIKEY=<OpenCode の鍵>
```

窓口（`/chat/completions`・`/responses`・`/messages`）はモデル名から自動で選びます。OpenCode は費用を返さないので、公開価格で見積もります（表にないモデルは `NOVEL_LLM_PRICES`）。詳しくは [CHANGELOG](CHANGELOG.md) の v1.1.1。

## 流れ

| 工程 | 何をするか | 既定のモデル |
|---|---|---|
| 一 テーマ | ジャンル・題材・章の数・想定読者・結末の型。あとから**テーマを直して、新しい作品フォルダで提案を頼み直せる**（元の作品はそのまま） | 人 |
| 二 提案 | 選んだモデルに n 案ずつ並列で出させる。案ごとの費用つき。再提出・指示つき再提出・**収束した骨格を避けて再提出**・案の編集・指示しての改稿 | Haiku 5.5 / GPT-6 Luna Pro / DeepSeek V4.1 Flash ほか |
| 三 分類 | 骨格をモデルに抜き出させ、**似ているかは道具が数える** →「収束」「バリエーション」「外れ値」。**分析とファイル出力（Markdown・CSV・JSON）は選んだ案だけ**、画面の最下部の帯から。骨格は 3 案ずつに小分けして頼み、取れた分から保存する（案が多くても上流で時間切れになりにくい） | Luna Pro（抽出だけ） |
| 四 BRIEF | 選んだ案から BRIEF を JSON で作り、機械検査し、Markdown に描き出す。指示つき改訂・直接編集 | Opus 5.5 |
| 五 確定 | 案と BRIEF を確定し、役ごとのモデルと予算を決めて作品フォルダを作る | 人 |
| 六 CANON | 書き手が設計 → 審査役が審査 → 人が承認 | Haiku → Luna Pro → 人 |
| 七 執筆 | 各章を書く → 審査 → 記録 → 最終審査。止まったら上位モデルで一度だけ直す | Haiku / Luna Pro（例外だけ Opus） |
| 八 仕上げ | 上位モデルの校正案 → 人が採否 → 当てる → **表紙**（画像生成モデルを選ぶ。スタイル：シンプル／イラスト／写実、モチーフ、題名の縦書き／横書き、書体：明朝／ゴシック／カジュアル、著者名）→ 縦書き EPUB | Opus（校正だけ）、表紙は選んだ画像生成モデル |

画面の上に、企画（studio）と執筆（engine）の費用が工程・役ごとに出ます。

## 中身

```
studio/     Web アプリ（server.py）、同じ工程の CLI（cli.py）、分類（similarity.py）、偽モデル（devtools/）
engine/     執筆エンジン（novelctl.py）、BRIEF から作品フォルダを作る scaffold.py、プロンプト、テンプレート、テスト
harness/    Claude Code 以外のハーネス（Hermes Agent / agy / omp）向けの手引き・開始プロンプト・試験 L1〜L4
docs/       設計の考え方（DESIGN.md）、issue の草稿
works/      作った作品（git には入れない。NOVEL_WORKS_DIR で別の場所にもできる）
```

## 考え方（詳しくは docs/DESIGN.md）

- **書く・照合するのは安いモデルで何度でも。全体の整合を直す・校正は上位モデルで一回だけ。止めるかどうかは人。**
- **合議は外れ値を生まない。** 分類は良し悪しではなく「型への寄り方」を見せる。外れ値を拾うのは人。
- **重複の判定は LLM に任せない。** モデルには骨格の抽出だけを頼み、近さは文字 2-gram と骨格の欄で数える。
- **BRIEF は JSON で作らせて lint する。** 伏線の回収が設置より前、章数の食い違いなどを、書く前に機械で止める。
- **表紙の文字は絵に描かせない。** 画像生成モデルには文字のない絵だけを頼み、題名と著者名はこちらで組んで重ねる（Chrome のヘッドレスで 1600×2560 に）。日本語の縦書きを正しく描けるモデルは少ないため。
- **AI の報告は機械で確かめる。** 校正案は原文が一か所に一致するものだけ。ハーネスの試験は報告ではなくディスクの状態で採点する。

## 費用をかけずに試す

```
python3 studio/devtools/mock_openrouter.py &
NOVEL_LLM_BASE_URL=http://127.0.0.1:8765/api/v1 NOVEL_PROVIDER_APIKEY=mock python3 studio/server.py
python3 -m unittest discover -s engine/tests      # 回帰テスト（偽モデルで端から端まで）
```

## 背景

もともとは [novel-tools](https://github.com/InfraOjisan/novel-tools)（テスト小説と実験の記録が大半）の中で育てた道具です。v1.0 で、道具だけをこのリポジトリに分けました。経緯は Zenn の連載「AIに小説を書かせたら『制作スタジオ』になった話」に書いています。

## ライセンス

MIT
