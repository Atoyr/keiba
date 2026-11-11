#!/usr/bin/env python3
"""想定4角位置の予測式の比較（群2＝頭単位・レース数を待たない・仮説45）

入力：references/脚質認定_<日付>_<レース名>.md（出走馬プロファイル）の各馬の近走4角r・機械/主観区分・自在フラグ、
      log/races.csv（日付・レース名・距離）、log/predictions.csv（実際の corner4_pos）。
出力：予測式ごとの「レース内順位相関（Spearman）」と「位置の平均絶対誤差（頭数比）」、
      位置が読めない馬の群別誤差と、近走4角rの範囲に実際が入った率。

予測はすべて発走前に確定していた情報だけで行う（結果は採点にだけ使う）。
主観補正の補正量はレース単位の leave-one-out で推定する（当該レースを除いた残りで推定）。
"""
import csv
import glob
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLASS_ORDER = {"逃げ": 0, "先行": 1, "差し": 2, "追込": 3}
CLASS_R = {"逃げ": 0.05, "先行": 0.25, "差し": 0.55, "追込": 0.85}
RECENCY_W = [0.35, 0.25, 0.20, 0.12, 0.08]

HEAD = re.compile(r"^#(\d+) (\S+?)｜4角r \[([^\]]*)\] r_med=([\d.]+|不明) IQR=([\d.]+|不明)")
CLS = re.compile(r"機械=(\S+?)(?:（[^）]*）)? ／ 主観=(\S*)")
DIST = re.compile(r"5走距離帯=(\S+)")


def parse_profile(text):
    jizai = set()
    m = re.search(r"自在フラグ：(.*)", text)
    if m:
        jizai = {int(x) for x in re.findall(r"#(\d+)", m.group(1))}
    horses = {}
    cur = None
    for line in text.split("\n"):
        h = HEAD.match(line)
        if h:
            toks = [x.strip() for x in h.group(3).split(",") if x.strip()]
            rs = [float(x) for x in toks if re.fullmatch(r"[\d.]+", x)]
            cur = {"no": int(h.group(1)), "rs": rs,
                   "r_med": None if h.group(4) == "不明" else float(h.group(4)),
                   "iqr": None if h.group(5) == "不明" else float(h.group(5)),
                   "mech": None, "subj": None, "dists": [], "aligned": len(rs) == len(toks)}
            cur["jizai"] = cur["no"] in jizai
            horses[cur["no"]] = cur
            continue
        if cur is None:
            continue
        c = CLS.search(line)
        if c and cur["mech"] is None:
            cur["mech"], cur["subj"] = c.group(1), c.group(2)
        d = DIST.search(line)
        if d and not cur["dists"]:
            for el in d.group(1).split(";"):
                p = el.split(":")[0]
                cur["dists"].append(int(p) if p.isdigit() else None)
    return horses


