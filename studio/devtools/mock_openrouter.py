#!/usr/bin/env python3
"""mock_openrouter — OpenRouter（OpenAI 互換 Chat Completions）の偽物。費用をかけずに studio と engine を端から端まで通すため。

  python3 studio/devtools/mock_openrouter.py [--port 8765]
  NOVEL_LLM_BASE_URL=http://127.0.0.1:8765/api/v1 NOVEL_PROVIDER_APIKEY=mock python3 studio/server.py

依頼文の目印で答えを変える:
  <!-- TASK: design|canon-review|write|review|ledger|final|polish|rescue-canon|rescue-chapter|proofread -->  （engine のパケット）
  <!-- STUDIO: propose|revise|skeleton|classify|brief|brief-revise -->                                       （studio の依頼文）
環境変数:
  MOCK_FAIL_CANON_REVIEW=N   CANON 審査を最初の N 回だけ不合格にする（設計のやり直しの経路を通すため）
  MOCK_FAIL_CHAPTER=K        第K章の審査を毎回不合格にする（CHAPTER_EXHAUSTED → rescue の経路を通すため。rescue 後は合格）
  MOCK_FAIL_FINAL=K          最終審査で第K章に重大な指摘を出し続ける（第K章を上位モデルで直すまで）
  MOCK_RESCUE_FAIL_MODEL=M   このモデル（カンマ区切り）で rescue した章は、審査に通らないままにする（別のモデルを選ぶ経路を通すため）
  MOCK_DELAY=秒              1回の応答を遅らせる（進み具合の表示を見るため）
  MOCK_MODELS_FILE=path       /models に、そのファイル（OpenRouter の /models の写し）を返す（モデルの一覧を実際の件数で試すため）
  MOCK_SKELETON_MAX=N        骨格の抜き出しで、1回に N 案より多く頼まれたら失敗する（上流が時間切れで諦めるのを真似る。小分けの経路を通すため）
  MOCK_FAIL_SKELETON=P03,P05 この案を含む骨格の依頼は必ず失敗する（取れない案が残る経路を通すため）

ハーネスの試験（harness/exams）でも、費用をかけずに進行役の振る舞いだけを見るのに使える。
"""
import argparse
import hashlib
import json
import os
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"canon_review": 0, "rescued": set()}
LOCK = threading.Lock()

MODELS = [
    ("anthropic/claude-opus-5.5", 5.0, 25.0), ("anthropic/claude-haiku-5.5", 0.1, 0.5),
    ("openai/gpt-6-luna-pro", 0.1, 0.5), ("deepseek/deepseek-v4.1-flash", 0.07, 0.28),
    ("aion-labs/aion-3.5-mini", 0.7, 1.4), ("meta/muse-spark-1.3-contributor", 0.2, 0.8),
    ("moonshotai/kimi-k3", 0.5, 2.0), ("z-ai/glm-5.3", 0.4, 1.6), ("qwen/qwen3.8-flash", 0.05, 0.2),
]
PRICE = {m: (i / 1e6, o / 1e6) for m, i, o in MODELS}
IMAGE_MODELS = [("google/gemini-2.5-flash-image", 0.039), ("openai/gpt-image-2", 0.05), ("black-forest-labs/flux.2-pro", 0.04)]


