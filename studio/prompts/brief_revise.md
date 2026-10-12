<!-- STUDIO: brief-revise -->
# 執筆指示書（BRIEF）の改訂

下の BRIEF に、プロデューサーが指示を付けました。指示を反映した BRIEF 全体を、同じ JSON の形で返してください。

<資料 名前="テーマ">
{{THEME}}
</資料>

<資料 名前="いまのBRIEF">
{{BRIEF}}
</資料>

<資料 名前="プロデューサーの指示">
{{INSTRUCTION}}
</資料>
{{LINT_BLOCK}}
## やること
- 指示を反映する。指示に無いところはそのまま残す。
- 一つ変えたら、それを参照している欄（chapters の function・hook、slots の plan、ending）もそろえる。
- 章の数：{{CHAPTERS}}（length.chapters と chapters の件数を一致させる）。
- slots の規則：設置はちょうど1回、回収は設置より後の章に1回まで。

## 出力
JSON だけを返してください。{"brief":{...いまの BRIEF と同じ形...},"changes":"何をどう変えたか（150字以内）"}
