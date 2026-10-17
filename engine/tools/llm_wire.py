"""llm_wire — モデルの窓口ごとの「言い方」の違いを吸収する（標準ライブラリのみ）。engine の call_llm.py と studio の llm.py が使う。

窓口（プロトコル）は3種類:
  chat       OpenAI 互換の /chat/completions（OpenRouter、OpenCode の DeepSeek・GLM・Kimi・MiMo など）
  responses  OpenAI の /responses（OpenCode の GPT・Grok・Muse Spark）
  messages   Anthropic の /messages（OpenCode の Claude・Qwen、OpenCode Go の MiniMax・Qwen）

どのモデルがどの窓口かは、base_url とモデル名から決める（protocol_for）。合わないときは
環境変数 NOVEL_LLM_PROTOCOLS="claude-:messages,gpt-:responses" か、config.json の llm.protocols（{"接頭辞": "窓口"}）で上書きできる。

ヘッダ（v1.1.1。2026-10-13 に opencode.ai へ実際に投げて確かめた）:
  User-Agent         ライブラリ名（Python-urllib）のままだと Cloudflare に弾かれる（HTTP 403・error code 1010）。
                     道具自身の名前を名乗れば通る。
  x-opencode-session OpenCode Go では必須（ないと HTTP 400・MissingSessionID）。会話（作品）ごとに同じ値を送ると、
                     振り分けとプロンプトのキャッシュに効く。指定がなければプロセスごとの値を送る。

費用：OpenCode は応答に費用を入れない。PRICES（OpenCode Go の公開価格）か NOVEL_LLM_PRICES で見積もる（estimate_cost）。
"""
import os
import re
import uuid

UA = "novel-studio/1.1 (+https://github.com/InfraOjisan/novel_studio)"

GO_RULES = [(r"^(claude-|minimax|qwen)", "messages"), (r"^(gpt|grok|muse)", "responses")]
ZEN_RULES = [(r"^claude-", "messages"), (r"^qwen3\.8-max", "chat"), (r"^qwen3\.[5-8]-(flash|plus|max)", "messages"),
             (r"^(gpt|grok|muse)", "responses")]


def _overrides(cfg_protocols=None):
    out = []
    env = os.environ.get("NOVEL_LLM_PROTOCOLS", "")
    for part in env.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out.append((k.strip(), v.strip()))
    for k, v in (cfg_protocols or {}).items():
        if not k.startswith("_"):
            out.append((k, v))
    return out


DEFAULT_SESSION = "novel-studio-" + uuid.uuid4().hex[:16]
PROTOCOLS = ("chat", "responses", "messages")

# OpenCode Go の公開価格（1M トークンあたりのドル。入力, 出力）。2026-10 時点の docs/go。載っていないモデルは見積もらない。
PRICES = {
    "glm-5.2": (1.40, 4.40), "glm-5.1": (1.40, 4.40), "kimi-k2.7-code": (0.95, 4.00), "kimi-k2.6": (0.95, 4.00),
    "mimo-v2.5": (0.14, 0.28), "mimo-v2.5-pro": (1.74, 3.48), "minimax-m3": (0.30, 1.20), "minimax-m2.7": (0.30, 1.20),
    "minimax-m2.5": (0.30, 1.20), "qwen3.7-max": (2.50, 7.50), "qwen3.7-plus": (0.40, 1.60), "qwen3.6-plus": (0.50, 3.00),
    "deepseek-v4-pro": (1.74, 3.48), "deepseek-v4-flash": (0.14, 0.28),
}


def _prices(cfg_prices=None):
    """NOVEL_LLM_PRICES="gpt-6-luna=1.25/10,claude-haiku-5-5=1/5"（1M あたりのドル）と config の llm.prices で足す・上書きする。"""
    out = dict(PRICES)
    for k, v in (cfg_prices or {}).items():
        if not k.startswith("_") and isinstance(v, (list, tuple)) and len(v) == 2:
            out[k] = (float(v[0]), float(v[1]))
    for part in os.environ.get("NOVEL_LLM_PRICES", "").split(","):
        if "=" in part and "/" in part:
            k, v = part.split("=", 1)
            try:
                a, b = v.split("/", 1)
                out[k.strip()] = (float(a), float(b))
            except ValueError:
                pass
    return out


def estimate_cost(model, usage, cfg_prices=None):
    """応答に費用がないときの見積もり（ドル）。分からなければ None。"""
    m = (model or "").split("/")[-1]
    pr = _prices(cfg_prices).get(m)
    if not pr or not usage:
        return None
    return round(((usage.get("prompt_tokens") or 0) * pr[0] + (usage.get("completion_tokens") or 0) * pr[1]) / 1e6, 8)


def alternatives(proto):
    """窓口が合わなかった（404 など）ときに次に試す窓口の順。"""
    return [p for p in ("chat", "messages", "responses") if p != proto]


