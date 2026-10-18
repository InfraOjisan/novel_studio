# studio — 小説を一から書くアプリ（novel_studio v1.0）

テーマを入れて複数のモデルに企画を出させ、似た案と外れた案を見分け、選んだ案から執筆指示書（BRIEF）を作り、
確定（Fix）したら CANON・各章の執筆・審査・校正・EPUB まで、同じ画面で進めます。Python 3.9 以上の標準ライブラリだけで動きます。

```
python3 studio/server.py            # → http://127.0.0.1:8780
```

API キーはリポジトリ直下の `.env`（`OPENROUTER_API_KEY=...` か `NOVEL_PROVIDER_APIKEY=...`）。画面には出しません。
OpenCode Go なら `.env` に `NOVEL_LLM_BASE_URL=https://opencode.ai/zen/go/v1` も書きます（窓口の使い分けとヘッダは `engine/tools/llm_wire.py`）。

## 工程

| 工程 | 何をするか | だれ（既定のモデル） | やり直し |
|---|---|---|---|
| 一 テーマ | ジャンル・題材・章の数・想定読者・結末の型などを入れる | 人 | **テーマを直して新しい作品フォルダで頼み直す**（名前は必ず変える。元の作品は変わらない。確定済みからも可）。同じフォルダのまま最初からやり直すこともできる（前の記録は `studio/history/`） |
| 二 提案 | 選んだモデルに n 案ずつ並列で出させる。案ごとの費用を出す | haiku-5.5 / luna-pro / deepseek-v4.1-flash ほか | 再提出、指示つき再提出、**収束した骨格を避けて再提出**、案を自分で直す、案を指示して改稿 |
| 三 分類 | 各案の骨格をモデルに抜き出させ、**似ているかはルールで数える** →「収束」「バリエーション」「外れ値」 | luna-pro（骨格の抽出だけ） | しきい値を動かして即座に分類し直す（モデルを呼ばない）。AI に読ませた分類も並べられる |
| 四 BRIEF | 選んだ案から BRIEF を JSON で作り、機械検査（lint）し、BRIEF.md に描き出す | opus-5.5 | 指示を足して作り直す、JSON を自分で直す。版はすべて残る |
| 五 確定 | 提案と BRIEF を確定し、役ごと（と工程ごと）のモデルと予算を決めて作品フォルダを作る | 人 | 確定のあとでもモデルを替えられる。確定を解除して BRIEF に戻れる（成果は studio/history/ へ） |
| 六 CANON | 書き手が設計 → 審査役が審査 → 人が承認 | haiku-5.5 → luna-pro → 人 | 指示を足して再設計、**上位モデルで全体を一度だけ直す** |
| 七 執筆 | 各章を書く → 審査 → 記録（LEDGER）→ 最終審査 | haiku-5.5 / luna-pro | 止まったら：**上位モデルで指摘箇所だけ直す**／書き手にもう3稿／原稿のまま採用 |
| 八 仕上げ | 文体の磨き（任意）、上位モデルの校正案 → 人が採否 → 当てる → 表紙 → 縦書き EPUB | opus-5.5（校正だけ）、表紙は選んだ画像生成モデル | 校正案は一件ずつ採否を選べる。表紙は「絵はそのまま、文字だけ組み直す」ができる（費用なし） |

画面上部の「これまでの費用」に、企画（studio）と執筆（engine）の費用が工程・役ごとに出ます。

## 設計の考え方（Novel_4〜Grauwall_06 の検証から）

