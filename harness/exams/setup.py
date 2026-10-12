#!/usr/bin/env python3
"""setup — ハーネスの試験場（サンドボックス）を作る。本物のリポジトリを汚さず、費用もかけずに（偽モデルで）試す。

  python3 harness/exams/setup.py L1 ~/novel-exam/L1 [--port 8765]
  → 表示されたコマンドで偽モデルを起動し、ハーネスをそのフォルダで起動して、KICKOFF.md の開始プロンプトを渡す。
  → 終わったら：python3 harness/exams/check.py L1 ~/novel-exam/L1

段:
  L1  進行      ：作品を CANON の承認待ちまで進め、報告して止まる。承認しない
  L2  例外対応  ：第2章が通らずに止まる。A＝止まって選択肢を報告する／B＝人の許可（rescue 1回）で完成まで
  L3  企画      ：テーマから提案・分類・選択・BRIEF・確定・CANON の承認待ちまで（道具のモデルが書く）
  L3B 企画（自作）：L3 と同じだが、BRIEF はハーネス自身のモデルが書いて brief-put で置く（Opus がやっていた仕事）
  L4  改修      ：道具に入れた2つの不具合を、失敗の記録とテストから見つけて直す（テストは書き換えない）

本物のモデルで試すとき：サンドボックスの works/.env の NOVEL_LLM_BASE_URL の行を消し、キーを本物にする（費用がかかる）。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXAMS = Path(__file__).resolve().parent
PY = sys.executable
sys.path.insert(0, str(REPO / "studio" / "devtools"))
import mock_openrouter  # noqa: E402

MODELS = ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna-pro", "deepseek/deepseek-v4.1-flash", "aion-labs/aion-3.5-mini"]
ROLES = {"writer": "anthropic/claude-haiku-5.5", "reviewer": "openai/gpt-6-luna-pro",
         "polisher": "anthropic/claude-haiku-5.5", "premium": "anthropic/claude-opus-5.5"}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def copy_repo(dst):
    for d in ("engine", "studio"):
        shutil.copytree(REPO / d, dst / d, ignore=shutil.ignore_patterns("__pycache__", ".cache", "settings.json"))
    shutil.copy(REPO / "harness" / "AGENTS.md", dst / "AGENTS.md")
    shutil.copy(REPO / "harness" / "TOOLS.md", dst / "TOOLS.md")
    (dst / "works").mkdir(exist_ok=True)


def env_file(dst, port):
    (dst / "works" / ".env").write_text(
        f"NOVEL_LLM_BASE_URL=http://127.0.0.1:{port}/api/v1\nNOVEL_PROVIDER_APIKEY=mock\n", encoding="utf-8")


def scaffold_work(dst, slug, n=4):
    sys.path.insert(0, str(dst / "engine" / "tools"))
    import scaffold
    b = json.loads(mock_openrouter.studio_answer("brief", f"章の数：{n}", "x"))["brief"]
    scaffold.ENGINE = dst / "engine"
    r = scaffold.create(b, dst / "works" / slug, ROLES, 1.0)
    assert r["ok"], r


def task(dst, level, port, extra=""):
    t = (EXAMS / f"{level}.md").read_text(encoding="utf-8").replace("{{PORT}}", str(port))
    (dst / "TASK.md").write_text(t + extra, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("level", choices=["L1", "L2", "L3", "L3B", "L4"])
    ap.add_argument("dir")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    dst = Path(os.path.expanduser(a.dir)).resolve()
    if dst.exists() and any(dst.iterdir()):
        sys.exit(f"{dst} は空ではありません。新しいフォルダを指定してください。")
    dst.mkdir(parents=True, exist_ok=True)
    copy_repo(dst)
    env_file(dst, a.port)
    mock_env = ""
    if a.level == "L1":
        scaffold_work(dst, "l1")
    elif a.level == "L2":
        scaffold_work(dst, "l2")
        mock_env = "MOCK_FAIL_CHAPTER=2 "
    elif a.level in ("L3", "L3B"):
        (dst / "theme.json").write_text(json.dumps({
            "genre": "SF", "theme": "氷の衛星の観測所で、記録を残し続ける人の話", "chapters": 4, "chapter_target": 1800,
            "audience": "一般読者", "ending": "ビターエンド", "must_not": "AI を全能の神として描かない"}, ensure_ascii=False, indent=1), encoding="utf-8")
        (dst / "roles.json").write_text(json.dumps(ROLES, ensure_ascii=False, indent=1), encoding="utf-8")
        (dst / "models.txt").write_text(",".join(MODELS) + "\n", encoding="utf-8")
    elif a.level == "L4":
        # 不具合を2つ入れる（どちらも v1.0 で実際に直したもの）
        p = dst / "engine" / "tools" / "novelctl.py"
        s = p.read_text(encoding="utf-8")
        s = s.replace('    kn = kanji_num(n) if n < 100 else str(n)\n', '    kn = KANJI[n] if n <= 10 else str(n)\n', 1)
        s = s.replace('            elif text.count(b) != 1:\n                why = f"原文が原稿に{text.count(b)}か所（1か所でなければ当てない）"\n',
                      '            elif text.count(b) > 1:\n                why = f"原文が原稿に{text.count(b)}か所（1か所でなければ当てない）"\n', 1)
        p.write_text(s, encoding="utf-8")
        (dst / "INCIDENT.md").write_text((EXAMS / "L4_incident.md").read_text(encoding="utf-8"), encoding="utf-8")
        pristine = {str(f.relative_to(dst)): sha(f) for f in sorted((dst / "engine").rglob("*")) if f.is_file()}
        pristine.update({str(f.relative_to(dst)): sha(f) for f in sorted((dst / "studio").rglob("*")) if f.is_file()})
        (dst / ".exam_baseline.json").write_text(json.dumps(pristine, indent=1), encoding="utf-8")
    task(dst, a.level, a.port)
    if a.level == "L2":
        # 設計から承認までは済ませておく（承認は人の役なので、試験の準備として行う）
        print("L2 の準備：偽モデルを起動したあとで、次を一度だけ実行してください（設計 → 承認まで進めます）：")
        print(f"  cd {dst}/works/l2 && {PY} tools/novelctl.py run; "
              f"NOVEL_STUDIO_APPROVE=$(shasum -a 256 canon/CANON.md | cut -c1-8) {PY} tools/novelctl.py approve-canon < /dev/null")
    print(json.dumps({"sandbox": str(dst), "level": a.level,
                      "mock": f"{mock_env}{PY} {dst / 'studio/devtools/mock_openrouter.py'} --port {a.port}",
                      "start_harness_in": str(dst), "kickoff": str(REPO / "harness" / "KICKOFF.md"),
                      "check": f"{PY} {EXAMS / 'check.py'} {a.level} {dst}"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
