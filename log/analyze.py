#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""予測ログ集計スクリプト。log/ ディレクトリで python3 analyze.py を実行。標準ライブラリのみ使用。"""
import csv
import os
import re
import sys
from collections import defaultdict

# Windows既定コンソール(cp932)では罫線・✕等でUnicodeEncodeErrorを起こし途中で落ちるため強制UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


# ---------- 印 v4／シミュレーション v3 の較正入力（ロードマップ v2 §5-6・2026-09-12）----------

def _spearman(a, b):
    n = len(a)
    if n < 3:
        return None

    def rk(v):
        s = sorted(range(n), key=lambda i: v[i])
        r = [0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[s[j + 1]] == v[s[i]]:
                j += 1
            for k in range(i, j + 1):
                r[s[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    ra, rb = rk(a), rk(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** .5
    return num / den if den else None


def _parse_add(s):
    d = {}
    for t in (s or "").split(";"):
        m = re.match(r"^([^\d+\-]+)([+\-]\d+(?:\.\d+)?)$", t.strip())
        if m:
            d[m.group(1)] = d.get(m.group(1), 0) + float(m.group(2))
    return d


def _parse_coef(s):
    d = {}
    for t in (s or "").split(";"):
        m = re.match(r"^([^\d]+)(\d+(?:\.\d+)?)$", t.strip())
        if m:
            d[m.group(1)] = float(m.group(2))
    return d


def section_ablation(preds, races):
    print()
    print("=" * 60)
    print("■ 序列アブレーション（群2・分解記録のある行・ロードマップ v2 §5-6）")
    print("=" * 60)
    by = defaultdict(list)
    for p in preds:
        if to_f(p.get("finish_pos")) is None or to_f(p.get("base_score")) is None or to_f(p.get("final_score")) is None:
            continue
        if not p.get("additive_breakdown") or not p.get("coef_breakdown"):
            continue
        by[p["race_id"]].append(p)
    if not by:
        print("分解記録のある行なし")
        return
    ADD4 = ["騎手", "上がり", "斤量", "馬体重"]
    variants = {
        "base": lambda p, c, a: to_f(p["base_score"]),
        "final（記録）": lambda p, c, a: to_f(p["final_score"]),
        "final−R−枠": lambda p, c, a: to_f(p["final_score"]) - a.get("R", 0) - a.get("枠", 0),
        "base×適性": lambda p, c, a: to_f(p["base_score"]) * c.get("適性", 1.0),
        "v4近似（base＋騎手上がり斤量馬体重）": lambda p, c, a: to_f(p["base_score"]) + sum(a.get(k, 0) for k in ADD4),
        "sim_score": lambda p, c, a: to_f(p.get("sim_score")),
        "rank_score（sim＋4加算）": lambda p, c, a: (to_f(p.get("sim_score")) + sum(a.get(k, 0) for k in ADD4)) if to_f(p.get("sim_score")) is not None else None,
        "人気": lambda p, c, a: -to_f(p.get("popularity")) if to_f(p.get("popularity")) is not None else None,
    }
    res, pop = defaultdict(list), defaultdict(list)
    for rid, rs in by.items():
        if len(rs) < 5:
            continue
        fin = [-to_f(p["finish_pos"]) for p in rs]
        popv = [-to_f(p["popularity"]) for p in rs] if all(to_f(p.get("popularity")) is not None for p in rs) else None
        for k, fn in variants.items():
            vals = [fn(p, _parse_coef(p["coef_breakdown"]), _parse_add(p["additive_breakdown"])) for p in rs]
            if any(v is None for v in vals):
                continue
            s = _spearman(vals, fin)
            if s is not None:
                res[k].append(s)
            if popv and k != "人気":
                s2 = _spearman(vals, popv)
                if s2 is not None:
                    pop[k].append(s2)
    print(f"{'序列':34s} {'着順ρ':>7s} {'人気ρ':>7s}  R数")
    for k in variants:
        if res.get(k):
            pr = f"{sum(pop[k]) / len(pop[k]):7.3f}" if pop.get(k) else "      -"
            print(f"{k:34s} {sum(res[k]) / len(res[k]):7.3f} {pr}  {len(res[k])}")
    print("  ※ 着順ρ は大きいほど良い。人気ρ は「人気と同じ並びか」の指標で、v4 は着順ρを保ったまま人気ρを下げるのが目標（§4-3 判断6）")
    print()
    print("要素別（n≧5）：")
    elem = defaultdict(lambda: [0, 0])
    for rs in by.values():
        for p in rs:
            pl = to_f(p["finish_pos"]) <= 3
            for k, v in _parse_coef(p["coef_breakdown"]).items():
                if k in ("適性", "バイアス", "枠"):
                    d = "＞1" if v > 1.0 else ("＜1" if v < 1.0 else "＝1")
                    elem[f"係数:{k}{d}"][0] += 1
                    elem[f"係数:{k}{d}"][1] += pl
            for k, v in _parse_add(p["additive_breakdown"]).items():
                d = "＋" if v > 0 else ("−" if v < 0 else "0")
                elem[f"加算:{k}{d}"][0] += 1
                elem[f"加算:{k}{d}"][1] += pl
    for k, (n, h) in sorted(elem.items(), key=lambda kv: -kv[1][0]):
        if n >= 5:
            print(f"  {k:18s} n={n:3d} 複勝{h / n * 100:3.0f}%")
    ov = [0, 0]
    grp = {"両方": [0, 0, 0.0], "印のみ（人気5番以下）": [0, 0, 0.0], "人気のみ（無印）": [0, 0, 0.0]}
    for rid, rs in by.items():
        if any(to_f(p.get("popularity")) is None for p in rs):
            continue
        marked = {p["horse_no"] for p in rs if (p.get("mark") or "").strip() in ("◎", "○", "▲", "△")}
        top4 = {p["horse_no"] for p in sorted(rs, key=lambda p: to_f(p["popularity"]))[:4]}
        ov[0] += len(marked & top4)
        ov[1] += len(marked)
        for p in rs:
            h = p["horse_no"]
            pl = to_f(p["finish_pos"]) <= 3
            po = to_f(p.get("place_odds_max")) or 0
            key = "両方" if (h in marked and h in top4) else ("印のみ（人気5番以下）" if h in marked else ("人気のみ（無印）" if h in top4 else None))
            if key:
                g = grp[key]
                g[0] += 1
                g[1] += pl
                g[2] += po if pl else 0
    print()
    print(f"印（◎○▲△）と人気上位4頭の重なり {ov[0]}/{ov[1]}")
    for k, (n, h, r) in grp.items():
        if n:
            print(f"  {k:22s} n={n:3d} 複勝{h / n * 100:3.0f}% 複勝上限ROI {r / n * 100:4.0f}%")
    print("  ※ 人気圏外に付けた印の複勝率がリターンの源泉（ロードマップ v2 §7-7）。n<10 は参考値")


def section_sim_calibration(preds, races):
    print()
    print("=" * 60)
    print("■ シミュレーション較正（ロードマップ v2 §5-2 較正ループ）")
    print("=" * 60)
    race_by = {r["race_id"]: r for r in races}
    by = defaultdict(list)
    for p in preds:
        if to_f(p.get("sim_pos")) is not None and to_f(p.get("corner4_pos")) is not None:
            by[p["race_id"]].append(p)
    rh, mae, cap = [], [], [0, 0]
    for rid, rs in by.items():
        if len(rs) < 5:
            continue
        pos = [to_f(p["sim_pos"]) for p in rs]
        act = [to_f(p["corner4_pos"]) for p in rs]
        s = _spearman(pos, act)
        if s is not None:
            rh.append(s)
        mae.append(sum(abs(a - b) for a, b in zip(pos, act)) / len(rs))
        p6 = {i for i, v in enumerate(pos) if v <= 6.5}
        a6 = {i for i, v in enumerate(act) if v <= 6}
        cap[0] += len(p6 & a6)
        cap[1] += len(a6)
    if rh:
        print(f"(1) 位置取り：想定4角 vs 実4角 ρ={sum(rh) / len(rh):.3f} MAE={sum(mae) / len(mae):.2f}番手 実6番手以内の捕捉 {cap[0]}/{cap[1]}（{len(rh)}R）")
    else:
        print("(1) 位置取り：sim_pos と corner4_pos の両方がある行なし（基準線は v2 §7-3：ρ0.544・MAE2.45・捕捉48/56）")
    main = {}
    for r in races:
        m = re.search(r"本線=(前残り|中立|前崩れ)", r.get("notes") or "")
        if m and (r.get("pace_actual") or r.get("bias_actual")):
            main[r["race_id"]] = m.group(1)
    if main:
        pace_ok = bias_ok = n = 0
        for rid, sc in main.items():
            r = race_by[rid]
            pa = r.get("pace_actual") or ""
            ba = r.get("bias_actual") or ""
            n += 1
            pace_ok += {"前残り": "スロー" in pa, "中立": ("平均" in pa or "イーブン" in pa), "前崩れ": ("ハイ" in pa or "前傾" in pa)}[sc]
            bias_ok += {"前残り": any(k in ba for k in ("内前", "前有利", "前〜")), "中立": "フラット" in ba, "前崩れ": ("外差し" in ba or "差し" in ba)}[sc]
        print(f"(2) 本線シナリオ：ペース軸の一致 {pace_ok}/{n}／決着軸の一致 {bias_ok}/{n}（races.notes 本線= のあるレース）")
    else:
        print("(2) 本線シナリオ：races.notes に 本線= があり結果入力済みのレースなし")
    col = {"前残り": "s_front", "中立": "s_mid", "前崩れ": "s_back"}
    tally = defaultdict(lambda: [0, 0])
    for p in preds:
        rid = p["race_id"]
        if rid not in main or to_f(p.get("finish_pos")) is None:
            continue
        v = (p.get(col[main[rid]]) or "").strip()
        if v in ("A", "B", "C", "D"):
            tally[v][0] += 1
            tally[v][1] += to_f(p["finish_pos"]) <= 3
    if tally:
        print("(3) 本線シナリオでの S列別 複勝率： " + " ".join(f"{k}={h}/{n}({h / n * 100:.0f}%)" for k, (n, h) in sorted(tally.items())))
    else:
        print("(3) S列別複勝率：判定できる行なし")
    ov = [0, 0]
    for p in preds:
        if "上書き=" in (p.get("notes") or "") and to_f(p.get("finish_pos")) is not None:
            ov[0] += 1
            ov[1] += to_f(p["finish_pos"]) <= 3
    print(f"(4) LLM 上書きのある馬：n={ov[0]} 複勝{ov[1]}" if ov[0] else "(4) 上書き= タグのある行なし")
    t2 = defaultdict(lambda: [0, 0])
    for p in preds:
        ad = to_f(p.get("agari_diff"))
        if ad is None or to_f(p.get("finish_pos")) is None:
            continue
        k = "裏付けあり(≦0.3秒)" if ad <= 0.3 else "裏付けなし"
        t2[k][0] += 1
        t2[k][1] += to_f(p["finish_pos"]) <= 3
    if t2:
        print("(5) 末脚指数： " + " ".join(f"{k} {h}/{n}({h / n * 100:.0f}%)" for k, (n, h) in t2.items()))
    else:
        print("(5) 末脚指数：agari_diff のある行なし")


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
    print("■ モデル序列の診断（第1回較正レビュー 構造課題A・B）")
    print("=" * 60)
    # A-3。レビューで手計算した3指標を毎回自動で出す。
    #  (1) 1着馬の最終点順位  … 課題B「最終点の序列が勝ち馬を捉えていない」の直接指標
    #  (2) 印プール捕捉率      … 振り返りV2 L3の中心指標。選定と買い方の分離
    #  (3) 印の単調性          … 課題A「▲だけが序列から外れている」の検出
    by_race_all = defaultdict(list)
    for p in preds:
        by_race_all[p["race_id"]].append(p)
    race_name = {r["race_id"]: r.get("race_name", r["race_id"]) for r in races}

    ranks, lines = [], []
    for rid, rows in by_race_all.items():
        scored = [p for p in rows if to_f(p.get("final_score")) is not None]
        winner = [p for p in rows if p.get("finish_pos") == "1"]
        if not scored or not winner or to_f(winner[0].get("final_score")) is None:
            continue
        order_desc = sorted(scored, key=lambda p: -to_f(p["final_score"]))
        rank = next(i for i, p in enumerate(order_desc, 1)
                    if p.get("horse_no") == winner[0].get("horse_no"))
        ranks.append(rank)
        lines.append(f"  {race_name.get(rid, rid)}: 1着馬の最終点順位 {rank}/{len(scored)}位"
                     f"（印 {winner[0].get('mark') or '-'}）")
    print("(1) 1着馬の最終点順位")
    if ranks:
        for line in lines:
            print(line)
        for k in (1, 2, 3):
            hit = sum(1 for r in ranks if r <= k)
            print(f"  → 上位{k}頭に1着馬が入った: {hit}/{len(ranks)} ({hit / len(ranks) * 100:.0f}%)"
                  + ("  ※目標: 上位3頭で50%超" if k == 3 else ""))
        print(f"  → 順位の中央値 {sorted(ranks)[len(ranks) // 2]}位 / 平均 {sum(ranks) / len(ranks):.1f}位")
    else:
        print("  final_score と着順の両方が揃ったレースなし")

    print("(2) 印プール捕捉率（3着内3頭のうち印付き＝◎○▲△☆ の頭数・V2のL3指標）")
    POOL = {"◎", "○", "▲", "△", "☆"}
    caught_total = n_race = 0
    dist = defaultdict(int)
    for rid, rows in by_race_all.items():
        top3 = [p for p in rows if (to_f(p.get("finish_pos")) or 99) <= 3]
        if len(top3) != 3:
            continue
        c = sum(1 for p in top3 if (p.get("mark") or "").strip() in POOL)
        caught_total += c
        n_race += 1
        dist[c] += 1
    if n_race:
        print(f"  {caught_total}/{n_race * 3} 頭 ({caught_total / (n_race * 3) * 100:.0f}%)"
              f" ｜ 内訳 " + " ".join(f"{k}/3:{dist[k]}R" for k in sorted(dist, reverse=True)))
    else:
        print("  結果入力済みレースなし")

    print("(3) 印の単調性（複勝率が ◎≧○≧▲≧△≧無印 になっているか）")
    seq = ["◎", "○", "▲", "△", "-"]
    rate = {}
    for m in seq:
        rows = by_mark.get(m, [])
        if rows:
            rate[m] = sum(1 for r in rows if r.get("in_place") == "1") / len(rows)
    inversions = [(a, b) for a, b in zip(seq, seq[1:])
                  if a in rate and b in rate and rate[a] < rate[b]]
    print("  " + " ≧ ".join(f"{m}{rate[m] * 100:.0f}%" for m in seq if m in rate))
    if inversions:
        print("  ★逆転 " + " / ".join(f"{a}<{b}" for a, b in inversions)
              + " → 印の序列が機能していない（課題A）")
    else:
        print("  逆転なし（序列は単調）")

    print()
    print("=" * 60)
    print("■ 展開不利の好走・4角位置×上がり（T1・検証待ち仮説15）")
    print("=" * 60)
    # 『競馬予想_評価ルール.md』第169項「着順と上がりの乖離＝負けたが上がり最速は
    # 展開不利の好走、次走加点材料」を機械抽出する。従来この判断は主観メモ経由の
    # 一本経路しかなく、次走のベース点に体系的に反映されていなかった。
    # pos_gain（4角位置−着順）は observation ではなく導出値なのでCSVに持たず、ここで計算する。
    rows_t1 = [p for p in preds if to_f(p.get("finish_pos")) is not None
               and to_f(p.get("last_3f")) is not None]
    if not rows_t1:
        print("last_3f 未記入（2026-08-03導入・以後のレースから記録）。")
        print("→ 記入が進むと『上がり上位なのに着外＝次走加点候補』と仮説15のクロス集計がここに出る")
    else:
        print("(1) 展開不利の好走候補（レース内の上がり3F順位≦3 かつ 着順≧4）")
        by_r = defaultdict(list)
        for p in rows_t1:
            by_r[p["race_id"]].append(p)
        cand = 0
        for rid, rs in by_r.items():
            ordered = sorted(rs, key=lambda p: to_f(p["last_3f"]))
            for i, p in enumerate(ordered, 1):
                fin = int(to_f(p["finish_pos"]))
                if i <= 3 and fin >= 4:
                    c4 = to_f(p.get("corner4_pos"))
                    gain = f" 4角{int(c4)}番手→{fin}着(pos_gain{int(c4) - fin:+d})" if c4 else ""
                    print(f"  {race_name.get(rid, rid)} #{p.get('horse_no')} {p.get('horse_name')}"
                          f": 上がり{p['last_3f']}秒(レース{i}位) {fin}着{gain}")
                    cand += 1
        if not cand:
            print("  該当なし")

        print("(2) 4角6番手以内 × 上がり3位以内 の積（仮説15の判定入力）")
        cells = {(True, True): [0, 0], (True, False): [0, 0],
                 (False, True): [0, 0], (False, False): [0, 0]}
        for rid, rs in by_r.items():
            ordered = sorted(rs, key=lambda p: to_f(p["last_3f"]))
            rank = {id(p): i for i, p in enumerate(ordered, 1)}
            for p in rs:
                c4 = to_f(p.get("corner4_pos"))
                if c4 is None:
                    continue
                k = (c4 <= 6, rank[id(p)] <= 3)
                cells[k][0] += 1
                if p.get("in_place") == "1":
                    cells[k][1] += 1
        for (pos_ok, agari_ok), (n_, hit) in cells.items():
            if n_:
                lab = f"4角{'6番手以内' if pos_ok else '7番手以降'}×上がり{'3位以内' if agari_ok else '4位以下'}"
                print(f"  {lab}: 複勝 {hit}/{n_} ({hit / n_ * 100:.0f}%)")
        print("  ※仮説15は距離帯（短距離/マイル以上）で分けて判定する。本集計は全距離まとめ")

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
    rule_dir = {r["rule_id"]: (r.get("direction") or "").strip() for r in load("rules_master.csv")}
    for rid in sorted(by_rule):
        rows = by_rule[rid]
        fired = [r for r in rows if r.get("fired") == "1"]
        followed = sum(1 for r in fired if r.get("followed") == "1")
        outcomes = defaultdict(int)
        caps = defaultdict(int)
        for r in fired:
            outcomes[r.get("outcome", "?")] += 1
            if (r.get("capture") or "").strip():
                caps[r["capture"].strip()] += 1
        oc = " / ".join(f"{k}:{v}" for k, v in outcomes.items())
        cp = (" capture[" + " ".join(f"{k}:{v}" for k, v in caps.items()) + "]") if caps else ""
        d = rule_dir.get(rid, "")
        print(f"{rid}({d}) {rules.get(rid, '')[:26]}: 発火 {len(fired)} "
              f"遵守 {followed}/{len(fired)} [{oc}]{cp}")
    print("※ capture は結果に照らした予測方向の当否（的中/空振り/逆行）。損益寄与の outcome とは独立の第2軸。")
    print("  direction=手続き のルールは capture では判定せず、遵守率と validate.py の機械化可否で扱う（R19）。")

    # --- 保全ルールのコスト軸（2026-08-17新設） ---
    # 「紐に残せ・点を足せ」型の保全ルールは外れても capture=空振り（無害）にしか
    # 見えず、R19の降格条件（逆行≧的中）が構造的に発動しない。実コストは追加購入
    # 点数のROI希釈なので、bets.notes の 保全=R## タグ（ルールが強制した追加買い目を
    # 別行で記録）から投資/回収を直接測る。恒常的に回収<100%の保全ルールが削減候補。
    hozen = defaultdict(lambda: [0.0, 0.0, 0])
    for b in load("bets.csv"):
        m = re.search(r"保全=(R\d+)", b.get("notes") or "")
        if m:
            hozen[m.group(1)][0] += to_f(b.get("cost")) or 0
            hozen[m.group(1)][1] += to_f(b.get("return")) or 0
            hozen[m.group(1)][2] += 1
    print()
    print("■ 保全ルールのコスト（bets.notes 保全=R## タグ集計）")
    if hozen:
        for rid in sorted(hozen):
            c, ret, n = hozen[rid]
            roi = f"{ret / c * 100:.0f}%" if c else "-"
            print(f"{rid}: 保全買い目 {n}行 投資{c:g}円 回収{ret:g}円 ROI {roi}")
        print("※ 恒常的にROI<100%の保全ルールは較正レビューの削減候補（判定はn=10レビューのみ・R19）")
    else:
        print("タグ付き行なし（2026-08-17以降、保全ルールが強制した追加買い目は別行＋保全=R##で記録する）")

    section_ablation(preds, races)
    section_sim_calibration(preds, races)

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
        direction = (rule.get("direction") or "").strip()
        cnt = 0
        oc, cap = {}, {}
        for f_ in fires:
            if f_.get("rule_id") != rid_ or f_.get("fired") != "1":
                continue
            rname = name_by_id.get(f_.get("race_id"), "")
            if rname and rname in origin:  # 生成元レースは除外（R17）
                continue
            cnt += 1
            o = f_.get("outcome") or "?"
            oc[o] = oc.get(o, 0) + 1
            c_ = (f_.get("capture") or "").strip()
            if c_:
                cap[c_] = cap.get(c_, 0) + 1
        ocs = " ".join(f"{k}:{v}" for k, v in oc.items()) or "-"
        caps = (" capture[" + " ".join(f"{k}:{v}" for k, v in cap.items()) + "]") if cap else ""
        # 判定は capture 軸で行う（R19・2026-08-03改訂）。旧 outcome 軸は中立に68%が滞留し
        # 「逆効果≧効いた」が構造的に発動しえなかった（P1-3）
        judge = ""
        if direction == "手続き":
            judge = " ※手続き系＝capture対象外（遵守率で判断）"
        elif cnt < 5:
            judge = " （判定保留: 発火5件未満）"
        elif not cap:
            judge = " ★発火5件到達だが capture 未記入で判定不能"
        else:
            neg, pos = cap.get("逆行", 0), cap.get("的中", 0)
            miss = cap.get("空振り", 0)
            judged = sum(cap.values())
            # 空振り優勢ガード（2026-08-03・R19に追加）。空振りは機能の証拠にも
            # 逆機能の証拠にもならないため、件数だけで昇格させると未検証のルールが現行化する。
            # 起点：R20（発火6件・空振り5=83%）が条文上は昇格条件を満たしてしまった件
            if judged and miss / judged > 0.8:
                judge = f" （判定保留: 空振り{miss}/{judged}＝{miss / judged * 100:.0f}%で8割超）"
            else:
                judge = " ★判定可能" + ("（降格候補: 逆行≧的中）" if neg >= pos and neg > 0 else "")
        prov_lines.append(f"  {rid_}({direction})発火 {cnt}件 [{ocs}]{caps}{judge}")

    # R13はG1のみの別トラック
    g1_done = sum(1 for r in done if "G1" in (r.get("grade") or "").upper().replace("GI", "G1"))

    # ☆的中
    star = [p for p in preds if (p.get("mark") or "").strip() == "☆" and p.get("finish_pos")]
    star_hit = sum(1 for p in star if p.get("in_place") == "1")

    # 半減版帯域ズレ（races.csv notes に「帯域ズレ」を含む行を宣言としてカウント）
    zure = sum(1 for r in done if "帯域ズレ" in (r.get("notes") or ""))

    print(f"全体 n={n_total}/10（うちpaper {n_paper}） ｜ R13(G1) {g1_done}/10 ｜ "
          f"☆的中 {star_hit}/{len(star)} ｜ 半減版帯域ズレ {zure}回")

    # サンプル単位の内訳（ロードマップ v1.2）。
    # 「n=20/n=30」を一本のレース数で管理すると、頭単位・過去統計で今日決められる項目まで
    # レース待ちになる。単位ごとに残量を出して待つべき項目だけを待つ。
    horse_rows = [p for p in preds
                  if to_f(p.get("final_score")) is not None and p.get("in_place") in ("0", "1")]
    fired_dir = [f_ for f_ in fires if f_.get("fired") == "1"
                 and rule_dir.get(f_.get("rule_id"), "") != "手続き"]
    fired_cap = [f_ for f_ in fired_dir if (f_.get("capture") or "").strip()]
    print("サンプル単位の内訳:")
    print(f"  群1 過去統計（レース数不要）  : 仮説9/13/14/15/17 は集計実行のみで判定可")
    print(f"  群2 頭単位                    : final_score＋着順が揃った頭行 {len(horse_rows)}行"
          f"（{len({p['race_id'] for p in horse_rows})}レース分）→ 較正曲線の初回フィット可")
    print(f"  群3 レース単位                : n={n_total}。券種構成・軸選定・pace_flag はここのみ")
    print(f"  capture記入率                 : {len(fired_cap)}/{len(fired_dir)}"
          f"（direction≠手続き の発火行。R19判定の入力）")
    print("暫定ルール発火（生成元レース除外・R17）:")
    for line in prov_lines:
        print(line)
    if n_total >= 10:
        print("-" * 60)
        print("★★ n=10 到達：較正レビューを発動する（R19手順） ★★")
        print("  1. validate.py → analyze.py → backtest.py を実行")
        print("  2. 発火5件以上かつ 逆行≧的中（capture軸）の暫定ルールを降格/廃止判定")
        print("  3. 検証待ち仮説を一括判定（log/README.md）。群1の仮説はレース数を待たずに集計実行")
    else:
        print(f"→ レビューまで残り {10 - n_total} レース（ペーパー予想で加速可）")


if __name__ == "__main__":
    main()