- **合議は外れ値を生まない。** だから分類は「良し悪し」ではなく「型への寄り方」を見せ、外れ値を拾うのは人、外れ値を増やすのは「収束した骨格を避けて」の再提出（ローリング）。
- **重複の判定は LLM に任せない。** 骨格の抽出だけをモデルに頼み、近さは文字 2-gram と骨格の欄ごとの一致で数える（`similarity.py`）。
- **BRIEF は JSON で作らせる。** これまで Claude が手で合わせていた BRIEF.md・config.json の伏線配置・CANON テンプレートを `engine/tools/scaffold.py` が機械で作るので、手合わせが要らない。
- **上位モデルは例外処理だけ。** 書く・照合するのは安いモデルで何度でも、全体の整合を直す・校正は上位モデルで一回、止めるかどうかは人。

## 構成

```
studio/
├── server.py        Web サーバー（ThreadingHTTPServer）。長い工程はジョブとして裏で走る
├── core.py          工程の本体。画面と CLI の両方がここだけを呼ぶ
├── cli.py           同じ工程の CLI（JSON 入出力）。ハーネス（harness/）が使う
├── llm.py           モデル呼び出し（OpenRouter・OpenCode）と費用の記録、/models の単価
├── similarity.py    収束・バリエーション・外れ値の分類（ルール）
├── prompts/         提案・改稿・骨格・分類・BRIEF の依頼文
├── web/             画面（HTML・CSS・JS。ビルド不要）
├── settings.json    （任意）既定のモデルなどを上書き。core.py の DEFAULT_SETTINGS を参照
└── devtools/mock_openrouter.py   費用をかけずに端から端まで試す偽モデル
works/<slug>/        作品フォルダ。studio.json（企画の状態）と、確定後は engine の作業場所
```

## 費用をかけずに試す

```
python3 studio/devtools/mock_openrouter.py &
NOVEL_LLM_BASE_URL=http://127.0.0.1:8765/api/v1 NOVEL_PROVIDER_APIKEY=mock python3 studio/server.py
#   OpenCode Go の真似：NOVEL_LLM_BASE_URL=http://127.0.0.1:8765/zen/go/v1
```

`MOCK_FAIL_CANON_REVIEW=5`（CANON 審査を5回落とす）や `MOCK_FAIL_CHAPTER=2`（第2章の審査を落とし続ける）で、例外処理の経路も通せます。

## 表紙

仕上げの画面で作ります。題名は CANON のもの、著者名は入力したもの（EPUB の著者にも入ります）。

| 項目 | 選択肢 |
|---|---|
| 画像生成モデル | OpenRouter の画像生成モデルの一覧（/images/models）から。「使わない」なら文字と色だけの表紙（費用なし） |
| スタイル | シンプル／イラスト／写実 |
| モチーフ | 自由入力（描いてほしいもの） |
| 題名 | 縦書き／横書き |
| 書体 | 明朝／ゴシック／カジュアル（Google Fonts の Shippori Mincho B1・Zen Kaku Gothic New・Zen Maru Gothic。オフラインなら端末の書体） |
| 著者名 | 自由入力 |

画像生成モデルには文字のない絵だけを描かせ、題名と著者名は HTML で組んで、Chrome（ヘッドレス）で 1600×2560 の PNG にします。Chrome が見つからないときは、絵だけを表紙にして題名は扉に入れます（`NOVEL_CHROME` で Chrome の場所を指定できます）。費用は「これまでの費用」の執筆側に `cover` として出ます。

v1.1 より前に確定した作品は、作品フォルダの道具（tools/）が古いので、表紙の欄に出る「道具を最新にする」を押してから作ります（`python3 engine/tools/scaffold.py upgrade works/<slug>` と同じ。プロンプト・BRIEF・CANON・本文は変わらず、記録は notes/INTERVENTIONS.md に残ります）。

## 注意

- 自分の Mac（127.0.0.1）だけで使う前提です。外に公開しないでください（認証はありません）。
- CANON の承認は画面のボタンで行います（指紋を確かめて押す）。CLI とハーネスからは承認できません。
- 作品ごとの工程の記録は `works/<slug>/studio.json`、費用は `works/<slug>/studio/usage.jsonl`（企画）と `logs/usage.jsonl`（執筆）。