def protocol_for(base, model, cfg_protocols=None):
    m = (model or "").split("/")[-1].lower()
    for prefix, proto in _overrides(cfg_protocols):
        if m.startswith(prefix.lower()) and proto in PROTOCOLS:
            return proto
    b = (base or "").lower()
    if "api.anthropic.com" in b:
        return "messages"
    if is_opencode(b):
        for pat, proto in (GO_RULES if "/go" in b else ZEN_RULES):
            if re.match(pat, m):
                return proto
    return "chat"


def is_openrouter(base):
    return "openrouter.ai" in (base or "").lower()


def adapt_reasoning(body, base):
    """/chat/completions の推論の指定を窓口の方言に合わせる（v1.1.2）。
    OpenRouter は reasoning: {effort | max_tokens}。ほかの OpenAI 互換（OpenCode など）は標準の reasoning_effort だけを読むので書き換える。"""
    if is_openrouter(base) or "reasoning" not in body:
        return body
    r = body.pop("reasoning") or {}
    if r.get("effort"):
        body["reasoning_effort"] = r["effort"]
    return body


def get_effort(body):
    return body.get("reasoning_effort") or (body.get("reasoning") or {}).get("effort")


def set_effort(body, eff):
    if "reasoning_effort" in body:
        body["reasoning_effort"] = eff
    else:
        body["reasoning"] = {"effort": eff}


def is_opencode(base):
    """opencode.ai か、その形を真似た窓口（パスに /zen/。テスト用の偽サーバ）。"""
    b = (base or "").lower()
    return "opencode.ai" in b or "/zen/" in b


def hint(code, detail, proto=None):
    """よくある失敗に、直し方の一言を添える。"""
    if code == 403 and "1010" in detail:
        return "（窓口の前の Cloudflare に弾かれました：User-Agent。道具が古い作品は「道具を最新にする」を押してください）"
    if "MissingSessionID" in detail or "x-opencode-session" in detail:
        return "（OpenCode Go は x-opencode-session ヘッダが必須です。道具が古い作品は「道具を最新にする」を押してください）"
    if code == 404 or "not supported" in detail.lower():
        return f"（窓口 {proto} にこのモデルがない可能性。NOVEL_LLM_PROTOCOLS=\"モデル名の頭:chat|messages|responses\" で指定できます）"
    if code == 401:
        return "（鍵が違うか、窓口と鍵の組み合わせが違います。OpenCode Go の鍵は https://opencode.ai/zen/go/v1 で使います）"
    return ""


def headers(key, protocol="chat", session=None, title="novel_studio", stream=False):
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": UA,
         "HTTP-Referer": "https://github.com/InfraOjisan/novel_studio", "X-Title": title}
    if stream:
        h["Accept"] = "text/event-stream"
    if protocol == "messages":
        h["x-api-key"] = key
        h["anthropic-version"] = "2023-06-01"
    h["x-opencode-session"] = session or DEFAULT_SESSION  # OpenRouter には無害な余分のヘッダ
    return h


def build(protocol, model, system, text, temperature=None, max_tokens=16000, reasoning=None, json_mode=False):
    """返り値：(パス, 本文)。chat は呼び出し側が組む（ストリーミングや OpenRouter 固有の欄があるため）。"""
    if protocol == "messages":
        body = {"model": model, "max_tokens": int(max_tokens), "messages": [{"role": "user", "content": text}]}
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = min(1.0, float(temperature))
        return "/messages", body
    if protocol == "responses":
        body = {"model": model, "input": [{"role": "user", "content": text}], "max_output_tokens": int(max_tokens)}
        if system:
            body["instructions"] = system
        if reasoning:
            body["reasoning"] = {"effort": reasoning}
        if json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        return "/responses", body
    raise ValueError(f"build は chat 以外の窓口用です：{protocol}")


def parse(protocol, d):
    """返り値：(本文, 終わり方, usage{prompt_tokens, completion_tokens, cost}, モデル名)。終わり方は chat にそろえる（stop / length）。"""
    if d.get("error"):
        raise ValueError(str(d["error"])[:500])
    if protocol == "messages":
        text = "".join(b.get("text", "") for b in d.get("content") or [] if b.get("type") == "text")
        u = d.get("usage") or {}
        finish = "length" if d.get("stop_reason") == "max_tokens" else "stop"
        return text, finish, {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens"), "cost": u.get("cost")}, d.get("model")
    if protocol == "responses":
        parts = []
        if isinstance(d.get("output_text"), str):
            parts.append(d["output_text"])
        else:
            for item in d.get("output") or []:
                if item.get("type") == "message":
                    for c in item.get("content") or []:
                        if c.get("type") in ("output_text", "text"):
                            parts.append(c.get("text", ""))
        u = d.get("usage") or {}
        reason = (d.get("incomplete_details") or {}).get("reason")
        finish = "length" if d.get("status") == "incomplete" and reason in ("max_output_tokens", "max_tokens") else "stop"
        return "".join(parts), finish, {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens"), "cost": u.get("cost")}, d.get("model")
    ch = (d.get("choices") or [{}])[0]
    u = d.get("usage") or {}
    return ((ch.get("message") or {}).get("content") or ""), ch.get("finish_reason"), u, d.get("model")
