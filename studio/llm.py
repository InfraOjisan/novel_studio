"""llm — studio の OpenRouter 呼び出し（標準ライブラリのみ）。1回ごとの費用を返す。

engine の tools/call_llm.py はストリーミングで長い本文を受ける。studio の依頼（提案・骨格・BRIEF）は
JSON を一つ返すだけなので、ストリーミングなしで受け、usage.cost（ドル）を記録する。
cost が返らないプロバイダのときは、/models の単価から計算する。
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KEY_NAMES = ["NOVEL_PROVIDER_APIKEY", "NOVEL_LLM_API_KEY", "OPENROUTER_API_KEY", "HERMES_CUSTOM_OPENROUTER_API_KEY"]
CACHE = Path(__file__).resolve().parent / ".cache"


def _dotenv(name):
    """リポジトリ直下・studio/・作品置き場（NOVEL_WORKS_DIR）の .env から値を読む。"""
    places = [REPO / ".env", REPO / "studio" / ".env", REPO / "works" / ".env"]
    if os.environ.get("NOVEL_WORKS_DIR"):
        places.insert(0, Path(os.environ["NOVEL_WORKS_DIR"]) / ".env")
    for env in places:
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                k, _, v = line.strip().partition("=")
                if k.strip().removeprefix("export ").strip() == name and v.strip():
                    return v.strip().strip('"').strip("'")
    return None


def base_url():
    return (os.environ.get("NOVEL_LLM_BASE_URL") or _dotenv("NOVEL_LLM_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")


def find_key():
    for n in KEY_NAMES:
        if os.environ.get(n):
            return os.environ[n].strip()
    for n in KEY_NAMES:
        v = _dotenv(n)
        if v:
            return v
    return None


def models(refresh=False):
    """[{id, name, in_per_m, out_per_m, context}]。OpenRouter の /models は鍵なしで読める。1日キャッシュ。"""
    CACHE.mkdir(exist_ok=True)
    f = CACHE / "models.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        with urllib.request.urlopen(urllib.request.Request(base_url() + "/models", headers={"User-Agent": "novel-studio"}), timeout=20) as r:
            data = json.loads(r.read().decode("utf-8")).get("data", [])
        out = []
        for m in data:
            p = m.get("pricing") or {}
            try:
                pin, pout = float(p.get("prompt") or 0) * 1e6, float(p.get("completion") or 0) * 1e6
            except ValueError:
                pin = pout = 0.0
            out.append({"id": m["id"], "name": m.get("name", m["id"]), "in_per_m": round(pin, 4), "out_per_m": round(pout, 4),
                        "context": m.get("context_length")})
        out.sort(key=lambda x: x["id"])
        f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return []


def price_of(model):
    for m in models():
        if m["id"] == model:
            return m["in_per_m"] / 1e6, m["out_per_m"] / 1e6
    return None


class LLMError(Exception):
    pass


def chat(model, prompt, system="", temperature=0.7, max_tokens=16000, json_mode=True, reasoning=None, timeout=600, retries=2):
    """返り値：(本文, 記録)。記録 = {model, in, out, usd, sec}。失敗は LLMError。"""
    key = find_key()
    if not key:
        raise LLMError("APIキーが見つかりません。リポジトリ直下の .env に OPENROUTER_API_KEY=... を置くか、環境変数で渡してください。")
    body = {"model": model, "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": max_tokens, "usage": {"include": True}}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if reasoning:
        body["reasoning"] = {"effort": reasoning}
    last = None
    for attempt in range(retries + 1):
        t0 = time.time()
        req = urllib.request.Request(base_url() + "/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://github.com/InfraOjisan/novel_studio", "X-Title": "novel_studio"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last = f"HTTP {e.code}: {detail}"
            if e.code == 400 and "response_format" in body:
                body.pop("response_format")
                continue
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(60, 10 * 2 ** attempt))
                continue
            raise LLMError(last)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(5)
                continue
            raise LLMError(last)
        if d.get("error"):
            raise LLMError(json.dumps(d["error"], ensure_ascii=False)[:500])
        ch = (d.get("choices") or [{}])[0]
        text = ((ch.get("message") or {}).get("content") or "").strip()
        u = d.get("usage") or {}
        usd = u.get("cost")
        if usd is None:
            pr = price_of(d.get("model") or model)
            usd = (u.get("prompt_tokens", 0) * pr[0] + u.get("completion_tokens", 0) * pr[1]) if pr else 0.0
        rec = {"model": d.get("model") or model, "in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
               "usd": round(float(usd or 0), 6), "sec": round(time.time() - t0, 1), "finish": ch.get("finish_reason")}
        if not text:
            last = f"空の応答（finish_reason={ch.get('finish_reason')}。推論で出力枠を使い切った可能性）"
            if attempt < retries:
                body["max_tokens"] = int(body["max_tokens"] * 1.5)
                if reasoning:
                    body["reasoning"] = {"effort": "low"}
                continue
            raise LLMError(last)
        return text, rec
    raise LLMError(last or "不明なエラー")


def extract_json(raw):
    """前置きやコードブロックが混ざっても、最初に読める JSON オブジェクトを取り出す。"""
    import re
    raw = re.sub(r"```(?:json)?", "", raw or "")
    i = raw.find("{")
    while i >= 0:
        depth, instr, esc = 0, False, False
        for j in range(i, len(raw)):
            c = raw[j]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
                continue
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[i:j + 1])
                    except json.JSONDecodeError:
                        break
        i = raw.find("{", i + 1)
    return None
