#!/usr/bin/env python3
"""make_cover — 表紙を作る。画像生成モデルで「文字のない絵」を描かせ、題名と著者名はこちらで組んで重ねる。

  python3 tools/novelctl.py cover --style illust --motif "霧の中の鐘楼" --title-dir vertical --font mincho --author "くろだ"
  python3 tools/make_cover.py ...（同じ引数）

  --model ID        画像生成モデル（OpenRouter の /images）。none なら絵を描かず、文字と色だけの表紙
  --style           simple（シンプル）| illust（イラスト）| photo（写実）
  --motif TEXT      描いてほしいもの（自由入力）
  --title-dir       vertical（縦書き）| horizontal（横書き）
  --font            mincho（明朝）| gothic（ゴシック）| casual（カジュアル）
  --author TEXT     著者名（EPUB の著者にも使う）
  --title TEXT      題名（省略時は CANON の「タイトル：」）
  --reuse-art       前回の絵を使い回し、文字だけ組み直す（モデルを呼ばない）

なぜ文字を絵に描かせないか：画像生成モデルは日本語の文字、とくに縦書きを正しく描けないことが多い。
絵は文字なしで描かせ、題名と著者名は HTML で組んで Chrome（ヘッドレス）で 1600×2560 の画像にする。

出力（images/ の下。作品フォルダの保護対象ではない）:
  cover_art.png      モデルが描いた絵（文字なし）
  cover.html         文字組みの元
  cover.png          表紙（EPUB とストアの書影に使う）
  cover.json         この表紙の設定（題名・著者名・スタイル・モデル・費用）。make_epub.py が著者名と表紙をここから読む
費用は logs/usage.jsonl に役 cover として残す。
"""
import argparse
import base64
import datetime
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "images"
W, H = 1600, 2560
PAD = 240  # ヘッドレス Chrome は窓の下の数十px（版で違う）を描かないので、窓を大きめにして撮り、下を切り落とす

STYLE = {
    "simple": ("シンプル", "minimalist graphic design for a literary book cover, flat shapes, a limited palette of two or three colors, "
                          "generous negative space, one symbolic motif, quiet and modern"),
    "illust": ("イラスト", "hand-painted book cover illustration, gouache and watercolor textures, soft natural light, "
                          "atmospheric storybook mood, detailed but calm composition"),
    "photo": ("写実", "photorealistic image for a novel cover, cinematic lighting, shallow depth of field, natural colors, "
                     "high detail, like a fine-art photograph"),
}
FONTS = {
    # (Google Fonts の名前, 端末にある書体の候補)。Google Fonts が読めないとき（オフライン）は後ろの候補で組む
    "mincho": ("Shippori+Mincho+B1:wght@700", '"Shippori Mincho B1", "Hiragino Mincho ProN", "Yu Mincho", "YuMincho", "Noto Serif JP", "Noto Serif CJK JP", serif'),
    "gothic": ("Zen+Kaku+Gothic+New:wght@700", '"Zen Kaku Gothic New", "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", "Noto Sans JP", "Noto Sans CJK JP", sans-serif'),
    "casual": ("Zen+Maru+Gothic:wght@700", '"Zen Maru Gothic", "Hiragino Maru Gothic ProN", "Klee", "Tsukushi A Round Gothic", "Noto Sans JP", "Noto Sans CJK JP", sans-serif'),
}
FONT_JA = {"mincho": "明朝", "gothic": "ゴシック", "casual": "カジュアル"}


def cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def title_from_canon(c):
    import re
    f = ROOT / "canon" / "CANON.md"
    if f.exists():
        m = re.search(r"タイトル：\s*(.+)", f.read_text(encoding="utf-8"))
        if m and "（記入）" not in m.group(1):
            return m.group(1).strip().strip("『』「」")
    return c.get("publish", {}).get("fallback_title", "無題")


def mood_from_brief():
    f = ROOT / "brief.json"
    if not f.exists():
        return ""
    b = json.loads(f.read_text(encoding="utf-8"))
    parts = [b.get("genre", ""), (b.get("setting") or "")[:160], (b.get("core") or "")[:80]]
    return " / ".join(p for p in parts if p)


def art_prompt(style, motif, title_dir, mood):
    space = ("keep the right third of the image calm and uncluttered from top to bottom (a vertical title will be placed there)"
             if title_dir == "vertical" else "keep the upper third of the image calm and uncluttered (a title will be placed there)")
    return (f"Cover artwork for a Japanese novel. {STYLE[style][1]}. "
            f"Main motif: {motif or 'chosen to fit the story'}. "
            + (f"Story genre and setting (for mood only): {mood}. " if mood else "")
            + f"Portrait orientation. {space}. "
            "Absolutely no text, letters, characters, numbers, signatures, logos or watermarks anywhere in the image.")


