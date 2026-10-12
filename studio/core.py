"""core — studio の工程（テーマ → 提案 → 分類 → BRIEF → Fix → CANON → 執筆 → 仕上げ）。

Web 画面（server.py）とハーネス向けの CLI（cli.py）の両方がこのモジュールだけを呼ぶ。
状態はすべて works/<slug>/studio.json とその下のファイルにあり、会話や画面の記憶に依存しない。
Fix のあとは、作品フォルダそのものが engine（tools/novelctl.py）の作業場所になる。
"""
import concurrent.futures as cf
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORKS = Path(os.environ.get("NOVEL_WORKS_DIR", REPO / "works"))
ENGINE = REPO / "engine"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ENGINE / "tools"))
import llm  # noqa: E402
import similarity  # noqa: E402
import scaffold  # noqa: E402

PROPOSAL_FIELDS = ["kind", "genre", "title", "setting", "synopsis", "highlight", "appeal", "must_keep", "genre_reason"]
LOCKS = {}
LOCKS_GUARD = threading.Lock()
SETTINGS_FILE = HERE / "settings.json"
DEFAULT_SETTINGS = {
    "propose_models": ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna-pro", "deepseek/deepseek-v4.1-flash"],
    "skeleton_model": "openai/gpt-6-luna-pro",
    "brief_model": "anthropic/claude-opus-5.5",
    "roles": {"writer": "anthropic/claude-haiku-5.5", "reviewer": "openai/gpt-6-luna-pro",
              "polisher": "anthropic/claude-haiku-5.5", "premium": "anthropic/claude-opus-5.5"},
    "threshold": 0.5,
}


class StudioError(Exception):
    pass


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def settings():
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    return s


def lock(slug):
    with LOCKS_GUARD:
        return LOCKS.setdefault(slug, threading.RLock())


def wdir(slug):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", slug or ""):
        raise StudioError(f"作品の名前「{slug}」は英数字・-・_ の60字以内にしてください")
    return WORKS / slug


def load(slug):
    f = wdir(slug) / "studio.json"
    if not f.exists():
        raise StudioError(f"作品 {slug} がありません")
    return json.loads(f.read_text(encoding="utf-8"))


