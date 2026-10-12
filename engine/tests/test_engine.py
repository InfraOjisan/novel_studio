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
        env = dict(os.environ, MOCK_FAIL_CHAPTER="2")
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
        self.assertEqual(self.ctl(work, "rescue", "chapter", "2").returncode, 0)
        out = self.ctl(work, "run")
        self.assertEqual(out.returncode, 3, out.stdout[-800:])
        st = json.loads(self.ctl(work, "status", "--json").stdout)
        self.assertEqual(st["phase"], "done")
        self.assertEqual(st["rescues"], 1)
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

    def test_chapter_numbers_over_twenty(self):
        work = self.tmp / "w22"
        r = scaffold.create(sample_brief(22), work)
        self.assertTrue(r["ok"], r)
        code = ("import sys; sys.path.insert(0,'tools'); import novelctl as n;"
                "print(n.kanji_num(11), n.kanji_num(20), n.kanji_num(22));"
                "print(bool(n.extract_chapter('# 第二十二章　霧\\n本文。', 22)), bool(n.extract_chapter('# 第十一章　霧\\n本文。', 11)))")
        out = subprocess.run([PY, "-c", code], cwd=work, capture_output=True, text=True).stdout.split()
        self.assertEqual(out, ["十一", "二十", "二十二", "True", "True"])


if __name__ == "__main__":
    unittest.main()
