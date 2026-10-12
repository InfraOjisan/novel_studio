#!/usr/bin/env python3
"""proofread_apply — 校正の修正案（output/proofread_edits.json）を当てて、校正版を別ファイルに書き出す。原稿は変更しない。

  python3 tools/proofread_apply.py            enabled=true の修正だけを当てる
  python3 tools/proofread_apply.py --dry-run  当てられるかどうかだけ確かめる

v1.0：作品ごとに Python のリストへ手で書いていた EDITS を JSON にした。
修正案は `novelctl.py proofread`（上位モデル）が作り、人や studio が enabled を切り替えてから当てる。手で足してもよい。

  {"edits": [{"chapter": 4, "before": "原文（その章で1か所だけ一致する長さ）", "after": "修正", "kind": "誤字|表現|連続性|設定",
              "reason": "理由", "enabled": true}]}

入力:  polished/chNN.md（文体仕上げ稿）。なければ chapters/chNN.md
出力:
  output/final_proofread.md       校正版の結合原稿（make_epub.py --source proofread の入力）
  output/proofread_changes.md     当てた修正の一覧（章ごと・原文→修正・理由）と、当てられなかったもの
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N_CH = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["chapters"]


def main(argv):
    dry = "--dry-run" in argv
    f = ROOT / "output" / "proofread_edits.json"
    edits = json.loads(f.read_text(encoding="utf-8")).get("edits", []) if f.exists() else []
    canon = (ROOT / "canon" / "CANON.md").read_text(encoding="utf-8")
    m = re.search(r"タイトル：\s*(.+)", canon)
    title = m.group(1).strip().strip("『』「」") if m else "無題"
    log, parts, missing = [], [], []
    for n in range(1, N_CH + 1):
        src = ROOT / "polished" / f"ch{n:02d}.md"
        if not src.exists():
            src = ROOT / "chapters" / f"ch{n:02d}.md"
        text = src.read_text(encoding="utf-8")
        for e in edits:
            if e.get("chapter") != n or not e.get("enabled", True):
                continue
            old, new = e["before"], e.get("after", "")
            c = text.count(old)
            if c != 1:
                missing.append(f"第{n}章: 見つからない/複数（{c}件）: {old[:30]}")
                continue
            text = text.replace(old, new)
            log.append((n, e.get("kind", "表現"), old, new or "（削除）", e.get("reason", "")))
        text = re.sub(r"[ \t]+\n", "\n", text)
        parts.append(text.strip().replace("# 第", "## 第", 1))
    if dry:
        print(f"当てられる修正 {len(log)}件、当てられない修正 {len(missing)}件")
        for x in missing:
            print("  未適用:", x)
        return 0 if not missing else 1
    (ROOT / "output" / "final_proofread.md").write_text(f"# {title}\n\n" + "\n\n".join(parts) + "\n", encoding="utf-8")
    lines = ["# 当てた修正の一覧", "", "対象：chapters/（または polished/）→ output/final_proofread.md（原稿は変更していません）", ""]
    cats = {}
    for n, cat, *_ in log:
        cats[cat] = cats.get(cat, 0) + 1
    lines.append("件数：" + ("　".join(f"{k} {v}件" for k, v in sorted(cats.items())) or "0件"))
    cur = None

    def esc(s):
        return s.replace("|", "｜").replace("\n", "⏎")
    for n, cat, old, new, why in log:
        if n != cur:
            lines += ["", f"## 第{n}章", "", "| 分類 | 原文 | 修正 | 理由 |", "|---|---|---|---|"]
            cur = n
        lines.append(f"| {cat} | {esc(old)} | {esc(new)} | {esc(why)} |")
    if missing:
        lines += ["", "## 当てられなかった修正", ""] + [f"- {x}" for x in missing]
    (ROOT / "output" / "proofread_changes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    body_chars = sum(len(re.sub(r"\s", "", p.split("\n", 1)[1])) for p in parts if "\n" in p)
    print(f"output/final_proofread.md（本文{body_chars}字）と output/proofread_changes.md を作成。適用{len(log)}件、失敗{len(missing)}件")
    for x in missing:
        print("  未適用:", x)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
