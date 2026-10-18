#!/usr/bin/env python3
"""call_llm — パケット1つを OpenRouter（OpenAI互換 Chat Completions）にストリーミングで送り、本文だけを標準出力に返す。

エージェントハーネス（Hermes / OpenCode / Goose など）に依存しない。Python標準ライブラリのみ。

  python3 tools/call_llm.py <packet> <role>     novelctl.py から呼ばれる通常の使い方（role = writer / reviewer / polisher / premium）
  python3 tools/call_llm.py --ping [<role>]      疎通・キー・モデル名・応答速度の確認（数十トークンだけ使う）

v1.0：1回の呼び出しごとに logs/usage.jsonl へ1行（役・工程・モデル・トークン・費用・秒）を追記する。
      novelctl status --json と studio がこれを集計して費用を出す。

ストリーミングにしている理由:
  - 推論が長いモデルでも「生きているか」が分かる（NOVEL_PROGRESS_FILE に受信状況を書き出す → novelctl status に表示）
  - 無通信タイムアウト（llm.idle_timeout_sec）で、本当に詰まった接続だけを検出して再試行できる

APIキーの探し方（上から順に、最初に見つかったもの）:
  1. config.json の llm.api_key_env に並べた環境変数（既定: NOVEL_LLM_API_KEY, OPENROUTER_API_KEY, HERMES_CUSTOM_OPENROUTER_API_KEY）
  2. プロジェクト直下の .env、なければ親フォルダの .env（例: OPENROUTER_API_KEY=sk-or-...）
任意:
  NOVEL_LLM_BASE_URL   既定 https://openrouter.ai/api/v1（環境変数、または近くの .env に書いてもよい）
  NOVEL_PROGRESS_FILE  受信状況を書き出すファイル（novelctl が自動で設定）
"""
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_wire as wire  # noqa: E402  窓口ごとの違い（ヘッダ・/responses・/messages）。v1.1.1
import hashlib  # noqa: E402
SESSION = "novelctl-" + hashlib.sha1(str(ROOT).encode()).hexdigest()[:16]  # 作品ごとに同じ値（x-opencode-session）
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
LLM = CFG["llm"]
JSON_TASKS = {"canon-review", "review", "final", "proofread", "canon-check"}
RETRY_HTTP = (408, 409, 425, 429, 500, 502, 503, 504)


def err(msg):
    print(msg, file=sys.stderr, flush=True)


def die(msg, code=1):
    err(msg)
    sys.exit(code)


class ApiError(Exception):
    pass


ABORT_REASONING = None  # profiles.*.reasoning_abort_chars（run が設定）。本文が出ないまま推論がこれを超えたら締め切りを待たずに打ち切る


class Deadline(Exception):
    """1回の呼び出しが壁時計の締め切り（profiles.*.deadline_sec）を過ぎた。推論の暴走を打ち切って投げ直すため。"""


class Degenerate(Exception):
    pass


def degenerate(tail):
    """末尾160字が40字以下の単位の繰り返しになっていれば暴走とみなす。"""
    if len(set(tail)) <= 2:
        return True
    for unit in range(1, 41):
        pat = tail[-unit:]
        if pat * (len(tail) // unit) == tail[len(tail) % unit:]:
            return True
    return False


class Progress:
    def __init__(self, path, task):
        self.path, self.task, self.t0, self.last = path, task, time.time(), 0.0
        self.reasoning = self.content = 0
        self.last_recv = time.time()
        self.attempt = 1

    def tick(self, force=False):
        self.last_recv = time.time()
        if not self.path or (not force and time.time() - self.last < 2):
            return
        self.last = time.time()
        try:
            Path(self.path).write_text(json.dumps({
                "task": self.task, "attempt": self.attempt, "pid": os.getpid(),
                "started": self.t0, "last_recv": self.last_recv,
                "reasoning_chars": self.reasoning, "content_chars": self.content,
            }), encoding="utf-8")
        except OSError:
            pass


def request(url, key, body, idle_timeout):
    # User-Agent はライブラリ名にしない（Cloudflare の 403・error code 1010 を避ける）。OpenCode には作品ごとの session を渡す
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=wire.headers(key, "chat", session=SESSION, title=LLM.get("app_title", "novelctl"), stream=True))
    # timeout はソケットの1回の読み取りごとに効く＝無通信タイムアウトとして働く
    return urllib.request.urlopen(req, timeout=idle_timeout)


