#!/usr/bin/env python3
"""novel_studio — 小説を1から書くための Web アプリ（標準ライブラリのみ。Python 3.9 以上）。

  python3 studio/server.py [--port 8780] [--host 127.0.0.1]
  → ブラウザで http://127.0.0.1:8780

APIキー：リポジトリ直下の .env（OPENROUTER_API_KEY=... または NOVEL_PROVIDER_APIKEY=...）か環境変数。キーは画面には出しません。
費用をかけずに試す：python3 studio/devtools/mock_openrouter.py を立ててから
  NOVEL_LLM_BASE_URL=http://127.0.0.1:8765/api/v1 NOVEL_PROVIDER_APIKEY=mock python3 studio/server.py

時間のかかる工程（提案・分類・BRIEF・上位モデルの例外処理・校正）はジョブとして裏で走り、画面は /api/jobs/<id> を見て待ちます。
自分の手元（127.0.0.1）だけで使う前提です。外に公開しないでください。
"""
import argparse
import json
import mimetypes
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core
import llm

WEB = Path(__file__).resolve().parent / "web"
JOBS = {}
JOBS_LOCK = threading.Lock()


def run_job(slug, kind, fn, *a, **kw):
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "slug": slug, "kind": kind, "state": "running", "started": time.time(), "result": None, "error": None}
    with JOBS_LOCK:
        JOBS[jid] = job

    def target():
        try:
            job["result"] = fn(*a, **kw)
            job["state"] = "done"
        except core.StudioError as e:
            job["error"], job["state"] = str(e), "error"
        except Exception as e:  # noqa: BLE001 画面に理由を返す
            job["error"], job["state"] = f"{type(e).__name__}: {e}", "error"
            traceback.print_exc()
        job["ended"] = time.time()
    threading.Thread(target=target, daemon=True).start()
    return jid


def running_jobs(slug):
    return [{"id": j["id"], "kind": j["kind"], "since": round(time.time() - j["started"])} for j in JOBS.values()
            if j["slug"] == slug and j["state"] == "running"]


class H(BaseHTTPRequestHandler):
    server_version = "novel-studio/1.0"

    def log_message(self, fmt, *a):
        if "/api/jobs/" not in (a[0] if a else ""):
            super().log_message(fmt, *a)

    def _send(self, code, obj=None, raw=None, ctype="application/json; charset=utf-8", headers=None):
        data = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        parts = [p for p in u.path.split("/") if p]
        try:
            if not parts or parts[0] != "api":
                f = WEB / ("index.html" if not parts else "/".join(parts))
                if not f.resolve().is_relative_to(WEB.resolve()) or not f.exists():
                    return self._send(404, {"error": "not found"})
                return self._send(200, raw=f.read_bytes(), ctype=(mimetypes.guess_type(str(f))[0] or "text/plain") + "; charset=utf-8")
            if parts[1:] == ["works"]:
                return self._send(200, {"works": core.list_works(), "has_key": bool(llm.find_key()), "base_url": llm.base_url()})
            if parts[1:] == ["models"]:
                return self._send(200, {"models": llm.models(refresh=q.get("refresh") == "1"), "settings": core.settings()})
            if parts[1] == "jobs" and len(parts) == 3:
                j = JOBS.get(parts[2])
                return self._send(200 if j else 404, j or {"error": "no such job"})
            if parts[1] == "works" and len(parts) >= 3:
                slug = parts[2]
                if len(parts) == 3:
                    snap = core.snapshot(slug)
                    snap["jobs"] = running_jobs(slug)
                    return self._send(200, snap)
                if parts[3] == "file":
                    t = core.read_file(slug, q.get("path", ""))
                    if t is None:  # まだ無いのは異常ではない（画面が毎回確かめる）
                        return self._send(200, {"path": q.get("path"), "text": None})
                    if isinstance(t, Path):
                        return self._send(200, raw=t.read_bytes(), ctype="application/epub+zip",
                                          headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(t.name)}"})
                    return self._send(200, {"path": q.get("path"), "text": t})
                if parts[3] == "brief" and len(parts) == 5:
                    return self._send(200, core.render_brief(slug, parts[4]))
            return self._send(404, {"error": "not found"})
        except core.StudioError as e:
            return self._send(400, {"error": str(e)})

    def do_POST(self):
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        try:
            b = self._body()
            if parts[:2] != ["api", "works"]:
                return self._send(404, {"error": "not found"})
            if len(parts) == 2:
                st = core.create_work(b["slug"], b.get("theme", {}))
                return self._send(200, {"slug": st["slug"]})
            slug, act = parts[2], (parts[3] if len(parts) > 3 else "")
            if act == "theme":
                core.change_theme(slug, b["theme"])
                return self._send(200, {"ok": True})
            if act == "propose":
                return self._send(200, {"job": run_job(slug, "propose", core.propose, slug, b["models"], b.get("n", 2), b.get("instruction", ""),
                                                       b.get("mode", "new"), float(b.get("temperature", 0.8)))})
            if act == "edit":
                return self._send(200, {"pid": core.edit_proposal(slug, b["pid"], b["fields"])})
            if act == "revise":
                return self._send(200, {"job": run_job(slug, "revise", core.revise_proposal, slug, b["pid"], b["model"], b["instruction"])})
            if act == "hide":
                core.hide_proposal(slug, b["pid"], b.get("hidden", True))
                return self._send(200, {"ok": True})
            if act == "classify":
                return self._send(200, {"job": run_job(slug, "classify", core.classify, slug, b.get("model"), b.get("method", "tool"),
                                                       b.get("threshold"), float(b.get("w_text", 0.4)), bool(b.get("refresh")))})
            if act == "reclassify":
                return self._send(200, core.reclassify(slug, b["threshold"], b.get("w_text", 0.4)))
            if act == "select":
                core.select(slug, b["pid"])
                return self._send(200, {"ok": True})
            if act == "brief":
                return self._send(200, {"job": run_job(slug, "brief", core.make_brief, slug, b.get("model"), b.get("instruction", ""), b.get("base"))})
            if act == "brief_edit":
                return self._send(200, {"v": core.edit_brief(slug, b["v"], b["brief"])})
            if act == "fix":
                return self._send(200, core.fix(slug, b["v"], b.get("roles"), b.get("budget")))
            if act == "approve":
                return self._send(200, core.approve(slug, b["fingerprint"]))
            if act == "edits":
                return self._send(200, core.set_edits(slug, b["enabled"]))
            if act == "engine":
                a = b.get("action")
                if a in ("rescue_canon", "rescue_chapter", "proofread"):
                    return self._send(200, {"job": run_job(slug, a, core.engine_action, slug, a, b)})
                return self._send(200, core.engine_action(slug, a, b))
            return self._send(404, {"error": "not found"})
        except core.StudioError as e:
            return self._send(400, {"error": str(e)})
        except (KeyError, ValueError) as e:
            return self._send(400, {"error": f"入力が足りないか不正です：{e}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    core.WORKS.mkdir(parents=True, exist_ok=True)
    print(f"novel_studio: http://{a.host}:{a.port}  （作品フォルダ {core.WORKS}、モデル {llm.base_url()}、キー{'あり' if llm.find_key() else 'なし'}）", flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
