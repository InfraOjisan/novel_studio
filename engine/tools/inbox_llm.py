#!/usr/bin/env python3
"""inbox_llm — 書き手（人・Claude など、APIで呼べない作者）用の「受け箱」アダプタ。

novelctl.py の writer_cmd / reviewer_cmd にこれを指定すると、モデル呼び出しの代わりに
work/inbox/<パケット名から .packet.md を除いた名前>.md を答えとして読み、標準出力に返す。
答えが置かれていなければ終了コード4で止まり、novelctl は AWAITING_AUTHOR でエスカレーションする。

  python3 tools/inbox_llm.py <packet> writer|reviewer

使い終わった答えは work/inbox/done/ に移す（書き直しで同じパケット名が再利用されても混ざらない）。
パケットと答えの組は work/ に残るので、あとから「誰に何を渡し、何が返ったか」をすべて追える。
"""
import datetime
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: inbox_llm.py <packet> writer|reviewer")
    packet = Path(sys.argv[1])
    key = packet.name.removesuffix(".packet.md")
    inbox = ROOT / "work" / "inbox"
    ans = inbox / f"{key}.md"
    if not ans.exists():
        (inbox / "WAITING.txt").write_text(f"{packet}\n{ans}\n", encoding="utf-8")
        print(f"work/inbox/{key}.md がありません（パケット: work/{packet.name}）", file=sys.stderr)
        sys.exit(4)
    text = ans.read_text(encoding="utf-8")
    done = inbox / "done"
    done.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(str(ans), done / f"{key}.{stamp}.md")
    (inbox / "WAITING.txt").unlink(missing_ok=True)
    print(f"inbox: {key} ({len(text)} chars) author={sys.argv[2] if len(sys.argv) > 2 else '-'}", file=sys.stderr)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