def stream_once(url, key, body, prog, idle_timeout, deadline=None):
    parts, finish, usage, model = [], None, None, None
    t_start = time.time()
    with request(url, key, body, idle_timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if ABORT_REASONING and prog.content == 0 and prog.reasoning > ABORT_REASONING:
                raise Deadline(f"推論が{prog.reasoning}字を超えても本文が出ない（打ち切りの目安 {ABORT_REASONING}字）")
            if deadline:
                el = time.time() - t_start
                # 本文がまだ出ていなければ締め切りで、出始めていても1.5倍で打ち切る（書き終わる直前の本文は捨てない）
                if (prog.content == 0 and el > deadline) or el > deadline * 1.5:
                    raise Deadline(f"{el:.0f}秒たっても終わらない（締め切り {deadline}秒・推論{prog.reasoning}字・本文{prog.content}字）")
            if line.startswith(":"):  # OpenRouter のキープアライブ（処理中）
                prog.tick()
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("error"):
                raise ApiError(json.dumps(obj["error"], ensure_ascii=False)[:800])
            model = obj.get("model") or model
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    parts.append(d["content"])
                    prog.content += len(d["content"])
                    if prog.content > 200 and prog.content % 50 < len(d["content"]) + 1:
                        tail = "".join(parts)[-160:]
                        if degenerate(tail):
                            raise Degenerate(f"同じ文字列の繰り返しが止まらなくなった（末尾: {tail[-20:]!r}）")
                if d.get("reasoning"):
                    prog.reasoning += len(d["reasoning"])
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                    if finish == "error":
                        raise ApiError("ストリーム中にプロバイダ側でエラー（finish_reason=error）")
            prog.tick()
    return "".join(parts), finish, usage, model


def find_key():
    """エージェントのシェルに環境変数が引き継がれなくても動くよう、複数の場所からキーを探す。"""
    names = LLM.get("api_key_env", ["NOVEL_LLM_API_KEY", "OPENROUTER_API_KEY"])
    for n in names:
        if os.environ.get(n):
            return os.environ[n].strip()
    # プロジェクト直下 → 親フォルダ（Novel/ 以下に Novel_1, Novel_2 … と並べる運用で .env を共有できる）
    for env in (ROOT / ".env", ROOT.parent / ".env", ROOT.parent.parent / ".env"):
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.strip().partition("=")
            if k.strip().removeprefix("export ").strip() in names and v.strip():
                return v.strip().strip('"').strip("'")
    return None


def env_value(name):
    """環境変数、なければ近くの .env から値を読む（キー以外の設定用。第3回で追加）。"""
    if os.environ.get(name):
        return os.environ[name].strip()
    for env in (ROOT / ".env", ROOT.parent / ".env", ROOT.parent.parent / ".env"):
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                k, _, v = line.strip().partition("=")
                if k.strip().removeprefix("export ").strip() == name and v.strip():
                    return v.strip().strip('"').strip("'")
    return None


# 工程ごとの既定（v1.1.2）。最終審査は全章の記録を読むので、章の審査と同じ締め切り・出力枠では足りない。
# 作品の config.json より小さい値にはしない（引き上げるだけ）。task_overrides があればそちらが優先。
TASK_DEFAULTS = {"final": {"deadline_sec": 900, "max_tokens": 32000}}


def profile_for(role, task):
    prof = dict(LLM["profiles"][role])
    for k, v in TASK_DEFAULTS.get(task, {}).items():
        if isinstance(v, (int, float)) and (prof.get(k) or 0) < v:
            prof[k] = v
    prof.update(LLM.get("task_overrides", {}).get(task, {}))
    return prof


def build_body(role, task, text):
    prof = profile_for(role, task)
    # .env の NOVEL_MODEL は model_env_roles に挙げた役にだけ効く（第6回：審査役は config の model を使う。2026-10-11）
    env_roles = LLM.get("model_env_roles")
    if LLM.get("model_env") and env_value(LLM["model_env"]) and (env_roles is None or role in env_roles):
        prof["model"] = env_value(LLM["model_env"])
    override = os.environ.get("NOVEL_MODEL_OVERRIDE")
    if override:  # novelctl rescue --model：この一回だけ、人が選んだモデルで（予備のモデルには切り替えない）
        prof["model"] = override
        prof.pop("fallback_models", None)
    body = {
        "model": prof["model"],
        "messages": [{"role": "system", "content": prof["system"]}, {"role": "user", "content": text}],
        "max_tokens": prof.get("max_tokens", 24000),
        "temperature": prof.get("temperature", 0.8),
        "stream": True,
        "usage": {"include": True},
    }
    if prof.get("fallback_models"):
        # OpenRouter のモデル切り替え：先頭のモデルが使えないとき、順に次のモデルへ（2026-10-11）
        body["models"] = [prof["model"]] + list(prof["fallback_models"])
    if "top_p" in prof:
        body["top_p"] = prof["top_p"]
    if prof.get("reasoning_max_tokens"):
        # 推論の量をトークン数で打ち切る（effort と同時には指定できない）。審査役の推論の暴走で本文が出ない事故の対策
        body["reasoning"] = {"max_tokens": int(prof["reasoning_max_tokens"])}
    elif prof.get("reasoning_effort"):
        body["reasoning"] = {"effort": prof["reasoning_effort"]}
    if task in JSON_TASKS and LLM.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}
    return body


