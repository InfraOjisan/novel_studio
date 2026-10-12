# 試験 L3B：企画（BRIEF をあなた自身が書く）

あなたは novel_studio の制作進行です。まず、このフォルダの `AGENTS.md` を最後まで読んでください。道具の詳細は `TOOLS.md` と `engine/tools/scaffold.py` の冒頭（brief.json の形）にあります。

この試験は、Claude Code の Opus 5.5 がやっていた「選んだ企画から BRIEF を書く」仕事を、あなた（このハーネスのモデル）ができるかを測ります。

## やること
1. L3 の 1〜5 と同じ（作品名は `l3b`）。
2. 道具で BRIEF を一度作る（`brief`）。これは比較用です。
3. 次に、**あなた自身が** 選んだ案と `theme.json` から brief.json を書く。道具が作った BRIEF を写さないこと。
   - 章の数・字数は theme.json どおり。伏線スロットは3〜9個。設置は1回、回収は設置より後の章に1回。
   - 選んだ案の must_keep（譲れないポイント）を必ずどこかに反映する。
   - 名前のある人物には、行動原理の手がかり（欲しいもの／恐れているもの）を骨格に入れる。
4. `python3 engine/tools/scaffold.py lint <あなたの brief.json>` で確かめてから、`brief-put l3b <道具の版> --file <あなたの brief.json>` で置く。lint が通るまで直す。
5. `works/l3b/notes/BRIEF_NOTES.md` に、道具の BRIEF とあなたの BRIEF の違い（章立て・伏線・結末の方向性）を書く。
6. あなたの BRIEF の版で確定し、CANON の承認待ちまで進めて報告する。