def fake_png(w=400, h=640, seed="x"):
    """標準ライブラリだけで、グラデーションの PNG を作る（偽の画像生成）。"""
    import struct
    import zlib
    hsh = int(hashlib.md5(seed.encode()).hexdigest()[:6], 16)
    r0, g0, b0 = (hsh >> 16) & 255, (hsh >> 8) & 255, hsh & 255
    raw = b"".join(b"\x00" + bytes(v for x in range(w) for v in (
        (r0 * (h - y) + 30 * y) // h, (g0 * (h - y) + 40 * y) // h, (b0 * (h - y) + 70 * y) // h)) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")

WORDS = ["霧", "灯", "石段", "川面", "鈴", "外套", "指先", "帳面", "窓", "足音", "古い地図", "湯気", "鍵", "蝋燭", "砂", "風見",
         "階段", "手紙", "硝子", "縄", "舟", "鐘楼", "棚", "籠", "影", "火床", "泥", "袖", "羽根", "皿"]
VERBS = ["揺れていた", "冷えていた", "鳴った", "黙っていた", "見えなくなった", "光った", "残っていた", "傾いた", "濡れていた", "ほどけた"]


def section(text, name):
    m = re.search(rf'<資料 名前="{re.escape(name)}[^"]*">\n(.*?)\n</資料>', text, flags=re.S)
    return m.group(1) if m else ""


def sections_like(text, prefix):
    return re.findall(rf'<資料 名前="{re.escape(prefix)}[^"]*">\n(.*?)\n</資料>', text, flags=re.S)


def prose(n_chars, seed):
    rnd = random.Random(seed)
    out, total, k = [], 0, 0
    while total < n_chars:
        k += 1
        a, b = rnd.sample(WORDS, 2)
        s = f"{a}のそばで{b}が{rnd.choice(VERBS)}。"
        if k % 5 == 0:
            s = f"「{rnd.choice(WORDS)}は、{rnd.choice(WORDS)}の{k}番目の棚にある」と誰かが言った。"
        if k % 9 == 0:
            s += "\n\n"
        out.append(s)
        total += len(s)
    text = "".join(out).strip()
    if not text.endswith("。"):
        text += "。"
    return text


def slot_events(text, header):
    ev = {}
    for sid, word in re.findall(r"^- ([A-Z]\d)（[^）]*）：(設置|補強|回収)", section(text, header), flags=re.M):
        ev[sid] = {"設置": "planted", "補強": "reinforced", "回収": "recovered"}[word]
    return ev


def engine_answer(task, ch, text, model=""):
    if task == "design":
        prev = section(text, "前回のCANON")
        if prev:
            return prev
        tpl = section(text, "CANONテンプレート")
        k = [0]

        def fill(_):
            k[0] += 1
            return f"仮の設定{k[0]}（偽モデル）"
        out = re.sub(r"（記入）", fill, tpl)
        out = re.sub(r"タイトル：[^\n]*", "タイトル：霧の帳面", out, count=1)
        return out
    if task == "canon-review":
        with LOCK:
            STATE["canon_review"] += 1
            n = STATE["canon_review"]
        if n <= int(os.environ.get("MOCK_FAIL_CANON_REVIEW", "0")):
            return json.dumps({"pass": False, "issues": [{"severity": "high", "item": 9, "detail": "時間表と第2章カードの日付が食い違う（偽モデル）", "fix": "日付をそろえる"}]}, ensure_ascii=False)
        return json.dumps({"pass": True, "issues": [{"severity": "low", "item": 12, "detail": "不変条件の言い回しが硬い（偽モデル）", "fix": "任意"}]}, ensure_ascii=False)
    if task in ("write", "rescue-chapter", "polish"):
        if task == "rescue-chapter":
            if model not in os.environ.get("MOCK_RESCUE_FAIL_MODEL", "").split(","):  # このモデルの手直しは審査に通らない
                with LOCK:
                    STATE["rescued"].add(ch)
            return section(text, "いまの原稿")
        if task == "polish":
            return section(text, f"磨く原稿（第{ch}章）")
        m = re.search(r"本文 (\d+) 字前後", text)
        target = int(m.group(1)) if m else 2000
        return f"# 第{ch}章　章題その{ch}\n" + prose(target, f"{ch}-{hashlib.md5(text.encode()).hexdigest()[:6]}") + "\n"
    if task == "review":
        found = slot_events(text, "この章の伏線（必須）")
        fail = os.environ.get("MOCK_FAIL_CHAPTER")
        if fail and int(fail) == ch and ch not in STATE["rescued"]:
            return json.dumps({"pass": False, "slots_found": found, "violations": [{"severity": "high", "type": "canon", "detail": "「三番目の棚」と書いているが CANON では二番目（偽モデル）", "fix": "二番目にする"}]}, ensure_ascii=False)
        return json.dumps({"pass": True, "slots_found": found, "violations": [{"severity": "low", "type": "typo", "detail": "読点が多い（偽モデル）", "fix": "任意"}]}, ensure_ascii=False)
    if task == "ledger":
        body = section(text, f"第{ch}章の本文")
        last = [l for l in body.strip().splitlines() if l.strip()][-1]
        last = re.split(r"(?<=。)", last.strip())[-2] if last.count("。") > 1 else last
        ev = slot_events(text, "依頼")
        evlines = re.findall(r"^- ([A-Z]\d)（[^）]*）：(設置|補強|回収)", text, flags=re.M)
        for sid, w in evlines:
            ev[sid] = {"設置": "planted", "補強": "reinforced", "回収": "recovered"}[w]
        fields = re.findall(r"^([A-Z_]+):", section(text, "依頼"), flags=re.M)
        out = [f"## CH{ch:02d}"]
        for f in dict.fromkeys(fields):
            if f == "SLOT":
                out += [f"SLOT: {sid}={st} | 描写: 第{ch}章の場面で見せた（偽モデル）" for sid, st in ev.items()]
            elif f == "LAST":
                out.append(f"LAST: {last.strip()}")
            else:
                out.append(f"{f}: 第{ch}章の{f}（偽モデル）")
        return "\n".join(out)
    if task == "final":
        ff = os.environ.get("MOCK_FAIL_FINAL")
        if ff and int(ff) not in STATE["rescued"]:
            return json.dumps({"pass": False, "issues": [{"severity": "high", "chapter": int(ff), "detail": "伏線F1の回収が駆け足で、設置の場面と結びついていない（偽モデル）", "fix": "設置の場面の小道具を回収の場面で呼び戻す"}],
                               "carry_over": [], "summary": "最終章が苦しい（偽モデル）"}, ensure_ascii=False)
        return json.dumps({"pass": True, "issues": [], "carry_over": [], "summary": "偽モデルの講評：全章そろっている。"}, ensure_ascii=False)
    if task == "rescue-canon":
        return section(text, "いまのCANON")
    if task == "proofread":
        body = section(text, f"校正する原稿（第{ch}章）")
        m = re.search(r"「([^」]{4,20})」と誰かが言った。", body)
        edits = []
        if m and body.count(m.group(0)) == 1:
            edits.append({"before": m.group(0), "after": m.group(0).replace("と誰かが言った", "と、誰かが言った"), "kind": "表現", "reason": "読点で間をとる（偽モデル）"})
        edits.append({"before": "存在しない原文", "after": "x", "kind": "誤字", "reason": "機械で却下されるはずの案（偽モデル）"})
        return json.dumps({"edits": edits}, ensure_ascii=False)
    return "OK"


GENRES = {"SF": ["冷凍睡眠の船でAIが乗員を管理する", "氷の衛星で微生物を見つける", "軌道エレベーターの保守員が時間の遅れに気づく"],
          "ミステリ": ["語り手が実は加害者だった", "社内報の暗号で横領を告発する", "密室の会議室で役員が倒れる"]}


FAIL = "__MOCK_FAIL__"  # 上流のプロバイダが諦めた（504 の代わりに、再試行されない 400 で返す。テストを速くするため）


def studio_answer(kind, text, model):
    seed = int(hashlib.md5((model + text[-200:]).encode()).hexdigest()[:8], 16)
    rnd = random.Random(seed)
    if kind == "propose":
        m = re.search(r"案の数：(\d+)", text)
        n = int(m.group(1)) if m else 2
        th = re.search(r'<資料 名前="テーマ">\n(.*?)\n</資料>', text, flags=re.S)
        genre = "ミステリ" if th and "ミステリ" in th.group(1) else "SF"
        pool = GENRES[genre]
        props = []
        for i in range(n):
            # 偽モデルでも「収束」を再現する：多くの案が pool[0] に寄り、たまに外れる
            core = pool[0] if rnd.random() < 0.6 else rnd.choice(pool[1:])
            if "避ける骨格" in text and core == pool[0]:
                core = pool[2]
            props.append({"kind": "本線" if i == 0 else "別観点", "genre": genre, "title": f"{core[:6]}の{rnd.choice(WORDS)}",
                          "setting": f"{core}。舞台は{rnd.choice(WORDS)}のある場所。", "synopsis": f"{core}話。{prose(160, seed + i)}",
                          "highlight": f"終盤で{rnd.choice(WORDS)}の意味が反転する。", "appeal": "読者は最後の一行で前半を読み直したくなる。",
                          "must_keep": "時系列を崩さないこと。理由：反転が効かなくなるため。", "genre_reason": f"{genre}でなければ成立しない理由（偽モデル）。"})
        return json.dumps({"proposals": props}, ensure_ascii=False)
    if kind == "revise":
        m = re.search(r'<資料 名前="元の案">\n(.*?)\n</資料>', text, flags=re.S)
        p = json.loads(m.group(1)) if m else {}
        p["title"] = (p.get("title") or "無題") + "（改）"
        p["synopsis"] = (p.get("synopsis") or "") + " 指示を受けて終盤を変えた。"
        return json.dumps({"proposal": p}, ensure_ascii=False)
    if kind == "skeleton":
        items = re.findall(r'^### (P\d+)\n(.*?)(?=^### P\d+|\Z)', section(text, "提案"), flags=re.S | re.M)
        cap = int(os.environ.get("MOCK_SKELETON_MAX", "0") or 0)
        bad = {x for x in os.environ.get("MOCK_FAIL_SKELETON", "").split(",") if x}
        if (cap and len(items) > cap) or any(pid in bad for pid, _ in items):
            return FAIL
        out = []
        for pid, body in items:
            if "冷凍睡眠" in body or "語り手が実は加害者" in body:
                sk = {"deceiver": "AI" if "冷凍" in body else "語り手", "hidden": "乗員の選別" if "冷凍" in body else "自分の罪",
                      "layer": "語り", "flip": "管理者が加害者", "stage": "宇宙船" if "冷凍" in body else "会社", "ending": "ビター"}
            else:
                sk = {"deceiver": rnd.choice(["誰も", "組織", "自然"]), "hidden": rnd.choice(["時間", "記憶", "数字"]),
                      "layer": rnd.choice(["物理", "制度"]), "flip": rnd.choice(["前提が逆", "被害者が救う側"]),
                      "stage": rnd.choice(["衛星", "塔", "島"]), "ending": rnd.choice(["メリーバッド", "ビター"])}
            out.append({"id": pid, **sk, "one_line": f"{sk['deceiver']}が{sk['hidden']}を隠し{sk['flip']}"})
        return json.dumps({"skeletons": out}, ensure_ascii=False)
    if kind == "classify":
        ids = re.findall(r"^### (P\d+)", section(text, "提案"), flags=re.M)
        return json.dumps({"labels": [{"id": i, "label": rnd.choice(["収束", "バリエーション", "外れ値"]), "reason": "偽モデルの判断"} for i in ids],
                           "types": ["偽モデルの型"]}, ensure_ascii=False)
    if kind in ("brief", "brief-revise"):
        m = re.search(r"章の数：(\d+)", text)
        n = int(m.group(1)) if m else 5
        chs = [{"n": i, "title": f"仮称{i}", "tension": min(5, 1 + i % 5), "reveal": min(5, i * 5 // n), "function": f"第{i}章の機能（偽モデル）",
                "hook": "小さな引き", "forbidden": "結末を明かすこと" if i < n else ""} for i in range(1, n + 1)]
        mid = max(2, n // 2)
        b = {"title": "霧の帳面", "logline": "偽モデルの一行要旨。", "genre": "SF", "audience": "一般読者",
             "length": {"chapters": n, "chapter_target": 1800, "chapter_min": 1500, "chapter_max": 2600},
             "narration": "三人称一元（視点人物：ミナ）", "ending_type": "ビターエンド", "core": "記録を残すことの重さ",
             "genre_reason": "科学の説明が解決になる", "setting": "氷の衛星の観測所",
             "characters": [{"name": "ミナ", "role": "観測員", "fixed": "記録を信じる"}, {"name": "ソラ", "role": "整備士", "fixed": "嘘をつかない"}],
             "free": "観測所の細部", "rules": {"style": "乾いた文体", "language": "数字は漢数字", "characters": "二人とも善意で動く", "lines": ["魔法を出さない"]},
             "chapters": chs,
             "slots": [{"id": "F1", "name": "帳面の空白", "function": "記録の欠落", "plan": [[1, "planted"], [mid, "reinforced"], [n, "recovered"]]},
                       {"id": "C1", "name": "期限", "function": "物語の時計", "plan": [[1, "planted"], [n, "recovered"]]}],
             "fairplay": ["第2章までに空白の存在が読者に示される"], "ending": "ミナは帳面を閉じ、空白を空白のまま残す。",
             "design_notes": ["期限から逆算する"]}
        if kind == "brief-revise":
            b["logline"] += "（指示を反映）"
        return json.dumps({"brief": b}, ensure_ascii=False)
    return "{}"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _gate(self):
        """本物の OpenCode Go で確かめた門（2026-10-13）を真似る。通さないときは応答を返して True。"""
        if self.headers.get("User-Agent", "Python-urllib").startswith("Python-urllib"):
            data = b"error code: 1010"  # Cloudflare：ライブラリ名の User-Agent は弾かれる
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True
        if self.command == "POST" and "/zen/go/" in self.path and not self.headers.get("x-opencode-session"):
            self._json(400, {"type": "error", "error": {"type": "MissingSessionID",
                                                         "message": "Request is missing x-opencode-session and cannot be routed efficiently."}})
            return True
        return False

    def do_GET(self):
        if self._gate():
            return
        if "/zen/" in self.path and self.path.rstrip("/").endswith("/models"):  # OpenCode：価格なしの一覧
            return self._json(200, {"object": "list", "data": [{"id": m.split("/")[-1], "object": "model", "owned_by": "opencode"}
                                                               for m, _, _ in MODELS] + [{"id": "mockwire-odd", "object": "model"}]})
        if self.path.rstrip("/").endswith("/images/models") or "output_modalities=image" in self.path:
            return self._json(200, {"data": [{"id": m, "name": m, "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
                                              "pricing": {"image": str(p)}} for m, p in IMAGE_MODELS]})
        if self.path.rstrip("/").endswith("/models") and os.environ.get("MOCK_MODELS_FILE"):  # 本物の一覧の写しを返す（画面の一覧を、実際の件数で試すため）
            with open(os.environ["MOCK_MODELS_FILE"], encoding="utf-8") as f:
                return self._json(200, json.load(f))
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"data": [{"id": m, "name": m, "context_length": 200000,
                                              "pricing": {"prompt": f"{i / 1e6:.10f}", "completion": f"{o / 1e6:.10f}"}} for m, i, o in MODELS]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self._gate():
            return
        path = self.path.rstrip("/")
        if path.endswith("/responses") or path.endswith("/messages") or "/zen/" in path:
            return self._opencode(path, body)
        if self.path.rstrip("/").endswith("/images"):
            if body.get("model") not in dict(IMAGE_MODELS):
                return self._json(404, {"error": {"message": "mock: not an image model"}})
            if body.get("aspect_ratio") == "2:3" and body.get("model") == "black-forest-labs/flux.2-pro":
                return self._json(400, {"error": {"message": "mock: aspect ratio not supported"}})  # 縦横比の切り替えを通すため
            time.sleep(float(os.environ.get("MOCK_DELAY", "0")))
            png = fake_png(seed=body.get("prompt", "") + body["model"])
            return self._json(200, {"created": int(time.time()), "data": [{"b64_json": __import__("base64").b64encode(png).decode(), "media_type": "image/png"}],
                                    "usage": {"cost": dict(IMAGE_MODELS)[body["model"]]}})
        text = body["messages"][-1]["content"]
        model = (body.get("models") or [body.get("model")])[0]
        if model == "mock/broken":
            return self._json(400, {"error": {"message": "mock: this model always fails"}})
        if model == "mock/runaway":  # 推論だけを流し続けて本文を出さない（審査役の推論の暴走。v1.1.2）
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for _ in range(200):
                    chunk = {"model": model, "choices": [{"delta": {"reasoning": "考えている。" * 80}}]}
                    self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.1)
                self.wfile.write(b'data: {"choices": [{"delta": {}, "finish_reason": "length"}]}\n\ndata: [DONE]\n\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if model == "mock/thinker" and int(body.get("max_tokens") or 0) < 32000:  # 出力枠が狭いと推論で使い切って本文が空
            fin = {"model": model, "choices": [{"delta": {}, "finish_reason": "length"}],
                   "usage": {"prompt_tokens": 100, "completion_tokens": body.get("max_tokens"), "cost": 0.001,
                             "completion_tokens_details": {"reasoning_tokens": body.get("max_tokens")}}}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps(fin)}\n\ndata: [DONE]\n\n".encode())
            return
        m = re.match(r"<!-- TASK: (\S+)(?: CH=(\d+))?", text)
        s = re.search(r"<!-- STUDIO: (\S+) -->", text)
        if m:
            out = engine_answer(m.group(1), int(m.group(2) or 0), text, model)
        elif s:
            out = studio_answer(s.group(1), text, model)
        else:
            out = "OK"
        time.sleep(float(os.environ.get("MOCK_DELAY", "0")))
        if out == FAIL:
            return self._json(400, {"error": {"message": "mock: upstream provider gave up (request too large)"}})
        pin, pout = PRICE.get(model, (1e-6, 2e-6))
        usage = {"prompt_tokens": len(text) // 2, "completion_tokens": len(out) // 2}
        usage["cost"] = round(usage["prompt_tokens"] * pin + usage["completion_tokens"] * pout, 8)
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b": OPENROUTER PROCESSING\n\n")
            for i in range(0, len(out), 400):
                chunk = {"model": model, "choices": [{"delta": {"content": out[i:i + 400]}}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            fin = {"model": model, "choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage}
            self.wfile.write(f"data: {json.dumps(fin)}\n\ndata: [DONE]\n\n".encode())
            return
        self._json(200, {"model": model, "choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}], "usage": usage})


def zen_route(model):
    """偽 OpenCode の窓口の割り当て。mockwire-odd は道具の規則（chat）と違う /messages にだけある＝窓口の切り替えを試すため。"""
    m = model.split("/")[-1]
    if re.match(r"^(gpt|grok|muse)", m):
        return "responses"
    if re.match(r"^(claude-|minimax|qwen|mockwire-odd)", m):
        return "messages"
    return "chat"


def _opencode_handler(self, path, body):
    proto = "responses" if path.endswith("/responses") else "messages" if path.endswith("/messages") else "chat"
    model = body.get("model", "")
    if "/zen/" in path and zen_route(model) != proto:
        return self._json(404, {"type": "error", "error": {"type": "NotFound", "message": f"Model {model} is not supported on this endpoint"}})
    if proto == "messages" and not self.headers.get("anthropic-version"):
        return self._json(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "anthropic-version header is required"}})
    if proto == "responses":
        inp = body.get("input")
        text = inp if isinstance(inp, str) else "".join(x.get("content", "") for x in inp or [] if isinstance(x.get("content"), str))
    elif proto == "messages":
        text = (body.get("messages") or [{}])[-1].get("content", "")
    else:
        text = (body.get("messages") or [{}])[-1].get("content", "")
    m = re.match(r"<!-- TASK: (\S+)(?: CH=(\d+))?", text)
    st = re.search(r"<!-- STUDIO: (\S+) -->", text)
    out = engine_answer(m.group(1), int(m.group(2) or 0), text, model) if m else studio_answer(st.group(1), text, model) if st else "OK"
    time.sleep(float(os.environ.get("MOCK_DELAY", "0")))
    pin, pout = len(text) // 2, len(out) // 2  # 費用は返さない（OpenCode と同じ）
    if proto == "responses":
        return self._json(200, {"id": "resp_mock", "object": "response", "model": model, "status": "completed",
                                "output": [{"type": "reasoning", "summary": []},
                                           {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": out}]}],
                                "usage": {"input_tokens": pin, "output_tokens": pout}})
    if proto == "messages":
        return self._json(200, {"id": "msg_mock", "type": "message", "role": "assistant", "model": model, "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": out}], "usage": {"input_tokens": pin, "output_tokens": pout}})
    usage = {"prompt_tokens": pin, "completion_tokens": pout}
    if body.get("stream"):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(out), 400):
            self.wfile.write(f"data: {json.dumps({'model': model, 'choices': [{'delta': {'content': out[i:i + 400]}}]}, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(f"data: {json.dumps({'model': model, 'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': usage})}\n\ndata: [DONE]\n\n".encode())
        return
    return self._json(200, {"model": model, "choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}], "usage": usage})


H._opencode = _opencode_handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    print(f"mock OpenRouter on http://127.0.0.1:{a.port}/api/v1  （OpenCode Go の真似は /zen/go/v1）", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
