#!/usr/bin/env python3
"""studio/cli.py — studio と同じ工程を、ハーネス（HermesAgent / agy / omp など）やスクリプトから使うための CLI。

すべて JSON を返す（標準出力に1つ）。終了コード 0=成功 / 2=人の判断が要る・拒否 / 1=エラー。
画面（server.py）と同じ core.py を呼ぶので、画面とハーネスを混ぜて使ってよい（同じ works/<slug>/studio.json を読む）。

  python3 studio/cli.py works
  python3 studio/cli.py new <slug> --theme theme.json
  python3 studio/cli.py show <slug> [--brief]                 状態の要約（--brief で提案の本文を省く）
  python3 studio/cli.py theme <slug> --theme theme.json        テーマを変えて最初から（記録は studio/history/ に退避）
  python3 studio/cli.py derive <slug> <new-slug> [--theme theme.json]  テーマを直して、新しい作品フォルダで提案し直す（元の作品は変えない）
  python3 studio/cli.py propose <slug> --models m1,m2 [--n 2] [--mode new|resubmit|rolling] [--instruction "..."] [--temperature 0.8]
  python3 studio/cli.py edit <slug> <pid> --fields fields.json  人（または進行役）が案を直す。新しい案として足される
  python3 studio/cli.py revise <slug> <pid> --model m --instruction "..."
  python3 studio/cli.py classify <slug> [--ids P01,P03,...] [--method tool|ai|both] [--model m] [--threshold 0.5] [--refresh]
        骨格は小分け（settings の skeleton_batch 案ずつ、skeleton_parallel 本まで同時）に抜き出し、取れた分から保存する
  python3 studio/cli.py export-proposals <slug> [--ids P01,P03,...] [--fmt md|csv|json] [--out path]   案をまとめて書き出す（見比べる用）
  python3 studio/cli.py reclassify <slug> --threshold 0.45     骨格は使い回し、しきい値だけ変える（モデルを呼ばない）
  python3 studio/cli.py select <slug> <pid>
  python3 studio/cli.py brief <slug> [--model m] [--instruction "..."] [--base V]
  python3 studio/cli.py brief-put <slug> <base-V> --file brief.json   自分で書いた BRIEF を新しい版として置く（lint 付き）
  python3 studio/cli.py brief-show <slug> <V> [--markdown]
  python3 studio/cli.py fix <slug> <V> [--roles roles.json] [--tasks tasks.json] [--budget 3]
  python3 studio/cli.py spinoff <slug> <pid> <new-slug>        案を別の作品として切り出す（BRIEF の工程から。元の作品は変えない）
  python3 studio/cli.py roles <slug> [--roles roles.json] [--tasks tasks.json] [--budget 3]   確定のあとでモデルを替える（指紋を取り直す）
        tasks.json = {"canon-review": M, "review": M, "ledger": M, "final": M}（空文字なら役のモデルに戻す）
  python3 studio/cli.py unfix <slug> [--note "理由"]           確定を解除して BRIEF に戻る（執筆の成果は studio/history/ へ退ける）
  python3 studio/cli.py canon-put <slug> --file CANON.md [--ch N]  人が直した CANON に差し替える（--ch N で第N章以降を書き直し）
  python3 studio/cli.py engine <slug> <action> [--ch N] [--feedback "..."] [--flags retry,accept] [--chapters 1,2]
        rescue_canon / rescue_chapter は [--model M]（この一回だけ別のモデルで）と [--note "指示"] も取る
        action = start | step | redesign | rescue_canon | rescue_chapter | resolve | reset_chapter | polish | proofread | assemble | epub | apply_proofread | cover | canon_check
        canon_check は [--model M]（承認の前に CANON の矛盾を一度だけ探す）。redesign は --flags reset で執筆中でも CANON から作り直す
  python3 studio/cli.py engine <slug> cover [--model M|none] [--style simple|illust|photo] [--motif "..."]
        [--title-dir vertical|horizontal] [--font mincho|gothic|casual] [--author "..."] [--reuse-art]
  python3 studio/cli.py image-models                         画像生成モデルの一覧
  python3 studio/cli.py costs <slug>

承認（approve-canon）は CLI にはありません。人が画面か端末で行います。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402


def out(obj, rc=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return rc


def summary(st, short=False):
    props = {}
    for pid, p in st["proposals"].items():
        props[pid] = {k: p.get(k) for k in ("model", "kind", "genre", "title", "usd", "origin", "parent", "hidden")}
        if not short:
            props[pid]["synopsis"] = p.get("synopsis")
        lab = (st.get("classify") or {}).get("labels", {}).get(pid)
        if lab:
            props[pid]["label"] = lab["label"]
    c = st.get("classify") or {}
    return {"slug": st["slug"], "stage": core.stage_of(st), "theme": st["theme"], "theme_version": st["theme_version"], "derived_from": st.get("derived_from"),
            "rounds": [{k: r[k] for k in ("id", "mode", "models", "n", "proposals", "errors")} for r in st["rounds"]],
            "proposals": props, "selected": st.get("selected"),
            "classify": {"clusters": [{k: cl.get(k) for k in ("id", "label", "type", "members", "cohesion")} for cl in c.get("clusters", [])],
                         "mean_similarity": c.get("mean_similarity"), "threshold": c.get("threshold")} if c else None,
            "avoid": core.avoid_list(st),
            "briefs": [{"v": b["v"], "from": b["from"], "model": b["model"], "origin": b.get("origin"), "lint": b["lint"], "usd": b["usd"]} for b in st["briefs"]],
            "fixed": st.get("fixed"), "costs": core.costs(st["slug"])}


def rd_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--theme")
    ap.add_argument("--models")
    ap.add_argument("--model")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--mode", default="new")
    ap.add_argument("--instruction", default="")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--fields")
    ap.add_argument("--method", default="tool")
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--base", type=int)
    ap.add_argument("--file")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--roles")
    ap.add_argument("--budget", type=float)
    ap.add_argument("--feedback")
    ap.add_argument("--flags", default="")
    ap.add_argument("--chapters", default="")
    ap.add_argument("--ch", type=int)
    ap.add_argument("--style")
    ap.add_argument("--motif", default="")
    ap.add_argument("--title-dir", dest="title_dir")
    ap.add_argument("--font")
    ap.add_argument("--author", default="")
    ap.add_argument("--reuse-art", dest="reuse_art", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--tasks")
    ap.add_argument("--ids", default="")
    ap.add_argument("--fmt", default="md")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    A = a.args
    try:
        if a.cmd == "works":
            return out({"works": core.list_works()})
        if a.cmd == "new":
            core.create_work(A[0], rd_json(a.theme) if a.theme else {})
            return out(summary(core.load(A[0])))
        if a.cmd == "show":
            return out(summary(core.load(A[0]), short=a.brief))
        if a.cmd == "theme":
            core.change_theme(A[0], rd_json(a.theme))
            return out(summary(core.load(A[0])))
        if a.cmd == "derive":  # derive <元の作品> <新しい作品> --theme theme.json（テーマを直して新しい作品で出し直す）
            st = core.derive_work(A[0], A[1], rd_json(a.theme) if a.theme else core.load(A[0])["theme"])
            return out(summary(st))
        if a.cmd == "propose":
            models = [m.strip() for m in (a.models or ",".join(core.settings()["propose_models"])).split(",") if m.strip()]
            return out(core.propose(A[0], models, a.n, a.instruction, a.mode, a.temperature))
        if a.cmd == "edit":
            return out({"pid": core.edit_proposal(A[0], A[1], rd_json(a.fields))})
        if a.cmd == "revise":
            return out({"pid": core.revise_proposal(A[0], A[1], a.model or core.settings()["skeleton_model"], a.instruction)})
        if a.cmd == "classify":
            ids = [x for x in a.ids.split(",") if x] or None
            r = core.classify(A[0], a.model, a.method, a.threshold, refresh_skeletons=a.refresh, ids=ids,
                              progress=lambda d, t, w="": print(f"{w} {d}/{t}", file=sys.stderr, flush=True))
            return out({k: r.get(k) for k in ("ids", "labels", "clusters", "mean_similarity", "top_pairs", "ai", "missing", "errors")},
                       0 if not r.get("missing") else 2)
        if a.cmd == "export-proposals":
            name, data, _ = core.export_proposals(A[0], [x for x in a.ids.split(",") if x], a.fmt)
            p = Path(a.out or name)
            p.write_bytes(data)
            return out({"file": str(p), "bytes": len(data)})
        if a.cmd == "reclassify":
            r = core.reclassify(A[0], a.threshold)
            return out({k: r.get(k) for k in ("labels", "clusters", "mean_similarity")})
        if a.cmd == "select":
            core.select(A[0], A[1])
            return out({"selected": A[1]})
        if a.cmd == "brief":
            return out({"v": core.make_brief(A[0], a.model, a.instruction, a.base)})
        if a.cmd == "brief-put":
            v = core.edit_brief(A[0], int(A[1]), rd_json(a.file))
            st = core.load(A[0])
            return out({"v": v, "lint": next(b["lint"] for b in st["briefs"] if b["v"] == v)})
        if a.cmd == "brief-show":
            r = core.render_brief(A[0], int(A[1]))
            if not a.markdown:
                r.pop("markdown", None)
            return out(r, 0 if r["lint"]["ok"] else 2)
        if a.cmd == "fix":
            return out(core.fix(A[0], int(A[1]), rd_json(a.roles) if a.roles else None, a.budget, rd_json(a.tasks) if a.tasks else None))
        if a.cmd == "spinoff":
            return out(summary(core.spinoff(A[0], A[1], A[2])))
        if a.cmd == "roles":
            return out(core.set_roles(A[0], rd_json(a.roles) if a.roles else None, rd_json(a.tasks) if a.tasks else None, a.budget))
        if a.cmd == "unfix":
            return out(core.unfix(A[0], a.note))
        if a.cmd == "canon-put":
            return out(core.canon_edit(A[0], Path(a.file).read_text(encoding="utf-8"), a.ch))
        if a.cmd == "engine":
            if A[1] in ("rescue_chapter", "reset_chapter") and not a.ch:
                return out({"error": "--ch（章番号）が必要です"}, 1)
            args = {"n": a.ch, "feedback": a.feedback or "", "flags": [x for x in a.flags.split(",") if x],
                    "chapters": [int(x) for x in a.chapters.split(",") if x],
                    "model": a.model, "style": a.style, "motif": a.motif, "title_dir": a.title_dir, "font": a.font,
                    "author": a.author, "reuse_art": a.reuse_art, "note": a.note, "reset": "reset" in a.flags.split(",")}
            r = core.engine_action(A[0], A[1], args)
            st = core.engine_status(A[0])
            return out({**r, "status": {k: st.get(k) for k in ("phase", "current", "escalation", "next", "running", "usage")}},
                       0 if r["rc"] in (0, 3) else 2)
        if a.cmd == "status":
            return out(core.engine_status(A[0]))
        if a.cmd == "image-models":
            import llm
            return out({"models": llm.image_models()})
        if a.cmd == "costs":
            return out(core.costs(A[0]))
        if a.cmd in ("approve", "approve-canon"):
            return out({"error": "承認は人が行います（studio の画面、または作品フォルダで python3 tools/novelctl.py approve-canon）"}, 2)
        return out({"error": f"不明なコマンド {a.cmd}", "usage": __doc__}, 1)
    except core.StudioError as e:
        return out({"error": str(e)}, 2)
    except (IndexError, FileNotFoundError, ValueError) as e:
        return out({"error": f"引数が足りないか不正です：{e}", "usage": __doc__.strip().splitlines()[0]}, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
