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
        for f in cls.llm.CACHE.glob(f"*models_{cls.llm._base_tag()}*"):
            f.unlink(missing_ok=True)

    def test_broken_cache_is_refetched(self):
        llm = self.llm
        llm.CACHE.mkdir(exist_ok=True)
        f = llm.CACHE / f"models_{llm._base_tag()}.json"
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


if __name__ == "__main__":
    unittest.main()
