#!/usr/bin/env python3
"""create_issues — docs/issues.json から GitHub にラベルと issue を作る（gh CLI の REST を使う）。

  python3 scripts/create_issues.py            何を作るかを表示するだけ（既定）
  python3 scripts/create_issues.py --apply    実際に作る（同じタイトルの issue が既にあれば作らない）
  python3 scripts/create_issues.py --repo OWNER/REPO --apply

前提：gh auth login 済み。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "docs" / "issues.json").read_text(encoding="utf-8"))


def gh(*args, input=None):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True, input=input)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:500])
    return json.loads(r.stdout or "null")


def main(argv):
    apply = "--apply" in argv
    repo = argv[argv.index("--repo") + 1] if "--repo" in argv else "InfraOjisan/novel_studio"
    labels = {x["name"] for x in (gh(f"repos/{repo}/labels?per_page=100") if apply else [])}
    titles = set()
    if apply:
        page = 1
        while True:
            got = gh(f"repos/{repo}/issues?state=all&per_page=100&page={page}")
            titles |= {x["title"] for x in got}
            if len(got) < 100:
                break
            page += 1
    for name, spec in DATA["labels"].items():
        if name in labels:
            continue
        print(f"label  {name}")
        if apply:
            gh(f"repos/{repo}/labels", "--method", "POST", "--input", "-",
               input=json.dumps({"name": name, "color": spec["color"], "description": spec["description"]}))
    made = 0
    for it in DATA["issues"]:
        if it["title"] in titles:
            print(f"skip   {it['title']}（既にある）")
            continue
        print(f"issue  {it['title']}  [{', '.join(it['labels'])}]")
        if apply:
            gh(f"repos/{repo}/issues", "--method", "POST", "--input", "-",
               input=json.dumps({"title": it["title"], "body": it["body"], "labels": it["labels"]}, ensure_ascii=False))
            made += 1
    print(("作成しました：" if apply else "（確認のみ。作るなら --apply）予定：") + f"{made if apply else len(DATA['issues'])}件")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