# ---------------------------------------------------------------- 画像生成（OpenRouter）
def _key_and_base():
    sys.path.insert(0, str(ROOT / "tools"))
    import call_llm
    key = call_llm.find_key()
    if not key:
        sys.exit("APIキーが見つかりません（call_llm.py と同じ場所を探しています）。")
    base = (call_llm.env_value("NOVEL_LLM_BASE_URL") or call_llm.LLM.get("base_url", "https://openrouter.ai/api/v1")).rstrip("/")
    return key, base, call_llm


def _post(url, key, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://github.com/InfraOjisan/novel_studio", "X-Title": "novel_studio cover"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def generate_art(model, prompt):
    """返り値：(画像のバイト列, 拡張子, 記録)。まず /images（画像生成の窓口）、だめなら chat/completions の画像出力。"""
    key, base, call_llm = _key_and_base()
    t0 = time.time()
    last = None
    for body in ({"model": model, "prompt": prompt, "aspect_ratio": "2:3", "n": 1},
                 {"model": model, "prompt": prompt, "aspect_ratio": "9:16", "n": 1},
                 {"model": model, "prompt": prompt, "n": 1}):
        try:
            d = _post(base + "/images", key, body)
            item = (d.get("data") or [{}])[0]
            if item.get("b64_json"):
                mt = item.get("media_type") or "image/png"
                ext = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}.get(mt, ".png")
                u = d.get("usage") or {}
                return base64.b64decode(item["b64_json"]), ext, {"model": model, "cost": u.get("cost"), "sec": round(time.time() - t0, 1),
                                                                  "aspect": body.get("aspect_ratio", "auto"), "endpoint": "images"}
            last = "応答に画像がない"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
            if e.code == 400:
                continue  # 縦横比が合わない → 次の縦横比で
            if e.code == 404:
                break  # /images が無い窓口 → chat/completions へ
            raise SystemExit(f"画像生成に失敗しました：{last}")
        except urllib.error.URLError as e:
            raise SystemExit(f"画像生成の窓口につながりません：{e}")
    # chat/completions で画像を出す古い形（modalities）
    try:
        d = _post(base + "/chat/completions", key, {"model": model, "messages": [{"role": "user", "content": prompt}],
                                                   "modalities": ["image", "text"], "usage": {"include": True}})
        msg = (d.get("choices") or [{}])[0].get("message") or {}
        for im in msg.get("images") or []:
            url = (im.get("image_url") or {}).get("url", "")
            if url.startswith("data:"):
                head, b64 = url.split(",", 1)
                ext = ".jpg" if "jpeg" in head else ".webp" if "webp" in head else ".png"
                u = d.get("usage") or {}
                return base64.b64decode(b64), ext, {"model": model, "cost": u.get("cost"), "sec": round(time.time() - t0, 1),
                                                    "aspect": "auto", "endpoint": "chat"}
        last = "応答に画像がない（chat）"
    except urllib.error.HTTPError as e:
        last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
    raise SystemExit(f"画像生成に失敗しました：{last}")


def log_usage(rec):
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": "cover", "task": "cover", "model": rec["model"],
                                "in": None, "out": None, "cost": rec.get("cost"), "sec": rec.get("sec"), "chars": 0, "ok": True},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 文字組み
