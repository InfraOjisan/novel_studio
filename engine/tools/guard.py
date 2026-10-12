#!/usr/bin/env python3
"""guard — 作業場所の見張り番（第3回の実験用）。

書き手（チームのエージェント、Aion）が「読んで守る」規則とは別に、ここでは機械で確かめられることだけを確かめる。
novelctl.py が毎ステップ呼び出すほか、人やハーネスが単独で実行できる。

  python3 tools/guard.py check          作業場所全体を点検（終了コード 0=問題なし / 1=指摘あり / 2=停止すべき）
  python3 tools/guard.py scan FILE...   ファイルの本文だけを点検（禁止の設定・ゲームの言葉・引き写し）
  python3 tools/guard.py seal           保護ファイルの指紋を guard/manifest.json に記録する（人が行う。setup が最初に一度行う）

点検すること:
  1. STOP ファイル（作業場所の直下に STOP があれば、全員そこで止まる）
  2. 保護ファイルの改変（BRIEF・AGENTS・config・tools・prompts・templates・series を書き手が書き換えていないか）
  3. 作業場所の直下に、決められた以外のファイルやフォルダが増えていないか
  4. 本文と CANON に、越えてはならない線の言葉（禁止の設定・ゲームの言葉）が出ていないか
  5. 本文と CANON が、手元の参照文書（guard/corpus/ の既刊・参考文）を長く引き写していないか

ここで見つけられないもの（文体の模倣、言い換えた借用、筋の盗用）は、審査と人の目で見る。
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
G = CFG.get("guard", {})
MANIFEST = ROOT / G.get("manifest", "guard/manifest.json")
STOP = ROOT / G.get("stop_file", "STOP")
OVERLAP = int(G.get("overlap_chars", 30))
BANNED = [(re.compile(b["pattern"]), b["reason"]) for b in G.get("banned", [])]
_CORPUS = None


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _norm(text):
    """引き写しの比較用。空白・改行・記号の揺れを落とす。"""
    return re.sub(r"[\s　「」『』（）()、。・…―－\-!！?？\"'＊*#]", "", text)


def protected_files():
    out = []
    for pat in G.get("protected", []):
        out += [p for p in ROOT.glob(pat) if p.is_file()]
    return sorted(set(out))


def seal():
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    data = {str(p.relative_to(ROOT)): _sha(p) for p in protected_files()}
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"sealed {len(data)} files -> {MANIFEST.relative_to(ROOT)}")


def check_stop():
    if STOP.exists():
        reason = STOP.read_text(encoding="utf-8").strip() or "（理由の記入なし）"
        return [("stop", f"STOP ファイルがあります：{reason}")]
    return []


def check_integrity():
    if not MANIFEST.exists():
        return [("integrity", "guard/manifest.json がありません（setup が seal していない）")]
    want = json.loads(MANIFEST.read_text(encoding="utf-8"))
    issues = []
    for rel, h in want.items():
        p = ROOT / rel
        if not p.exists():
            issues.append(("integrity", f"保護ファイルが消えています：{rel}"))
        elif _sha(p) != h:
            issues.append(("integrity", f"保護ファイルが書き換えられています：{rel}"))
    have = {str(p.relative_to(ROOT)) for p in protected_files()}
    for rel in sorted(have - set(want)):
        issues.append(("integrity", f"保護された場所に、記録にないファイルがあります：{rel}"))
    return issues


def check_layout():
    allowed = set(G.get("allowed_top", []))
    extra = [p.name for p in ROOT.iterdir() if p.name not in allowed and not p.name.startswith(".")]
    return [("layout", f"作業場所の直下に、決められていないものがあります：{n}") for n in sorted(extra)]


def _corpus():
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = {}
        d = ROOT / G.get("corpus_dir", "guard/corpus")
        for f in sorted(d.glob("*")) if d.exists() else []:
            t = _norm(f.read_text(encoding="utf-8", errors="replace"))
            for i in range(0, max(0, len(t) - OVERLAP + 1)):
                _CORPUS.setdefault(t[i:i + OVERLAP], f.name)
    return _CORPUS


NEG = re.compile(r"しない|させない|出さない|使わない|書かない|越えない|ない。|ないこと|禁止|禁じ|線")


def scan_text(text, allow=(), skip_negated=False):
    """本文や CANON の点検。返り値は [(種類, 説明)]。
    allow に含まれる文字列の中の一致は数えない。skip_negated=True（CANON用）なら、否定や禁止を述べている行は数えない。"""
    issues = []
    masked = text
    for a in allow:
        masked = masked.replace(a, "　" * len(a))
    if skip_negated:
        masked = "\n".join("" if NEG.search(line) else line for line in masked.splitlines())
    for rx, reason in BANNED:
        hits = sorted(set(m.group(0) for m in rx.finditer(masked)))
        if hits:
            issues.append(("line", f"越えてはならない線：{reason}（本文中の語：{'、'.join(hits[:5])}）"))
    corp = _corpus()
    if corp:
        t = _norm(text)
        found = {}
        i = 0
        while i <= len(t) - OVERLAP:
            src = corp.get(t[i:i + OVERLAP])
            if src:
                found.setdefault(src, t[i:i + OVERLAP])
                i += OVERLAP
            else:
                i += 1
        for src, frag in found.items():
            issues.append(("copy", f"参照文書「{src}」と{OVERLAP}字以上同じ並びがあります：「{frag[:20]}…」。自分の言葉で書き直す。"))
    return issues


def check_texts():
    issues = []
    allow = G.get("allow_phrases", [])
    targets = [ROOT / "canon" / "CANON.md"] + sorted((ROOT / "chapters").glob("*.md")) + sorted((ROOT / "work").glob("ch*_draft.md"))
    for f in targets:
        if f.exists():
            for kind, msg in scan_text(f.read_text(encoding="utf-8"), allow, skip_negated=(f.name == "CANON.md")):
                issues.append((kind, f"{f.relative_to(ROOT)}: {msg}"))
    return issues


def main(argv):
    cmd = argv[0] if argv else "check"
    if cmd == "seal":
        seal()
        return 0
    if cmd == "scan":
        rc = 0
        for f in argv[1:]:
            for kind, msg in scan_text(Path(f).read_text(encoding="utf-8"), G.get("allow_phrases", [])):
                print(f"[{kind}] {f}: {msg}")
                rc = 1
        return rc
    if cmd == "check":
        hard = check_stop() + check_integrity()
        soft = check_layout() + check_texts()
        for kind, msg in hard + soft:
            print(f"[{kind}] {msg}")
        if hard:
            print("GUARD: 停止すべき問題があります。人に報告してください。")
            return 2
        if soft:
            print("GUARD: 指摘があります。")
            return 1
        print("GUARD: 問題なし")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
