#!/usr/bin/env python3
"""novelctl — 小説生成パイプラインの状態管理・パケット組み立て・検証ツール（novel_studio v1.0 エンジン）

進行役エージェント（Hermes / OpenCode / Goose など、シェルを実行できるものなら何でも）はこのツールだけを使って進める。
モデル呼び出しは tools/call_llm.py が OpenRouter へ直接行うため、ハーネスには依存しない。物語の状態はすべてディスク上にあり、
会話履歴に依存しない。どこで中断しても `status` → `step` で再開できる。

  python3 tools/novelctl.py init            初期化（最初に1回）
  python3 tools/novelctl.py status [--json] 現在地と次の一手を表示（--json は機械向け。studio とハーネスが使う）
  python3 tools/novelctl.py step            次の1ステップだけ実行（モデル呼び出しは最大2回）
  python3 tools/novelctl.py start           run をシェルから切り離して起動し、すぐ戻る（推奨）
  python3 tools/novelctl.py run [--max N]   完了かエスカレーションまで step を繰り返す（手前で実行し続ける）
  python3 tools/novelctl.py packet KIND [N] パケットを組み立てて保存だけする（手動確認用）
       KIND = design | canon-review | write | review | ledger | final
  python3 tools/novelctl.py check N         第N章の下書き（work/）または確定稿を機械検査
  python3 tools/novelctl.py approve-canon   pause_after_canon=true のとき、CANON確認後に再開（人が端末で。studio はボタンで）
  python3 tools/novelctl.py redesign (--feedback TEXT | --feedback-file F) [--reset]
                                            CANONに人の指示を足して設計をやり直す（設計中・承認待ち。--reset なら執筆中でも：
                                            本文を work/archive/ に退けて第1章から書き直す）
  python3 tools/novelctl.py canon-check [--model M]
                                            承認の前に、上位モデルが CANON 全体の矛盾を一度だけ探す（work/canon_check.json）。
                                            日付・曜日の機械検査（費用なし）は status --json の canon_lint にいつも出る
  python3 tools/novelctl.py canon-edit --file F [--reset-from N]
                                            人が直した CANON に差し替える（人の操作）。承認済みなら鍵をかけ直し、
                                            --reset-from N で第N章以降を未執筆に戻す
  python3 tools/novelctl.py rescue canon [--model M] [--note T]
                                            上位モデル（premium）が CANON 全体を一度で直す（審査が収束しないとき）
  python3 tools/novelctl.py rescue chapter N [--model M] [--note T]
                                            上位モデルが第N章の指摘箇所を全体の整合を取りながら直す（3稿で通らないとき、最終審査で落ちたとき）。
                                            --model：その一回だけ別のモデルで（指定の上位モデルで直らなかったとき）。--note：人の指示を足す
  python3 tools/novelctl.py proofread [N...]  上位モデルが全章（または指定章）を校正し output/proofread_edits.json に修正案を出す
  python3 tools/novelctl.py resolve [--retry]  エスカレーションを解除（--retry で試行回数をリセット）
  python3 tools/novelctl.py reset-chapter N 第N章以降を未執筆に戻す（LEDGERも巻き戻す）
  python3 tools/novelctl.py assemble        確定済みの章を output/final.md に結合
  python3 tools/novelctl.py epub [--proofread]  縦書きEPUBと評価ページ用の作品情報を output/ に作る
  python3 tools/novelctl.py cover [--model M] [--style simple|illust|photo] [--motif T] [--title-dir vertical|horizontal]
                                  [--font mincho|gothic|casual] [--author T] [--reuse-art]
                                            表紙を作る（画像生成モデルが文字のない絵を描き、題名と著者名はこちらで組む）
  python3 tools/novelctl.py polish [N] [--force]  完成稿の文体を polisher（Aion）で磨き polished/ に保存
  python3 tools/novelctl.py start polish    polish をシェルから切り離して起動（推奨）
  python3 tools/novelctl.py assemble --polished  polished/ の章を output/polished.md に結合

書き手が「人（またはClaude）」のとき: config.json の writer_cmd / reviewer_cmd を tools/inbox_llm.py にする。
  パケットは work/*.packet.md、答えは work/inbox/<パケット名から .packet.md を除いた名前>.md に置く。
  答えがない状態で step すると AWAITING_AUTHOR で止まる → 答えを置いて `resolve` → `step`。

終了コード: 0=前進 / 2=エスカレーション（人の判断が必要） / 3=完了 / 1=エラー

v1.0 の考え方：書く・照合するのは安いモデルで何度でも、全体の整合を直す・常識で確かめるのは上位モデル（premium）で一回だけ、
止めるかどうかは人が決める（Grauwall_06 の実績：上位モデルの出番は設計の修正2回・章の手直し5回・校正1回、残り約90回は安いモデル）。
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import guard  # noqa: E402  第3回の実験で追加：STOP・保護ファイル・越えてはならない線・引き写しの機械検査
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
N_CH = CFG["chapters"]
PROGRESS = ROOT / "state" / "progress.json"
LEDGER = ROOT / "state" / "LEDGER.md"
CANON = ROOT / "canon" / "CANON.md"
EV_JA = {"planted": "設置する", "reinforced": "補強する（読者に再提示する）", "recovered": "回収する"}
KANJI = "〇一二三四五六七八九十"


class Escalate(Exception):
    def __init__(self, code, detail, hint=""):
        super().__init__(detail)
        self.code, self.detail, self.hint = code, detail, hint


# ---------------------------------------------------------------- 基本
def P(*a):
    return ROOT.joinpath(*a)


def rd(path):
    return Path(path).read_text(encoding="utf-8")


def wr(path, s):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(s, encoding="utf-8")


def log(msg):
    P("logs").mkdir(exist_ok=True)
    with open(P("logs", "run.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def nn(n):
    return f"{int(n):02d}"


def count_chars(s):
    return len(re.sub(r"\s", "", s))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load():
    if not PROGRESS.exists():
        sys.exit("未初期化です。先に `python3 tools/novelctl.py init` を実行してください。")
    return json.loads(PROGRESS.read_text(encoding="utf-8"))


def save(p):
    wr(PROGRESS, json.dumps(p, ensure_ascii=False, indent=2))


def between(text, b, e):
    i = text.find(b)
    if i < 0:
        return None
    j = text.find(e, i + len(b))
    if j < 0:
        return None
    return text[i + len(b):j].strip()


# ---------------------------------------------------------------- 素材の切り出し
def rules():
    return between(rd(P("BRIEF.md")), "<!-- RULES:BEGIN -->", "<!-- RULES:END -->")


def role(n):
    return between(rd(P("BRIEF.md")), f"<!-- ROLE:{nn(n)} -->", f"<!-- /ROLE:{nn(n)} -->")


def canon():
    return rd(CANON)


def card(n):
    return between(canon(), f"<!-- CARD:{nn(n)} -->", f"<!-- /CARD:{nn(n)} -->")


def canon_core():
    return re.sub(r"<!-- CARD:(\d\d) -->.*?<!-- /CARD:\1 -->", "", canon(), flags=re.S).strip()


def events_for(n):
    return [(sid, ev) for sid, evs in CFG["slot_plan"].items() for c, ev in evs if c == n]


def recover_ch(sid):
    rc = [c for c, ev in CFG["slot_plan"][sid] if ev == "recovered"]
    return rc[0] if rc else None


def events_text(n):
    ev = events_for(n)
    if not ev:
        return "（この章で必須の伏線操作はない）"
    return "\n".join(f"- {sid}（{CFG['slot_names'][sid]}）：{EV_JA[e]}。中身と見せ方はCANONの第7節「{sid}」の行に従う。"
                     for sid, e in ev)


def hide_text(n):
    items = [(sid, recover_ch(sid)) for sid in CFG["slot_plan"] if recover_ch(sid) and recover_ch(sid) > n]
    if not items:
        return "（なし：すべて回収済み、または本章で回収）"
    # この章で補強する糸は、章カードに書かれた範囲だけは出してよい（2026-10-11：第14章で在りかを語る指定と食い違った）
    def line(sid, c):
        if any(ch == n and ev == "reinforced" for ch, ev in CFG["slot_plan"][sid]):
            return f"- {sid}（{CFG['slot_names'][sid]}）：この章では章カードに書かれた範囲だけ出してよい。回収（第{c}章）の場面は書かない"
        return f"- {sid}（{CFG['slot_names'][sid]}）の中身：第{c}章まで明かさない"
    return "\n".join(line(sid, c) for sid, c in items)


def ledger_sections(text):
    parts = re.split(r"^## CH(\d\d)\s*$", text, flags=re.M)
    secs = {}
    for k in range(1, len(parts), 2):
        secs[int(parts[k])] = parts[k + 1].strip()
    return parts[0], secs


def ledger_for_packet(n):
    _, secs = ledger_sections(rd(LEDGER))
    full = CFG["ledger_recent_full"]
    out = []
    for c in sorted(secs):
        if c >= n:
            continue
        body = secs[c]
        if c < n - full:  # 古い章は要約・事実・伏線状態だけに圧縮
            body = "\n".join(l for l in body.splitlines() if l.startswith(("SUMMARY:", "FACT:", "SLOT:")))
        out.append(f"## CH{nn(c)}\n{body}")
    return "\n\n".join(out) if out else "（まだ記録なし：これが最初の章）"


def chapter_body(text):
    lines = text.strip().splitlines()
    return "\n".join(lines[1:]).strip() if lines else ""


def prev_tail(n):
    if n <= 1:
        return "（なし：これが第1章。物語はここから始まる）"
    body = chapter_body(rd(P("chapters", f"ch{nn(n - 1)}.md")))
    k = CFG["prev_tail_chars"]
    return ("……" + body[-k:]) if len(body) > k else body


def fill(tpl, n=None):
    c = CFG["chapter_chars"]
    rep = {"TARGET": c["target"], "MINC": c["min"], "MAXC": c["max"], "CHAPTERS": N_CH,
           "CANON_MAX": CFG["canon_max_chars"]}
    if n is not None:
        rep.update({"N": n, "NN": nn(n), "EVENTS": events_text(n), "HIDE": hide_text(n)})
    for k, v in rep.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def given_secs():
    """config.json の given_chapters（Claude が先に書いた章）を、設計と CANON 審査のパケットに差し込む（第6回。2026-10-11）。"""
    s = ""
    for i in CFG.get("given_chapters", []):
        f = P("chapters", f"ch{nn(i)}.md")
        if f.exists():
            s += sec(f"既に書かれた章（第{i}章。変更できない）", rd(f))
    return s


def series_secs():
    """config.json の series_files（設定資料の抜粋・台帳の公開版）を、設計と設計審査のパケットに差し込む。"""
    s = ""
    for f in CFG.get("series_files", []):
        s += sec(f"設定資料：{Path(f).name}", rd(P(f)))
    return s


def sec(title, body):
    # 区切りに記号の連続（==== など）を使うと、モデルがそれを真似て反復暴走することがあるため、タグで囲む
    return f"\n\n<資料 名前=\"{title}\">\n{body.strip()}\n</資料>\n"


# ---------------------------------------------------------------- パケット
def build_packet(kind, n=None):
    tag = f"<!-- TASK: {kind}" + (f" CH={nn(n)}" if n else "") + " -->"
    s = tag + "\nこれは一回きりの依頼です。ツールやファイル操作は使わず、指定された形式の文章だけを返答してください。"
    if kind == "design":
        fb = P("work", "canon_feedback.md")
        if CANON.exists() and fb.exists():
            s += sec("BRIEF", rd(P("BRIEF.md")))
            s += series_secs()
            s += given_secs()
            s += sec("前回のCANON", canon())
            s += sec("修正指示", rd(fb))
            s += sec("依頼", fill(rd(P("prompts", "design_fix.md"))))
        else:
            s += sec("BRIEF", rd(P("BRIEF.md")))
            s += series_secs()
            s += given_secs()
            s += sec("CANONテンプレート", rd(P("templates", "CANON_TEMPLATE.md")))
            s += sec("依頼", fill(rd(P("prompts", "design.md"))))
    elif kind == "canon-review":
        s += sec("BRIEF", rd(P("BRIEF.md")))
        s += series_secs()
        s += given_secs()
        s += sec("CANON", canon())
        s += sec("依頼", fill(rd(P("prompts", "canon_review.md"))))
    elif kind == "write":
        draft, fb = P("work", f"ch{nn(n)}_draft.md"), P("work", f"ch{nn(n)}_feedback.md")
        s += sec("共通執筆ルール", rules())
        s += sec("CANON（設定書・章カード以外）", canon_core())
        s += sec(f"この章の機能（BRIEF 第{n}章）", role(n))
        s += sec(f"この章のカード（CANON 第{n}章）", card(n))
        if n < N_CH:
            s += sec(f"参考：次章のカード（第{n + 1}章。この章では書かない。章末の引きをここへつなぐ）", card(n + 1))
        s += sec("LEDGER（これまでの記録）", ledger_for_packet(n))
        s += sec("前章の結び", prev_tail(n))
        if draft.exists() and fb.exists():
            s += sec("前回の原稿", rd(draft))
            s += sec("修正指示", rd(fb))
            s += sec("依頼", fill(rd(P("prompts", "rewrite.md")), n))
        else:
            s += sec("依頼", fill(rd(P("prompts", "write.md")), n))
    elif kind == "review":
        s += sec("共通執筆ルール", rules())
        s += sec("CANON（設定書・章カード以外）", canon_core())
        s += sec(f"この章の機能（BRIEF 第{n}章）", role(n))
        s += sec(f"この章のカード（CANON 第{n}章）", card(n))
        s += sec("LEDGER（これまでの記録）", ledger_for_packet(n))
        s += sec("前章の結び", prev_tail(n))
        s += sec("この章の伏線（必須）", events_text(n))
        s += sec("まだ明かしてはいけないこと", hide_text(n))
        s += sec(f"審査対象の原稿（第{n}章）", rd(P("work", f"ch{nn(n)}_draft.md")))
        s += sec("依頼", fill(rd(P("prompts", "review.md")), n))
    elif kind == "ledger":
        s += sec("LEDGERの書式", rd(P("templates", "LEDGER_TEMPLATE.md")))
        s += sec(f"第{n}章の本文", rd(P("chapters", f"ch{nn(n)}.md")))
        s += sec("依頼", fill(rd(P("prompts", "ledger.md")), n))
    elif kind == "final":
        s += sec("CANON", canon())
        s += sec("LEDGER（全章）", rd(LEDGER))
        s += sec(f"第{N_CH}章の本文", rd(P("chapters", f"ch{nn(N_CH)}.md")))
        s += sec("依頼", fill(rd(P("prompts", "final_review.md"))))
    elif kind == "polish":
        src = P("chapters", f"ch{nn(n)}.md")
        s += sec("CANON（設定書・章カード以外）", canon_core())
        s += sec(f"この章のカード（CANON 第{n}章）", card(n))
        s += sec(f"磨く原稿（第{n}章）", rd(src))
        fb = P("work", f"polish_ch{nn(n)}_feedback.md")
        if fb.exists():
            s += sec("前回の磨き直しへの修正指示", rd(fb))
        s += sec("依頼", fill(rd(P("prompts", "polish.md")), n).replace("{{SRC_CHARS}}", str(count_chars(chapter_body(rd(src))))))
    elif kind == "rescue-canon":
        s += sec("BRIEF", rd(P("BRIEF.md")))
        s += series_secs()
        s += given_secs()
        s += sec("いまのCANON", canon())
        s += sec("審査の指摘の履歴（古い順）", canon_review_history())
        fb = P("work", "canon_feedback.md")
        if fb.exists():
            s += sec("いま残っている指摘", rd(fb))
        if RESCUE_NOTE.get("text"):
            s += sec("プロデューサーの指示（最優先）", RESCUE_NOTE["text"])
        s += sec("依頼", fill(rd(P("prompts", "rescue_canon.md"))))
    elif kind == "rescue-chapter":
        s += sec("共通執筆ルール", rules())
        s += sec("CANON（設定書・章カード以外）", canon_core())
        s += sec(f"この章の機能（BRIEF 第{n}章）", role(n))
        s += sec(f"この章のカード（CANON 第{n}章）", card(n))
        if n < N_CH:
            s += sec(f"参考：次章のカード（第{n + 1}章）", card(n + 1))
        s += sec("LEDGER（これまでの記録）", ledger_for_packet(n))
        s += sec("前章の結び", prev_tail(n))
        s += sec("この章の伏線（必須）", events_text(n))
        s += sec("まだ明かしてはいけないこと", hide_text(n))
        s += sec("いまの原稿", rd(P("work", f"ch{nn(n)}_draft.md")))
        s += sec("審査の指摘の履歴（稿ごと）", chapter_problem_history(n))
        if RESCUE_NOTE.get("text"):
            s += sec("プロデューサーの指示（最優先）", RESCUE_NOTE["text"])
        s += sec("依頼", fill(rd(P("prompts", "rescue_chapter.md")), n))
    elif kind == "canon-check":
        s += sec("BRIEF", rd(P("BRIEF.md")))
        s += series_secs()
        s += sec("CANON", canon())
        lint = canon_date_lint(canon())
        if lint:
            s += sec("日付の機械検査で見つかったこと（これも指摘に含める）", "\n".join(f"- {x}" for x in lint))
        s += sec("依頼", fill(rd(P("prompts", "canon_check.md"))))
    elif kind == "proofread":
        s += sec("共通執筆ルール", rules())
        s += sec("CANON（設定書・章カード以外）", canon_core())
        if CFG.get("audience"):
            s += sec("想定読者", CFG["audience"])
        s += sec(f"校正する原稿（第{n}章）", rd(proof_source(n)))
        s += sec("依頼", fill(rd(P("prompts", "proofread.md")), n))
    elif kind == "continue":
        raise ValueError("continue は内部専用")
    else:
        raise ValueError(f"不明なパケット種別: {kind}")
    name = f"{kind}" + (f"_ch{nn(n)}" if n else "") + ".packet.md"
    path = P("work", name)
    wr(path, s)
    if len(s) > CFG["packet_warn_chars"]:
        log(f"WARN packet {name} is {len(s)} chars (> {CFG['packet_warn_chars']})")
        print(f"警告: パケットが {len(s)} 字あります。モデルのコンテキスト長を確認してください。")
    return path


# ---------------------------------------------------------------- モデル呼び出し
def spent_usd():
    """logs/usage.jsonl の費用の合計（ドル）。"""
    f = P("logs", "usage.jsonl")
    total = 0.0
    if f.exists():
        for line in rd(f).splitlines():
            try:
                total += float(json.loads(line).get("cost") or 0)
            except (ValueError, TypeError):
                pass
    return total


def usage_summary():
    f = P("logs", "usage.jsonl")
    out = {"total_usd": 0.0, "calls": 0, "by_role": {}}
    if f.exists():
        for line in rd(f).splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            c = float(r.get("cost") or 0)
            out["total_usd"] += c
            out["calls"] += 1
            b = out["by_role"].setdefault(r.get("role", "?"), {"usd": 0.0, "calls": 0, "in": 0, "out": 0})
            b["usd"] += c
            b["calls"] += 1
            b["in"] += int(r.get("in") or 0)
            b["out"] += int(r.get("out") or 0)
    out["total_usd"] = round(out["total_usd"], 4)
    for b in out["by_role"].values():
        b["usd"] = round(b["usd"], 4)
    return out


def call(packet, out, role_="writer", model=None):
    budget = CFG.get("budget_usd")
    if budget and spent_usd() >= float(budget):
        raise Escalate("BUDGET_EXCEEDED", f"費用が予算 ${budget} に達しました（使用 ${spent_usd():.3f}）",
                       "人が config.json の budget_usd を見直すか、ここで止める")
    tpl = CFG.get(f"{role_}_cmd") or ["python3", "tools/call_llm.py", "{packet}", role_]
    use_arg = any("{packet}" in a for a in tpl)
    cmd = [a.replace("{packet}", str(packet)) for a in tpl]
    if cmd and cmd[0] in ("python3", "python"):
        cmd[0] = sys.executable
    env = os.environ.copy()
    env.update(CFG.get("call_env", {}))
    env.pop("NOVEL_MODEL_OVERRIDE", None)
    if model:
        env["NOVEL_MODEL_OVERRIDE"] = model  # この一回だけ、役のモデルを差し替える（rescue で人が選んだモデル。v1.1）
    inflight = P("state", "inflight.json")
    env["NOVEL_PROGRESS_FILE"] = str(inflight)
    wr(inflight, json.dumps({"task": Path(packet).name, "started": time.time(), "last_recv": time.time(),
                             "reasoning_chars": 0, "content_chars": 0, "attempt": 1}))
    log(f"CALL {role_}{'(' + model + ')' if model else ''} {Path(packet).name} -> {Path(out).name}")
    with open(packet, encoding="utf-8") as f:
        try:
            r = subprocess.run(cmd, stdin=None if use_arg else f, capture_output=True, text=True,
                               env=env, timeout=CFG["timeout_sec"], cwd=ROOT)
        except subprocess.TimeoutExpired:
            inflight.unlink(missing_ok=True)
            raise Escalate("CALL_TIMEOUT", f"モデル呼び出しが {CFG['timeout_sec']} 秒でタイムアウト",
                           "config.json の timeout_sec を延ばすか、モデルの負荷を確認して `resolve` 後に再実行")
        except FileNotFoundError as e:
            raise Escalate("CALL_NOT_FOUND", f"コマンドが見つからない: {e}", "config.json の writer_cmd / reviewer_cmd を確認")
    inflight.unlink(missing_ok=True)
    wr(out, r.stdout)
    if r.stderr.strip():
        wr(str(out) + ".stderr", r.stderr)
        for line in r.stderr.strip().splitlines()[-3:]:
            log(f"  {line[:300]}")
    if r.returncode == 4:
        raise Escalate("AWAITING_AUTHOR", f"書き手の答えを待っています: {r.stderr.strip()[-300:]}",
                       "パケットを読み、答えを work/inbox/ に置いてから `resolve` → `step`")
    if r.returncode != 0 or not r.stdout.strip():
        raise Escalate("CALL_FAILED", f"モデル呼び出し失敗 (rc={r.returncode})。stderr: {r.stderr[-500:]}",
                       "OPENROUTER_API_KEY・config.json の llm.profiles のモデル名・ネットワークを確認し `resolve` 後に再実行")
    return r.stdout


def continue_call(packet, partial, out):
    s = rd(packet) + sec("あなたの出力（途中で切れている）", partial) + sec(
        "依頼", "上の出力は途中で切れました。最後の文字の直後から続きだけを出力してください。"
                "すでに書いた部分を繰り返さないこと。見出しや前置きは不要です。")
    cp = Path(str(packet).replace(".packet.md", ".continue.packet.md"))
    wr(cp, s)
    return call(cp, out, "writer")


# ---------------------------------------------------------------- 抽出・検査
def extract_json(raw):
    raw = re.sub(r"```(?:json)?", "", raw)
    i = raw.find("{")
    while i >= 0:
        depth, instr, esc = 0, False, False
        for j in range(i, len(raw)):
            ch = raw[j]
            if instr:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = False
                continue
            if ch == '"':
                instr = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[i:j + 1])
                    except json.JSONDecodeError:
                        break
        i = raw.find("{", i + 1)
    return None


def kanji_num(n):
    """1〜99 を漢数字に（十一、二十、三十五）。v1.0：20章を超える作品のため。"""
    if n <= 10:
        return KANJI[n]
    t, o = divmod(n, 10)
    return (KANJI[t] if t > 1 else "") + "十" + (KANJI[o] if o else "")


def extract_chapter(raw, n):
    kn = kanji_num(n) if n < 100 else str(n)
    zen = "".join(chr(0xFF10 + int(d)) for d in str(n))
    m = re.search(rf"^#\s*第\s*(?:{n}|{kn}|{zen})\s*章.*$", raw, flags=re.M)
    if not m:
        return None
    text = raw[m.start():]
    text = re.sub(r"\n```.*$", "", text, flags=re.S).strip()
    first, _, rest = text.partition("\n")
    first = re.sub(r"^#\s*第\s*\S+?\s*章", f"# 第{n}章", first)
    return first + "\n" + rest.strip() + "\n"


def dedupe_repeat(text):
    """書き手が本文を丸ごと二度出力したとき（第3回 第6章で3稿中2稿）、後半の繰り返しを捨てる。
    戻り値は (直した本文, 捨てた字数)。繰り返しでなければ (そのまま, 0)。"""
    import difflib
    first, _, body = text.partition("\n")
    key = re.sub(r"\s+", "", body)[:40]
    if len(key) < 20:
        return text, 0
    # 空白を無視して key の二度目の出現を探す（本文の位置に戻すため、空白を飛ばしながら照合する）
    flat_idx = [i for i, c in enumerate(body) if not c.isspace()]
    flat = "".join(body[i] for i in flat_idx)
    j = flat.find(key, len(key))
    if j < 0:
        return text, 0
    pos = flat_idx[j]
    head, tail = body[:pos], body[pos:]
    if difflib.SequenceMatcher(None, head[:1500], tail[:1500]).ratio() < 0.85:
        return text, 0
    head = re.sub(r"#\s*第\s*\S+?\s*章[^\n]*\s*$", "", head.rstrip()).rstrip()
    return first + "\n" + head + "\n", len(tail)


def ends_properly(body):
    return bool(re.search(r"[。」』！？!?…―）)]\s*$", body))


META = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(sorted(CFG["slot_plan"], key=len, reverse=True)) +
                  r")(?![0-9])|伏線|スロット|章カード|CANON|LEDGER|承知しました|以下が本文|以上が第")
LEDGER_FIELDS = CFG.get("ledger_fields", ["SUMMARY:", "FACT:", "STATE:", "PLACE:", "INSERT:", "OPEN:", "LAST:"])


def mech_check(text, n):
    issues = []
    body = chapter_body(text)
    c = count_chars(body)
    lim = CFG["chapter_chars"]
    if c < lim["min"]:
        issues.append(("short", f"本文が{c}字で短い（{lim['min']}〜{lim['max']}字）。場面の描写を厚くして{lim['target']}字前後にする。"))
    if c > lim["max"]:
        issues.append(("long", f"本文が{c}字で長い（{lim['min']}〜{lim['max']}字）。説明的な部分を削って{lim['target']}字前後にする。"))
    if not ends_properly(body):
        issues.append(("truncated", "本文が文の途中で終わっている。最後の場面を文として完結させる。"))
    leak = sorted(set(META.findall(body)))
    if leak:
        issues.append(("meta", f"本文に管理用語や前置きが混入している：{'、'.join(leak)}。物語の文章だけにする。"))
    if "```" in body or re.search(r"^\s*#", body, flags=re.M):
        issues.append(("format", "見出し以外のMarkdown記法が本文に混ざっている。本文のみにする。"))
    issues += guard.scan_text(body, CFG.get("guard", {}).get("allow_phrases", []))
    return c, issues


def canon_check(text):
    issues = []
    if "（記入）" in text:
        issues.append(f"「（記入）」が {text.count('（記入）')} か所残っている。すべて具体的な内容で埋める。")
    for i in range(1, N_CH + 1):
        if f"<!-- CARD:{nn(i)} -->" not in text or f"<!-- /CARD:{nn(i)} -->" not in text:
            issues.append(f"第{i}章の章カードの目印（<!-- CARD:{nn(i)} --> と <!-- /CARD:{nn(i)} -->）がない。")
    for sid in CFG["slot_plan"]:
        if not re.search(rf"^[ \t]*(?:[-*・|][ \t]*)?(?:\*\*)?[ \t]*{sid}(?![0-9])", text, flags=re.M):
            issues.append(f"第7節に {sid} の行がない。")
    c = count_chars(text)
    if c > CFG["canon_max_chars"]:
        issues.append(f"CANONが{c}字で長すぎる（上限{CFG['canon_max_chars']}字）。各項目を短くする。")
    if CFG.get("require_insert_section") and not re.search(r"^## 13\.", text, flags=re.M):
        issues.append("第13節「差し込み申告」がない。BRIEFとシリーズ資料に無いものを足したら一件一行で申告する（なければ『INSERT: なし』）。")
    issues += [msg for _, msg in guard.scan_text(text, CFG.get("guard", {}).get("allow_phrases", []), skip_negated=True)]
    return issues


SLOT_RE = re.compile(r"^SLOT:\s*([A-Z]\d)\s*=\s*(planted|reinforced|recovered)", re.M)


def ledger_check(block, n):
    issues = []
    for pre in LEDGER_FIELDS:
        if not re.search(rf"^{pre}", block, flags=re.M):
            issues.append(f"{pre} の行がない")
    found = {sid: st for sid, st in SLOT_RE.findall(block)}
    for sid, ev in events_for(n):
        if sid not in found:
            issues.append(f"SLOT: {sid} の行がない（この章で{EV_JA[ev]}はず）")
        elif ev == "recovered" and found[sid] != "recovered":
            issues.append(f"{sid} はこの章で回収のはずだが {found[sid]} になっている")
    for sid, st in found.items():
        rc = recover_ch(sid) if sid in CFG["slot_plan"] else None
        if st == "recovered" and rc and rc > n:
            issues.append(f"PREMATURE: {sid} が第{n}章で回収扱い（予定は第{rc}章）")
    return issues


# ---------------------------------------------------------------- 進行（ステートマシン）
def fresh_progress():
    return {"phase": "design", "design_state": "draft", "design_attempts": 0, "canon_sha256": None,
            "current": 1, "final_state": "assemble", "final_reopened": False, "escalation": None,
            "chapters": {str(i): {"state": "pending", "attempts": 0, "parse_retries": 0,
                                  "ledger_attempts": 0, "warnings": []} for i in range(1, N_CH + 1)}}


def cmd_init():
    for d in ["canon", "state", "chapters", "work", "output", "logs"]:
        P(d).mkdir(exist_ok=True)
    if not LEDGER.exists():
        shutil.copy(P("templates", "LEDGER_TEMPLATE.md"), LEDGER)
    if not PROGRESS.exists():
        save(fresh_progress())
        log("INIT")
        print("初期化しました。")
    else:
        print("既に初期化済みです（progress.json は変更していません）。")
    if "call_llm.py" in " ".join(CFG["writer_cmd"]):
        names = CFG["llm"].get("api_key_env", ["OPENROUTER_API_KEY"])
        if not any(os.environ.get(n) for n in names) and not P(".env").exists():
            print("注意: APIキーが見つかりません（環境変数 " + " / ".join(names) + " も .env もなし）。"
                  "`python3 tools/call_llm.py --ping` が通ることを確認してから step してください。")


def verify_canon_lock(p):
    if p["canon_sha256"] and sha(CANON) != p["canon_sha256"]:
        raise Escalate("CANON_MODIFIED", "ロック後に canon/CANON.md が変更された",
                       "意図的な変更なら `resolve --relock`、そうでなければ git 等で元に戻して `resolve`")


def step_design(p):
    a = p["design_attempts"]
    if p["design_state"] == "draft":
        if a >= CFG["max_design_attempts"]:
            raise Escalate("DESIGN_EXHAUSTED", f"CANONの設計が{a}回失敗",
                           "work/canon_feedback.md を読み、人が選ぶ：`rescue canon`（上位モデルで一度だけ全体修正）／"
                           "`redesign --feedback ...`（人の指示で再設計）／CANON を読んで良しとするなら `approve-canon`。"
                           "上位モデルで直らなかったら `rescue canon --model <別のモデル>`")
        pk = build_packet("design")
        raw = call(pk, P("work", f"design_a{a + 1}.out"))
        m = re.search(r"^#\s*CANON.*", raw, flags=re.M | re.S)
        text = re.sub(r"\n```\s*$", "", m.group(0)).strip() + "\n" if m else ""
        issues = canon_check(text) if text else ["出力が `# CANON` で始まっていない。"]
        if text:
            wr(CANON, text)
        p["design_attempts"] = a + 1
        if issues:
            wr(P("work", "canon_feedback.md"), "\n".join(f"- {i}" for i in issues))
            log(f"DESIGN fail: {issues}")
            return f"CANON下書き不合格（機械検査）: {issues[0]} ほか{len(issues) - 1}件 → 再設計"
        p["design_state"] = "review"
        return "CANON下書きを canon/CANON.md に保存 → 次は審査"
    # review
    pk = build_packet("canon-review")
    raw = call(pk, P("work", f"canon_review_a{a}.out"), "reviewer")
    j = extract_json(raw)
    if j is None:
        p["design_parse_retries"] = p.get("design_parse_retries", 0) + 1
        if p["design_parse_retries"] > CFG["max_parse_retries"]:
            log("WARN canon review JSON unparsable; accepting on mechanical checks only")
            j = {"pass": True, "issues": []}
        else:
            return "審査結果のJSONを読めなかった → 審査をやり直す"
    high = [i for i in j.get("issues", []) if i.get("severity") == "high"]
    if j.get("pass") and not high:
        if p.get("canon_rescues") and p["canon_rescues"][-1].get("result") == "審査待ち":
            p["canon_rescues"][-1]["result"] = "合格"
        p["canon_sha256"] = sha(CANON)
        p["phase"] = "canon_hold" if CFG["pause_after_canon"] else "chapters"
        wr(P("work", "canon_review_notes.md"), json.dumps(j, ensure_ascii=False, indent=2))
        log("CANON locked")
        return "CANONが審査を通過し、ロックしました" + ("（人の確認待ち：`approve-canon`）" if CFG["pause_after_canon"] else " → 第1章へ")
    fb = "\n".join(f"- [審査項目{i.get('item')}] {i.get('detail')} → {i.get('fix')}" for i in high)
    wr(P("work", "canon_feedback.md"), fb)
    if p.get("canon_rescues") and p["canon_rescues"][-1].get("result") == "審査待ち":
        p["canon_rescues"][-1]["result"] = f"審査で不合格（high {len(high)}件）"
    p["design_state"] = "draft"
    log(f"CANON review fail: {len(high)} high")
    return f"CANON審査不合格（high {len(high)}件）→ 修正設計"


def step_chapter(p):
    verify_canon_lock(p)
    n = p["current"]
    ch = p["chapters"][str(n)]
    draft, fb = P("work", f"ch{nn(n)}_draft.md"), P("work", f"ch{nn(n)}_feedback.md")

    if n in CFG.get("given_chapters", []) and ch["state"] in ("pending", "rewrite"):
        # Claude が先に書いた章：書かず、審査もせず、記録（LEDGER）だけを作って通過する（第6回。2026-10-11）
        given = P("chapters", f"ch{nn(n)}.md")
        if not given.exists():
            raise Escalate("GIVEN_MISSING", f"渡されるはずの第{n}章（chapters/ch{nn(n)}.md）がない",
                           "人が chapters/ を確かめる")
        shutil.copy(given, draft)
        ch["state"] = "accepted"
        ch.setdefault("warnings", []).append("渡された本文（Claude）")
        log(f"CH{nn(n)} given (Claude)")
        return f"第{n}章は渡された本文（Claude）→ 次はLEDGER記録"

    if ch["state"] in ("pending", "rewrite"):
        if ch["state"] == "pending":
            for f in (draft, fb):
                if f.exists():
                    f.unlink()
        k = ch["attempts"] + 1
        pk = build_packet("write", n)
        raw = call(pk, P("work", f"ch{nn(n)}_write_a{k}.out"))
        text = extract_chapter(raw, n)
        if text and not ends_properly(chapter_body(text)) and count_chars(chapter_body(text)) < CFG["chapter_chars"]["max"]:
            more = continue_call(pk, text, P("work", f"ch{nn(n)}_write_a{k}_cont.out"))
            text = text.rstrip() + more.strip() + "\n"
            log(f"CH{nn(n)} continued after truncation")
        if text:
            text, cut = dedupe_repeat(text)
            if cut:
                ch.setdefault("warnings", []).append(f"a{k}: 本文の繰り返し{cut}字を削除")
                log(f"CH{nn(n)} a{k}: 本文の繰り返しを削除（{cut}字）")
        if not text:
            text = f"# 第{n}章\n" + raw.strip() + "\n"
            ch.setdefault("warnings", []).append(f"a{k}: 見出しが見つからず補完")
        wr(draft, text)
        wr(P("work", f"ch{nn(n)}_draft_a{k}.md"), text)
        ch["attempts"] = k
        ch["state"] = "drafted"
        ch["parse_retries"] = 0
        return f"第{n}章 下書き{k}稿を作成（{count_chars(chapter_body(text))}字）→ 次は審査"

    if ch["state"] == "drafted":
        text = rd(draft)
        c, mech = mech_check(text, n)
        hard_mech = [m for m in mech if m[0] in ("truncated", "meta", "format")]
        review = {"pass": True, "violations": [], "slots_found": {}}
        if not hard_mech:
            pk = build_packet("review", n)
            try:
                raw = call(pk, P("work", f"ch{nn(n)}_review_a{ch['attempts']}.out"), "reviewer")
            except Escalate as e:
                # 審査役（Aion）の推論の暴走で3回とも締め切り・上限に達したときは止まらない。
                # 機械検査だけで判定し「要校正」の印を残して先へ進む（最後の Claude の校正を関門にする）。2026-10-04 のプロデューサー判断
                runaway = ("締め切り" in e.detail or "finish_reason=length" in e.detail or "Upstream idle" in e.detail)
                if e.code != "CALL_FAILED" or not runaway or not CFG.get("review_fallback_on_runaway", True):
                    raise
                raw = None
                ch.setdefault("warnings", []).append(f"要校正 a{ch['attempts']}: 審査役が締め切りで3回失敗、機械検査のみで判定")
                log(f"CH{nn(n)} 要校正: 審査役の呼び出しが失敗（推論の暴走）→ 機械検査のみで判定")
            j = extract_json(raw) if raw is not None else {"pass": True, "violations": [], "slots_found": {}, "_fallback": "review_call_failed"}
            if j is None:
                ch["parse_retries"] += 1
                if ch["parse_retries"] <= CFG["max_parse_retries"]:
                    return f"第{n}章 審査JSONを読めなかった → 審査をやり直す"
                ch["warnings"].append(f"a{ch['attempts']}: 審査JSON不読のため機械検査のみで判定")
            else:
                review = j
        problems = [f"[{t}] {d}" for t, d in mech]
        high = [v for v in review.get("violations", []) if v.get("severity") == "high"]
        problems += [f"[{v.get('type')}] {v.get('detail')} → {v.get('fix')}" for v in high]
        found = review.get("slots_found", {}) or {}
        if found:
            for sid, ev in events_for(n):
                st = found.get(sid, "none")
                if st in (None, "none") or (ev == "recovered" and st != "recovered"):
                    problems.append(f"[slots] {sid}（{CFG['slot_names'][sid]}）を{EV_JA[ev]}必要があるが、原稿では確認できない。CANON第7節の{sid}の行どおりに描写する。")
            for sid, st in found.items():
                rc = recover_ch(sid) if sid in CFG["slot_plan"] else None
                if st == "recovered" and rc and rc > n:
                    problems.append(f"[premature] {sid} の中身を第{rc}章より前に明かしている。ほのめかしに留める。")
        lows = [v for v in review.get("violations", []) if v.get("severity") == "low"]
        wr(P("work", f"ch{nn(n)}_verdict_a{ch['attempts']}.json"),
           json.dumps({"chars": c, "mech": mech, "review": review, "problems": problems}, ensure_ascii=False, indent=2))
        if not problems:
            if ch.get("rescues") and ch["rescues"][-1].get("result") == "審査待ち":
                ch["rescues"][-1]["result"] = "合格"
            return accept(p, n, f"第{n}章 合格（{c}字）" + (f"／軽微な指摘{len(lows)}件は記録のみ" if lows else ""))
        wr(fb, "\n".join(f"- {x}" for x in problems))
        if ch.get("rescued") and ch.get("rescues"):
            ch["rescues"][-1]["result"] = "審査で不合格：" + " / ".join(problems[:2])[:200]
        if ch["attempts"] >= CFG["max_attempts"] or ch.get("rescued"):
            soft_only = all(x.startswith(("[short]", "[long]")) for x in problems)
            if soft_only:
                ch["warnings"].append(f"字数が範囲外のまま採用（{c}字）")
                return accept(p, n, f"第{n}章 字数のみ範囲外（{c}字）で採用")
            raise Escalate("CHAPTER_EXHAUSTED", f"第{n}章が{ch['attempts']}稿で合格しない: " + " / ".join(problems[:3]),
                           f"work/ch{nn(n)}_feedback.md と work/ch{nn(n)}_verdict_a*.json を確認。" +
                           (f"上位モデル（{', '.join(r['model'] for r in ch.get('rescues', []))}）で直しても通らなかった。"
                            f"別のモデルで `rescue chapter {n} --model <モデル> [--note 指示]`／" if ch.get("rescued") else
                            f"人が選ぶ：`rescue chapter {n}`（上位モデルで指摘箇所を一度だけ修正）／`resolve --retry`（書き手にもう3稿）／") +
                           "原稿のままで良ければ `resolve --accept`")
        ch["state"] = "rewrite"
        log(f"CH{nn(n)} fail a{ch['attempts']}: {len(problems)} problems")
        return f"第{n}章 不合格（{len(problems)}件）→ 書き直し（{ch['attempts'] + 1}稿目）"

    if ch["state"] == "accepted":
        if ch["ledger_attempts"] >= CFG["max_ledger_attempts"]:
            raise Escalate("LEDGER_FAILED", f"第{n}章のLEDGER記録が書式を満たさない",
                           f"work/ch{nn(n)}_ledger_a*.out を見て state/LEDGER.md に ## CH{nn(n)} 節を手で追記し `resolve --ledger-done`")
        ch["ledger_attempts"] += 1
        pk = build_packet("ledger", n)
        raw = call(pk, P("work", f"ch{nn(n)}_ledger_a{ch['ledger_attempts']}.out"), "reviewer")
        m = re.search(rf"^## CH{nn(n)}\s*$.*", raw, flags=re.M | re.S)
        block = re.sub(r"\n```.*$", "", m.group(0), flags=re.S).strip() if m else ""
        issues = ledger_check(block, n) if block else [f"`## CH{nn(n)}` で始まっていない"]
        if issues:
            if any(i.startswith("PREMATURE") for i in issues):
                ch["warnings"].append("LEDGERが早すぎる回収を報告: " + "; ".join(issues))
            log(f"CH{nn(n)} ledger fail: {issues}")
            return f"第{n}章 LEDGER記録が不備（{issues[0]}）→ 記録をやり直す"
        write_ledger_section(n, block)
        return finish_chapter(p, n)
    raise RuntimeError(f"不明な章状態: {ch['state']}")


def accept(p, n, msg):
    shutil.copy(P("work", f"ch{nn(n)}_draft.md"), P("chapters", f"ch{nn(n)}.md"))
    p["chapters"][str(n)]["state"] = "accepted"
    log(f"CH{nn(n)} accepted")
    return msg + f" → chapters/ch{nn(n)}.md に確定 → 次はLEDGER記録"


def write_ledger_section(n, block):
    head, secs = ledger_sections(rd(LEDGER))
    secs[n] = re.sub(rf"^## CH{nn(n)}\s*\n", "", block).strip()
    out = head.rstrip() + "\n\n" + "\n\n".join(f"## CH{nn(c)}\n{secs[c]}" for c in sorted(secs)) + "\n"
    wr(LEDGER, out)


def finish_chapter(p, n):
    p["chapters"][str(n)]["state"] = "done"
    log(f"CH{nn(n)} done")
    # 最終審査のあとで直した章なら、まだ終わっていない次の章へ（なければ最終工程へ）。v1.1
    nxt = next((i for i in range(n + 1, N_CH + 1) if p["chapters"][str(i)]["state"] != "done"), None)
    if nxt is None:
        p["phase"], p["final_state"] = "final", "assemble"
        return f"第{n}章 LEDGER記録完了 → 全章そろった。最終工程へ"
    p["current"] = nxt
    return f"第{n}章 LEDGER記録完了 → 第{nxt}章へ"


def cmd_assemble(quiet=False, polished=False):
    title = "無題"
    if CANON.exists():
        m = re.search(r"タイトル：\s*(.+)", canon())
        if m:
            title = m.group(1).strip()
    parts, total = [], 0
    for i in range(1, N_CH + 1):
        f = P("polished" if polished else "chapters", f"ch{nn(i)}.md")
        if polished and not f.exists():
            f = P("chapters", f"ch{nn(i)}.md")  # 磨かない章（渡された章など）は原稿をそのまま使う
        if f.exists():
            t = rd(f).strip()
            total += count_chars(chapter_body(t))
            parts.append(t.replace("# 第", "## 第", 1))
    name = "polished.md" if polished else "final.md"
    wr(P("output", name), f"# {title}\n\n" + "\n\n".join(parts) + "\n")
    if not quiet:
        print(f"output/{name} を作成（本文合計 {total} 字）")
    return total


def step_final(p):
    verify_canon_lock(p)
    if p["final_state"] == "assemble":
        total = cmd_assemble(quiet=True)
        _, secs = ledger_sections(rd(LEDGER))
        missing = []
        for sid, evs in CFG["slot_plan"].items():
            for c, ev in evs:
                if not re.search(rf"^SLOT:\s*{sid}\s*=", secs.get(c, ""), flags=re.M):
                    missing.append(f"{sid}@第{c}章")
        rng = CFG["total_chars"]
        notes = []
        if not rng["min"] <= total <= rng["max"]:
            notes.append(f"合計字数 {total} が目安 {rng['min']}〜{rng['max']} の範囲外")
        if missing:
            notes.append("LEDGERで確認できない伏線操作: " + "、".join(missing))
        p["final_notes"] = notes
        p["final_state"] = "review"
        return f"output/final.md を結合（{total}字）" + (f"／注意: {' / '.join(notes)}" if notes else "") + " → 最終審査へ"
    pk = build_packet("final")
    try:
        raw = call(pk, P("work", "final_review.out"), "reviewer")
    except Escalate as e:
        # 章の審査と同じ扱い（v1.1.2）：審査役の推論の暴走で最終審査ができなかったら止まらず、「未審査」の印をつけて完成へ。
        # 全体の整合は、このあとの上位モデルの校正（proofread）を関門にする。
        runaway = ("締め切り" in e.detail or "推論" in e.detail or "finish_reason=length" in e.detail or "Upstream idle" in e.detail)
        if e.code != "CALL_FAILED" or not runaway or not CFG.get("review_fallback_on_runaway", True):
            raise
        log("FINAL 未審査: 審査役の呼び出しが失敗（推論の暴走）→ 最終審査なしで完成扱い")
        p["final_unreviewed"] = True
        raw = json.dumps({"pass": True, "issues": [], "summary": "（最終審査は未実施：審査役が締め切り・出力枠の上限で失敗。校正で全体を確かめてください）"},
                         ensure_ascii=False)
    j = extract_json(raw) or {"pass": True, "issues": [], "summary": "（最終審査JSONを読めなかった）"}
    high = [i for i in j.get("issues", []) if i.get("severity") == "high"]
    report = ["# 最終レポート", "", f"- 合計字数: {cmd_assemble(quiet=True)}",
              f"- 最終審査: {'未実施（審査役の推論の暴走。要確認）' if p.get('final_unreviewed') else '合格' if j.get('pass') and not high else '要確認'}",
              f"- 講評: {j.get('summary', '')}", "", "## 指摘"]
    report += [f"- [{i.get('severity')}] 第{i.get('chapter')}章: {i.get('detail')} → {i.get('fix')}" for i in j.get("issues", [])] or ["- なし"]
    report += ["", "## 自動検査の注意"] + ([f"- {x}" for x in p.get("final_notes", [])] or ["- なし"])
    report += ["", "## 各章の警告"] + [f"- 第{k}章: {w}" for k, v in p["chapters"].items() for w in v.get("warnings", [])]
    wr(P("output", "final_report.md"), "\n".join(report) + "\n")
    if j.get("pass") and not high:
        p["phase"] = "done"
        return "最終審査合格。output/final.md と output/final_report.md が完成品です"
    only_last = all(i.get("chapter") == N_CH for i in high)
    if only_last and not p["final_reopened"]:
        p["final_reopened"] = True
        wr(P("work", f"ch{nn(N_CH)}_draft.md"), rd(P("chapters", f"ch{nn(N_CH)}.md")))
        wr(P("work", f"ch{nn(N_CH)}_feedback.md"), "\n".join(f"- [最終審査] {i.get('detail')} → {i.get('fix')}" for i in high))
        c = p["chapters"][str(N_CH)]
        c.update({"state": "rewrite", "attempts": 0, "ledger_attempts": 0})
        p["phase"], p["current"] = "chapters", N_CH
        return f"最終審査で第{N_CH}章に重大な指摘 → 第{N_CH}章を一度だけ書き直す"
    raise Escalate("FINAL_REVIEW_FAILED", "最終審査で重大な指摘あり（output/final_report.md 参照）",
                   "上位モデルで直すなら `rescue chapter N [--model M] [--note 指示]`（最終審査の指摘を修正指示にする）、"
                   "該当章を `reset-chapter N` で巻き戻すか、原稿のまま完成とするなら `resolve --done`")


# ---------------------------------------------------------------- 文体仕上げ（polisher）
def scene_breaks(body):
    return len(re.findall(r"^\s*[＊*]\s*$", body, flags=re.M))


def dialogues(body):
    return body.count("「")


def polish_check(src, out, n):
    pc = CFG.get("polish", {})
    issues = []
    sb, ob = chapter_body(src), chapter_body(out)
    cs, co = count_chars(sb), count_chars(ob)
    ratio = co / max(cs, 1)
    if ratio < pc.get("len_ratio_min", 0.9):
        issues.append(f"字数が{co}字で、原稿（{cs}字）より大きく減っている。場面や台詞を削らない。")
    if ratio > pc.get("len_ratio_max", 1.3):
        issues.append(f"字数が{co}字で、原稿（{cs}字）より増えすぎている。描写の追加は控えめにする。")
    if dialogues(ob) < dialogues(sb) * pc.get("dialogue_ratio_min", 0.85):
        issues.append(f"台詞（「」）が{dialogues(sb)}個から{dialogues(ob)}個に減っている。台詞は一つも削らない。")
    if scene_breaks(ob) != scene_breaks(sb):
        issues.append(f"場面転換（＊の行）が{scene_breaks(sb)}個から{scene_breaks(ob)}個に変わっている。場面の区切りはそのまま残す。")
    if not ends_properly(ob):
        issues.append("最後が文の途中で切れている。")
    leak = sorted(set(META.findall(ob)))
    if leak:
        issues.append(f"管理用語や前置きが混入している：{'、'.join(leak)}")
    if "```" in ob or re.search(r"^\s*#", ob, flags=re.M):
        issues.append("見出し以外のMarkdown記法が混ざっている。")
    # 原稿の最後の一文は変えない（結びの一文は作品の設計の一部）
    last_line = [l for l in sb.strip().splitlines() if l.strip()][-1].strip()
    sents = [x for x in re.split(r"(?<=[。！？」』])", last_line) if x.strip()]
    last = sents[-1].strip() if sents else last_line
    if last not in ob:
        issues.append(f"章の最後の一文「{last[:40]}」が変わっている。最後の一文は一字も変えずに残す。")
    return cs, co, issues


def cmd_polish(args):
    targets = [int(x) for x in args if x.isdigit()] or [i for i in range(1, N_CH + 1) if i not in CFG.get("given_chapters", [])]
    force = "--force" in args
    P("polished").mkdir(exist_ok=True)
    maxa = CFG.get("polish", {}).get("max_attempts", 3)
    report = []
    for n in targets:
        dst = P("polished", f"ch{nn(n)}.md")
        if dst.exists() and not force:
            print(f"第{n}章: polished/ch{nn(n)}.md は作成済み（やり直すなら --force）")
            continue
        src = rd(P("chapters", f"ch{nn(n)}.md"))
        fbp = P("work", f"polish_ch{nn(n)}_feedback.md")
        fbp.unlink(missing_ok=True)
        best = None
        for k in range(1, maxa + 1):
            pk = build_packet("polish", n)
            cmd_backup = CFG["writer_cmd"]
            try:
                CFG["writer_cmd"] = CFG.get("polisher_cmd", CFG["writer_cmd"])
                raw = call(pk, P("work", f"polish_ch{nn(n)}_a{k}.out"), "polisher")
            except Escalate as e:
                log(f"POLISH CH{nn(n)} call failed: {e.code} {e.detail[:200]}")
                print(f"第{n}章: 呼び出し失敗 [{e.code}] {e.detail[:300]}")
                return 2
            finally:
                CFG["writer_cmd"] = cmd_backup
            text = extract_chapter(raw, n) or (f"# 第{n}章\n" + raw.strip() + "\n")
            # 章題は原稿のものを使う
            text = src.strip().splitlines()[0] + "\n" + chapter_body(text) + "\n"
            cs, co, issues = polish_check(src, text, n)
            wr(P("work", f"polish_ch{nn(n)}_a{k}.md"), text)
            log(f"POLISH CH{nn(n)} a{k}: {cs}->{co} issues={len(issues)}")
            if not issues:
                best = text
                break
            wr(fbp, "\n".join(f"- {i}" for i in issues))
            print(f"第{n}章 a{k}: 不合格 {issues}")
        if best:
            wr(dst, best)
            report.append(f"- 第{n}章: 合格（{count_chars(chapter_body(src))}字 → {count_chars(chapter_body(best))}字）")
            print(f"第{n}章: polished/ch{nn(n)}.md を保存")
        else:
            report.append(f"- 第{n}章: {maxa}回とも機械検査不合格。原稿のまま採用せず、人の判断待ち（work/polish_ch{nn(n)}_a*.md）")
            print(f"第{n}章: {maxa}回とも不合格。work/polish_ch{nn(n)}_a*.md を確認してください")
    if report:
        with open(P("output", "polish_report.md"), "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.datetime.now().isoformat(timespec='seconds')}\n" + "\n".join(report) + "\n")
    return 0


def lock_owner():
    """ロックを持つ生きたプロセスの pid。ロックがない・持ち主が死んでいれば None。"""
    f = P("state", ".lock")
    if not f.exists():
        return None
    try:
        pid = int(f.read_text().strip() or 0)
        os.kill(pid, 0)
        return pid
    except PermissionError:
        return pid
    except (ValueError, ProcessLookupError, OSError):
        return None


class Lock:
    """同時実行の防止（エージェントが step と run を重ねて起動しても壊れないように）。"""
    path = P("state", ".lock")

    def __enter__(self):
        pid = lock_owner()
        if pid:
            sys.exit(f"BUSY: 別の novelctl（pid {pid}）が実行中です。終わるまで待ってから status を確認してください。")
        wr(self.path, str(os.getpid()))
        return self

    def __exit__(self, *a):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def guard_gate():
    """毎ステップの最初に、STOP と保護ファイルの改変を見る。書き手の規則とは別に、機械で止める。"""
    hard = guard.check_stop() + guard.check_integrity()
    if hard:
        raise Escalate("GUARD_STOP", " / ".join(m for _, m in hard),
                       "人が原因を確かめる。STOP を外すか、保護ファイルを元に戻してから `resolve`")


def cmd_step():
    p = load()
    try:
        guard_gate()
    except Escalate as e:
        log(f"GUARD {e.detail}")
        print(f"ESCALATE [{e.code}] {e.detail}\n対応: {e.hint}")
        return 2
    if p.get("escalation"):
        e = p["escalation"]
        print(f"ESCALATE [{e['code']}] {e['detail']}\n対応: {e['hint']}")
        return 2
    try:
        if p["phase"] == "design":
            msg = step_design(p)
        elif p["phase"] == "canon_hold":
            print("CANONの人間確認待ちです。canon/CANON.md を確認し `approve-canon` を実行してください。")
            return 2
        elif p["phase"] == "chapters":
            msg = step_chapter(p)
        elif p["phase"] == "final":
            msg = step_final(p)
        else:
            print("DONE: 完成しています。output/final.md / output/final_report.md")
            return 3
    except Escalate as e:
        p["escalation"] = {"code": e.code, "detail": e.detail, "hint": e.hint, "phase": p["phase"], "chapter": p["current"]}
        save(p)
        log(f"ESCALATE {e.code}: {e.detail}")
        print(f"ESCALATE [{e.code}] {e.detail}\n対応: {e.hint}")
        return 2
    save(p)
    log(f"STEP {msg}")
    print(f"OK: {msg}")
    return 3 if p["phase"] == "done" else 0


def cmd_start(args):
    """run をシェルから切り離して起動し、すぐ戻る。エージェントのツールの制限時間で殺されない。"""
    pid = lock_owner()
    if pid:
        print(f"BUSY: すでに実行中です（pid {pid}）。status で様子を見てください。")
        return 0
    P("logs").mkdir(exist_ok=True)
    out = open(P("logs", "run.out"), "a", encoding="utf-8")
    out.write(f"\n===== start {datetime.datetime.now().isoformat(timespec='seconds')} =====\n")
    out.flush()
    sub = ["polish"] + list(args[1:]) if args and args[0] == "polish" else ["run"] + list(args)
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve())] + sub,
                            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                            start_new_session=True, close_fds=True)
    log(f"START detached pid={proc.pid}")
    print(f"STARTED: バックグラウンドで実行を開始しました（pid {proc.pid}）。進み具合は status、ログは logs/run.out。")
    return 0


def cmd_run(max_steps):
    for _ in range(max_steps):
        rc = cmd_step()
        if rc != 0:
            return rc
    print(f"{max_steps} ステップ実行しました。続けるには再度 run / step。")
    return 0


def cmd_status():
    p = load()
    print(f"phase: {p['phase']}   current: 第{p['current']}章   canon_locked: {bool(p['canon_sha256'])}")
    if p["phase"] == "design":
        print(f"design: {p['design_state']}（試行 {p['design_attempts']}/{CFG['max_design_attempts']}）")
    for k, v in p["chapters"].items():
        f = P("chapters", f"ch{nn(k)}.md")
        c = count_chars(chapter_body(rd(f))) if f.exists() else 0
        w = f"  警告{len(v['warnings'])}" if v.get("warnings") else ""
        print(f"  第{k}章 {v['state']:<9} 稿{v['attempts']} {c:>5}字{w}")
    if LEDGER.exists():
        _, secs = ledger_sections(rd(LEDGER))
        st = {}
        for c in sorted(secs):
            for sid, s in SLOT_RE.findall(secs[c]):
                st[sid] = f"{s}@{c}"
        print("  伏線: " + "  ".join(f"{sid}:{st.get(sid, '-')}" for sid in CFG["slot_plan"]))
    if P("polished").exists():
        done = sorted(f.stem for f in P("polished").glob("ch*.md"))
        if done:
            print("  文体仕上げ済み: " + " ".join(done))
    inflight = P("state", "inflight.json")
    if inflight.exists():
        try:
            f = json.loads(rd(inflight))
            now = time.time()
            print(f"CALLING: {f.get('task')}  試行{f.get('attempt')}  経過{now - f['started']:.0f}秒  "
                  f"推論{f.get('reasoning_chars', 0)}字  本文{f.get('content_chars', 0)}字  "
                  f"最終受信{now - f['last_recv']:.0f}秒前")
        except (ValueError, KeyError):
            pass
    if p.get("escalation"):
        e = p["escalation"]
        print(f"ESCALATE [{e['code']}] {e['detail']}\n対応: {e['hint']}")
        print("NEXT: 上の対応を行ってから python3 tools/novelctl.py resolve")
    elif p["phase"] == "canon_hold":
        print("NEXT: canon/CANON.md を人が確認 → python3 tools/novelctl.py approve-canon")
    elif p["phase"] == "done":
        print("DONE: output/final.md")
    elif lock_owner() and inflight.exists():
        print("RUNNING: モデル呼び出し中。『最終受信』が数十秒以内なら正常。待ってから再度 status")
    elif lock_owner():
        print("RUNNING: novelctl が実行中（モデル呼び出しの合間）。待ってから再度 status")
    elif inflight.exists():
        print("STALE: 前回の呼び出しは途中で止められました（実行中のプロセスなし）。")
        print("NEXT: python3 tools/novelctl.py start   （切り離して再開。シェルの制限時間に殺されない）")
    else:
        print("NEXT: python3 tools/novelctl.py start   （または 1 ステップだけなら step）")


def cmd_resolve(args):
    p = load()
    e = p.get("escalation")
    p["escalation"] = None
    n = p["current"]
    ch = p["chapters"][str(n)]
    if "--retry" in args:
        if p["phase"] == "design":
            p["design_attempts"] = 0
            p["design_state"] = "draft"
        else:
            ch["attempts"], ch["ledger_attempts"] = 0, 0
            if ch["state"] == "drafted":
                ch["state"] = "rewrite"
    if "--accept" in args:
        accept(p, n, "人の判断で採用")
    if "--ledger-done" in args:
        finish_chapter(p, n)
    if "--relock" in args:
        p["canon_sha256"] = sha(CANON)
    if "--done" in args:
        p["phase"] = "done"
    save(p)
    log(f"RESOLVE {e and e['code']} {args}")
    print("エスカレーションを解除しました。`status` で確認してください。")


def cmd_reset(n):
    p = load()
    arch = P("work", "archive", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    arch.mkdir(parents=True, exist_ok=True)
    for i in range(n, N_CH + 1):
        f = P("chapters", f"ch{nn(i)}.md")
        if f.exists():
            shutil.move(str(f), arch / f.name)
        for g in P("work").glob(f"ch{nn(i)}_*"):
            shutil.move(str(g), arch / g.name)
        p["chapters"][str(i)] = {"state": "pending", "attempts": 0, "parse_retries": 0, "ledger_attempts": 0, "warnings": []}
    head, secs = ledger_sections(rd(LEDGER))
    secs = {c: b for c, b in secs.items() if c < n}
    wr(LEDGER, head.rstrip() + "\n\n" + "\n\n".join(f"## CH{nn(c)}\n{secs[c]}" for c in sorted(secs)) + "\n")
    p.update({"phase": "chapters", "current": n, "escalation": None, "final_state": "assemble"})
    save(p)
    log(f"RESET from CH{nn(n)}")
    print(f"第{n}章以降を未執筆に戻しました（旧ファイルは {arch.relative_to(ROOT)}）。")



# ---------------------------------------------------------------- v1.0：上位モデルの例外処理・校正・人の指示での再設計
def canon_review_history():
    out = []
    for f in sorted(P("work").glob("canon_review_a*.out"), key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
        j = extract_json(rd(f)) or {}
        items = [f"- [{i.get('severity')}] 項目{i.get('item')}: {i.get('detail')} → {i.get('fix')}" for i in j.get("issues", [])]
        out.append(f"### {f.stem}\n" + ("\n".join(items) if items else "- （指摘なし／読めない）"))
    return "\n\n".join(out[-4:]) if out else "（履歴なし）"


def chapter_problem_history(n):
    out = []
    for f in sorted(P("work").glob(f"ch{nn(n)}_verdict_a*.json")):
        try:
            j = json.loads(rd(f))
        except ValueError:
            continue
        out.append(f"### {f.stem}\n" + "\n".join(f"- {x}" for x in j.get("problems", [])))
    fb = P("work", f"ch{nn(n)}_feedback.md")
    if fb.exists():
        out.append("### いま残っている指摘\n" + rd(fb))
    return "\n\n".join(out) if out else "（指摘の記録なし）"


def proof_source(n):
    f = P("polished", f"ch{nn(n)}.md")
    return f if f.exists() else P("chapters", f"ch{nn(n)}.md")


RESCUE_NOTE = {}
MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/\-]{3,120}$")


def rescue_opts(args):
    """rescue の追加の指定：--model M（この一回だけ使うモデル）、--note TEXT（人の指示）。"""
    model = args[args.index("--model") + 1] if "--model" in args else None
    note = args[args.index("--note") + 1] if "--note" in args else ""
    if model and not MODEL_RE.match(model):
        raise ValueError(f"モデル名が不正です：{model}")
    return model, note.strip()


def premium_model():
    return (CFG["llm"]["profiles"].get("premium") or {}).get("model", "premium")


def reopen_for_final(p, n):
    """最終審査で落ちた章を、上位モデルで直せる状態に戻す（最終審査の指摘を修正指示にする）。"""
    j = extract_json(rd(P("work", "final_review.out"))) if P("work", "final_review.out").exists() else {}
    issues = [i for i in (j or {}).get("issues", []) if i.get("chapter") == n and i.get("severity") == "high"] or \
             [i for i in (j or {}).get("issues", []) if i.get("chapter") == n]
    wr(P("work", f"ch{nn(n)}_draft.md"), rd(P("chapters", f"ch{nn(n)}.md")))
    wr(P("work", f"ch{nn(n)}_feedback.md"), "\n".join(f"- [最終審査] {i.get('detail')} → {i.get('fix')}" for i in issues) or "- [最終審査] output/final_report.md の指摘を直す")
    ch = p["chapters"][str(n)]
    ch.update({"state": "drafted", "ledger_attempts": 0})
    p.update({"phase": "chapters", "current": n, "final_state": "assemble"})
    log(f"REOPEN CH{nn(n)} for rescue after final review")


def cmd_rescue(args):
    """上位モデルを一回呼ぶ例外処理。書くのではなく、止まったところを一度で直す。
    v1.1：--model で人がモデルを選べる（指定した上位モデルで直らなかったとき、別のモデルで）。--note で人の指示を足せる。"""
    if not args or args[0] not in ("canon", "chapter"):
        print("usage: novelctl.py rescue canon [--model M] [--note T] | rescue chapter N [--model M] [--note T]")
        return 1
    try:
        model, note = rescue_opts(args)
    except ValueError as e:
        print(f"NG: {e}")
        return 1
    RESCUE_NOTE["text"] = note
    used = model or premium_model()
    p = load()
    if args[0] == "canon":
        if p["phase"] not in ("design", "canon_hold"):
            print(f"REFUSED: いまは {p['phase']} です。CANON の例外処理は設計中か承認待ちのときだけです。")
            return 2
        if not CANON.exists():
            print("REFUSED: canon/CANON.md がまだありません。")
            return 2
        a = p["design_attempts"]
        k = len(p.setdefault("canon_rescues", [])) + 1
        pk = build_packet("rescue-canon")
        try:
            raw = call(pk, P("work", f"rescue_canon_a{a}_r{k}.out"), "premium", model)
        except Escalate as e:
            print(f"ESCALATE [{e.code}] {e.detail}\n対応: {e.hint}")
            return 2
        m = re.search(r"^#\s*CANON.*", raw, flags=re.M | re.S)
        text = re.sub(r"\n```\s*$", "", m.group(0)).strip() + "\n" if m else ""
        issues = canon_check(text) if text else ["出力が `# CANON` で始まっていない。"]
        if issues:
            p["canon_rescues"].append({"model": used, "result": "機械検査で不合格", "note": note[:200]})
            save(p)
            log(f"RESCUE canon ({used}) fail: {issues}")
            print(f"NG: {used} の CANON が機械検査に通りません：" + " / ".join(issues[:5]) + "。別のモデルを --model で選べます。")
            return 2
        shutil.copy(CANON, P("work", f"canon_before_rescue_r{k}.md"))
        wr(CANON, text)
        p["canon_rescues"].append({"model": used, "result": "審査待ち", "note": note[:200]})
        p.update({"phase": "design", "design_state": "review", "canon_sha256": None, "escalation": None})
        p["rescues"] = p.get("rescues", 0) + 1
        save(p)
        log(f"RESCUE canon by {used} → review")
        print(f"OK: {used} が CANON 全体を直しました（旧版は work/canon_before_rescue_r{k}.md）→ 次は審査。start で続けます。")
        return 0
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else p["current"]
    esc = (p.get("escalation") or {}).get("code")
    if p["phase"] == "final" and esc == "FINAL_REVIEW_FAILED" and 1 <= n <= N_CH:
        reopen_for_final(p, n)
    ch = p["chapters"][str(n)]
    if p["phase"] != "chapters" or n != p["current"] or not P("work", f"ch{nn(n)}_draft.md").exists():
        print(f"REFUSED: 第{n}章はいま直せる状態ではありません（phase={p['phase']} current={p['current']}）。")
        return 2
    hist = ch.setdefault("rescues", [])
    k = len(hist) + 1
    pk = build_packet("rescue-chapter", n)
    try:
        raw = call(pk, P("work", f"ch{nn(n)}_rescue_r{k}.out"), "premium", model)
    except Escalate as e:
        print(f"ESCALATE [{e.code}] {e.detail}\n対応: {e.hint}")
        return 2
    text = extract_chapter(raw, n)
    reason = None
    if not text:
        reason = "出力に章見出しがない"
    else:
        c, mech = mech_check(text, n)
        hard = [m for m in mech if m[0] in ("truncated", "meta", "format", "line", "copy")]
        if hard:
            reason = "機械検査で不合格：" + " / ".join(d for _, d in hard)
    if reason:
        hist.append({"model": used, "result": reason, "note": note[:200]})
        save(p)
        log(f"RESCUE CH{nn(n)} ({used}) fail: {reason}")
        print(f"NG: {used}：{reason}。別のモデルを --model で選べます。")
        return 2
    wr(P("work", f"ch{nn(n)}_draft.md"), text)
    wr(P("work", f"ch{nn(n)}_draft_rescue_r{k}.md"), text)
    hist.append({"model": used, "result": "審査待ち", "note": note[:200]})
    ch.update({"state": "drafted", "rescued": True, "parse_retries": 0, "attempts": ch["attempts"] + 1})
    ch.setdefault("warnings", []).append(f"上位モデル（{used}）で指摘箇所を修正")
    p["escalation"] = None
    p["rescues"] = p.get("rescues", 0) + 1
    save(p)
    log(f"RESCUE CH{nn(n)} by {used} → review once")
    print(f"OK: 第{n}章を {used} が直しました（{c}字）→ 審査を一度だけ通します。通らなければ、別のモデルでもう一度か、人の判断（resolve --accept）。")
    return 0


PROOF_KINDS = ("誤字", "表現", "連続性", "設定")


def cmd_proofread(args):
    """上位モデルによる全文校正。修正案は JSON に出すだけで、原稿は変えない（当てるのは tools/proofread_apply.py）。"""
    targets = [int(x) for x in args if x.isdigit()] or list(range(1, N_CH + 1))
    out = P("output", "proofread_edits.json")
    data = json.loads(rd(out)) if out.exists() else {"edits": []}
    keep = [e for e in data["edits"] if e.get("chapter") not in targets]
    protected = CFG.get("proofread", {}).get("protected_words", [])
    added, rejected = [], []
    for n in targets:
        src = proof_source(n)
        if not src.exists():
            print(f"第{n}章: 原稿がありません")
            continue
        text = rd(src)
        pk = build_packet("proofread", n)
        try:
            raw = call(pk, P("work", f"proofread_ch{nn(n)}.out"), "premium")
        except Escalate as e:
            print(f"ESCALATE [{e.code}] {e.detail}")
            return 2
        j = extract_json(raw) or {}
        for e in j.get("edits", []):
            b, a = str(e.get("before", "")), str(e.get("after", ""))
            why = None
            if not b or b == a:
                why = "原文が空、または修正が同じ"
            elif text.count(b) != 1:
                why = f"原文が原稿に{text.count(b)}か所（1か所でなければ当てない）"
            elif len(b) > 160:
                why = "原文が長すぎる（局所修正ではない）"
            elif any(w in b and w not in a for w in protected):
                why = "保護語を消している"
            rec = {"chapter": n, "before": b, "after": a, "kind": e.get("kind") if e.get("kind") in PROOF_KINDS else "表現",
                   "reason": str(e.get("reason", ""))[:200], "enabled": why is None}
            if why:
                rec["rejected"] = why
                rejected.append(rec)
            added.append(rec)
        print(f"第{n}章: 修正案 {len(j.get('edits', []))}件（機械で却下 {sum(1 for r in added if r['chapter'] == n and 'rejected' in r)}件）")
    data["edits"] = sorted(keep + added, key=lambda e: e["chapter"])
    data["source"] = "premium"
    wr(out, json.dumps(data, ensure_ascii=False, indent=1))
    log(f"PROOFREAD {targets}: {len(added)} edits, {len(rejected)} rejected")
    print("output/proofread_edits.json に保存。人が enabled を確かめてから python3 tools/proofread_apply.py → epub --proofread")
    return 0


# ---------------------------------------------------------------- v1.2：CANON の矛盾を承認前に拾う
WEEKDAYS = "月火水木金土日"
DATE_RE = re.compile(r"(\d{1,2})(?:月|/)(\d{1,2})日?(?:\s*[（(]([月火水木金土日])[)）])?")


def canon_date_lint(text):
    """CANON の日付の機械検査（費用なし）。第1節の期間と章カードの日付、曜日、章の日付の順番。返り値は指摘の文の一覧。"""
    out = []
    if not text:
        return out
    ym = re.search(r"(20\d\d|19\d\d)年", text)
    year = int(ym.group(1)) if ym else None
    if year:  # 曜日（10/9（金）・10月9日（金））
        seen = set()
        for m in DATE_RE.finditer(text):
            mo, d, wd = int(m.group(1)), int(m.group(2)), m.group(3)
            if not wd or (mo, d, wd) in seen or not (1 <= mo <= 12 and 1 <= d <= 31):
                continue
            seen.add((mo, d, wd))
            try:
                real = WEEKDAYS[datetime.date(year, mo, d).weekday()]
            except ValueError:
                out.append(f"{mo}月{d}日 は存在しない日付です。")
                continue
            if real != wd:
                out.append(f"{year}年{mo}月{d}日は{real}曜日ですが、CANON では（{wd}）になっています。")
    period = None
    pm = re.search(r"(?:時代・場所・季節の範囲|期間)[：:](.+)", text)
    if pm:
        ds = [(int(a), int(b)) for a, b, _ in DATE_RE.findall(pm.group(1))]
        if len(ds) >= 2:
            period = (min(ds), max(ds))
    marked = re.findall(r"<!-- CARD:(\d+) -->(.*?)<!-- /CARD:\1 -->", text, flags=re.S)
    if marked:
        cards = [(int(n), body) for n, body in marked]
    else:  # 印のない CANON（人が直したものなど）：見出しの「第N章」で区切る
        parts = re.split(r"\n(?=[#\s]*第\s*\d+\s*章[「『\s])", text)
        cards = [(int(m.group(1)), c) for c in parts for m in [re.match(r"[#\s]*第\s*(\d+)\s*章", c)] if m]
    last = None
    for n, c in cards:
        tm = re.search(r"時期と場所[：:](.+)", c)
        if not tm:
            continue
        ds = [(int(a), int(b)) for a, b, _ in DATE_RE.findall(tm.group(1)) if 1 <= int(a) <= 12]
        if not ds:
            continue
        first = ds[0]
        if period and not (period[0] <= first <= period[1]):
            out.append(f"第{n}章の時期（{first[0]}/{first[1]}）が、第1節の物語の期間（{period[0][0]}/{period[0][1]}〜{period[1][0]}/{period[1][1]}）の外です。")
        if last and first < last[1]:
            out.append(f"第{n}章の時期（{first[0]}/{first[1]}）が、第{last[0]}章（{last[1][0]}/{last[1][1]}）より前に戻っています（回想なら章カードにそう書く）。")
        last = (n, first)
    return out


def cmd_canon_check(args):
    """承認の前に、上位モデル（または --model のモデル）が CANON 全体の矛盾を一度だけ探す。CANON は変えない。"""
    try:
        model, _ = rescue_opts(args)
    except ValueError as e:
        print(f"NG: {e}")
        return 1
    if not CANON.exists():
        print("REFUSED: canon/CANON.md がまだありません。")
        return 2
    used = model or premium_model()
    lint = canon_date_lint(canon())
    pk = build_packet("canon-check")
    try:
        raw = call(pk, P("work", "canon_check.out"), "premium", model)
    except Escalate as e:
        print(f"ESCALATE [{e.code}] {e.detail}")
        return 2
    j = extract_json(raw) or {"issues": [], "summary": "（JSON を読めなかった。work/canon_check.out を見てください）"}
    issues = [{"severity": "high", "where": "日付の機械検査", "detail": x, "fix": "", "source": "lint"} for x in lint]
    for i in j.get("issues", []) or []:
        if isinstance(i, dict) and str(i.get("detail", "")).strip():
            issues.append({"severity": "high" if i.get("severity") == "high" else "low", "where": str(i.get("where", ""))[:80],
                           "detail": str(i.get("detail", ""))[:600], "fix": str(i.get("fix", ""))[:600], "source": "model"})
    res = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "model": used, "canon_fp": sha(CANON)[:8],
           "summary": str(j.get("summary", ""))[:800], "issues": issues}
    wr(P("work", "canon_check.json"), json.dumps(res, ensure_ascii=False, indent=1))
    md = [f"# CANON の矛盾チェック（{used}・{res['ts']}）", "", res["summary"], ""]
    md += [f"- [{i['severity']}] {i['where']}：{i['detail']}" + (f" → {i['fix']}" if i["fix"] else "") for i in issues] or ["- 指摘なし"]
    wr(P("work", "canon_check.md"), "\n".join(md) + "\n")
    log(f"CANON-CHECK by {used}: {len(issues)} issues ({sum(1 for i in issues if i['severity'] == 'high')} high)")
    print(f"OK: {used} が CANON を確かめました。指摘 {len(issues)} 件（重大 {sum(1 for i in issues if i['severity'] == 'high')} 件）→ work/canon_check.md")
    return 0


def cmd_canon_edit(args):
    """人が直した CANON に差し替える（studio の「CANON を直す」）。承認済みなら鍵をかけ直す。--reset-from N で第N章以降を未執筆に戻す。"""
    if "--file" not in args:
        print("usage: novelctl.py canon-edit --file F [--reset-from N]")
        return 1
    text = rd(Path(args[args.index("--file") + 1]))
    issues = canon_check(text)
    if issues:
        print("NG: 直した CANON が機械検査に通りません：" + " / ".join(issues[:5]))
        return 2
    p = load()
    if p["phase"] == "design" and p.get("design_state") == "review" and not p.get("escalation"):
        print("REFUSED: いま審査役が CANON を審査しています。止まってから直してください。")
        return 2
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if CANON.exists():
        shutil.copy(CANON, P("work", f"canon_before_edit_{stamp}.md"))
    wr(CANON, text if text.endswith("\n") else text + "\n")
    p = load()
    if p.get("canon_sha256"):
        p["canon_sha256"] = sha(CANON)  # 承認済み：人が直したので鍵をかけ直す
    if (p.get("escalation") or {}).get("code") == "CANON_MODIFIED":
        p["escalation"] = None
    save(p)
    log(f"CANON-EDIT by human（旧版 work/canon_before_edit_{stamp}.md）")
    if "--reset-from" in args:
        cmd_reset(int(args[args.index("--reset-from") + 1]))
    print(f"OK: CANON を差し替えました（旧版は work/canon_before_edit_{stamp}.md）。指紋 {sha(CANON)[:8]}")
    return 0


def cmd_redesign(args):
    p = load()
    reset = "--reset" in args and p["phase"] in ("chapters", "final", "done")
    if p["phase"] not in ("design", "canon_hold") and not reset:
        print(f"REFUSED: いまは {p['phase']} です。再設計は設計中か承認待ちのとき（執筆中なら --reset をつける）だけです。")
        return 2
    if "--feedback-file" in args:
        text = rd(Path(args[args.index("--feedback-file") + 1]))
    elif "--feedback" in args:
        text = args[args.index("--feedback") + 1]
    else:
        print("usage: novelctl.py redesign (--feedback TEXT | --feedback-file F)")
        return 1
    if not CANON.exists():
        print("REFUSED: canon/CANON.md がまだありません。")
        return 2
    if not text.strip():
        print("REFUSED: 指示が空です。")
        return 1
    if reset:
        cmd_reset(1)  # 執筆中でも CANON から作り直す（v1.2）：本文と LEDGER を退けてから、設計に戻す
        p = load()
    wr(P("work", "canon_feedback.md"), "- 【人の指示】" + text.strip().replace("\n", "\n  ") + "\n")
    p.update({"phase": "design", "design_state": "draft", "design_attempts": 0, "canon_sha256": None, "escalation": None})
    save(p)
    log("REDESIGN by human: " + text.strip()[:200])
    print("OK: 人の指示を修正指示として、CANON の設計をやり直します。start で続けます。")
    return 0


NEXT = {
    "canon_hold": ("approve", "canon/CANON.md を人が確認 → approve-canon（または redesign / rescue canon）"),
    "done": ("done", "完成。output/final.md（必要なら proofread → proofread_apply → epub --proofread）"),
}


def status_dict():
    p = load()
    d = {"phase": p["phase"], "current": p["current"], "canon_locked": bool(p["canon_sha256"]),
         "design": {"state": p["design_state"], "attempts": p["design_attempts"], "max": CFG["max_design_attempts"]},
         "chapters": [], "slots": {}, "escalation": p.get("escalation"), "rescues": p.get("rescues", 0),
         "running": bool(lock_owner()), "inflight": None, "usage": usage_summary(),
         "budget_usd": CFG.get("budget_usd"), "premium_model": premium_model(), "canon_rescues": p.get("canon_rescues", []), "canon_fp": sha(CANON)[:8] if CANON.exists() else None,
         "canon_lint": canon_date_lint(canon()) if CANON.exists() else [],
         "canon_check": json.loads(rd(P("work", "canon_check.json"))) if P("work", "canon_check.json").exists() else None}
    for k, v in p["chapters"].items():
        f = P("chapters", f"ch{nn(k)}.md")
        d["chapters"].append({"n": int(k), "state": v["state"], "attempts": v["attempts"],
                              "chars": count_chars(chapter_body(rd(f))) if f.exists() else 0,
                              "warnings": v.get("warnings", []), "rescued": bool(v.get("rescued")),
                              "rescues": v.get("rescues", [])})
    if LEDGER.exists():
        _, secs = ledger_sections(rd(LEDGER))
        for c in sorted(secs):
            for sid, st in SLOT_RE.findall(secs[c]):
                d["slots"][sid] = {"state": st, "chapter": c}
    inflight = P("state", "inflight.json")
    if inflight.exists():
        try:
            d["inflight"] = json.loads(rd(inflight))
        except ValueError:
            pass
    if p.get("escalation"):
        d["next"] = {"kind": "escalate", "text": p["escalation"]["hint"]}
    elif p["phase"] in NEXT:
        k, t = NEXT[p["phase"]]
        d["next"] = {"kind": k, "text": t}
    elif d["running"]:
        d["next"] = {"kind": "wait", "text": "実行中。しばらく待って status"}
    else:
        d["next"] = {"kind": "start", "text": "start で進める"}
    return d


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    c, a = argv[0], argv[1:]
    if c == "init":
        cmd_init()
    elif c == "status":
        if "--json" in a:
            print(json.dumps(status_dict(), ensure_ascii=False, indent=1))
        else:
            cmd_status()
    elif c == "step":
        with Lock():
            return cmd_step()
    elif c == "start":
        return cmd_start(a)
    elif c == "run":
        with Lock():
            return cmd_run(int(a[a.index("--max") + 1]) if "--max" in a else 200)
    elif c == "packet":
        n = int(a[1]) if len(a) > 1 else None
        print(build_packet(a[0], n))
    elif c == "check":
        n = int(a[0])
        f = P("work", f"ch{nn(n)}_draft.md")
        f = f if f.exists() else P("chapters", f"ch{nn(n)}.md")
        cnt, iss = mech_check(rd(f), n)
        print(f"{f.relative_to(ROOT)}: {cnt}字")
        for t, d in iss:
            print(f"  [{t}] {d}")
        return 0 if not iss else 1
    elif c == "approve-canon":
        p = load()
        if not CANON.exists():
            print("canon/CANON.md がまだありません。設計が終わってから承認してください。")
            return 2
        h = sha(CANON)[:8]
        if CFG.get("human_approval", True) and os.environ.get("NOVEL_STUDIO_APPROVE") == h:
            # studio の「承認」ボタン：人が画面で指紋を確かめて押したときだけ、この環境変数が付く（v1.0）
            log(f"APPROVED by studio ({h})")
        elif CFG.get("human_approval", True):
            if not sys.stdin.isatty():
                log("APPROVE refused: not a terminal（エージェントからの承認の試みとして記録）")
                print("REFUSED: approve-canon は人が端末で行う操作です（エージェントからは実行しない）。")
                return 2
            got = input(f"canon/CANON.md を読みましたか。承認するなら指紋 {h} を入力してください: ").strip()
            if got != h:
                print("REFUSED: 指紋が一致しません。承認していません。")
                return 2
        p["canon_sha256"] = sha(CANON)
        p["phase"] = "chapters"
        p["escalation"] = None  # 設計が収束しないまま人が良しとした場合も、ここで止まりを解く
        save(p)
        log(f"APPROVE canon {h}")
        print("CANONを承認・再ロックしました。`step` で第1章から進みます。")
    elif c == "resolve":
        cmd_resolve(a)
    elif c == "reset-chapter":
        cmd_reset(int(a[0]))
    elif c == "assemble":
        cmd_assemble(polished="--polished" in a)
    elif c == "rescue":
        with Lock():
            return cmd_rescue(a)
    elif c == "proofread":
        with Lock():
            return cmd_proofread(a)
    elif c == "redesign":
        with Lock():
            return cmd_redesign(a)
    elif c == "canon-check":
        with Lock():
            return cmd_canon_check(a)
    elif c == "canon-edit":
        with Lock():
            return cmd_canon_edit(a)
    elif c == "polish":
        with Lock():
            return cmd_polish(a)
    elif c == "cover":
        sys.path.insert(0, str(ROOT / "tools"))
        import make_cover
        return make_cover.main(a)
    elif c == "epub":
        sys.path.insert(0, str(ROOT / "tools"))
        import make_epub
        make_epub.build("proofread" if "--proofread" in a else "final")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