def cover_html(title, author, style, title_dir, font, art):
    gf, family = FONTS[font]
    e = html.escape
    band = style == "simple" and bool(art)  # シンプル（絵あり）は題名の下に紙の帯を敷く
    lines = split_title(title, 12 if title_dir == "vertical" else 10)
    n = max(len(x) for x in lines)
    avail = (1820 if title_dir == "vertical" else 1320) - (112 if band else 0)
    size = int(max(72, min(210 if title_dir == "vertical" else 190, avail / (n * 1.1))))  # 1.1 は字間（.08em）のぶん
    title_html = "<br>".join(e(x) for x in lines)
    a_size = 62 if len(author) <= 10 else 50
    if art:
        bg = f'<img class="art" src="{e(art)}" alt="">'
        ink, shadow = "#fffdf8", "0 3px 28px rgba(0,0,0,.55), 0 0 2px rgba(0,0,0,.6)"
        scrim = ("linear-gradient(to left, rgba(10,12,18,.62) 0, rgba(10,12,18,.35) 24%, rgba(10,12,18,0) 46%), "
                 "linear-gradient(to top, rgba(10,12,18,.55) 0, rgba(10,12,18,0) 22%)" if title_dir == "vertical" else
                 "linear-gradient(to bottom, rgba(10,12,18,.62) 0, rgba(10,12,18,.3) 26%, rgba(10,12,18,0) 44%), "
                 "linear-gradient(to top, rgba(10,12,18,.55) 0, rgba(10,12,18,0) 20%)")
    else:
        palettes = {"simple": ("#efe9dc", "#22252b", "#b4462f"), "illust": ("#1f3a4d", "#f6efe2", "#e2b04a"), "photo": ("#15171c", "#f2efe8", "#8aa4b8")}
        paper, ink_c, accent = palettes[style]
        bg = (f'<div class="plain" style="background:{paper}"></div>'
              f'<div class="rule" style="background:{accent}"></div>')
        ink, shadow, scrim = ink_c, "none", "none"
    # シンプル（絵あり）は、題名の下に紙の帯を敷いて、絵の明るさに関係なく読めるようにする
    if band:
        ink, shadow = "#1d2026", "none"
    tpos = ("top: 150px; right: 140px; height: 1900px;" if title_dir == "vertical" else "top: 210px; left: 140px; right: 140px; text-align: center;")
    apos = ("bottom: 170px; left: 150px; height: 900px;" if title_dir == "vertical" else "bottom: 170px; left: 140px; right: 140px; text-align: center;")
    band_css = ""
    if band:
        band_css = (".title { background: rgba(246,242,233,.92); padding: 56px 40px; }" if title_dir == "vertical"
                    else ".title { background: rgba(246,242,233,.92); padding: 40px 56px; }")
        band_css += ".author { background: rgba(246,242,233,.92); padding: 26px 22px; color: #1d2026; }"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family={gf}&display=block" rel="stylesheet">
