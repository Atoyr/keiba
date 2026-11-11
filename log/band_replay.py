#!/usr/bin/env python3
"""荒れ度帯域の過小評価と、相手を広げた三連複のリプレイ（群3＝レース単位・仮説46）

(1) 宣言した帯域（races.notes の band=）ごと、ハンデ戦／非ハンデ戦ごとに、実際の三連単が10万円を超えた率を出す。
(2) predictions.csv の final_score 順と妙味・R値だけで三連複の構造を組み直し、実際の三連複配当で的中・回収を再計算する。
    使うのは発走前に確定していた列（final_score・myomi_score・r_value）だけで、着順は採点にだけ使う。
    ☆は妙味4以上の馬から R≧4.0 を優先して選ぶ（配分表第2項の荒帯域の紐の優先順位に合わせる）。

ハンデ戦の判定は races.csv に列が無いため HANDICAP_RACES で持つ。出典は『レース別_過去傾向データ.md』の見出し
（ハンデと明記された⑨⑩⑪⑭⑯㉔）・本指示のレジーム節（夏ローカルハンデG3＝函館記念・北九州記念）・races.notes の荒れ度宣言（ハンデ+2）。
関屋記念は2025年からハンデ、中京記念・新潟記念は2025年から別定（同ファイルの制度注記）。新しいハンデ戦を予想したらここへ足す。
"""
import csv
import re
import sys
from collections import defaultdict
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HANDICAP_RACES = {
    "函館記念", "ラジオNIKKEI賞", "北九州記念", "七夕賞", "小倉記念", "関屋記念", "CBC賞",
    "京成杯オータムハンデキャップ", "アルゼンチン共和国杯",
}
STRUCTS = [
    # (名前, 型, final上位の頭数, ☆の頭数)
    ("軸1頭流し・相手7（中帯域相当）", "axis", 6, 2),
    ("軸1頭流し・相手9（荒側）", "axis", 8, 2),
    ("BOX6（final上位5＋☆1）", "box", 5, 1),
    ("BOX8（final上位6＋☆2）", "box", 6, 2),
    ("BOX10（final上位8＋☆2）", "box", 8, 2),
]


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def is_handicap(r):
    return r["race_name"] in HANDICAP_RACES or bool(re.search(r"荒れ度=[^/]*ハンデ\+[1-9]", r["notes"] or ""))


def load():
    races = {r["race_id"]: r for r in csv.DictReader(open(ROOT / "log/races.csv", encoding="utf-8"))
             if r["result_1st"] and r["payout_sanrenpuku"]}
    preds = defaultdict(list)
    for p in csv.DictReader(open(ROOT / "log/predictions.csv", encoding="utf-8")):
        if p["race_id"] in races and (p["finish_pos"] or "").isdigit():
            preds[p["race_id"]].append(p)
    return races, preds


def stars(ps, exclude, n):
    cand = [x for x in ps if int(x["horse_no"]) not in exclude and f(x["myomi_score"]) >= 4]
    cand.sort(key=lambda x: (f(x["r_value"]) < 4.0, -f(x["myomi_score"]), -f(x["final_score"])))
    return {int(x["horse_no"]) for x in cand[:n]}


def build(ps, kind, k, n_star):
    fs = sorted(ps, key=lambda x: -f(x["final_score"]))
    top = [int(x["horse_no"]) for x in fs[:k]]
    pool = set(top) | stars(ps, set(top), n_star)
    if kind == "box":
        return None, pool, comb(len(pool), 3)
    axis = top[0]
    others = pool - {axis}
    return axis, others, comb(len(others), 2)


def band_table(races):
    t = defaultdict(lambda: [0, 0])
    for r in races.values():
        b = re.search(r"band=(\S)", r["notes"] or "")
        keys = ["帯域:" + (b.group(1) if b else "記録なし"), "ハンデ戦" if is_handicap(r) else "非ハンデ戦"]
        big = f(r["payout_sanrentan"]) >= 100000
        for k in keys:
            t[k][0] += big
            t[k][1] += 1
    return t


def replay(races, preds):
    out = defaultdict(lambda: [0, 0, 0.0, 0])  # 的中, 投資, 回収, レース数
    detail = defaultdict(list)
    for rid, ps in preds.items():
        r = races[rid]
        top3 = {int(x["horse_no"]) for x in ps if int(x["finish_pos"]) <= 3}
        if len(top3) != 3:
            continue
        pay = f(r["payout_sanrenpuku"])
        grp = "ハンデ戦" if is_handicap(r) else "非ハンデ戦"
        for name, kind, k, ns in STRUCTS:
            axis, pool, pts = build(ps, kind, k, ns)
            hit = top3 <= pool if axis is None else (axis in top3 and (top3 - {axis}) <= pool)
            for g in ("全体", grp):
                o = out[(g, name)]
                o[0] += hit
                o[1] += pts * 100
                o[2] += pay if hit else 0
                o[3] += 1
            if hit:
                detail[(grp, name)].append(pay)
    return out, detail


def main():
    races, preds = load()
    if not races:
        print("対象レースなし")
        return 1
    print("■ (1) 宣言帯域・ハンデ別の三連単10万円超の率")
    t = band_table(races)
    for k in sorted(t):
        a, n = t[k]
        print(f"  {k:<12} {a}/{n}（{a / n:.0%}）")
    print()
    print("■ (2) 三連複のリプレイ（100円単位・実配当で照合・モデル順位は予想時の final_score）")
    out, detail = replay(races, preds)
    for g in ("全体", "ハンデ戦", "非ハンデ戦"):
        print(f"== {g}")
        for name, *_ in STRUCTS:
            hit, cost, ret, n = out[(g, name)]
            if not n:
                continue
            mx = max(detail.get((g, name), [0])) if g != "全体" else max(
                detail.get(("ハンデ戦", name), [0]) + detail.get(("非ハンデ戦", name), [0]))
            ex = (ret - mx) / cost if cost else 0
            print(f"  {name:<26} {n}R 的中{hit} 投資{cost:,} 回収{ret:,.0f} ROI{ret / cost:.0%}"
                  f"（1R平均{cost / n:,.0f}円・最高配当を除くROI{ex:.0%}）")
    print()
    print("※ n<10 は参考値。帯域・配分表の改訂は較正レビューでのみ判定する（R19）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