def save(slug, st):
    f = wdir(slug) / "studio.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def record_cost(slug, stage, rec, note=""):
    d = wdir(slug) / "studio"
    d.mkdir(exist_ok=True)
    with open(d / "usage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), "stage": stage, "note": note, **rec}, ensure_ascii=False) + "\n")


def costs(slug):
    """studio の費用（工程ごと）と engine の費用（役ごと）。"""
    out = {"studio": {}, "engine": {}, "studio_usd": 0.0, "engine_usd": 0.0}
    f = wdir(slug) / "studio" / "usage.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b = out["studio"].setdefault(r["stage"], {"usd": 0.0, "calls": 0})
            b["usd"] += float(r.get("usd") or 0)
            b["calls"] += 1
            out["studio_usd"] += float(r.get("usd") or 0)
    g = wdir(slug) / "logs" / "usage.jsonl"
    if g.exists():
        for line in g.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b = out["engine"].setdefault(r.get("role", "?"), {"usd": 0.0, "calls": 0})
            b["usd"] += float(r.get("cost") or 0)
            b["calls"] += 1
            out["engine_usd"] += float(r.get("cost") or 0)
    for grp in (out["studio"], out["engine"]):
        for b in grp.values():
            b["usd"] = round(b["usd"], 5)
    out["studio_usd"] = round(out["studio_usd"], 5)
    out["engine_usd"] = round(out["engine_usd"], 5)
    out["total_usd"] = round(out["studio_usd"] + out["engine_usd"], 5)
    return out


def prompt(name, **kw):
    t = (HERE / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    for k, v in kw.items():
        t = t.replace("{{" + k + "}}", str(v))  # .format は JSON の { } で壊れるので使わない（Novel_4 の教訓）
    return t


# ---------------------------------------------------------------- テーマ
THEME_FIELDS = {"genre": "ジャンル", "theme": "テーマ・題材", "length": "長さ", "audience": "想定読者", "ending": "結末の型",
                "must": "必ず入れたいこと", "must_not": "入れたくないこと", "mood": "雰囲気・参考", "note": "そのほか"}


def theme_text(theme):
    lines = [f"- {label}：{theme[k]}" for k, label in THEME_FIELDS.items() if str(theme.get(k, "")).strip()]
    ch = theme.get("chapters")
    if ch:
        lines.append(f"- 章の数の目安：{ch}章（1章 {theme.get('chapter_target', 3000)} 字前後）")
    return "\n".join(lines) or "- （指定なし。自由に）"


def create_work(slug, theme):
    d = wdir(slug)
    if (d / "studio.json").exists():
        raise StudioError(f"作品 {slug} はすでにあります")
    d.mkdir(parents=True, exist_ok=True)
    st = {"slug": slug, "created": now(), "theme": theme, "theme_version": 1, "rounds": [], "proposals": {},
          "classify": None, "selected": None, "briefs": [], "fixed": None, "next_pid": 1, "log": [f"{now()} 作品を作成"]}
    save(slug, st)
    return st


def list_works():
    out = []
    if WORKS.exists():
        for d in sorted(WORKS.iterdir()):
            f = d / "studio.json"
            if f.exists():
                st = json.loads(f.read_text(encoding="utf-8"))
                out.append({"slug": d.name, "title": (st["theme"].get("theme") or "")[:40], "created": st["created"],
                            "stage": stage_of(st, d), "fixed": bool(st.get("fixed"))})
    return out


def change_theme(slug, theme):
    """テーマを変える＝最初からやり直す。これまでの提案・分類・BRIEF は studio/history/ に退避する。"""
    with lock(slug):
        st = load(slug)
        if st.get("fixed"):
            raise StudioError("Fix 済みの作品はテーマを変えられません。新しい作品として作ってください")
        h = wdir(slug) / "studio" / "history"
        h.mkdir(parents=True, exist_ok=True)
        (h / f"theme_v{st['theme_version']}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        st.update({"theme": theme, "theme_version": st["theme_version"] + 1, "rounds": [], "proposals": {}, "classify": None,
                   "selected": None, "briefs": []})
        st["log"].append(f"{now()} テーマを変更（v{st['theme_version']}）。前のテーマの記録は studio/history/")
        save(slug, st)
        return st


# ---------------------------------------------------------------- 提案
def _check_props(obj, n):
    if not isinstance(obj, dict) or not isinstance(obj.get("proposals"), list) or not obj["proposals"]:
        return "proposals の配列がない"
    for i, p in enumerate(obj["proposals"]):
        miss = [f for f in ("title", "synopsis", "setting") if not str(p.get(f, "")).strip()]
        if miss:
            return f"{i + 1}件目に {', '.join(miss)} がない"
    return None


def _call_json(slug, stage, model, text, check, temperature=0.7, note="", reasoning=None, max_tokens=16000):
    """一回だけ書式のやり直しを求める（compe の規則）。費用は失敗も含めて記録する。"""
    last = None
    for attempt in (1, 2):
        try:
            raw, rec = llm.chat(model, text, temperature=temperature, reasoning=reasoning, max_tokens=max_tokens)
        except llm.LLMError as e:
            raise StudioError(f"{model}: {e}")
        record_cost(slug, stage, rec, note)
        obj = llm.extract_json(raw)
        err = check(obj)
        if not err:
            return obj, rec
        last = err
        text = text + f"\n\n# 前回の出力の問題\n{err}。指定の JSON の形だけで、もう一度返してください。"
    raise StudioError(f"{model}: 書式どおりの JSON が返らない（{last}）")


def propose(slug, models, n=2, instruction="", mode="new", temperature=0.8, avoid=None):
    """models それぞれに n 案ずつ出させる（並列）。mode = new | resubmit | rolling。
    rolling：分類で「収束」になった骨格を『避ける骨格』として渡し、その外の案だけを求める（ローリング／合宿方式）。"""
    st = load(slug)
    if st.get("fixed"):
        raise StudioError("Fix 済みです")
    n = max(1, min(int(n), 4))
    kind_rule = "本線と考える案を1つ以上と、まったく違う観点の案を1つ以上" if n >= 2 else "本線と考える案を1つ"
    extra = []
    if instruction.strip():
        extra.append(f"- プロデューサーからの追加の指示：{instruction.strip()}")
    if mode == "rolling":
        av = avoid if avoid is not None else avoid_list(st)
        if av:
            extra.append("- 次の骨格に寄った案は出さないでください（すでに何度も出た型です）。骨格（誰が欺く・何を隠す・どの層で驚かせる・何がひっくり返る）を一切まねないこと。\n"
                         "  避ける骨格：\n" + "\n".join(f"  - {x}" for x in av))
    text = prompt("propose", THEME=theme_text(st["theme"]), N=n, KIND_RULE=kind_rule, EXTRA="\n".join(extra))
    rid = f"r{len(st['rounds']) + 1}"
    results, errors = {}, {}

    def one(m):
        obj, rec = _call_json(slug, "propose", m, text, lambda o: _check_props(o, n), temperature=temperature, note=rid)
        return obj["proposals"][:n], rec
    with cf.ThreadPoolExecutor(max_workers=min(8, len(models) or 1)) as ex:
        futs = {ex.submit(one, m): m for m in models}
        for f in cf.as_completed(futs):
            m = futs[f]
            try:
                results[m] = f.result()
            except StudioError as e:
                errors[m] = str(e)
    with lock(slug):
        st = load(slug)
        pids = []
        for m in models:
            if m not in results:
                continue
            props, rec = results[m]
            each = round(rec["usd"] / max(1, len(props)), 6)
            for p in props:
                pid = f"P{st['next_pid']:02d}"
                st["next_pid"] += 1
                st["proposals"][pid] = {"id": pid, "round": rid, "model": m, "usd": each, "sec": rec["sec"], "origin": "model",
                                        **{f: str(p.get(f, "")) for f in PROPOSAL_FIELDS}}
                pids.append(pid)
        st["rounds"].append({"id": rid, "ts": now(), "models": models, "n": n, "mode": mode, "instruction": instruction,
                             "proposals": pids, "errors": errors, "theme_version": st["theme_version"]})
        st["log"].append(f"{now()} 提案 {rid}（{mode}）：{len(pids)}案、失敗 {len(errors)}モデル")
        save(slug, st)
    return {"round": rid, "proposals": pids, "errors": errors}


def avoid_list(st):
    c = st.get("classify") or {}
    out = []
    for cl in c.get("clusters", []):
        if cl.get("label") == "収束":
            out += [x for x in cl.get("one_lines", []) if x]
            if cl.get("type"):
                out.append(cl["type"])
    return list(dict.fromkeys(out))[:12]


def edit_proposal(slug, pid, fields):
    """人が案を直接直す。元の案は残し、直した版を新しい案として足す（どちらを選んだかを追えるように）。"""
    with lock(slug):
        st = load(slug)
        src = st["proposals"].get(pid)
        if not src:
            raise StudioError(f"{pid} がありません")
        new = f"P{st['next_pid']:02d}"
        st["next_pid"] += 1
        st["proposals"][new] = {**src, **{k: str(v) for k, v in fields.items() if k in PROPOSAL_FIELDS},
                                "id": new, "origin": "human", "parent": pid, "usd": 0.0, "round": src["round"]}
        st["log"].append(f"{now()} {pid} を人が編集 → {new}")
        save(slug, st)
        return new


def revise_proposal(slug, pid, model, instruction):
    st = load(slug)
    src = st["proposals"].get(pid)
    if not src:
        raise StudioError(f"{pid} がありません")
    body = json.dumps({f: src.get(f, "") for f in PROPOSAL_FIELDS}, ensure_ascii=False, indent=1)
    text = prompt("revise", THEME=theme_text(st["theme"]), PROPOSAL=body, INSTRUCTION=instruction)

    def chk(o):
        return None if isinstance(o, dict) and isinstance(o.get("proposal"), dict) and o["proposal"].get("synopsis") else "proposal がない"
    obj, rec = _call_json(slug, "revise", model, text, chk, temperature=0.6, note=pid)
    with lock(slug):
        st = load(slug)
        new = f"P{st['next_pid']:02d}"
        st["next_pid"] += 1
        st["proposals"][new] = {**{f: str(obj["proposal"].get(f, src.get(f, ""))) for f in PROPOSAL_FIELDS}, "id": new,
                                "round": src["round"], "model": model, "usd": rec["usd"], "sec": rec["sec"], "origin": "revise",
                                "parent": pid, "instruction": instruction, "changes": str(obj.get("changes", ""))}
        st["log"].append(f"{now()} {pid} を {model} で改稿 → {new}")
        save(slug, st)
    return new


def active_proposals(st):
    return [p for p in st["proposals"].values() if not p.get("hidden")]


def hide_proposal(slug, pid, hidden=True):
    with lock(slug):
        st = load(slug)
        st["proposals"][pid]["hidden"] = hidden
        save(slug, st)


# ---------------------------------------------------------------- 分類
def _props_block(props):
    return "\n\n".join(f"### {p['id']}\n" + "\n".join(f"{f}: {p.get(f, '')}" for f in PROPOSAL_FIELDS if p.get(f)) for p in props)


def classify(slug, model=None, method="tool", threshold=None, w_text=0.4, refresh_skeletons=False):
    """method = tool（骨格を抜き出してルールで分類。既定）| ai（モデルに読ませて分類）| both。"""
    st = load(slug)
    props = active_proposals(st)
    if len(props) < 2:
        raise StudioError("分類するには案が2つ以上必要です")
    model = model or settings()["skeleton_model"]
    threshold = float(threshold if threshold is not None else settings()["threshold"])
    prev = st.get("classify") or {}
    sk = {} if refresh_skeletons else dict(prev.get("skeletons", {}))
    result = {"ts": now(), "method": method, "model": model, "skeletons": sk}
    need = [p for p in props if p["id"] not in sk]
    if method in ("tool", "both") and need:
        ids = [p["id"] for p in need]

        def chk(o):
            got = {s.get("id") for s in (o or {}).get("skeletons", []) if isinstance(s, dict)}
            miss = [i for i in ids if i not in got]
            return f"{', '.join(miss)} の骨格がない" if miss else None
        obj, rec = _call_json(slug, "skeleton", model, prompt("skeleton", PROPOSALS=_props_block(need)), chk, temperature=0.1)
        for s in obj["skeletons"]:
            if s.get("id") in ids:
                sk[s["id"]] = {k: s.get(k, "") for k in similarity.SK_FIELDS + ["one_line"]}
    if method in ("tool", "both"):
        result.update(similarity.classify(props, sk, threshold, w_text))
    if method in ("ai", "both"):
        def chk2(o):
            return None if isinstance(o, dict) and isinstance(o.get("labels"), list) else "labels がない"
        obj, rec = _call_json(slug, "classify", model, prompt("classify", PROPOSALS=_props_block(props)), chk2, temperature=0.1)
        result["ai"] = {"labels": {x["id"]: {"label": x.get("label"), "reason": x.get("reason", "")} for x in obj["labels"] if x.get("id")},
                        "types": obj.get("types", [])}
        if method == "ai":
            result["labels"] = {k: {"label": v["label"], "cluster": None, "nearest": None, "sim": None} for k, v in result["ai"]["labels"].items()}
            result["clusters"] = []
    with lock(slug):
        st = load(slug)
        st["classify"] = result
        st["log"].append(f"{now()} 分類（{method}、しきい値 {threshold}）")
        save(slug, st)
    return result


def reclassify(slug, threshold, w_text=0.4):
    """骨格は抜き出し済みのものを使い、しきい値だけ変えて分類し直す（モデルを呼ばない）。"""
    st = load(slug)
    c = st.get("classify")
    if not c or not c.get("skeletons"):
        raise StudioError("先に分類（tool）を実行してください")
    props = [p for p in active_proposals(st) if p["id"] in c["skeletons"]]
    c.update(similarity.classify(props, c["skeletons"], float(threshold), float(w_text)))
    with lock(slug):
        st = load(slug)
        st["classify"] = c
        save(slug, st)
    return c


# ---------------------------------------------------------------- BRIEF
def select(slug, pid):
    with lock(slug):
        st = load(slug)
        if st.get("fixed"):
            raise StudioError("Fix 済みです")
        if pid not in st["proposals"]:
            raise StudioError(f"{pid} がありません")
        st["selected"] = pid
        st["log"].append(f"{now()} {pid} を選択")
        save(slug, st)


def _length(st):
    t = st["theme"]
    ch = int(t.get("chapters") or 8)
    tgt = int(t.get("chapter_target") or 3000)
    return ch, tgt, int(tgt * 0.8), int(tgt * 1.5)


def _brief_check(ch):
    def chk(o):
        b = (o or {}).get("brief")
        if not isinstance(b, dict):
            return "brief がない"
        r = scaffold.lint(b)
        # 形の誤りだけはやり直させる。中身の lint は人に見せる
        hard = [e for e in r["errors"] if "がありません" in e or "chapters が" in e]
        return "；".join(hard[:5]) if hard else None
    return chk


def make_brief(slug, model=None, instruction="", base_version=None):
    """選んだ案から BRIEF を作る（base_version があれば、その版に指示を足して改訂する）。"""
    st = load(slug)
    if st.get("fixed"):
        raise StudioError("Fix 済みです")
    model = model or settings()["brief_model"]
    ch, tgt, lo, hi = _length(st)
    if base_version:
        base = next((b for b in st["briefs"] if b["v"] == int(base_version)), None)
        if not base:
            raise StudioError(f"BRIEF v{base_version} がありません")
        lint_block = ""
        if base.get("lint", {}).get("errors"):
            lint_block = "\n<資料 名前=\"機械検査の指摘（これも直す）\">\n" + "\n".join(f"- {e}" for e in base["lint"]["errors"]) + "\n</資料>\n"
        text = prompt("brief_revise", THEME=theme_text(st["theme"]), BRIEF=json.dumps(base["brief"], ensure_ascii=False, indent=1),
                      INSTRUCTION=instruction or "機械検査の指摘を直す", LINT_BLOCK=lint_block, CHAPTERS=base["brief"].get("length", {}).get("chapters", ch))
        pid = base["from"]
        stage = "brief-revise"
    else:
        pid = st.get("selected")
        if not pid:
            raise StudioError("先に案を選んでください")
        p = st["proposals"][pid]
        ib = f"\n<資料 名前=\"プロデューサーの追加の指示\">\n{instruction}\n</資料>\n" if instruction.strip() else ""
        text = prompt("brief", THEME=theme_text(st["theme"]), PROPOSAL=json.dumps({f: p.get(f, "") for f in PROPOSAL_FIELDS}, ensure_ascii=False, indent=1),
                      INSTRUCTION_BLOCK=ib, CHAPTERS=ch, TARGET=tgt, MINC=lo, MAXC=hi, AUDIENCE=st["theme"].get("audience") or "一般読者")
        stage = "brief"
    obj, rec = _call_json(slug, stage, model, text, _brief_check(ch), temperature=0.5, max_tokens=32000, reasoning="medium")
    return _add_brief(slug, obj["brief"], pid, model, instruction, rec["usd"], base_version, str(obj.get("changes", "")))


def _add_brief(slug, b, pid, model, instruction, usd, base_version=None, changes="", origin="model"):
    with lock(slug):
        st = load(slug)
        v = max([x["v"] for x in st["briefs"]] or [0]) + 1
        st["briefs"].append({"v": v, "ts": now(), "from": pid, "model": model, "instruction": instruction, "base": base_version,
                             "usd": usd, "origin": origin, "changes": changes, "brief": b, "lint": scaffold.lint(b)})
        st["log"].append(f"{now()} BRIEF v{v}（{origin}{'、v' + str(base_version) + ' から' if base_version else ''}）")
        save(slug, st)
    return v


def edit_brief(slug, v, brief):
    """人が BRIEF を直接直す。新しい版として保存する。"""
    st = load(slug)
    if st.get("fixed"):
        raise StudioError("Fix 済みです")
    base = next((b for b in st["briefs"] if b["v"] == int(v)), None)
    if not base:
        raise StudioError(f"BRIEF v{v} がありません")
    if isinstance(brief, str):
        try:
            brief = json.loads(brief)
        except ValueError as e:
            raise StudioError(f"JSON として読めません：{e}")
    return _add_brief(slug, brief, base["from"], "human", "人が編集", 0.0, v, origin="human")


def render_brief(slug, v):
    st = load(slug)
    b = next((x for x in st["briefs"] if x["v"] == int(v)), None)
    if not b:
        raise StudioError(f"BRIEF v{v} がありません")
    r = scaffold.lint(b["brief"])
    md = scaffold.render(b["brief"]) if r["ok"] else None
    return {"lint": r, "markdown": md}


# ---------------------------------------------------------------- Fix と engine
def fix(slug, v, roles=None, budget=None):
    """提案と BRIEF を確定し、作品フォルダを engine の作業場所にする。ここから先は BRIEF を変えない。"""
    with lock(slug):
        st = load(slug)
        if st.get("fixed"):
            raise StudioError("すでに Fix 済みです")
        b = next((x for x in st["briefs"] if x["v"] == int(v)), None)
        if not b:
            raise StudioError(f"BRIEF v{v} がありません")
        roles = roles or settings()["roles"]
        r = scaffold.create(b["brief"], wdir(slug), roles, budget, extra_top=("studio", "studio.json"))
        if not r["ok"]:
            raise StudioError("Fix できません：" + "；".join(r["errors"]))
        st["fixed"] = {"ts": now(), "brief_v": int(v), "proposal": b["from"], "roles": roles, "budget": budget,
                       "brief_sha": r["brief_sha"]}
        st["selected"] = b["from"]
        st["log"].append(f"{now()} Fix：{b['from']} と BRIEF v{v}（{r['brief_sha']}）")
        save(slug, st)
        return st["fixed"]


def novelctl(slug, *args, env_extra=None, timeout=3600):
    d = wdir(slug)
    if not (d / "tools" / "novelctl.py").exists():
        raise StudioError("まだ Fix されていません")
    env = os.environ.copy()
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, "tools/novelctl.py", *args], cwd=d, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=timeout)
    return {"rc": r.returncode, "out": r.stdout[-6000:], "err": r.stderr[-3000:]}


