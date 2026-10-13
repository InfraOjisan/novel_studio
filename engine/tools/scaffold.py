#!/usr/bin/env python3
"""scaffold — brief.json（構造化した BRIEF）から、novelctl がそのまま動く作品フォルダを作る。標準ライブラリのみ。

  python3 engine/tools/scaffold.py lint  brief.json                 BRIEF の機械検査（JSON で結果を返す）
  python3 engine/tools/scaffold.py render brief.json                BRIEF.md を標準出力へ
  python3 engine/tools/scaffold.py upgrade works/<slug>             作りかけの作品の tools/ だけを最新にする（表紙など新しい機能を使うため）
  python3 engine/tools/scaffold.py create brief.json works/<slug> [--roles roles.json] [--budget 3.0]
                                                                     作品フォルダを作る（engine をコピー → BRIEF.md・config.json・
                                                                     CANON テンプレートを生成 → init → guard seal）

なぜ JSON か（v1.0）:
  これまで BRIEF.md（5万字）・config.json の slot_plan・CANON テンプレート・LEDGER の欄を、作品ごとに Claude が手で合わせていた。
  BRIEF を JSON で出させて機械で描き出せば、この「手合わせ」が要らなくなり、安いモデルでも BRIEF を作れる。
  食い違いは lint が出す（slot_plan と章立ての齟齬、回収が設置より前、字数の範囲など）。

brief.json の形（必須は * ）:
{
 "title"*: "仮タイトル", "logline"*: "一行要旨（結末まで）", "genre"*: "SF", "audience"*: "想定読者",
 "length"*: {"chapters": 5, "chapter_target": 3500, "chapter_min": 3000, "chapter_max": 4500},
 "narration"*: "語りの人称と視点人物", "ending_type"*: "ビターエンド", "core"*: "物語の核",
 "genre_reason": "なぜこのジャンルでなければならないか", "setting"*: "舞台（固定する点）",
 "characters"*: [{"name": "", "role": "", "fixed": "骨格（固定）"}],
 "free": "作者の裁量に任せること",
 "rules"*: {"style": "文体とトーン", "language": "言葉づかい・表記", "characters": "人物の規律", "lines": ["越えてはならない線"]},
 "chapters"*: [{"n": 1, "title": "仮称", "tension": 2, "reveal": 1, "function": "章の機能", "hook": "章末の引き", "forbidden": "禁止"}],
 "slots"*: [{"id": "F1", "name": "名前", "function": "機能", "plan": [[1, "planted"], [3, "recovered"]]}],
 "fairplay": ["フェアプレイ条件"], "ending"*: "結末の方向性", "design_notes": ["設計の注意"],
 "banned": [{"pattern": "正規表現", "reason": "理由"}]
}
"""
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
EV = ("planted", "reinforced", "recovered")
EV_JA = {"planted": "設置", "reinforced": "補強", "recovered": "回収"}
JA_EV = {v: k for k, v in EV_JA.items()}
REQUIRED = ["title", "logline", "genre", "audience", "length", "narration", "ending_type", "core",
            "setting", "characters", "rules", "chapters", "slots", "ending"]


def nn(n):
    return f"{int(n):02d}"


def parse_plan(plan):
    """[[1,"planted"],...] / "1:設置 3:回収" / {"planted":[1]} のどれでも受ける。"""
    out = []
    if isinstance(plan, str):
        for c, ev in re.findall(r"(\d+)\s*[:：]?\s*(設置|補強|回収|planted|reinforced|recovered)", plan):
            out.append([int(c), JA_EV.get(ev, ev)])
    elif isinstance(plan, dict):
        for ev, chs in plan.items():
            for c in chs if isinstance(chs, list) else [chs]:
                out.append([int(c), JA_EV.get(ev, ev)])
    else:
        for item in plan or []:
            c, ev = item[0], item[1]
            out.append([int(c), JA_EV.get(ev, ev)])
    return sorted(out, key=lambda x: (x[0], EV.index(x[1]) if x[1] in EV else 9))