def spearman(a, b):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = rank(a), rank(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else None


def load():
    races = {r["race_id"]: r for r in csv.DictReader(open(ROOT / "log/races.csv", encoding="utf-8"))}
    actual = defaultdict(dict)
    for p in csv.DictReader(open(ROOT / "log/predictions.csv", encoding="utf-8")):
        try:
            actual[p["race_id"]][int(p["horse_no"])] = float(p["corner4_pos"])
        except (ValueError, TypeError):
            pass
    md = {}
    for f in glob.glob(str(ROOT / "references/脚質認定_*.md")):
        m = re.search(r"脚質認定_(\d{4}-\d{2}-\d{2})_(.+)\.md$", f)
        if m:
            md[(m.group(1), m.group(2))] = f
    data = []
    for rid, r in races.items():
        f = md.get((r["date"], r["race_name"]))
        act = actual.get(rid)
        if not f or not act or len(act) < 8:
            continue
        dm = re.search(r"(\d{3,4})", r["course"] or "")
        dist = int(dm.group(1)) if dm else None
        prof = parse_profile(open(f, encoding="utf-8").read())
        n = len(act)
        rows = []
        for no, c4 in act.items():
            h = prof.get(no)
            if not h:
                continue
            h = dict(h)
            h["a"] = (c4 - 1) / (n - 1) if n > 1 else 0.0
            rows.append(h)
        if len(rows) >= 8:
            data.append({"rid": rid, "dist": dist, "rows": rows})
    return data


def p_rmed(h, *_):
    return h["r_med"] if h["r_med"] is not None else 0.5


def p_mean(h, *_):
    return statistics.mean(h["rs"]) if h["rs"] else 0.5


def p_latest(h, *_):
    return h["rs"][0] if h["rs"] else 0.5


def p_recency(h, *_):
    if not h["rs"]:
        return 0.5
    w = RECENCY_W[:len(h["rs"])]
    return sum(x * y for x, y in zip(h["rs"], w)) / sum(w)


def p_dist(h, race, *_):
    # 4角r と 5走距離帯 の走数が一致するときだけ走を対応づける（ずれた対応づけを推測しない）
    if race["dist"] and h["rs"] and h["aligned"] and len(h["rs"]) == len(h["dists"]):
        near = [r for r, d in zip(h["rs"], h["dists"]) if d and abs(d - race["dist"]) <= 200]
        if near:
            return statistics.median(near)
    return p_rmed(h)


def p_subj_only(h, *_):
    return CLASS_R.get(h["subj"], p_rmed(h))


def subj_dir(h):
    a, b = CLASS_ORDER.get(h["mech"]), CLASS_ORDER.get(h["subj"])
    if a is None or b is None or a == b:
        return None
    return "前" if b < a else "後"


def fit_shift(train):
    dev = defaultdict(list)
    for race in train:
        for h in race["rows"]:
            d = subj_dir(h)
            if d and h["r_med"] is not None:
                dev[d].append(h["a"] - h["r_med"])
    return {k: statistics.mean(v) for k, v in dev.items() if v}


def evaluate(data):
    preds = {
        "r_med（現行）": lambda h, race, sh: p_rmed(h),
        "平均": lambda h, race, sh: p_mean(h),
        "直近1走": lambda h, race, sh: p_latest(h),
        "直近重み": lambda h, race, sh: p_recency(h),
        "距離帯±200mの中央値": lambda h, race, sh: p_dist(h, race),
        "主観区分のみ": lambda h, race, sh: p_subj_only(h),
        "r_med＋主観補正（LOO）": lambda h, race, sh: p_rmed(h) + sh.get(subj_dir(h), 0.0) if subj_dir(h) else p_rmed(h),
    }
    out = {}
    for name, fn in preds.items():
        rhos, errs = [], []
        for i, race in enumerate(data):
            sh = fit_shift(data[:i] + data[i + 1:])
            pv = [fn(h, race, sh) for h in race["rows"]]
            av = [h["a"] for h in race["rows"]]
            rho = spearman(pv, av)
            if rho is not None:
                rhos.append(rho)
            errs += [abs(p - a) for p, a in zip(pv, av)]
        out[name] = (statistics.mean(rhos), statistics.mean(errs), len(rhos), len(errs))
    return out


def groups(data):
    g = defaultdict(list)
    cover = defaultdict(lambda: [0, 0])
    for race in data:
        for h in race["rows"]:
            e = abs(p_rmed(h) - h["a"])
            if h["mech"] == "未確定" or h["iqr"] is None:
                k = "未確定・IQR不明"
            elif h["iqr"] >= 0.35:
                k = "IQR≥0.35"
            elif h["jizai"]:
                k = "自在フラグ（IQR<0.35）"
            else:
                k = "安定（IQR<0.35・自在なし）"
            g[k].append(e)
            if len(h["rs"]) >= 2:
                lo, hi = min(h["rs"]), max(h["rs"])
                cover[k][1] += 1
                cover[k][0] += lo - 0.05 <= h["a"] <= hi + 0.05
    return g, cover


def main():
    data = load()
    if not data:
        print("対象レースなし（プロファイル md と corner4_pos の両方があるレースが0）")
        return 1
    n_h = sum(len(r["rows"]) for r in data)
    print(f"■ 想定4角位置の予測式比較（群2・{len(data)}レース・{n_h}頭）")
    print(f"{'予測式':<24}{'順位相関ρ':>10}{'平均誤差(頭数比)':>16}")
    res = evaluate(data)
    for name, (rho, err, nr, nh) in sorted(res.items(), key=lambda x: -x[1][0]):
        print(f"{name:<24}{rho:>10.3f}{err:>16.3f}")
    sh = fit_shift(data)
    print(f"※ 主観補正の全データ推定値：主観が前 {sh.get('前', 0):+.3f} ／ 主観が後 {sh.get('後', 0):+.3f}（r単位・負＝想定より前）")
    print()
    print("■ 位置が読めない馬（r_med 基準の誤差・近走4角rの範囲±0.05に実際が入った率）")
    g, cover = groups(data)
    for k in ("安定（IQR<0.35・自在なし）", "自在フラグ（IQR<0.35）", "IQR≥0.35", "未確定・IQR不明"):
        v = g.get(k, [])
        c = cover.get(k, [0, 0])
        cr = f"{c[0]}/{c[1]}（{c[0] / c[1]:.0%}）" if c[1] else "—"
        print(f"  {k:<22} n={len(v):>3} 平均誤差 {statistics.mean(v) if v else float('nan'):.3f} ／ 範囲内 {cr}")
    print()
    print("※ n<10 レースの比較は参考値。採否は較正レビューで判定する（R19）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