def engine_status(slug):
    r = novelctl(slug, "status", "--json", timeout=60)
    try:
        return json.loads(r["out"])
    except ValueError:
        raise StudioError("status を読めません：" + (r["err"] or r["out"])[-500:])


def canon_fingerprint(slug):
    f = wdir(slug) / "canon" / "CANON.md"
    return hashlib.sha256(f.read_bytes()).hexdigest()[:8] if f.exists() else None


def approve(slug, fingerprint):
    """人が画面で CANON を読み、指紋を確かめて押したときだけ承認する。"""
    fp = canon_fingerprint(slug)
    if not fp or fingerprint != fp:
        raise StudioError("指紋が一致しません（CANON が変わった可能性）。読み直してから承認してください")
    return novelctl(slug, "approve-canon", env_extra={"NOVEL_STUDIO_APPROVE": fp})


ENGINE_ACTIONS = {
    "start": lambda a: ["start"],
    "step": lambda a: ["step"],
    "redesign": lambda a: ["redesign", "--feedback", a["feedback"]],
    "rescue_canon": lambda a: ["rescue", "canon"],
    "rescue_chapter": lambda a: ["rescue", "chapter", str(int(a["n"]))],
    "resolve": lambda a: ["resolve"] + [f"--{x}" for x in a.get("flags", []) if x in ("retry", "accept", "relock", "done", "ledger-done")],
    "reset_chapter": lambda a: ["reset-chapter", str(int(a["n"]))],
    "polish": lambda a: ["start", "polish"],
    "proofread": lambda a: ["proofread"] + [str(int(x)) for x in a.get("chapters", [])],
    "assemble": lambda a: ["assemble"],
    "epub": lambda a: ["epub"] + (["--proofread"] if a.get("proofread") else []),
}