def log_usage(role, task, model, usage, sec, chars, ok=True, note=""):
    """費用の家計簿（v1.0）。OpenRouter は usage.include=true で cost（ドル）を返す。"""
    u = usage or {}
    cost = u.get("cost")
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "task": task, "model": model,
           "in": u.get("prompt_tokens"), "out": u.get("completion_tokens"), "cost": cost,
           "sec": round(sec, 1), "chars": chars, "ok": ok}
    rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if rt:
        rec["reasoning"] = rt  # 出力のうち推論に使ったトークン（暴走でどれだけ捨てたかを見る。v1.1.2）
    if cost is None:  # OpenCode は費用を返さない：公開価格（llm_wire.PRICES・llm.prices・NOVEL_LLM_PRICES）で見積もる
        est = wire.estimate_cost(model, u, LLM.get("prices"))
        if est is not None:
            rec["cost"], rec["est"] = est, True
    if note:
        rec["note"] = note
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def nonstream_once(base, key, proto, chat_body, prog, idle_timeout, deadline=None):
    """/responses と /messages の窓口（OpenCode の GPT・Claude など）。ストリーミングせずに一度で受ける。"""
    msgs = chat_body["messages"]
    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    user = next((m["content"] for m in msgs if m["role"] == "user"), "")
    eff = wire.get_effort(chat_body)
    path, body = wire.build(proto, chat_body["model"], system, user, chat_body.get("temperature"), chat_body.get("max_tokens", 24000),
                            eff, "response_format" in chat_body)
    timeout = int(deadline * 1.5) if deadline else max(600, idle_timeout * 5)
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=wire.headers(key, proto, session=SESSION, title=LLM.get("app_title", "novelctl")))
    prog.tick(force=True)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except (socket.timeout, TimeoutError):
        raise Deadline(f"{timeout}秒たっても応答がない（{proto}）")
    try:
        content, finish, usage, model = wire.parse(proto, d)
    except ValueError as e:
        raise ApiError(str(e))
    prog.content = len(content)
    prog.tick(force=True)
    return content, finish, usage, model


