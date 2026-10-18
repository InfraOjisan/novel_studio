#!/usr/bin/env python3
"""novel_studio v1.0 の回帰テスト（標準ライブラリの unittest だけ。API キー・費用は不要）。

  python3 -m unittest discover -s engine/tests -v

偽モデル（studio/devtools/mock_openrouter.py）を自分で立てて、scaffold → novelctl → 校正 → EPUB を端から端まで通す。
ハーネスの試験（harness/exams の L4）でも、このテストが通ることを合格の条件にしている。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "engine" / "tools"))
sys.path.insert(0, str(REPO / "studio"))
import scaffold  # noqa: E402
import similarity  # noqa: E402

PY = sys.executable


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def sample_brief(n=4):
    sys.path.insert(0, str(REPO / "studio" / "devtools"))
    import mock_openrouter as m
    return json.loads(m.studio_answer("brief", f"章の数：{n}", "x"))["brief"]


class Lint(unittest.TestCase):
    def test_sample_ok(self):
        self.assertTrue(scaffold.lint(sample_brief())["ok"])

    def test_recovered_before_planted(self):
        b = sample_brief()
        b["slots"][0]["plan"] = [[3, "planted"], [1, "recovered"]]
        r = scaffold.lint(b)
        self.assertFalse(r["ok"])
        self.assertTrue(any("回収" in e and "設置" in e for e in r["errors"]))

    def test_chapter_count_mismatch(self):
        b = sample_brief()
        b["chapters"] = b["chapters"][:-1]
        self.assertFalse(scaffold.lint(b)["ok"])

    def test_plan_text_form(self):
        self.assertEqual(scaffold.parse_plan("1:設置 3:補強 5:回収"), [[1, "planted"], [3, "reinforced"], [5, "recovered"]])

    def test_render_markers(self):
        b = sample_brief(6)
        md = scaffold.render(b)
        self.assertIn("<!-- RULES:BEGIN -->", md)
        for i in range(1, 7):
            self.assertIn(f"<!-- ROLE:{i:02d} -->", md)
        t = scaffold.canon_template(b, 16000)
        self.assertEqual(t.count("<!-- CARD:"), 6)
        self.assertNotIn("{{", t)


class Similarity(unittest.TestCase):
    def test_convergent_and_outlier(self):
        same = {"deceiver": "語り手", "hidden": "自分の罪", "layer": "語り", "flip": "被害者が加害者", "stage": "会社", "ending": "ビター", "one_line": ""}
        props = [{"id": f"P{i}", "title": "語り手の罪", "synopsis": "語り手が実は加害者だった。会社の会議室で。"} for i in range(1, 5)]
        props.append({"id": "P9", "title": "氷の下の呼吸", "synopsis": "潮汐で氷が呼吸する衛星で、熱を運ぶ回路を作る。"})
        sk = {f"P{i}": same for i in range(1, 5)}
        sk["P9"] = {"deceiver": "自然", "hidden": "時間のずれ", "layer": "物理", "flip": "前提が逆", "stage": "氷の衛星", "ending": "開いた結末", "one_line": ""}
        r = similarity.classify(props, sk, 0.5)
        self.assertEqual(r["labels"]["P1"]["label"], "収束")
        self.assertEqual(r["labels"]["P9"]["label"], "外れ値")


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = dict(os.environ, MOCK_FAIL_CHAPTER="2", MOCK_RESCUE_FAIL_MODEL="anthropic/claude-opus-5.5")
        cls.mock = subprocess.Popen([PY, str(REPO / "studio" / "devtools" / "mock_openrouter.py"), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        time.sleep(0.8)
        cls.tmp = Path(tempfile.mkdtemp(prefix="novel-e2e-"))
        cls.env = dict(os.environ, NOVEL_LLM_BASE_URL=f"http://127.0.0.1:{cls.port}/api/v1", NOVEL_PROVIDER_APIKEY="mock")

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ctl(self, work, *args, env=None):
        return subprocess.run([PY, "tools/novelctl.py", *args], cwd=work, capture_output=True, text=True, env=env or self.env,
                              stdin=subprocess.DEVNULL, timeout=300)

    def test_full_run(self):
        work = self.tmp / "w"
        r = scaffold.create(sample_brief(4), work)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.ctl(work, "run").returncode, 2)  # canon_hold で止まる
        st = json.loads(self.ctl(work, "status", "--json").stdout)
        self.assertEqual(st["phase"], "canon_hold")
        # 端末でない承認は拒否される（エージェントからは承認できない）
        self.assertIn("REFUSED", self.ctl(work, "approve-canon").stdout)
        import hashlib
        fp = hashlib.sha256((work / "canon" / "CANON.md").read_bytes()).hexdigest()[:8]
        self.assertIn("承認", self.ctl(work, "approve-canon", env=dict(self.env, NOVEL_STUDIO_APPROVE=fp)).stdout)
        out = self.ctl(work, "run")
        self.assertIn("CHAPTER_EXHAUSTED", out.stdout)  # 第2章は偽モデルの審査で落ち続ける
        # 指定の上位モデル（opus）で直しても通らない → 別のモデルを選んで直す（v1.1）
        self.assertEqual(self.ctl(work, "rescue", "chapter", "2").returncode, 0)
        out = self.ctl(work, "run")
        self.assertIn("CHAPTER_EXHAUSTED", out.stdout)
        self.assertIn("--model", out.stdout)
        self.assertEqual(self.ctl(work, "rescue", "chapter", "2", "--model", "openai/gpt-6-luna-pro", "--note", "棚の番号は表どおり").returncode, 0)
        self.assertIn("棚の番号は表どおり", (work / "work" / "rescue-chapter_ch02.packet.md").read_text(encoding="utf-8"))
        self.assertNotEqual(self.ctl(work, "rescue", "chapter", "2", "--model", "bad model!").returncode, 0)
        out = self.ctl(work, "run")
        self.assertEqual(out.returncode, 3, out.stdout[-800:])
        st = json.loads(self.ctl(work, "status", "--json").stdout)
        self.assertEqual(st["phase"], "done")
        self.assertEqual(st["rescues"], 2)
        hist = next(c for c in st["chapters"] if c["n"] == 2)["rescues"]
        self.assertEqual([h["model"] for h in hist], ["anthropic/claude-opus-5.5", "openai/gpt-6-luna-pro"])
        self.assertTrue(hist[0]["result"].startswith("審査で不合格") and hist[1]["result"] == "合格")
        used = [json.loads(x)["model"] for x in (work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines() if '"premium"' in x]
        self.assertIn("openai/gpt-6-luna-pro", used)
        self.assertGreater(st["usage"]["total_usd"], 0)
        self.assertIn("premium", st["usage"]["by_role"])
        self.assertEqual(self.ctl(work, "proofread").returncode, 0)
        edits = json.loads((work / "output" / "proofread_edits.json").read_text(encoding="utf-8"))["edits"]
        self.assertTrue(any(e.get("rejected") for e in edits))  # 原稿に無い原文は機械で却下される
        a = subprocess.run([PY, "tools/proofread_apply.py"], cwd=work, capture_output=True, text=True)
        self.assertIn("失敗0件", a.stdout)
        self.ctl(work, "epub", "--proofread")
        self.assertTrue(list((work / "output").glob("*.epub")))
        g = subprocess.run([PY, "tools/guard.py", "check"], cwd=work, capture_output=True, text=True)
        self.assertEqual(g.returncode, 0, g.stdout)

    def test_cover(self):
        """表紙：画像生成（偽モデル）→ 文字組み → EPUB に表紙と著者名が入る。Chrome が無い環境では絵だけの表紙になる。"""
        import struct
        import zipfile
        work = self.tmp / "wc"
        self.assertTrue(scaffold.create(sample_brief(3), work)["ok"])
        self.ctl(work, "run")
        import hashlib
        fp = hashlib.sha256((work / "canon" / "CANON.md").read_bytes()).hexdigest()[:8]
        self.ctl(work, "approve-canon", env=dict(self.env, NOVEL_STUDIO_APPROVE=fp))
        self.ctl(work, "run")
        r = self.ctl(work, "cover", "--model", "black-forest-labs/flux.2-pro", "--style", "illust", "--motif", "霧の鐘楼",
                     "--title-dir", "vertical", "--font", "mincho", "--author", "テスト著者")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        meta = json.loads((work / "images" / "cover.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["author"], "テスト著者")
        self.assertTrue((work / "images" / meta["cover"]).exists())
        if meta["cover"] == "cover.png" and not meta.get("note"):
            w, h = struct.unpack(">II", (work / "images" / "cover.png").read_bytes()[16:24])
            self.assertEqual((w, h), (1600, 2560))
        usage = [json.loads(x) for x in (work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(u["role"] == "cover" and u["cost"] for u in usage))
        # 文字だけ組み直す（モデルを呼ばない）
        n_before = len(usage)
        self.assertEqual(self.ctl(work, "cover", "--reuse-art", "--title-dir", "horizontal", "--font", "casual", "--author", "別名").returncode, 0)
        self.assertEqual(len((work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines()), n_before)
        # 文字の色（v1.2.1）：自動は絵の明るさを測る仕組みが HTML に入る。指定した色はそのまま。読めない指定は断る
        self.assertIn("getImageData", (work / "images" / "cover.html").read_text(encoding="utf-8"))
        self.assertEqual(self.ctl(work, "cover", "--reuse-art", "--ink", "#c9a227").returncode, 0)
        cj = json.loads((work / "images" / "cover.json").read_text(encoding="utf-8"))
        self.assertEqual(cj["ink"], "#c9a227")
        html_ = (work / "images" / "cover.html").read_text(encoding="utf-8")
        self.assertIn("#c9a227", html_)
        self.assertNotIn("getImageData", html_)
        self.assertNotEqual(self.ctl(work, "cover", "--reuse-art", "--ink", "red;}").returncode, 0)
        self.ctl(work, "epub")
        epub = next((work / "output").glob("*.epub"))
        opf = zipfile.ZipFile(epub).read("OEBPS/content.opf").decode("utf-8")
        self.assertIn("別名", opf)
        self.assertIn('properties="cover-image"', opf)
        g = subprocess.run([PY, "tools/guard.py", "check"], cwd=work, capture_output=True, text=True)
        self.assertNotEqual(g.returncode, 2, g.stdout)  # 表紙は保護ファイルを触らない

    def test_chapter_numbers_over_twenty(self):
        work = self.tmp / "w22"
        r = scaffold.create(sample_brief(22), work)
        self.assertTrue(r["ok"], r)
        code = ("import sys; sys.path.insert(0,'tools'); import novelctl as n;"
                "print(n.kanji_num(11), n.kanji_num(20), n.kanji_num(22));"
                "print(bool(n.extract_chapter('# 第二十二章　霧\\n本文。', 22)), bool(n.extract_chapter('# 第十一章　霧\\n本文。', 11)))")
        out = subprocess.run([PY, "-c", code], cwd=work, capture_output=True, text=True).stdout.split()
        self.assertEqual(out, ["十一", "二十", "二十二", "True", "True"])


class Wire(unittest.TestCase):
    """窓口の違い（llm_wire）。OpenCode Go の実際の門（User-Agent・x-opencode-session）は 2026-10-13 に確かめたもの。"""

    def setUp(self):
        import llm_wire
        self.w = llm_wire
        os.environ.pop("NOVEL_LLM_PROTOCOLS", None)
        os.environ.pop("NOVEL_LLM_PRICES", None)

    def test_protocol_for(self):
        go, zen, orr = "https://opencode.ai/zen/go/v1", "https://opencode.ai/zen/v1", "https://openrouter.ai/api/v1"
        pf = self.w.protocol_for
        self.assertEqual([pf(go, m) for m in ("claude-haiku-5-5", "gpt-6-luna", "mimo-v2.6-pro", "deepseek-v4.1-flash", "minimax-m3", "qwen3.7-plus")],
                         ["messages", "responses", "chat", "chat", "messages", "messages"])
        self.assertEqual([pf(zen, m) for m in ("claude-opus-5-5", "gpt-6-sol", "qwen3.8-max", "qwen3.8-flash", "kimi-k3")],
                         ["messages", "responses", "chat", "messages", "chat"])
        self.assertEqual(pf(orr, "anthropic/claude-haiku-5.5"), "chat")  # OpenRouter はいつも chat
        self.assertEqual(pf(go, "kimi-k3", {"kimi-": "messages"}), "messages")  # config の llm.protocols
        os.environ["NOVEL_LLM_PROTOCOLS"] = "gpt-:chat"
        self.assertEqual(pf(go, "gpt-6-luna"), "chat")

    def test_headers(self):
        h = self.w.headers("k", "messages", session="s1")
        self.assertFalse(h["User-Agent"].startswith("Python-urllib"))
        self.assertEqual((h["x-opencode-session"], h["x-api-key"], h["anthropic-version"]), ("s1", "k", "2023-06-01"))
        self.assertTrue(self.w.headers("k")["x-opencode-session"])  # 指定がなくても送る（OpenCode Go では必須）

    def test_build_parse(self):
        path, b = self.w.build("responses", "gpt-6-luna", "sys", "hi", 0.7, 100, "low", True)
        self.assertEqual((path, b["instructions"], b["max_output_tokens"], b["text"]["format"]["type"]), ("/responses", "sys", 100, "json_object"))
        path, b = self.w.build("messages", "claude-haiku-5-5", "sys", "hi", 1.5, 100)
        self.assertEqual((path, b["system"], b["temperature"]), ("/messages", "sys", 1.0))
        t, fin, u, _ = self.w.parse("responses", {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                                                  "output": [{"type": "message", "content": [{"type": "output_text", "text": "あ"}]}],
                                                  "usage": {"input_tokens": 3, "output_tokens": 1}})
        self.assertEqual((t, fin, u["prompt_tokens"]), ("あ", "length", 3))
        t, fin, u, _ = self.w.parse("messages", {"content": [{"type": "thinking"}, {"type": "text", "text": "い"}], "stop_reason": "end_turn",
                                                 "usage": {"input_tokens": 2, "output_tokens": 5}})
        self.assertEqual((t, fin, u["completion_tokens"]), ("い", "stop", 5))

    def test_estimate_cost(self):
        self.assertAlmostEqual(self.w.estimate_cost("minimax-m3", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}), 1.5)
        self.assertIsNone(self.w.estimate_cost("gpt-6-luna", {"prompt_tokens": 10}))
        os.environ["NOVEL_LLM_PRICES"] = "gpt-6-luna=1/10"
        self.assertAlmostEqual(self.w.estimate_cost("gpt-6-luna", {"prompt_tokens": 1_000_000, "completion_tokens": 100_000}), 2.0)


class OpenCodeEndToEnd(unittest.TestCase):
    """偽の OpenCode Go（/zen/go/v1）で、studio と engine の両方が窓口を使い分けて通ること。"""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.mock = subprocess.Popen([PY, str(REPO / "studio" / "devtools" / "mock_openrouter.py"), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        cls.base = f"http://127.0.0.1:{cls.port}/zen/go/v1"
        cls.tmp = Path(tempfile.mkdtemp(prefix="novel-oc-"))
        cls.env = dict(os.environ, NOVEL_LLM_BASE_URL=cls.base, NOVEL_PROVIDER_APIKEY="mock")
        cls.env.pop("NOVEL_LLM_PROTOCOLS", None)

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_library_user_agent_is_refused(self):
        import urllib.error
        import urllib.request
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/models", timeout=10)
        self.assertEqual(cm.exception.code, 403)
        self.assertIn(b"1010", cm.exception.read())

    def test_studio_chat(self):
        code = ("import sys, json; sys.path.insert(0, 'studio'); import llm\n"
                "out = []\n"
                "for m in ['claude-haiku-5-5', 'gpt-6-luna', 'deepseek-v4-flash', 'mockwire-odd']:\n"
                "    t, r = llm.chat(m, '<!-- STUDIO: propose -->\\nテーマ', session='novel-studio-t')\n"
                "    out.append([m, r['protocol'], r['price'], bool(llm.extract_json(t))])\n"
                "print(json.dumps(out))")
        r = subprocess.run([PY, "-c", code], cwd=REPO, capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual([g[1] for g in got], ["messages", "responses", "chat", "messages"])  # mockwire-odd は chat → messages に切り替わる
        self.assertEqual([g[2] for g in got], ["unknown", "unknown", "estimated", "unknown"])
        self.assertTrue(all(g[3] for g in got))

    def test_engine_until_canon_hold(self):
        work = self.tmp / "oc"
        r = scaffold.create(sample_brief(4), work, roles={"writer": "claude-haiku-5-5", "reviewer": "gpt-6-luna", "premium": "minimax-m3"})
        self.assertTrue(r["ok"], r)
        run = subprocess.run([PY, "tools/novelctl.py", "run"], cwd=work, capture_output=True, text=True, env=self.env,
                             stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(run.returncode, 2, run.stdout[-800:] + run.stderr[-800:])  # canon_hold で止まる
        recs = [json.loads(x) for x in (work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(recs and all(x["ok"] for x in recs))
        self.assertEqual({x["model"] for x in recs}, {"claude-haiku-5-5", "gpt-6-luna"})
        p = subprocess.run([PY, "tools/call_llm.py", "--ping", "premium"], cwd=work, capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        last = json.loads((work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last["model"], "minimax-m3")
        self.assertTrue(last.get("est") and last["cost"] > 0)  # OpenCode は費用を返さない → 公開価格で見積もる


class Derive(unittest.TestCase):
    """テーマを直して、新しい作品フォルダで提案し直す（v1.1.1）。名前は必ず変える＝元の作品は上書きされない。"""

    def setUp(self):
        import core
        self.core = core
        self.saved = core.WORKS
        core.WORKS = Path(tempfile.mkdtemp(prefix="novel-derive-"))

    def tearDown(self):
        shutil.rmtree(self.core.WORKS, ignore_errors=True)
        self.core.WORKS = self.saved

    def test_derive(self):
        c = self.core
        c.create_work("moon", {"genre": "SF", "theme": "氷の月"})
        before = (c.WORKS / "moon" / "studio.json").read_bytes()
        self.assertEqual(c.suggest_slug("moon"), "moon-2")
        for bad in ("moon", "", "../x"):
            with self.assertRaises(c.StudioError):
                c.derive_work("moon", bad, {"genre": "SF", "theme": "氷の月の潮"})
        st = c.derive_work("moon", "moon-2", {"genre": "SF", "theme": "氷の月の潮"})
        self.assertEqual(st["derived_from"]["slug"], "moon")
        self.assertEqual(st["derived_from"]["changed"], ["テーマ・題材"])
        self.assertEqual((st["rounds"], st["proposals"]), ([], {}))
        with self.assertRaises(c.StudioError):
            c.derive_work("moon", "moon-2", {"theme": "別"})  # もうある名前には作らない
        self.assertEqual((c.WORKS / "moon" / "studio.json").read_bytes(), before)  # 元の作品は一切変わらない
        self.assertEqual(c.suggest_slug("moon-2"), "moon-3")
        self.assertEqual([k["slug"] for k in c.derived_children("moon")], ["moon-2"])
        (c.WORKS / "stray").mkdir()
        (c.WORKS / "stray" / "memo.txt").write_text("x")
        with self.assertRaises(c.StudioError):
            c.create_work("stray", {})  # 中身のあるフォルダには作らない


class ModelsCache(unittest.TestCase):
    """モデル一覧のキャッシュ（studio/.cache）：並列の提案が同時に読み書きしても、壊れたキャッシュが残っていても落ちないこと。"""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.mock = subprocess.Popen([PY, str(REPO / "studio" / "devtools" / "mock_openrouter.py"), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        cls.saved = os.environ.get("NOVEL_LLM_BASE_URL")
        os.environ["NOVEL_LLM_BASE_URL"] = f"http://127.0.0.1:{cls.port}/api/v1"
        import llm
        cls.llm = llm

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        cls.mock.wait()
        if cls.saved is None:
            os.environ.pop("NOVEL_LLM_BASE_URL", None)
        else:
            os.environ["NOVEL_LLM_BASE_URL"] = cls.saved
        for f in cls.llm.CACHE.glob(f"*models*{cls.llm._base_tag()}*"):
            f.unlink(missing_ok=True)

    def test_broken_cache_is_refetched(self):
        llm = self.llm
        llm.CACHE.mkdir(exist_ok=True)
        f = llm.CACHE / f"models_v2_{llm._base_tag()}.json"
        f.write_text('[{"id": "anthropic/claude', encoding="utf-8")  # 書きかけのまま残ったキャッシュ
        self.assertIn("anthropic/claude-haiku-5.5", [m["id"] for m in llm.models()])
        self.assertTrue(json.loads(f.read_text(encoding="utf-8")))

    def test_parallel_read_write(self):
        import concurrent.futures as cf
        llm = self.llm

        def one(i):
            return llm.models(refresh=(i % 2 == 0))
        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            res = list(ex.map(one, range(60)))
        self.assertTrue(all(r for r in res))
        self.assertEqual(list(llm.CACHE.glob("*.tmp")), [])  # 書きかけの別名が残らない


class ChunkedClassify(unittest.TestCase):
    """8案を分析しても上流で落ちないこと：骨格は小分けに頼み、落ちたら1案ずつに分け、取れた分から保存する。選んだ案だけを分析・書き出す。"""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = dict(os.environ, MOCK_SKELETON_MAX="1", MOCK_FAIL_SKELETON="P05")  # 2案以上の依頼は「上流が諦める」。P05 はいつも失敗
        cls.mock = subprocess.Popen([PY, str(REPO / "studio" / "devtools" / "mock_openrouter.py"), "--port", str(cls.port)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        cls.saved_env = {k: os.environ.get(k) for k in ("NOVEL_LLM_BASE_URL", "NOVEL_PROVIDER_APIKEY", "NOVEL_LLM_PROTOCOLS")}
        os.environ.update(NOVEL_LLM_BASE_URL=f"http://127.0.0.1:{cls.port}/api/v1", NOVEL_PROVIDER_APIKEY="mock")
        os.environ.pop("NOVEL_LLM_PROTOCOLS", None)
        import core
        cls.core = core
        cls.saved = core.WORKS
        core.WORKS = Path(tempfile.mkdtemp(prefix="novel-chunk-"))
        core.create_work("many", {"genre": "SF", "theme": "氷の月"})
        core.propose("many", ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna-pro", "deepseek/deepseek-v4.1-flash", "qwen/qwen3.8-flash"], n=2)
        assert len(core.load("many")["proposals"]) == 8

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        cls.mock.wait()
        shutil.rmtree(cls.core.WORKS, ignore_errors=True)
        cls.core.WORKS = cls.saved
        for k, v in cls.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def skeleton_calls(self):
        f = self.core.WORKS / "many" / "studio" / "usage.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines() if json.loads(x)["stage"] == "skeleton"]

    def test_1_split_and_keep(self):
        c = self.core
        seen = []
        r = c.classify("many", progress=lambda d, t, w="": seen.append((d, t)))
        self.assertEqual(r["missing"], ["P05"])
        self.assertEqual(len(r["ids"]), 7)
        self.assertNotIn("P05", r["labels"])
        calls = self.skeleton_calls()
        self.assertEqual(len(calls), 7)  # 3案ずつの依頼はすべて落ち、1案ずつで7案取れた
        self.assertTrue(all("," not in u["note"] for u in calls))
        self.assertEqual(seen[-1], (7, 8))
        self.assertEqual(sorted(c.load("many")["skeletons"]), ["P01", "P02", "P03", "P04", "P06", "P07", "P08"])

    def test_2_selected_only_and_cached(self):
        c = self.core
        before = len(self.skeleton_calls())
        r = c.classify("many", ids=["P01", "P02", "P07"])
        self.assertEqual(set(r["labels"]), {"P01", "P02", "P07"})
        self.assertEqual(len(self.skeleton_calls()), before)  # 抜き出し済みの骨格は使い回す
        self.assertEqual(set(c.reclassify("many", 0.3)["labels"]), {"P01", "P02", "P07"})
        with self.assertRaises(c.StudioError):
            c.classify("many", ids=["P01"])

    def test_3_ai_reads_skeletons_only(self):
        c = self.core
        st = c.load("many")
        props = [st["proposals"][i] for i in ("P01", "P02")]
        block = c._skeleton_block(props, c.skeleton_store(st))
        self.assertNotIn(props[0]["synopsis"][:30], block)
        self.assertIn("one_line", block)
        r = c.classify("many", ids=["P01", "P02", "P03", "P04"], method="both")
        self.assertEqual(set(r["ai"]["labels"]), {"P01", "P02", "P03", "P04"})

    def test_4_export(self):
        import csv
        import io
        c = self.core
        name, data, _ = c.export_proposals("many", ["P01", "P03", "P05"], "csv")
        text = data.decode("utf-8")
        self.assertTrue(text.startswith("\ufeff案,"))
        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual([r[0] for r in rows[1:]], ["P01", "P03", "P05"])
        md = c.export_proposals("many", ["P01", "P03"], "md")[1].decode()
        self.assertIn("| P01 |", md)
        self.assertIn("### あらすじ", md)
        self.assertEqual(len(json.loads(c.export_proposals("many", [], "json")[1])["proposals"]), 8)


class ModelList(unittest.TestCase):
    """モデルの一覧：使えないモデル（:batch・画像だけ・提供終了）を外し、絞り込みに使う情報を持たせる。"""

    def test_model_entry(self):
        import llm
        e = llm._model_entry
        base = {"architecture": {"output_modalities": ["text"]}, "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "created": 1760000000}
        self.assertIsNone(e({**base, "id": "a/b:batch"}))
        self.assertIsNone(e({**base, "id": "a/img", "architecture": {"output_modalities": ["image"]}}))
        self.assertIsNone(e({**base, "id": "a/old", "expiration_date": "2020-01-01"}))
        r = e({**base, "id": "a/b", "expiration_date": "2099-12-31"})
        self.assertEqual((r["in_per_m"], r["out_per_m"], r["free"], r["created"], r["expires"]), (1.0, 2.0, False, 1760000000, "2099-12-31"))
        self.assertTrue(e({**base, "id": "a/c:free", "pricing": {"prompt": "0", "completion": "0"}})["free"])
        self.assertEqual(e({**base, "id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}})["in_per_m"], None)
        self.assertFalse(e({"id": "gpt-6-luna", "object": "model"})["free"])


class Runaway(unittest.TestCase):
    """審査役の推論の暴走（v1.1.2）：同じ条件で3回繰り返さない。推論を軽く → 出力枠を広げる → 予備のモデルへ。最終審査は止まらない。"""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.mock = subprocess.Popen([PY, str(REPO / "studio" / "devtools" / "mock_openrouter.py"), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        cls.tmp = Path(tempfile.mkdtemp(prefix="novel-run-"))
        cls.env = dict(os.environ, NOVEL_LLM_BASE_URL=f"http://127.0.0.1:{cls.port}/api/v1", NOVEL_PROVIDER_APIKEY="mock")

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ctl(self, work, *args, env=None):
        return subprocess.run([PY, "tools/novelctl.py", *args], cwd=work, capture_output=True, text=True, env=env or self.env,
                              stdin=subprocess.DEVNULL, timeout=300)

    def usage(self, work):
        return [json.loads(x) for x in (work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_adapt_reasoning(self):
        import llm_wire as w
        b = w.adapt_reasoning({"reasoning": {"effort": "low"}}, "https://opencode.ai/zen/go/v1")
        self.assertEqual(b, {"reasoning_effort": "low"})  # OpenAI 互換の標準の書き方
        b = w.adapt_reasoning({"reasoning": {"effort": "low"}}, "https://openrouter.ai/api/v1")
        self.assertEqual(b, {"reasoning": {"effort": "low"}})  # OpenRouter はそのまま
        w.set_effort(b, "medium")
        self.assertEqual(w.get_effort(b), "medium")

    def test_deadline_switches_to_backup(self):
        work = self.tmp / "dl"
        roles = {"reviewer": {"model": "mock/runaway", "deadline_sec": 2, "fallback_models": ["openai/gpt-6-luna-pro"]}}
        self.assertTrue(scaffold.create(sample_brief(4), work, roles=roles)["ok"])
        t0 = time.time()
        r = self.ctl(work, "run")
        self.assertEqual(r.returncode, 2, r.stdout[-600:] + r.stderr[-600:])  # 予備のモデルで審査が通り canon_hold まで
        self.assertLess(time.time() - t0, 60)
        recs = self.usage(work)
        self.assertTrue(any(x["model"] == "mock/runaway" and x.get("note") == "runaway:deadline" for x in recs))
        self.assertTrue(any(x["model"] == "openai/gpt-6-luna-pro" and x["ok"] for x in recs))
        run_len = 0  # 暴走が続くのは「推論を一段軽くする」一回まで。その次は必ず予備のモデル（同じ条件で待ち続けない）
        for x in recs:
            run_len = run_len + 1 if x.get("note") == "runaway:deadline" else 0
            self.assertLessEqual(run_len, 2)

    def test_length_widens_output(self):
        work = self.tmp / "len"
        roles = {"reviewer": {"model": "mock/thinker", "max_tokens": 16000, "fallback_models": []}}
        self.assertTrue(scaffold.create(sample_brief(4), work, roles=roles)["ok"])
        r = self.ctl(work, "run")
        self.assertEqual(r.returncode, 2, r.stdout[-600:] + r.stderr[-600:])
        recs = [x for x in self.usage(work) if x["model"] == "mock/thinker"]
        self.assertTrue(any(x.get("note") == "empty:length" and x.get("reasoning") == 16000 for x in recs))
        self.assertTrue(any(x["ok"] for x in recs))

    def test_final_review_runaway_does_not_stop(self):
        work = self.tmp / "fin"
        self.assertTrue(scaffold.create(sample_brief(3), work)["ok"])
        self.assertEqual(self.ctl(work, "run").returncode, 2)
        cfg = json.loads((work / "config.json").read_text(encoding="utf-8"))
        cfg["llm"]["task_overrides"]["final"] = {"model": "mock/runaway", "deadline_sec": 1, "fallback_models": []}
        cfg["llm"]["retries"] = 2
        (work / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run([PY, "tools/guard.py", "seal"], cwd=work, capture_output=True)
        import hashlib
        fp = hashlib.sha256((work / "canon" / "CANON.md").read_bytes()).hexdigest()[:8]
        self.ctl(work, "approve-canon", env=dict(self.env, NOVEL_STUDIO_APPROVE=fp))
        r = self.ctl(work, "run")
        self.assertEqual(r.returncode, 3, r.stdout[-800:] + r.stderr[-800:])
        st = json.loads(self.ctl(work, "status", "--json").stdout)
        self.assertEqual(st["phase"], "done")
        self.assertIn("未実施", (work / "output" / "final_report.md").read_text(encoding="utf-8"))
        self.assertTrue((work / "notes").is_dir())


class CanonLint(unittest.TestCase):
    """v1.2：CANON の日付の機械検査（費用なし）。"""

    def lint(self, text):
        import importlib.util
        spec = importlib.util.spec_from_file_location("nctl_src", REPO / "engine" / "tools" / "novelctl.py")
        src = (REPO / "engine" / "tools" / "novelctl.py").read_text(encoding="utf-8")
        ns = {"re": __import__("re"), "datetime": __import__("datetime")}
        exec(src[src.index("WEEKDAYS ="):src.index("def cmd_canon_check")], ns)
        return ns["canon_date_lint"](text)

    def test_period_weekday_order(self):
        t = """## 1. 舞台