def engine_action(slug, action, args=None):
    args = args or {}
    if action == "apply_proofread":
        d = wdir(slug)
        r = subprocess.run([sys.executable, "tools/proofread_apply.py"], cwd=d, capture_output=True, text=True)
        return {"rc": r.returncode, "out": r.stdout, "err": r.stderr}
    if action not in ENGINE_ACTIONS:
        raise StudioError(f"不明な操作 {action}")
    return novelctl(slug, *ENGINE_ACTIONS[action](args))


def set_edits(slug, enabled):
    """校正案の採否（{index: bool}）を output/proofread_edits.json に書く。"""
    f = wdir(slug) / "output" / "proofread_edits.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    for k, v in enabled.items():
        i = int(k)
        if 0 <= i < len(d["edits"]) and not d["edits"][i].get("rejected"):
            d["edits"][i]["enabled"] = bool(v)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


SAFE_READ = re.compile(r"^(BRIEF\.md|brief\.json|config\.json|canon/CANON\.md|chapters/ch\d\d\.md|polished/ch\d\d\.md|work/ch\d\d_(feedback\.md|verdict_a\d+\.json|draft\.md)|"
                       r"work/canon_feedback\.md|work/canon_review_notes\.md|state/LEDGER\.md|output/[^/]+\.(md|json|epub)|logs/run\.(log|out)|notes/[^/]+\.md)$")