<style>
html, body {{ margin: 0; width: {W}px; height: {H}px; }}
.stage {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; }}
.art, .plain {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
.rule {{ position: absolute; left: 140px; right: 140px; bottom: 120px; height: 10px; }}
.scrim {{ position: absolute; inset: 0; background: {scrim}; }}
.title, .author {{ position: absolute; font-family: {family}; color: {ink}; text-shadow: {shadow}; font-weight: 700; }}
.title {{ {tpos} font-size: {size}px; line-height: 1.25; letter-spacing: .08em; {"writing-mode: vertical-rl; text-orientation: mixed;" if title_dir == "vertical" else ""} }}
.author {{ {apos} font-size: {a_size}px; letter-spacing: .18em; font-weight: 500; {"writing-mode: vertical-rl; text-align: end;" if title_dir == "vertical" else ""} }}
{band_css}
</style></head><body><div class="stage">{bg}<div class="scrim"></div>
<div class="title">{title_html}</div>{f'<div class="author">{e(author)}</div>' if author else ""}
</div></body></html>"""


def split_title(title, per_line):
    """長い題名を、読点や中黒の後ろで、なるべく同じ長さの2行（縦書きなら2列）に分ける。"""
    if len(title) <= per_line:
        return [title]
    mid = len(title) / 2
    cands = [i + 1 for i, ch in enumerate(title[:-1]) if ch in "、。・　 ，,─―ー〜~！？!?"]
    cut = min(cands, key=lambda i: abs(i - mid)) if cands and min(abs(i - mid) for i in cands) <= len(title) * 0.3 else round(mid)
    return [title[:cut].strip(), title[cut:].strip()]


def find_chrome():
    env = os.environ.get("NOVEL_CHROME")
    cands = [env] if env else []
    cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
        p = shutil.which(name)
        if p:
            cands.append(p)
    pw = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    if pw.exists():
        cands += [str(p) for p in sorted(pw.glob("chromium-*/chrome-linux*/chrome"))]
    return next((c for c in cands if c and Path(c).exists()), None)


def render(html_path, out_png):
    """Chrome（ヘッドレス）で HTML を 1600×2560 の画像にする。Python のパッケージは要らない。"""
    chrome = find_chrome()
    if not chrome:
        return "Chrome / Chromium が見つからないので、文字を組んだ表紙を画像にできませんでした（NOVEL_CHROME でパスを指定できます）"
    with tempfile.TemporaryDirectory() as prof:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
               f"--user-data-dir={prof}", f"--window-size={W},{H + PAD}", "--force-device-scale-factor=1",
               "--virtual-time-budget=10000", "--allow-file-access-from-files", f"--screenshot={out_png}", html_path.as_uri()]
        if hasattr(os, "geteuid") and os.geteuid() == 0:  # root で動く環境（コンテナ）では sandbox を外す
            cmd.insert(1, "--no-sandbox")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return "Chrome での画像化が時間切れになりました"
    if not Path(out_png).exists():
        return "Chrome での画像化に失敗しました：" + (r.stderr or r.stdout)[-300:]
    try:
        crop_png(out_png, H)
    except ValueError as e:
        return f"画像の切り取りに失敗しました：{e}"
    return None


def crop_png(path, height):
    """PNG の下を切り落とす（標準ライブラリだけ）。走査線のフィルタは上の行にしか依存しないので、後ろの行を捨てるだけでよい。"""
    import struct
    import zlib
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG ではない")
    pos, chunks = 8, []
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunks.append((typ, data[pos + 8:pos + 8 + ln]))
        pos += 12 + ln
    ihdr = dict(chunks)[b"IHDR"]
    w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", ihdr)
    if h <= height:
        return
    if depth != 8 or ctype not in (2, 6) or inter:
        raise ValueError(f"想定外の PNG（深さ{depth}・色{ctype}・インターレース{inter}）")
    stride = 1 + w * (3 if ctype == 2 else 4)
    raw = zlib.decompress(b"".join(d for t, d in chunks if t == b"IDAT"))[:stride * height]

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, height, depth, ctype, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    Path(path).write_bytes(out)


def main(argv):
    ap = argparse.ArgumentParser(prog="cover")
    ap.add_argument("--model", default="none")
    ap.add_argument("--style", choices=list(STYLE), default="illust")
    ap.add_argument("--motif", default="")
    ap.add_argument("--title-dir", choices=["vertical", "horizontal"], default="vertical")
    ap.add_argument("--font", choices=list(FONTS), default="mincho")
    ap.add_argument("--author", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--reuse-art", action="store_true")
    a = ap.parse_args(argv)
    c = cfg()
    IMG.mkdir(exist_ok=True)
    prev = json.loads((IMG / "cover.json").read_text(encoding="utf-8")) if (IMG / "cover.json").exists() else {}
    title = a.title or title_from_canon(c)
    author = a.author if a.author is not None else (prev.get("author") or c.get("publish", {}).get("author", ""))
    art, rec, prompt = None, None, None
    if a.reuse_art and prev.get("art") and (IMG / prev["art"]).exists():
        art = prev["art"]
        print(f"前回の絵（images/{art}）を使い回します。")
    elif a.model and a.model != "none":
        prompt = art_prompt(a.style, a.motif, a.title_dir, mood_from_brief())
        print(f"{a.model} に絵を頼んでいます（{STYLE[a.style][0]}）…", flush=True)
        data, ext, rec = generate_art(a.model, prompt)
        for old in IMG.glob("cover_art.*"):
            old.unlink()
        art = f"cover_art{ext}"
        (IMG / art).write_bytes(data)
        log_usage(rec)
        print(f"images/{art} を保存（{len(data) // 1024}KB、{rec['sec']}秒、費用 {rec.get('cost')}）")
    page = IMG / "cover.html"
    page.write_text(cover_html(title, author, a.style, a.title_dir, a.font, art), encoding="utf-8")
    out = IMG / "cover.png"
    tmp = IMG / "cover.render.png"
    tmp.unlink(missing_ok=True)
    err = render(page, str(tmp))
    if err:
        print("注意：" + err)
        if art:
            shutil.copy(IMG / art, IMG / ("cover" + Path(art).suffix))
            out = IMG / ("cover" + Path(art).suffix)
            print(f"絵だけを表紙にしました（{out.name}）。題名は EPUB の扉に入ります。")
        else:
            out = None
    else:
        for old in IMG.glob("cover.*"):
            if old.suffix in (".png", ".jpg", ".jpeg", ".webp") and old.name != tmp.name:
                old.unlink()
        tmp.replace(IMG / "cover.png")
    meta = {"title": title, "author": author, "style": a.style, "style_ja": STYLE[a.style][0], "motif": a.motif,
            "title_dir": a.title_dir, "font": a.font, "font_ja": FONT_JA[a.font], "model": a.model if not a.reuse_art else prev.get("model"),
            "art": art, "cover": out.name if out else None, "prompt": prompt or prev.get("prompt"),
            "usd": (rec or {}).get("cost") if rec else (prev.get("usd") if a.reuse_art else 0),
            "ts": datetime.datetime.now().isoformat(timespec="seconds"), "note": err}
    (IMG / "cover.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    if out:
        print(f"表紙：images/{out.name}（{FONT_JA[a.font]}・{'縦書き' if a.title_dir == 'vertical' else '横書き'}・{STYLE[a.style][0]}）。EPUB を作り直すと入ります。")
        return 0
    print("表紙を作れませんでした。")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
