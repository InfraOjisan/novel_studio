"""similarity — 提案の「収束／バリエーション／外れ値」を、ルールで分類する（LLM に判定させない）。

Novel_4 の検証で分かったこと：
  - AI の審判は重複の判定がぶれる（ARI 0.115〜1.0）。重複はルールで判定する。
  - 企画は類型に収束する（ミステリ5/5が「語り手が加害者」、SF 4/5 が「AI管理の冷凍睡眠船」）。
  - 収束しているのは表面の言葉より「骨格」（誰が欺く・何を隠す・どの層で驚かせる・何がひっくり返る）。
そこで、LLM には骨格の抽出だけをさせ（安いモデルで十分）、似ているかどうかは
「本文の文字2-gram の近さ」と「骨格の欄ごとの近さ」を混ぜた点数で、平均連結のクラスタリングにかける。
しきい値は画面で動かせ、LLM を呼び直さずに分類し直せる。
"""
import math
import re
from collections import Counter

SK_FIELDS = ["deceiver", "hidden", "layer", "flip", "stage", "ending"]
TEXT_FIELDS = ["title", "setting", "synopsis", "highlight"]


def norm(s):
    return re.sub(r"[\s　「」『』（）()、。・…―－\-!！?？\"'＊*#:：]", "", str(s or ""))


def grams(s, n=2):
    s = norm(s)
    return Counter(s[i:i + n] for i in range(max(0, len(s) - n + 1)))


def cos(a, b):
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())))


def field_sim(x, y):
    x, y = norm(x), norm(y)
    if not x or not y:
        return 0.0
    if x == y or x in y or y in x:
        return 1.0
    return cos(grams(x, 1), grams(y, 1)) * 0.5 + cos(grams(x), grams(y)) * 0.5


def pair_score(p, q, sp, sq, w_text=0.4):
    t = cos(grams(" ".join(str(p.get(f, "")) for f in TEXT_FIELDS)), grams(" ".join(str(q.get(f, "")) for f in TEXT_FIELDS)))
    if sp and sq:
        k = sum(field_sim(sp.get(f), sq.get(f)) for f in SK_FIELDS) / len(SK_FIELDS)
        return w_text * t + (1 - w_text) * k, t, k
    return t, t, None


def classify(props, skeletons, threshold=0.5, w_text=0.4):
    """props: [{id, ...}]、skeletons: {id: {...}}。返り値：{labels:{id:{label,cluster,nearest,sim}}, clusters:[...], matrix:{}}"""
    ids = [p["id"] for p in props]
    P = {p["id"]: p for p in props}
    n = len(ids)
    S = {}
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ids[i], ids[j]
            s, t, k = pair_score(P[a], P[b], skeletons.get(a), skeletons.get(b), w_text)
            S[(a, b)] = S[(b, a)] = round(s, 3)
    # 平均連結の凝集型クラスタリング
    clusters = [[x] for x in ids]

    def link(c1, c2):
        return sum(S[(a, b)] for a in c1 for b in c2) / (len(c1) * len(c2))
    while len(clusters) > 1:
        best, bi, bj = -1, -1, -1
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                v = link(clusters[i], clusters[j])
                if v > best:
                    best, bi, bj = v, i, j
        if best < threshold:
            break
        clusters[bi] = clusters[bi] + clusters[bj]
        del clusters[bj]
    clusters.sort(key=len, reverse=True)
    big = max(2, math.ceil(n * 0.3))
    labels, out_clusters = {}, []
    for ci, c in enumerate(clusters):
        if len(c) >= big:
            lab = "収束"
        elif len(c) >= 2:
            lab = "バリエーション"
        else:
            lab = None
        sk = [skeletons.get(x) or {} for x in c]
        common = {}
        for f in SK_FIELDS:
            vals = Counter(norm(s.get(f)) for s in sk if s.get(f))
            if vals:
                v, cnt = vals.most_common(1)[0]
                if cnt >= max(2, len(c) // 2 + 1) or len(c) == 1:
                    common[f] = next(s.get(f) for s in sk if norm(s.get(f)) == v)
        type_line = "／".join(f"{v}" for v in common.values()) or ""
        cohesion = round(sum(S[(a, b)] for a in c for b in c if a != b) / max(1, len(c) * (len(c) - 1)), 3) if len(c) > 1 else None
        out_clusters.append({"id": f"C{ci + 1}", "members": c, "label": lab, "type": type_line, "cohesion": cohesion,
                             "one_lines": [(skeletons.get(x) or {}).get("one_line", "") for x in c]})
        for x in c:
            others = [(S[(x, y)], y) for y in ids if y != x]
            sim, near = max(others) if others else (0.0, None)
            if lab is None:
                lab_x = "外れ値" if sim < threshold * 0.8 else "バリエーション"
            else:
                lab_x = lab
            labels[x] = {"label": lab_x, "cluster": f"C{ci + 1}", "nearest": near, "sim": sim}
        if lab is None:
            out_clusters[-1]["label"] = labels[c[0]]["label"]
    pairs = sorted(((S[(a, b)], a, b) for i, a in enumerate(ids) for b in ids[i + 1:]), reverse=True)
    mean = round(sum(s for s, _, _ in pairs) / len(pairs), 3) if pairs else None
    return {"labels": labels, "clusters": out_clusters, "threshold": threshold, "w_text": w_text,
            "mean_similarity": mean, "top_pairs": [{"a": a, "b": b, "sim": s} for s, a, b in pairs[:8]]}
