#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest.py — ログ済みレースに対する戦略リプレイ（標準ライブラリのみ）

analyze.py が「実際に買った結果」の集計であるのに対し、本スクリプトは
「同じ印・同じログで、別の戦略で買っていたら」を検証する。

検証できること:
  A. 印別の単勝/複勝ベタ買い ROI（◎○▲△☆）
  B. ベースライン（1人気複勝・1〜3人気複勝・1人気単勝）との対比
  C. R≧4.0 帯の単勝/複勝
  D. 三連単フォーメーションの頭比較: 頭◎のみ vs 頭◎○両置き（R12・検証待ち仮説1の直接検証）
  E. 三連複: ◎軸1頭流し vs 印5頭BOX

制約（結果の読み方）:
  - 単勝ROIは win_odds 記録済みのため正確
  - 複勝ROIは place_odds_max（事前の複勝上限）による【上限推定】。実払戻ではない
  - 三連単/三連複は的中組合せの配当のみ既知のため、「買い目集合に当たり目が
    含まれるか」で判定する（点数×単価は列挙で正確に計算）
  - n<10 の集計は参考値。閾値・ルールの改廃根拠にはしない（R19。n=10レビューで判定）

実行:  python3 log/backtest.py [--unit 100]
"""
import argparse
import csv
import os
from collections import defaultdict
from itertools import combinations, permutations

BASE = os.path.dirname(os.path.abspath(__file__))
MARK_ORDER = ["◎", "○", "▲", "△", "☆"]


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


def to_i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def build_dataset():
    """結果・配当が揃ったレースのみを対象化。欠損レースは明示してスキップ。"""
    races = {r["race_id"]: r for r in load("races.csv")}
    preds = defaultdict(list)
    for p in load("predictions.csv"):
        preds[p["race_id"]].append(p)

    usable, skipped = {}, []
    for rid, race in races.items():
        top3 = [to_i(race.get(k)) for k in ("result_1st", "result_2nd", "result_3rd")]
        if None in top3 or rid not in preds:
            skipped.append((rid, "結果 or predictions 欠損"))
            continue
        horses = {}
        for p in preds[rid]:
            no = to_i(p.get("horse_no"))
            if no is not None:
                horses[no] = p
        usable[rid] = {"race": race, "horses": horses, "top3": tuple(top3)}
    return usable, skipped


# ---------- A/B/C: 1頭単位のベタ買い ----------

def flat_bets(data, unit):
    def run(label, selector, kind):
        stake = ret = n = hits = 0
        for d in data.values():
            for no, p in d["horses"].items():
                if not selector(p):
                    continue
                n += 1
                stake += unit
                if kind == "win":
                    if to_i(p.get("finish_pos")) == 1 and to_f(p.get("win_odds")):
                        ret += to_f(p["win_odds"]) * unit
                        hits += 1
                else:  # place（上限推定）
                    if p.get("in_place") == "1" and to_f(p.get("place_odds_max")):
                        ret += to_f(p["place_odds_max"]) * unit
                        hits += 1
        return label, n, hits, stake, ret

    rows = []
    for m in MARK_ORDER:
        rows.append(run(f"{m} 単勝", lambda p, m=m: p.get("mark") == m, "win"))
        rows.append(run(f"{m} 複勝*", lambda p, m=m: p.get("mark") == m, "place"))
    rows.append(run("1人気 単勝", lambda p: to_i(p.get("popularity")) == 1, "win"))
    rows.append(run("1人気 複勝*", lambda p: to_i(p.get("popularity")) == 1, "place"))
    rows.append(run("1-3人気 複勝*",
                    lambda p: (to_i(p.get("popularity")) or 99) <= 3, "place"))
    rows.append(run("R≧4.0 単勝",
                    lambda p: (to_f(p.get("r_value")) or 0) >= 4.0, "win"))
    rows.append(run("R≧4.0 複勝*",
                    lambda p: (to_f(p.get("r_value")) or 0) >= 4.0, "place"))
    return rows


# ---------- D/E: 券種リプレイ ----------

def marks_of(d):
    by = defaultdict(list)
    for no, p in d["horses"].items():
        by[p.get("mark", "-")].append(no)
    return by


def sanrentan_formation(head, second, third):
    """フォーメーションの買い目集合（重複除外・列挙）"""
    combos = set()
    for a in head:
        for b in second:
            for c in third:
                if a != b and b != c and a != c:
                    combos.add((a, b, c))
    return combos


def ticket_replay(data, unit):
    """三連単 頭◎ vs 頭◎○（R12）、三連複 軸流し vs BOX をリプレイ"""
    results = defaultdict(lambda: {"cost": 0, "ret": 0, "races": 0, "hits": 0,
                                   "detail": []})
    for rid, d in data.items():
        by = marks_of(d)
        hon, tai = by.get("◎", []), by.get("○", [])
        ana, ren, myo = by.get("▲", []), by.get("△", []), by.get("☆", [])
        if not hon or not tai:
            continue
        second = sorted(set(hon + tai + ana + ren))
        third = sorted(set(second + myo))
        top3 = d["top3"]
        pay_tan = to_f(d["race"].get("payout_sanrentan"))
        pay_puku = to_f(d["race"].get("payout_sanrenpuku"))

        variants = [
            ("三連単F 頭◎のみ", sanrentan_formation(hon, second, third), pay_tan,
             lambda combos: top3 in combos),
            ("三連単F 頭◎○両置き(R12)",
             sanrentan_formation(sorted(set(hon + tai)), second, third), pay_tan,
             lambda combos: top3 in combos),
        ]
        # 三連複
        box5 = sorted(set(hon + tai + ana + ren + myo))
        puku_box = set(frozenset(c) for c in combinations(box5, 3))
        nagashi = set(frozenset((hon[0], b, c))
                      for b, c in combinations(sorted(set(tai + ana + ren + myo)), 2))
        winset = frozenset(top3)
        variants.append(("三連複 印BOX", puku_box, pay_puku,
                         lambda combos: winset in combos))
        variants.append(("三連複 ◎軸1頭流し", nagashi, pay_puku,
                         lambda combos: winset in combos))

        for label, combos, payout, hit_fn in variants:
            r = results[label]
            pts = len(combos)
            cost = pts * unit
            r["races"] += 1
            r["cost"] += cost
            hit = hit_fn(combos) and payout is not None
            if hit:
                r["ret"] += payout * unit / 100
                r["hits"] += 1
            r["detail"].append(f"{rid}: {pts}点 {'的中' if hit else '不的中'}")
    return results


def pct(ret, stake):
    return f"{ret / stake * 100:.0f}%" if stake else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", type=int, default=100, help="1点あたり単価（円）")
    ap.add_argument("--detail", action="store_true", help="レース別内訳を表示")
    args = ap.parse_args()

    data, skipped = build_dataset()
    n = len(data)
    print("=" * 64)
    print(f"■ バックテスト（対象 {n} レース）")
    if n < 10:
        print("※ n<10：全数値は参考値。閾値・ルール改廃の根拠にしない（R19）")
    for rid, why in skipped:
        print(f"  [skip] {rid}: {why}")
    print("=" * 64)

    print("\n--- ベタ買い戦略（* = place_odds_max による上限推定） ---")
    print(f"{'戦略':<16}{'n':>4}{'的中':>5}{'投資':>8}{'回収':>9}{'ROI':>7}")
    for label, cnt, hits, stake, ret in flat_bets(data, args.unit):
        if cnt == 0:
            continue
        print(f"{label:<16}{cnt:>4}{hits:>5}{stake:>8}{ret:>9.0f}{pct(ret, stake):>7}")

    print("\n--- 券種リプレイ（買い目は列挙で点数厳密・R16準拠） ---")
    replay = ticket_replay(data, args.unit)
    for label, r in replay.items():
        print(f"{label}: {r['races']}R {r['hits']}的中 "
              f"投資{r['cost']}円 回収{r['ret']:.0f}円 ROI {pct(r['ret'], r['cost'])}")
        if args.detail:
            for line in r["detail"]:
                print(f"    {line}")

    print("\n読み方：")
    print(" ・複勝*は上限推定＝楽観値。実ROIはこれを上回らない")
    print(" ・頭◎ vs 頭◎○ の差が R12（暫定）の直接検証。n=10で存廃判定")
    print(" ・馬券は自己責任。集計は的中を保証しない")


if __name__ == "__main__":
    main()
