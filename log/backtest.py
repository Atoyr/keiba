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
  F. 三連複 軸候補の総当たり比較（検証待ち仮説12の判定手順）:
     ◎ / 妙味最上位 / 最終点2位 / 最終点3位 / 当日1番人気 の5通りを
     同一レース・同一相手リストでリプレイし、荒れ度帯域別に分けて集計する。
     同一レース上の対戦比較（paired）は独立標本より分散が小さく、少ないレース数で
     優劣が出る。週1〜2本しか重賞がない制約への直接の回答（ロードマップ v1.2 群3）

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
import re
import sys
from collections import defaultdict
from itertools import combinations, permutations

# Windows既定コンソール(cp932)では罫線・✕等でUnicodeEncodeErrorを起こし途中で落ちるため強制UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


# ---------- F: 三連複 軸候補の総当たり比較（検証待ち仮説12） ----------

_BAND = re.compile(r"band\s*[=＝:：]\s*([^/／、,\s]+)")
_ARE = re.compile(r"荒れ度\s*[=＝:：]?\s*([0-9]+)")  # 宣言タグ規約より前は「荒れ度5」と区切りなしで書かれている


def band_of(race):
    """宣言タグ band= を優先。無ければ 荒れ度= の数値から帯域境界(0-2/3-5/6+)で導出する。
    band= は宣言タグ規約の導入後に書かれた行にしか無いため、遡及分はこの経路で拾う。"""
    notes = race.get("notes") or ""
    m = _BAND.search(notes)
    if m:
        return m.group(1)
    m = _ARE.search(notes)
    if m:
        v = int(m.group(1))
        return "低" if v <= 2 else ("中" if v <= 5 else "高")
    return "帯域不明"


def axis_candidates(d):
    """レースごとの軸候補5通り。値は馬番 or None（該当馬なし）。"""
    horses = d["horses"]
    by = marks_of(d)

    def top_by(key, rank):
        vals = [(to_f(p.get(key)), no) for no, p in horses.items() if to_f(p.get(key)) is not None]
        vals.sort(key=lambda t: -t[0])
        return vals[rank - 1][1] if len(vals) >= rank else None

    pop1 = [no for no, p in horses.items() if to_i(p.get("popularity")) == 1]
    return [
        ("◎", by.get("◎", [None])[0] if by.get("◎") else None),
        ("妙味最上位", top_by("myomi_score", 1)),
        ("最終点2位", top_by("final_score", 2)),
        ("最終点3位", top_by("final_score", 3)),
        ("当日1番人気", pop1[0] if pop1 else None),
    ]


def axis_comparison(data, unit):
    """軸1頭流し（相手＝印プールから軸を除いた集合）を軸候補別・帯域別にリプレイ。

    5候補すべてが解決できるレースだけを対象にする（対戦比較＝paired の成立条件。
    候補ごとに母集団が違うとROIの差が「別のレースを見ている差」に化ける）。
    戻り値: (stats, 採用レース数, 除外レース一覧)
    """
    stats = defaultdict(lambda: defaultdict(lambda: {"cost": 0, "ret": 0, "races": 0, "hits": 0}))
    used, dropped = 0, []
    for rid, d in data.items():
        by = marks_of(d)
        pool = sorted({no for m in MARK_ORDER for no in by.get(m, [])})
        if len(pool) < 3:
            dropped.append((rid, "印プールが3頭未満"))
            continue
        missing = [lab for lab, no in axis_candidates(d) if no is None]
        if missing:
            dropped.append((rid, f"軸候補を解決できない {missing}"))
            continue
        used += 1
        pay = to_f(d["race"].get("payout_sanrenpuku"))
        winset = frozenset(d["top3"])
        band = band_of(d["race"])
        for label, axis in axis_candidates(d):
            if axis is None:
                continue
            partners = [no for no in pool if no != axis]
            if len(partners) < 2:
                continue
            combos = {frozenset((axis, b, c)) for b, c in combinations(partners, 2)}
            cost = len(combos) * unit
            for key in (band, "全体"):
                s = stats[label][key]
                s["races"] += 1
                s["cost"] += cost
                if winset in combos and pay is not None:
                    s["ret"] += pay * unit / 100
                    s["hits"] += 1
    return stats, used, dropped


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

    print("\n--- 三連複 軸候補の総当たり（仮説12・相手＝印プールから軸を除いた集合） ---")
    axis, used, dropped = axis_comparison(data, args.unit)
    print(f"対象 {used}レース（5候補すべてが解決できたレースのみ＝paired比較）")
    for rid, why in dropped:
        print(f"  [skip] {rid}: {why}")
    bands = sorted({b for v in axis.values() for b in v if b != "全体"})
    header = f"{'軸候補':<14}{'全体':>22}" + "".join(f"{'band=' + b:>22}" for b in bands)
    print(header)
    for label in ("◎", "妙味最上位", "最終点2位", "最終点3位", "当日1番人気"):
        if label not in axis:
            continue
        cells = []
        for key in ["全体"] + bands:
            s = axis[label].get(key)
            cells.append(f"{s['hits']}/{s['races']}R {pct(s['ret'], s['cost']):>5}" if s else "-")
        print(f"{label:<14}" + "".join(f"{c:>22}" for c in cells))
    print(" ※同一レース・同一相手リストでの対戦比較。点数（＝費用）は軸により異なるためROIで比較する")

    print("\n読み方：")
    print(" ・複勝*は上限推定＝楽観値。実ROIはこれを上回らない")
    print(" ・頭◎ vs 頭◎○ の差が R12（暫定）の直接検証。n=10で存廃判定")
    print(" ・軸候補の総当たりは仮説12の判定入力。帯域別に分けて読む（中帯域と荒帯域で逆方向の事例あり）")
    print(" ・馬券は自己責任。集計は的中を保証しない")


if __name__ == "__main__":
    main()
