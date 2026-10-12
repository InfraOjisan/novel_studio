<!-- STUDIO: revise -->
# 企画案の改稿

プロデューサーが下の企画案を気に入り、指示を付けて改稿を求めています。

<資料 名前="テーマ">
{{THEME}}
</資料>

<資料 名前="元の案">
{{PROPOSAL}}
</資料>

<資料 名前="プロデューサーの指示">
{{INSTRUCTION}}
</資料>

## やること
- 指示に沿って案を直してください。指示の指摘を「消す」のではなく、指摘の元にある問題を「解く」こと（指摘を避けるために主題から逃げない）。
- 指示に無いところは、できるだけ元の案のまま残してください。
- 欄は元の案と同じです（kind, genre, title, setting, synopsis, highlight, appeal, must_keep, genre_reason）。

## 出力
JSON だけを返してください。
{"proposal":{"kind":"","genre":"","title":"","setting":"","synopsis":"","highlight":"","appeal":"","must_keep":"","genre_reason":""},"changes":"何をどう変えたか（150字以内）"}