def run(role, task, text, progress_path=None):
    key = find_key()
    if not key:
        die("APIキーが見つかりません。環境変数 " + " / ".join(LLM.get("api_key_env", [])) +
            " のいずれか、またはプロジェクト直下の .env（OPENROUTER_API_KEY=...）を設定してください。")
    # 環境変数か近くの .env の NOVEL_LLM_BASE_URL（偽モデルや別の OpenAI 互換の窓口）、なければ config（v1.0）
    base = (env_value("NOVEL_LLM_BASE_URL") or LLM.get("base_url", "https://openrouter.ai/api/v1")).rstrip("/")
    url = f"{base}/chat/completions"
    body = build_body(role, task, text)
    proto = wire.protocol_for(base, body["model"], LLM.get("protocols"))
    if wire.is_opencode(base):
        body.pop("usage", None)  # OpenRouter 固有の欄は送らない
        body.pop("models", None)
    spare = wire.alternatives(proto) if wire.is_opencode(base) else []
    wire.adapt_reasoning(body, base)  # OpenRouter 以外には標準の reasoning_effort で頼む（v1.1.2）
    idle = LLM.get("idle_timeout_sec", 180)
    # 締め切りも task_overrides で上書きできる（第6回：設計は束が大きく推論が長い。2026-10-11）
    prof = profile_for(role, task)
    deadline = prof.get("deadline_sec")
    global ABORT_REASONING
    ABORT_REASONING = prof.get("reasoning_abort_chars")
    # 推論の暴走（締め切り・出力枠の使い切り）が続いたときに替える予備のモデル。rescue --model で人が選んだときは替えない
    backups = [] if os.environ.get("NOVEL_MODEL_OVERRIDE") else list(prof.get("fallback_models") or [])
    raised = False

    def on_runaway(kind):
        """暴走したら、同じ条件では投げ直さない（v1.1.2）。①推論を一段軽く ②出力枠を広げる（使い切りのとき） ③予備のモデルへ。"""
        nonlocal raised, proto, spare
        eff = wire.get_effort(body)
        if eff in ("high", "medium"):
            wire.set_effort(body, {"high": "medium", "medium": "low"}[eff])
            return f"reasoning effort を {wire.get_effort(body)} に下げて再試行します"
        rmax = (body.get("reasoning") or {}).get("max_tokens")
        if kind == "length" and rmax and rmax > 4000:
            body["reasoning"] = {"max_tokens": max(4000, rmax // 2)}
            return f"推論の上限を {body['reasoning']['max_tokens']} に下げて再試行します"
        if kind == "length" and not raised and body.get("max_tokens", 0) < 64000:
            raised = True
            body["max_tokens"] = min(64000, int(body.get("max_tokens", 16000)) * 2)
            return f"推論で出力枠を使い切ったため、max_tokens を {body['max_tokens']} に広げて再試行します"
        if backups:
            nxt = backups.pop(0)
            body["model"] = nxt
            if "models" in body:
                body["models"] = [nxt] + backups
            proto = wire.protocol_for(base, nxt, LLM.get("protocols"))
            spare = wire.alternatives(proto) if wire.is_opencode(base) else []
            return f"推論が終わらないモデルを諦め、予備のモデル {nxt} に替えて再試行します"
        return "同じ条件で再試行します（下げる・広げる・替える先がない）"
    prog = Progress(progress_path, task)
    prog.tick(force=True)
    last = None
    for attempt in range(1, LLM.get("retries", 4) + 1):
        prog.attempt, prog.reasoning, prog.content = attempt, 0, 0
        t0 = time.time()
        try:
            if proto == "chat":
                content, finish, usage, model = stream_once(url, key, body, prog, idle, deadline)
            else:
                content, finish, usage, model = nonstream_once(base, key, proto, body, prog, idle, deadline)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            last = f"HTTP {e.code}: {detail}"
            err(f"attempt {attempt}: {last}")
            low = detail.lower()
            if spare and (e.code == 404 or "endpoint" in low or "not supported" in low or "unsupported" in low):
                proto = spare.pop(0)  # 窓口の取り違え：別の窓口で出し直す
                err(f"窓口を {proto} に切り替えて出し直します")
                continue
            fixable = e.code == 400 and "MissingSessionID" not in detail
            if fixable and "response_format" in body:
                body.pop("response_format")
                continue
            if fixable and "temperature" in body and "temperature" in low:
                body.pop("temperature")  # 推論型のモデルは temperature を受け付けないことがある
                continue
            if fixable and "reasoning_effort" in body and "reasoning" in low:
                body.pop("reasoning_effort")  # 推論の量を指定できないモデル
                continue
            if e.code in RETRY_HTTP:
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            die(last + wire.hint(e.code, detail, proto))
        except (socket.timeout, TimeoutError) as e:
            last = f"{idle}秒間なにも受信しなかった（無通信タイムアウト）: {e}"
            err(f"attempt {attempt}: {last}")
            continue
        except Deadline as e:
            last = str(e)
            err(f"attempt {attempt}: {last} → 打ち切って投げ直します")
            log_usage(role, task, body["model"], None, time.time() - t0, 0, ok=False, note="runaway:deadline")
            err(on_runaway("deadline"))
            continue
        except Degenerate as e:
            last = str(e)
            err(f"attempt {attempt}: {last}")
            t = body.get("temperature", 0.8)
            body["temperature"] = min(t, max(0.3, round(t - 0.25, 2)))
            err(f"temperature を {body['temperature']} に下げて再試行します")
            continue
        except (urllib.error.URLError, ConnectionError, http.client.HTTPException, ApiError) as e:
            last = f"{type(e).__name__}: {e}"
            err(f"attempt {attempt}: {last}")
            time.sleep(min(60, 5 * 2 ** attempt))
            continue
        if not content.strip():
            log_usage(role, task, model or body["model"], usage, time.time() - t0, 0, ok=False, note=f"empty:{finish}")
            last = f"空の応答（finish_reason={finish}）"
            err(f"attempt {attempt}: {last}")
            if finish == "length":
                err(on_runaway("length"))
            time.sleep(3)
            continue
        if finish == "length":
            err("WARN: max_tokens に達して出力が途中で切れた可能性があります")
        log_usage(role, task, model or body["model"], usage, time.time() - t0, len(content),
                  note="truncated" if finish == "length" else "")
        u = usage or {}
        err(f"usage: in={u.get('prompt_tokens')} out={u.get('completion_tokens')} "
            f"reasoning_chars={prog.reasoning} content_chars={len(content)} "
            f"cost={u.get('cost')} sec={time.time() - t0:.0f} model={model}")
        return content
    die(f"再試行しても失敗しました。最後のエラー: {last}")


def main():
    a = sys.argv[1:]
    if a and a[0] == "--ping":
        role = a[1] if len(a) > 1 else "writer"
        if role not in LLM["profiles"]:
            die(f"config.json の llm.profiles に {role} がありません")
        LLM["profiles"][role] = dict(LLM["profiles"][role], max_tokens=2000, reasoning_effort="low")
        t0 = time.time()
        out = run(role, "ping", "疎通確認です。「OK」とだけ返答してください。")
        print(f"PING OK ({time.time() - t0:.1f}s): {out.strip()[:40]}")
        return
    if len(a) < 2:
        die("usage: call_llm.py <packet> <role>  |  call_llm.py --ping [<role>]")
    packet, role = a[0], a[1]
    text = Path(packet).read_text(encoding="utf-8")
    m = re.match(r"<!-- TASK: (\S+)", text)
    task = m.group(1) if m else "write"
    sys.stdout.write(run(role, task, text, os.environ.get("NOVEL_PROGRESS_FILE")))


if __name__ == "__main__":
    main()
