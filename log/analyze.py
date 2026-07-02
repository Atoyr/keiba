#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""予測ログ集計スクリプト。log/ ディレクトリで python3 analyze.py を実行。標準ライブラリのみ使用。"""
import csv
import os
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    path = os.path.join(BASE, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    preds = load("predictions.csv")
    bets = load("bets.csv")
    races = load("races.csv")
    fires = load("rule_fires.csv")
    rules = {r["rule_id"]: r["rule_name"] for r in load("rules_master.csv")}

    print("=" * 60)
    print("■ 印別成績（結果入力済みの馬のみ）")
    print("=" * 60)
    by_mark = defaultdict(list)
    for p in preds:
        if p.get("finish_pos"):
            by_mark[p.get("mark") or "-"].append(p)
    order = ["◎", "○", "▲", "△", "☆", "-", "x"]
    for mark in sorted(by_mark, key=lambda m: order.index(m) if m in order else 99):
        rows = by_mark[mark]
        n = len(rows)
        inp = sum(1 for r in rows if r.get("in_place") == "1")
        finishes = [to_f(r["finish_pos"]) for r in rows if to_f(r["finish_pos"])]
        avg = sum(finishes) / len(finishes) if finishes else 0
        # 単勝回収率（1着かつオッズあり）
        stake = ret = 0
        for r in rows:
            odds = to_f(r.get("win_odds"))
            if odds is not None:
                stake += 100
                if r.get("finish_pos") == "1":
                    ret += odds * 100
        roi = f" 単勝回収率 {ret / stake * 100:.0f}%" if stake else ""
        print(f"{mark}: n={n} 複勝圏 {inp}/{n} ({inp / n * 100:.0f}%) 平均着順 {avg:.1f}{roi}")

    print()
    print("=" * 60)
    print("■ R値帯別 複勝率")
    print("=" * 60)
    bands = [("R>=4.0", lambda r: r >= 4.0), ("3.0-4.0", lambda r: 3.0 <= r < 4.0),
             ("2.0-3.0", lambda r: 2.0 <= r < 3.0), ("1.5-2.0", lambda r: 1.5 <= r < 2.0),
             ("R<1.5", lambda r: r < 1.5)]
    for label, cond in bands:
        rows = [p for p in preds if to_f(p.get("r_value")) is not None
                and cond(to_f(p["r_value"])) and p.get("finish_pos")]
        if rows:
            inp = sum(1 for r in rows if r.get("in_place") == "1")
            print(f"{label}: n={len(rows)} 複勝圏 {inp}/{len(rows)} ({inp / len(rows) * 100:.0f}%)")

    print()
    print("=" * 60)
    print("■ 券種別 収支")
    print("=" * 60)
    by_type = defaultdict(lambda: {"cost": 0, "ret": 0, "n": 0, "hit": 0, "missing": 0})
    for b in bets:
        t = b.get("bet_type", "?")
        d = by_type[t]
        d["n"] += 1
        d["hit"] += 1 if b.get("hit") == "1" else 0
        c, r = to_f(b.get("cost")), to_f(b.get("return"))
        if c is not None:
            d["cost"] += c
        if r is not None:
            d["ret"] += r
        if b.get("hit") == "1" and r is None:
            d["missing"] += 1
    total_c = total_r = 0
    for t, d in by_type.items():
        roi = f"{d['ret'] / d['cost'] * 100:.0f}%" if d["cost"] else "-"
        miss = f"（払戻未入力 {d['missing']} 件）" if d["missing"] else ""
        print(f"{t}: 購入 {d['cost']:.0f}円 払戻 {d['ret']:.0f}円 回収率 {roi} 的中 {d['hit']}/{d['n']}{miss}")
        total_c += d["cost"]
        total_r += d["ret"]
    if total_c:
        print(f"合計: 購入 {total_c:.0f}円 払戻 {total_r:.0f}円 回収率 {total_r / total_c * 100:.0f}%")

    print()
    print("=" * 60)
    print("■ ペーススコア事前フラグの的中率")
    print("=" * 60)
    judged = [r for r in races if r.get("pace_match") in ("0", "1")]
    if judged:
        ok = sum(1 for r in judged if r["pace_match"] == "1")
        print(f"一致 {ok}/{len(judged)} ({ok / len(judged) * 100:.0f}%)")
    else:
        print("判定済みレースなし")

    print()
    print("=" * 60)
    print("■ ルール別 遵守状況")
    print("=" * 60)
    by_rule = defaultdict(list)
    for f_ in fires:
        by_rule[f_["rule_id"]].append(f_)
    for rid in sorted(by_rule):
        rows = by_rule[rid]
        fired = [r for r in rows if r.get("fired") == "1"]
        followed = sum(1 for r in fired if r.get("followed") == "1")
        outcomes = defaultdict(int)
        for r in fired:
            outcomes[r.get("outcome", "?")] += 1
        oc = " / ".join(f"{k}:{v}" for k, v in outcomes.items())
        print(f"{rid} {rules.get(rid, '')[:30]}: 発火 {len(fired)} 遵守 {followed}/{len(fired)} [{oc}]")


if __name__ == "__main__":
    main()