def lint(b):
    """返り値：{"ok": bool, "errors": [...], "warnings": [...]}。errors があると create しない。"""
    E, W = [], []
    for k in REQUIRED:
        v = b.get(k)
        if v in (None, "", [], {}):
            E.append(f"{k} がありません")
    L = b.get("length") or {}
    n_ch = int(L.get("chapters") or 0)
    t, lo, hi = (int(L.get(k) or 0) for k in ("chapter_target", "chapter_min", "chapter_max"))
    if n_ch < 1:
        E.append("length.chapters が1以上ではありません")
    if n_ch > 60:
        E.append("length.chapters が60を超えています（章立ての見直しを）")
    if not (0 < lo <= t <= hi):
        E.append(f"字数の範囲が不正です（min {lo} ≤ target {t} ≤ max {hi} にする）")
    chs = b.get("chapters") or []
    if len(chs) != n_ch:
        E.append(f"chapters が {len(chs)} 件で、length.chapters（{n_ch}）と合いません")
    for i, c in enumerate(chs, 1):
        if int(c.get("n", 0)) != i:
            E.append(f"chapters の {i} 件目の n が {c.get('n')} です（1から順に）")
        if not str(c.get("function", "")).strip():
            E.append(f"第{i}章の function（機能）が空です")
        for k in ("tension", "reveal"):
            v = c.get(k)
            if v is not None and not (0 <= int(v) <= 5):
                W.append(f"第{i}章の {k} は 0〜5 で書く")
    ids = set()
    for s in b.get("slots") or []:
        sid = str(s.get("id", ""))
        if not re.fullmatch(r"[A-Z]\d", sid):
            E.append(f"スロットID「{sid}」は英大文字1字＋数字1字にする（例 F1）")
            continue
        if sid in ids:
            E.append(f"スロットID {sid} が重複しています")
        ids.add(sid)
        if not str(s.get("name", "")).strip():
            E.append(f"{sid} の name が空です")
        plan = parse_plan(s.get("plan"))
        if not plan:
            E.append(f"{sid} の plan（設置・補強・回収の章）が空です")
            continue
        for c, ev in plan:
            if ev not in EV:
                E.append(f"{sid} の plan に不明な操作 {ev}")
            if not 1 <= c <= max(n_ch, 1):
                E.append(f"{sid} の plan の章 {c} が範囲外（1〜{n_ch}）")
        pl = [c for c, ev in plan if ev == "planted"]
        rc = [c for c, ev in plan if ev == "recovered"]
        if len(pl) != 1:
            E.append(f"{sid} は設置（planted）がちょうど1回であること（いま {len(pl)} 回）")
        if len(rc) > 1:
            E.append(f"{sid} の回収（recovered）が {len(rc)} 回あります（1回まで）")
        if pl and rc and rc[0] <= pl[0]:
            E.append(f"{sid} の回収（第{rc[0]}章）が設置（第{pl[0]}章）より前か同じ章です")
        if pl and any(c < pl[0] for c, ev in plan if ev == "reinforced"):
            E.append(f"{sid} の補強が設置より前の章にあります")
        if not rc:
            W.append(f"{sid} は回収されません（持ち越し）。意図どおりなら問題なし")
    if not ids:
        E.append("伏線スロットが一つもありません")
    # 伏線の詰め込みの警告（v1.1：短編で最終章に回収が集中し、上位モデルでも直しきれなかった実例から）
    if n_ch and ids:
        ops = {}
        rec_last = 0
        for s in b.get("slots") or []:
            for c, ev in parse_plan(s.get("plan")):
                ops[c] = ops.get(c, 0) + 1
                if ev == "recovered" and c == n_ch:
                    rec_last += 1
        if rec_last >= 3:
            W.append(f"最終章で回収が{rec_last}件あります。最終章が詰め込みになりやすい（2件までにして、残りは前の章で回収する）")
        per_ch = max(2, t // 1000) if t else 2
        for c, k in sorted(ops.items()):
            if k > per_ch + 1:
                W.append(f"第{c}章で伏線の操作が{k}件あります（1章 {t} 字なら {per_ch} 件前後が目安）。章を増やすか、スロットを減らす")
    for bn in b.get("banned") or []:
        try:
            re.compile(bn.get("pattern", ""))
        except re.error as e:
            E.append(f"banned の正規表現が不正：{bn.get('pattern')}（{e}）")
    total = n_ch * t
    if total and total > 300000:
        W.append(f"総字数の目安が {total} 字です。長すぎないか確認する")
    if len(json.dumps(b, ensure_ascii=False)) > 60000:
        W.append("BRIEF が6万字を超えています。モデルの文脈を圧迫します")
    return {"ok": not E, "errors": E, "warnings": W}


def chapter_slot_line(b, n):
    ev = []
    for s in b["slots"]:
        for c, e in parse_plan(s.get("plan")):
            if c == n:
                ev.append(f"{s['id']} {EV_JA.get(e, e)}")
    return "／".join(ev) if ev else "なし"


def render(b):
    L = b["length"]
    n_ch = int(L["chapters"])
    rules = b.get("rules") or {}
    lines = rules.get("lines") or []
    out = [f"# BRIEF — 執筆指示書（『{b['title']}』）", "",
           "> この指示書は物語の「構造」を定めます。人物の細部・場面・台詞・伏線の見せ方は、作者が CANON（設定書）で創作してください。",
           "> ここに書かれていないことは自由です。ここに書かれていることは守ってください。",
           f"> 生成：novel_studio／scaffold（{datetime.date.today().isoformat()}）", "", "---", "",
           "## 0. 作者の裁量と、その責任", "",
           "- 書かれていないことは作者の裁量です。予定調和にしないでください。",
           "- 足したもの（BRIEF に無い人物・土地・道具・風習・由来）は、CANON 第13節に一件一行で申告し、物語の中で筋を通してください。",
           "- 名前のある人物には、行動原理（欲しいもの／恐れているもの／その行動を取る理由）を持たせてください。駒として消費しないこと。"]
    if b.get("free"):
        out += [f"- この作品で特に任せること：{b['free']}"]
    out += ["", "---", "", "## 1. 作品要件", "", "| 項目 | 要件 |", "|---|---|",
            f"| 仮タイトル | {b['title']} |",
            f"| 一行要旨 | {b['logline']} |",
            f"| ジャンル | {b['genre']} |",
            f"| 想定読者 | {b['audience']} |",
            f"| 長さ | 全{n_ch}章 × 各約{L['chapter_target']}字（{L['chapter_min']}〜{L['chapter_max']}字）、目安 {n_ch * int(L['chapter_target'])} 字 |",
            f"| 語り | {b['narration']} |",
            f"| 結末の型 | {b['ending_type']} |",
            f"| 核 | {b['core']} |", ""]
    if b.get("genre_reason"):
        out += ["### 1-1. ジャンルの必然（なぜこのジャンルでなければならないか）", "", b["genre_reason"], ""]
    out += ["### 1-2. 舞台（固定する点）", "", b["setting"], "", "### 1-3. 人物（名前と骨格は固定。細部は CANON で作者が足してよい）", ""]
    for c in b["characters"]:
        out.append(f"- **{c.get('name', '')}**（{c.get('role', '')}）：{c.get('fixed', '')}")
    out += ["", "---", "", "## 2. 共通執筆ルール（全章で必ず守る）", "", "<!-- RULES:BEGIN -->",
            "### 2-1. 語りと視点", f"- {b['narration']}", "- 視点人物が見聞きしていないことは書かない。他人の内面を地の文で断定しない。", "",
            "### 2-2. 文体とトーン", f"- {rules.get('style') or '作者が CANON 第10節で決めた文体を最後まで変えない。'}", "",
            "### 2-3. 言葉づかい・表記", f"- {rules.get('language') or '会話は「」、心の声は（）、書かれた文字は『』。'}",
            "- 簡体字・繁体字・ローマ字を混ぜない。実在のブランド名・商品名・楽曲名は使わない。", "",
            "### 2-4. 事実の一貫性",
            "- CANON の不変条件・時間表・小道具と数値・世界の前提と、LEDGER の FACT に絶対に矛盾しない。",
            "- 前章の最後の場面から自然に続ける。章の冒頭で「いつ、どこで、誰が」を示す。", "",
            "### 2-5. 伏線の規律",
            "- 各章で指定された伏線の「設置／補強／回収」を必ず行う。回収予定の章より前に中身を明かさない（ほのめかしは良い）。",
            "- 本文中に「伏線」「スロット」「CANON」やスロットID（" + "、".join(s["id"] for s in b["slots"]) + "）などの管理用語を絶対に書かない。", "",
            "### 2-6. 人物の規律", f"- {rules.get('characters') or '名前のある人物は、CANON 第4節の行動原理に沿って動かす。'}", "",
            "### 2-7. 越えてはならない線（どんなに面白くても書かない）"]
    out += [f"- {x}" for x in lines] or ["- （特になし）"]
    out += ["", "### 2-8. 出力形式",
            "- 1行目に `# 第N章　章題`（Nは半角数字）。2行目以降が本文。",
            "- 場面転換は、空行のあとに `＊` だけの行を置く。",
            "- 本文以外（前置き、あとがき、解説、要約、箇条書き、コードブロック）は一切書かない。章の最後は必ず文として完結させる。",
            "<!-- RULES:END -->", "", "---", "", f"## 3. 章立て（{n_ch}章）", "",
            "各章の「機能」は必須です。場面・出来事の中身は作者が決めます。", "",
            "| 章 | 仮称 | 目標字数 | 緊張度(1-5) | 明かす度合い(0-5) |", "|---|---|---|---|---|"]
    for c in b["chapters"]:
        out.append(f"| {c['n']} | {c.get('title', '')} | {c.get('target', L['chapter_target'])} | {c.get('tension', 3)} | {c.get('reveal', 2)} |")
    out.append("")
    for c in b["chapters"]:
        n = int(c["n"])
        out += [f"<!-- ROLE:{nn(n)} -->", f"**第{n}章　{c.get('title', '')}**", f"- 機能：{c['function']}",
                f"- 伏線：{chapter_slot_line(b, n)}"]
        if c.get("hook"):
            out.append(f"- 章末の引き：{c['hook']}")
        if c.get("forbidden"):
            out.append(f"- 禁止：{c['forbidden']}")
        out += [f"<!-- /ROLE:{nn(n)} -->", ""]
    out += ["---", "", "## 4. 伏線スロット", "", "スロットは「機能」だけを定義しています。**中身（何を、どう見せるか）は作者が創作し、CANON 第7節に記入してください。**", "",
            "### 4-1. スロット定義", "", "| ID | 名前 | 機能 |", "|---|---|---|"]
    for s in b["slots"]:
        out.append(f"| {s['id']} | {s['name']} | {s.get('function', '')} |")
    out += ["", "### 4-2. 配置表（章指定）", "", "| ID | 設置 | 補強 | 回収 |", "|---|---|---|---|"]
    for s in b["slots"]:
        pl = parse_plan(s["plan"])
        col = lambda e: "・".join(str(c) for c, x in pl if x == e) or "—"
        out.append(f"| {s['id']} | {col('planted')} | {col('reinforced')} | {col('recovered')} |")
    if b.get("fairplay"):
        out += ["", "### 4-3. フェアプレイ条件"] + [f"- {x}" for x in b["fairplay"]]
    out += ["", "---", "", "## 5. 結末の方向性", "", b["ending"] if isinstance(b["ending"], str) else "\n".join(f"- {x}" for x in b["ending"]), "",
            "---", "", "## 6. 第0フェーズ：設計（本文を書く前に）", "",
            "本文を書く前に、CANON テンプレートの全項目を埋めて **CANON（設定書）** を作ります。CANON は確定後、変更できません。", ""]
    notes = b.get("design_notes") or []
    out += [f"{i}. {x}" for i, x in enumerate(notes, 1)] or ["1. 物語の仕組みと結末から決め、章カードはそこから逆算する。"]
    out += [f"{len(notes) + 1}. 世界の前提（音・灯り・距離・時刻・体・数え方・屋内外）を CANON 第6節に決める。",
            f"{len(notes) + 2}. 本文で使う固有名詞・数値・日付は、すべて CANON で一度だけ固定する。", ""]
    return "\n".join(out)


def canon_template(b, canon_max):
    t = (ENGINE / "templates" / "CANON_TEMPLATE.md").read_text(encoding="utf-8")
    slots = []
    for s in b["slots"]:
        parts = [f"{s['id']}（{s['name']}）中身：（記入）"]
        for c, e in parse_plan(s["plan"]):
            parts.append(f"{EV_JA[e]}(第{c}章)：（記入）")
        slots.append("- " + "／".join(parts))
    cards = []
    for c in b["chapters"]:
        n = int(c["n"])
        cards += [f"<!-- CARD:{nn(n)} -->", f"### 第{n}章「（記入）」", "- 時期と場所：（記入）", "- 視点人物：（記入）",
                  "- 場面（2〜4）：（記入）", "- 伏線の扱い：（記入）", "- 章末の引き：（記入）", "- まだ明かさないこと：（記入）",
                  f"<!-- /CARD:{nn(n)} -->", ""]
    return (t.replace("{{SLOTS}}", "\n".join(slots)).replace("{{CARDS}}", "\n".join(cards).rstrip())
             .replace("{{CANON_MAX}}", str(canon_max)))


def default_config(b, roles=None, budget=None):
    L = b["length"]
    n_ch = int(L["chapters"])
    cfg = json.loads((ENGINE / "config.default.json").read_text(encoding="utf-8"))
    cfg["chapters"] = n_ch
    cfg["chapter_chars"] = {"target": int(L["chapter_target"]), "min": int(L["chapter_min"]), "max": int(L["chapter_max"])}
    cfg["total_chars"] = {"min": int(n_ch * int(L["chapter_min"]) * 0.95), "max": int(n_ch * int(L["chapter_max"]))}
    cfg["canon_max_chars"] = max(12000, min(40000, 6000 + n_ch * 1500))
    cfg["slot_names"] = {s["id"]: s["name"] for s in b["slots"]}
    cfg["slot_plan"] = {s["id"]: parse_plan(s["plan"]) for s in b["slots"]}
    cfg["audience"] = b.get("audience", "")
    cfg["guard"]["banned"] = list(b.get("banned") or [])
    cfg["publish"]["fallback_title"] = b["title"]
    for role, spec in (roles or {}).items():
        if role in cfg["llm"]["profiles"] and spec:
            if isinstance(spec, str):
                spec = {"model": spec}
            cfg["llm"]["profiles"][role].update({k: v for k, v in spec.items() if v not in (None, "")})
    if budget:
        cfg["budget_usd"] = float(budget)
    return cfg


def create(b, dest, roles=None, budget=None, extra_top=()):
    res = lint(b)
    if not res["ok"]:
        return {"ok": False, **res}
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / "state" / "progress.json").exists():
        return {"ok": False, "errors": [f"{dest} はすでに進行中の作品です（state/progress.json あり）"], "warnings": []}
    for d in ("tools", "prompts"):
        if (dest / d).exists():
            shutil.rmtree(dest / d)
        shutil.copytree(ENGINE / d, dest / d, ignore=shutil.ignore_patterns("__pycache__", "scaffold.py"))
    (dest / "templates").mkdir(exist_ok=True)
    shutil.copy(ENGINE / "templates" / "LEDGER_TEMPLATE.md", dest / "templates" / "LEDGER_TEMPLATE.md")
    shutil.copy(ENGINE / "AGENTS.md", dest / "AGENTS.md")
    cfg = default_config(b, roles, budget)
    cfg["guard"]["allowed_top"] = sorted(set(cfg["guard"]["allowed_top"]) | set(extra_top))
    (dest / "templates" / "CANON_TEMPLATE.md").write_text(canon_template(b, cfg["canon_max_chars"]), encoding="utf-8")
    (dest / "BRIEF.md").write_text(render(b), encoding="utf-8")
    (dest / "brief.json").write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    (dest / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    py = sys.executable or "python3"
    r1 = subprocess.run([py, "tools/novelctl.py", "init"], cwd=dest, capture_output=True, text=True)
    r2 = subprocess.run([py, "tools/guard.py", "seal"], cwd=dest, capture_output=True, text=True)
    sha = hashlib.sha256((dest / "BRIEF.md").read_bytes()).hexdigest()[:12]
    return {"ok": r1.returncode == 0 and r2.returncode == 0, "errors": [x for x in (r1.stderr.strip(), r2.stderr.strip()) if x],
            "warnings": res["warnings"], "path": str(dest), "brief_sha": sha,
            "log": (r1.stdout + r2.stdout).strip()}


def upgrade(dest):
    """作りかけの作品の tools/ だけを、いまの engine の版に入れ替える（プロンプト・テンプレート・BRIEF・CANON は変えない）。
    保護ファイルの指紋を取り直し、notes/INTERVENTIONS.md に記録する。人の操作として行う。"""
    dest = Path(dest)
    if not (dest / "tools" / "novelctl.py").exists():
        return {"ok": False, "errors": [f"{dest} は作品フォルダではありません"]}
    changed = []
    for f in sorted((ENGINE / "tools").glob("*.py")):
        if f.name == "scaffold.py":
            continue
        old = dest / "tools" / f.name
        if not old.exists() or old.read_bytes() != f.read_bytes():
            shutil.copy(f, old)
            changed.append(f.name)
    if changed:
        py = sys.executable or "python3"
        subprocess.run([py, "tools/guard.py", "seal"], cwd=dest, capture_output=True, text=True)
        (dest / "notes").mkdir(exist_ok=True)
        with open(dest / "notes" / "INTERVENTIONS.md", "a", encoding="utf-8") as fp:
            fp.write(f"- {datetime.datetime.now().isoformat(timespec='seconds')} 道具を最新にした（tools/ の {', '.join(changed)}）。"
                     "プロンプト・テンプレート・BRIEF・CANON は変えていない。保護ファイルの指紋を取り直した。\n")
    return {"ok": True, "changed": changed}


def main(argv):
    if len(argv) >= 2 and argv[0] == "upgrade":
        r = upgrade(argv[1])
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 2
    if len(argv) < 2 or argv[0] not in ("lint", "render", "create"):
        print(__doc__)
        return 1
    b = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    if argv[0] == "lint":
        r = lint(b)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 2
    if argv[0] == "render":
        r = lint(b)
        if not r["ok"]:
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 2
        print(render(b))
        return 0
    roles = json.loads(Path(argv[argv.index("--roles") + 1]).read_text(encoding="utf-8")) if "--roles" in argv else None
    budget = argv[argv.index("--budget") + 1] if "--budget" in argv else None
    r = create(b, argv[2], roles, budget)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