def read_file(slug, rel):
    if not SAFE_READ.match(rel or ""):
        raise StudioError(f"読めない場所です：{rel}")
    f = wdir(slug) / rel
    if not f.exists():
        return None
    if rel.endswith(".epub"):
        return f
    t = f.read_text(encoding="utf-8", errors="replace")
    if rel.startswith("logs/"):
        t = "\n".join(t.splitlines()[-80:])
    return t


def stage_of(st, d=None):
    if st.get("fixed"):
        d = d or wdir(st["slug"])
        p = d / "state" / "progress.json"
        if p.exists():
            ph = json.loads(p.read_text(encoding="utf-8")).get("phase")
            return {"design": "canon", "canon_hold": "canon", "chapters": "write", "final": "write", "done": "finish"}.get(ph, "canon")
        return "canon"
    if st.get("briefs"):
        return "brief"
    if st.get("classify"):
        return "classify"
    if st.get("proposals"):
        return "propose"
    return "theme"


def snapshot(slug):
    """画面に渡す状態一式。"""
    st = load(slug)
    out = {**st, "stage": stage_of(st), "costs": costs(slug), "avoid": avoid_list(st)}
    for b in out["briefs"]:
        b["lint"] = scaffold.lint(b["brief"])
    if st.get("fixed"):
        try:
            out["engine"] = engine_status(slug)
        except StudioError as e:
            out["engine"] = {"error": str(e)}
        out["canon_fp"] = canon_fingerprint(slug)
        od = wdir(slug) / "output"
        out["outputs"] = sorted(p.name for p in od.iterdir()) if od.exists() else []
    return out
