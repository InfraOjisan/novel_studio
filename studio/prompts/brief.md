<!-- STUDIO: brief -->
# 執筆指示書（BRIEF）の作成

プロデューサーが選んだ企画案から、小説の執筆指示書（BRIEF）を作ります。BRIEF は物語の**構造**を定めるもので、人物の細部・場面・台詞・伏線の見せ方は、あとで作者（別のモデル）が CANON（設定書）で創作します。
書きすぎないこと。決めるのは「守らせたいこと」と「各章の機能」と「伏線の配置」です。

<資料 名前="テーマ">
{{THEME}}
</資料>

<資料 名前="選ばれた企画案">
{{PROPOSAL}}
</資料>
{{INSTRUCTION_BLOCK}}
## 決まっていること
- 章の数：{{CHAPTERS}}
- 1章の字数：目安 {{TARGET}} 字（{{MINC}}〜{{MAXC}} 字）
- 想定読者：{{AUDIENCE}}

## 書き方の規則
- chapters は全{{CHAPTERS}}章ぶん、n は 1 から順に。各章の function（機能）は必須。tension（緊張度 1〜5）と reveal（明かす度合い 0〜5）で緩急を設計する。
- slots（伏線スロット）は 3〜9 個。id は英大文字1字＋数字1字（例 F1, C1, M1）。plan は [[章, "planted"], [章, "reinforced"], [章, "recovered"]]。設置はちょうど1回、回収は設置より後の章に1回（持ち越すなら回収なし）。
- 各スロットの「中身」は書かない（name と function だけ）。中身は作者が CANON で決める。
- characters は名前と骨格だけ（固定したい点）。細部は作者に任せる。名前のある人物には行動原理が必要だと作者に伝わるよう、骨格に「欲しいもの」か「恐れているもの」の手がかりを入れる。
- rules.lines には「越えてはならない線」（どんなに面白くても書かないこと）を箇条で。ジャンルの規範（ミステリのフェアプレイ、SF の科学の説明）をここか fairplay に入れる。
- ending は結末の方向性。読者にとっての結末と、人物にとっての結末を分けて書くとよい。
- 企画案の must_keep（譲れないポイント）は、必ずどこかの欄に反映する。
- banned は本文に出てはいけない語の正規表現（任意。例：魔法を禁じるなら {"pattern":"魔法|呪文","reason":"魔法なし"}）。

## 出力
JSON だけを返してください（前置き・コードブロックなし）。形は次のとおり。
{"brief":{
 "title":"", "logline":"結末まで含む一行要旨", "genre":"", "audience":"",
 "length":{"chapters":{{CHAPTERS}},"chapter_target":{{TARGET}},"chapter_min":{{MINC}},"chapter_max":{{MAXC}}},
 "narration":"語りの人称と視点人物", "ending_type":"", "core":"物語の核", "genre_reason":"",
 "setting":"舞台（固定する点）",
 "characters":[{"name":"","role":"","fixed":""}],
 "free":"作者の裁量に任せること",
 "rules":{"style":"","language":"","characters":"","lines":[""]},
 "chapters":[{"n":1,"title":"仮称","tension":2,"reveal":1,"function":"","hook":"章末の引き","forbidden":"この章で禁止すること"}],
 "slots":[{"id":"F1","name":"","function":"","plan":[[1,"planted"],[3,"recovered"]]}],
 "fairplay":[""], "ending":"", "design_notes":[""], "banned":[]
}}
