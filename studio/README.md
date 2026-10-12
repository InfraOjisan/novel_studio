# studio — 小説を一から書くアプリ（novel_studio v1.0）

テーマを入れて複数のモデルに企画を出させ、似た案と外れた案を見分け、選んだ案から執筆指示書（BRIEF）を作り、
確定（Fix）したら CANON・各章の執筆・審査・校正・EPUB まで、同じ画面で進めます。Python 3.9 以上の標準ライブラリだけで動きます。

```
python3 studio/server.py            # → http://127.0.0.1:8780
```

API キーはリポジトリ直下の `.env`（`OPENROUTER_API_KEY=...` か `NOVEL_PROVIDER_APIKEY=...`）。画面には出しません。

## 工程

| 工程 | 何をするか | だれ（既定のモデル） | やり直し |
|---|---|---|---|
| 一 テーマ | ジャンル・題材・章の数・想定読者・結末の型などを入れる | 人 | テーマを変える＝最初から（前の記録は `studio/history/`） |
| 二 提案 | 選んだモデルに n 案ずつ並列で出させる。案ごとの費用を出す | haiku-5.5 / luna-pro / deepseek-v4.1-flash ほか | 再提出、指示つき再提出、**収束した骨格を避けて再提出**、案を自分で直す、案を指示して改稿 |
| 三 分類 | 各案の骨格をモデルに抜き出させ、**似ているかはルールで数える** →「収束」「バリエーション」「外れ値」 | luna-pro（骨格の抽出だけ） | しきい値を動かして即座に分類し直す（モデルを呼ばない）。AI に読ませた分類も並べられる |
| 四 BRIEF | 選んだ案から BRIEF を JSON で作り、機械検査（lint）し、BRIEF.md に描き出す | opus-5.5 | 指示を足して作り直す、JSON を自分で直す。版はすべて残る |
| 五 確定 | 提案と BRIEF を確定し、役ごとのモデルと予算を決めて作品フォルダを作る | 人 | ここから先は BRIEF を変えない |
| 六 CANON | 書き手が設計 → 審査役が審査 → 人が承認 | haiku-5.5 → luna-pro → 人 | 指示を足して再設計、**上位モデルで全体を一度だけ直す** |
| 七 執筆 | 各章を書く → 審査 → 記録（LEDGER）→ 最終審査 | haiku-5.5 / luna-pro | 止まったら：**上位モデルで指摘箇所だけ直す**／書き手にもう3稿／原稿のまま採用 |
| 八 仕上げ | 文体の磨き（任意）、上位モデルの校正案 → 人が採否 → 当てる → 縦書き EPUB | opus-5.5（校正だけ） | 校正案は一件ずつ採否を選べる |

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
├── llm.py           OpenRouter 呼び出しと費用の記録、/models の単価
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
```

`MOCK_FAIL_CANON_REVIEW=5`（CANON 審査を5回落とす）や `MOCK_FAIL_CHAPTER=2`（第2章の審査を落とし続ける）で、例外処理の経路も通せます。

## 注意

- 自分の Mac（127.0.0.1）だけで使う前提です。外に公開しないでください（認証はありません）。
- CANON の承認は画面のボタンで行います（指紋を確かめて押す）。CLI とハーネスからは承認できません。
- 作品ごとの工程の記録は `works/<slug>/studio.json`、費用は `works/<slug>/studio/usage.jsonl`（企画）と `logs/usage.jsonl`（執筆）。