- 時代・場所・季節の範囲：現代（2026年）。10月9日（金）夜から10月12日（月）朝まで。
## 3. 時間表
- 10/10（日）昼 何かが起きる。
## 12. 章カード
<!-- CARD:01 -->
### 第1章「一」
- 時期と場所：10/9（金）夜。会議室。
<!-- /CARD:01 -->
<!-- CARD:02 -->
### 第2章「二」
- 時期と場所：10/14（水）。警察。
<!-- /CARD:02 -->
<!-- CARD:03 -->
### 第3章「三」
- 時期と場所：10/11（日）。社長室。
<!-- /CARD:03 -->
"""
        r = self.lint(t)
        self.assertTrue(any("10月10日は土曜日" in x for x in r), r)          # 曜日の食い違い
        self.assertTrue(any("第2章" in x and "期間" in x for x in r), r)     # 期間の外
        self.assertTrue(any("第3章" in x and "前に戻って" in x for x in r), r)  # 順番が戻る
        self.assertFalse(any("第1章" in x for x in r), r)

    def test_clean(self):
        t = "- 時代・場所・季節の範囲：2026年。10月9日（金）から10月19日（月）まで。\n第1章「一」\n- 時期と場所：10/9（金）\n第2章「二」\n- 時期と場所：10/19（月）\n"
        self.assertEqual(self.lint(t), [])


class StudioRestart(unittest.TestCase):
    """v1.2：承認前の矛盾チェック・画面で CANON を直す・CANON から作り直す・モデルを替える・確定の解除・案の切り出し・窓口の読み替え。"""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.mock = subprocess.Popen([PY, str(REPO / "studio" / "devtools" / "mock_openrouter.py"), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        cls.saved_env = dict(os.environ)
        os.environ.update(NOVEL_LLM_BASE_URL=f"http://127.0.0.1:{cls.port}/api/v1", NOVEL_PROVIDER_APIKEY="mock")
        import core
        cls.core = core
        cls.saved_works = core.WORKS
        core.WORKS = Path(tempfile.mkdtemp(prefix="novel-v12-"))

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        shutil.rmtree(cls.core.WORKS, ignore_errors=True)
        cls.core.WORKS = cls.saved_works
        os.environ.clear()
        os.environ.update(cls.saved_env)

    def test_localize_model(self):
        c = self.core
        ids = ["claude-haiku-5-5", "gpt-6-luna", "deepseek-v4.1-flash"]
        self.assertEqual(c.localize_model("anthropic/claude-haiku-5.5", ids), "claude-haiku-5-5")
        self.assertEqual(c.localize_model("deepseek/deepseek-v4.1-flash", ids), "deepseek-v4.1-flash")
        self.assertEqual(c.localize_model("openai/gpt-6-luna-pro", ids), "openai/gpt-6-luna-pro")  # 読み替え先がなければそのまま
        self.assertEqual(c.localize_model("claude-haiku-5-5", ["anthropic/claude-haiku-5.5"]), "anthropic/claude-haiku-5.5")

    def test_flow(self):
        c = self.core
        roles = {"writer": "anthropic/claude-haiku-5.5", "reviewer": "openai/gpt-6-luna-pro",
                 "polisher": "anthropic/claude-haiku-5.5", "premium": "anthropic/claude-opus-5.5"}
        c.create_work("mys", {"genre": "ミステリ", "theme": "密室", "chapters": 3, "chapter_target": 1200})
        c.propose("mys", ["anthropic/claude-haiku-5.5"], 2)
        pids = sorted(c.load("mys")["proposals"])
        c.select("mys", pids[0])
        v = c.make_brief("mys")
        c.fix("mys", v, roles, None, {"final": "deepseek/deepseek-v4.1-flash"})
        cfg = json.loads((c.WORKS / "mys" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["llm"]["task_overrides"]["final"]["model"], "deepseek/deepseek-v4.1-flash")  # 工程ごとのモデル
        self.assertEqual(c.novelctl("mys", "run")["rc"], 2)  # canon_hold

        # 承認の前に矛盾を探す（上位モデル一回・CANON は変えない）
        fp = c.canon_fingerprint("mys")
        r = c.engine_action("mys", "canon_check", {})
        self.assertEqual(r["rc"], 0, r)
        st = c.engine_status("mys")
        self.assertEqual(st["canon_check"]["canon_fp"], fp)
        self.assertTrue(st["canon_check"]["issues"])
        self.assertEqual(c.canon_fingerprint("mys"), fp)

        # 承認待ちの CANON を人が直す → 指紋が変わる → 承認 → 完成まで
        canon = (c.WORKS / "mys" / "canon" / "CANON.md").read_text(encoding="utf-8")
        c.canon_edit("mys", canon.replace("タイトル：霧の帳面", "タイトル：霧の帳面（改）"))
        fp2 = c.canon_fingerprint("mys")
        self.assertNotEqual(fp, fp2)
        c.approve("mys", fp2)
        self.assertEqual(c.novelctl("mys", "run")["rc"], 3)

        # 書き終えてから CANON を直す → 鍵をかけ直し、第2章から書き直し
        c.canon_edit("mys", canon.replace("タイトル：霧の帳面", "タイトル：霧の帳面（再）"), reset_from=2)
        st = c.engine_status("mys")
        self.assertEqual((st["phase"], st["current"]), ("chapters", 2))
        self.assertTrue(st["canon_locked"])
        self.assertEqual([ch["state"] for ch in st["chapters"]], ["done", "pending", "pending"])
        g = subprocess.run([PY, "tools/guard.py", "check"], cwd=c.WORKS / "mys", capture_output=True, text=True)
        self.assertEqual(g.returncode, 0, g.stdout)

        # 執筆中でも、指示を足して CANON から作り直す
        r = c.engine_action("mys", "redesign", {"feedback": "期間と章の日付を揃える", "reset": True})
        self.assertEqual(r["rc"], 0, r)
        st = c.engine_status("mys")
        self.assertEqual(st["phase"], "design")
        self.assertEqual([ch["state"] for ch in st["chapters"]], ["pending"] * 3)

        # 確定のあとでモデルを替える（指紋を取り直すので見張り番は止めない）
        r = c.set_roles("mys", {"reviewer": "deepseek/deepseek-v4.1-flash"}, {"final": ""})
        self.assertTrue(r["changed"])
        cfg = json.loads((c.WORKS / "mys" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["llm"]["profiles"]["reviewer"]["model"], "deepseek/deepseek-v4.1-flash")
        self.assertNotIn("model", cfg["llm"]["task_overrides"].get("final", {}))
        g = subprocess.run([PY, "tools/guard.py", "check"], cwd=c.WORKS / "mys", capture_output=True, text=True)
        self.assertEqual(g.returncode, 0, g.stdout)
        self.assertIn("モデルを替えた", (c.WORKS / "mys" / "notes" / "INTERVENTIONS.md").read_text(encoding="utf-8"))

        # 確定を解除して BRIEF に戻る（成果は退けるだけ）→ 確定し直せる
        spent = c.costs("mys")["engine_usd"]
        r = c.unfix("mys", "BRIEF の時間の設計から直す")
        st = c.load("mys")
        self.assertIsNone(st["fixed"])
        self.assertTrue((c.WORKS / "mys" / r["archive"] / "canon" / "CANON.md").exists())
        self.assertFalse((c.WORKS / "mys" / "config.json").exists())
        self.assertAlmostEqual(c.costs("mys")["engine_usd"], spent)  # 退けた分の費用も数える
        self.assertEqual(c.stage_of(st), "brief")
        c.fix("mys", v, roles)
        self.assertEqual(c.novelctl("mys", "run")["rc"], 2)

        # 案を別の作品として切り出す（確定済みの作品からでも。元の作品は変えない）
        before = (c.WORKS / "mys" / "studio.json").read_bytes()
        st2 = c.spinoff("mys", pids[1], "mys-b")
        self.assertEqual((st2["selected"], st2["proposals"]["P01"]["parent"]), ("P01", f"mys:{pids[1]}"))
        self.assertEqual(c.stage_of(st2), "propose")
        self.assertEqual((c.WORKS / "mys" / "studio.json").read_bytes(), before)
        with self.assertRaises(c.StudioError):
            c.spinoff("mys", pids[1], "mys-b")
        self.assertTrue(c.make_brief("mys-b"))


if __name__ == "__main__":
    unittest.main()
