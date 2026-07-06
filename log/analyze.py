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
    print("■ ベースライン比較（無脳戦略のROI・複勝上限オッズ使用の概算）")
    print("=" * 60)
    by_race = defaultdict(list)
    for p in preds:
        if p.get("finish_pos") and to_f(p.get("popularity")) is not None:
            by_race[p["race_id"]].append(p)

    def baseline_roi(label, pop_set):
        stake = ret = n = hit = 0
        for rows in by_race.values():
            for r in rows:
                pop = to_f(r.get("popularity"))
                odds = to_f(r.get("place_odds_max"))
                if pop is None or pop not in pop_set or odds is None:
                    continue
                stake += 100
                n += 1
                if r.get("in_place") == "1":
                    ret += odds * 100
                    hit += 1
        if stake:
            print(f"{label}: n={n} 的中 {hit}/{n} 回収率 {ret / stake * 100:.0f}%")
        else:
            print(f"{label}: データ不足")

    baseline_roi("1番人気 複勝ベタ買い", {1.0})
    baseline_roi("1〜3番人気 複勝均等買い", {1.0, 2.0, 3.0})
    print("※システムの印別回収率（上記）がこのベースラインを上回っているかで付加価値を判定する")

    print()
    print("=" * 60)
    print("■ r_adj分離（final_score − r_adj ＝ オッズ非依存のモデル素点）")
    print("=" * 60)
    by_mark_adj = defaultdict(list)
    for p in preds:
        r_adj = to_f(p.get("r_adj"))
        final = to_f(p.get("final_score"))
        if r_adj is not None and final is not None:
            by_mark_adj[p.get("mark") or "-"].append((final, r_adj))
    if by_mark_adj:
        for mark in sorted(by_mark_adj, key=lambda m: order.index(m) if m in order else 99):
            rows = by_mark_adj[mark]
            n = len(rows)
            avg_final = sum(f for f, _ in rows) / n
            avg_adj = sum(a for _, a in rows) / n
            print(f"{mark}: n={n} final_score平均 {avg_final:.1f} r_adj平均 {avg_adj:+.1f} モデル素点平均 {avg_final - avg_adj:.1f}")
    else:
        print("r_adj記入済みデータなし（記入が進み次第、モデル/市場の寄与分離が可能になる）")

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

    print()
    print("=" * 60)
    print("■ 較正カウンタ（n=10でレビュー発動・R19）")
    print("=" * 60)
    rules_full = load("rules_master.csv")
    # 結果確定済みレース（paper含む）= 較正サンプル
    done = [r for r in races if r.get("result_1st")]
    n_total = len(done)
    n_paper = sum(1 for r in done if (r.get("notes") or "").strip().lower().startswith("paper"))
    done_ids = {r["race_id"] for r in done}
    name_by_id = {r["race_id"]: r.get("race_name", "") for r in races}

    # 暫定ルールの発火数（R17: origin_raceを検証サンプルに数えない）
    prov = [r for r in rules_full if (r.get("status") or "").startswith("暫定")]
    prov_lines = []
    for rule in prov:
        rid_ = rule["rule_id"]
        origin = rule.get("origin_race") or ""
        cnt = 0
        oc = {}
        for f_ in fires:
            if f_.get("rule_id") != rid_ or f_.get("fired") != "1":
                continue
            rname = name_by_id.get(f_.get("race_id"), "")
            if rname and rname in origin:  # 生成元レースは除外（R17）
                continue
            cnt += 1
            o = f_.get("outcome") or "?"
            oc[o] = oc.get(o, 0) + 1
        ocs = " ".join(f"{k}:{v}" for k, v in oc.items()) or "-"
        judge = ""
        if cnt >= 5:
            neg = oc.get("逆効果", 0) + oc.get("違反したが結果OK", 0)
            pos = oc.get("効いた", 0)
            judge = " ★判定可能" + ("（降格候補: 逆効果≧効いた）" if neg >= pos and neg > 0 else "")
        prov_lines.append(f"  {rid_}発火 {cnt}件 [{ocs}]{judge}")

    # R13はG1のみの別トラック
    g1_done = sum(1 for r in done if "G1" in (r.get("grade") or "").upper().replace("GI", "G1"))

    # ☆的中
    star = [p for p in preds if (p.get("mark") or "").strip() == "☆" and p.get("finish_pos")]
    star_hit = sum(1 for p in star if p.get("in_place") == "1")

    # 半減版帯域ズレ（races.csv notes に「帯域ズレ」を含む行を宣言としてカウント）
    zure = sum(1 for r in done if "帯域ズレ" in (r.get("notes") or ""))

    print(f"全体 n={n_total}/10（うちpaper {n_paper}） ｜ R13(G1) {g1_done}/10 ｜ "
          f"☆的中 {star_hit}/{len(star)} ｜ 半減版帯域ズレ {zure}回")
    print("暫定ルール発火（生成元レース除外・R17）:")
    for line in prov_lines:
        print(line)
    if n_total >= 10:
        print("-" * 60)
        print("★★ n=10 到達：較正レビューを発動する（R19手順） ★★")
        print("  1. validate.py → analyze.py → backtest.py を実行")
        print("  2. 発火5件以上かつ 逆効果≧効いた の暫定ルールを降格/廃止判定")
        print("  3. 検証待ち仮説1〜8・R値/妙味/荒れ度の閾値を一括判定（log/README.md）")
    else:
        print(f"→ レビューまで残り {10 - n_total} レース（ペーパー予想で加速可）")


if __name__ == "__main__":
    main()
