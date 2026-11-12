#!/usr/bin/env python3
"""週次回収率とハンデ戦／非ハンデ戦の区分集計（群3＝レース単位・システム v3 の KPI 計測）

(1) 週（月曜〜日曜・ISO週）ごとの実購入の投資・回収・回収率と、目標ライン（WEEK_TARGET）に届いた週の割合
(2) 直近 ROLLING_WEEKS 週の移動回収率（購入のあった週だけを数える）
(3) ハンデ戦／非ハンデ戦 × 券種 の実購入収支（最高払戻1本を除いた回収率を併記）
(4) ハンデ戦／非ハンデ戦 の決着の荒れ方（三連単10万円超・1番人気の3着内・印別の3着内）
(5) 概算的中率（bets.notes の `概算的中率X%`）から見た期待的中数と実的中数（外れが分散の範囲かを読む）
(6) 三連複の構造リプレイを週次 KPI で比較（事前登録した候補だけ・band_replay と同じ組み方）
(7) 週予算の消化（WEEK_BUDGET_FROM 以降の週）

入力は log/races.csv・log/bets.csv・log/predictions.csv だけ。paper（races.notes が paper で始まる）は bets 行が無いので自動的に外れる。
ハンデ戦の判定は races.notes の `斤量=ハンデ` タグを優先し、無ければ band_replay.HANDICAP_RACES を使う（書式は log/README.md）。
回収率は少数の高配当に強く依存するため、必ず「最高払戻レースを除いた値」と並べて読む（1レースの複数行的中は1つの結果なのでレース単位で除く）。
"""
import csv
import datetime
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from band_replay import HANDICAP_RACES, build as replay_build  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

WEEK_TARGET = 1.20      # 週次回収率の目標ライン（ロードマップ v2 §14）
ROLLING_WEEKS = 4       # 移動回収率の窓（購入のあった週の数）
WEEK_BUDGET = 9000      # 週の実購入上限（log/README.md 記録の原則9・validate.py と同値）
RACE_BUDGET = 6000      # 1レースの実購入上限（同上）
WEEK_BUDGET_FROM = "2026-11-12"
MARKS = ["◎", "○", "▲", "△", "☆"]
HIT_RATE = re.compile(r"概算的中率\s*([0-9.]+)\s*%")
# (6) の候補は事前登録制。足すときは log/README.md 仮説46 に登録してから（後付けの最良構造を選ばない）
REPLAY_POLICIES = [
    ("全レース 軸1頭・相手7", lambda g: ("axis", 6, 2)),
    ("全レース 軸1頭・相手9", lambda g: ("axis", 8, 2)),
    ("ハンデ相手9／非ハンデ相手7（配分表 v3.0 のハンデ列）", lambda g: ("axis", 8, 2) if g == "ハンデ" else ("axis", 6, 2)),
    ("ハンデBOX6／非ハンデ相手7", lambda g: ("box", 5, 1) if g == "ハンデ" else ("axis", 6, 2)),
]
WEIGHT_TAG = re.compile(r"斤量=(ハンデ|別定|定量|馬齢)")


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def weight_type(r):
    """races 行 → 'ハンデ' / '非ハンデ'。タグ優先・無ければ band_replay の一覧"""
    m = WEIGHT_TAG.search(r.get("notes") or "")
    if m:
        return "ハンデ" if m.group(1) == "ハンデ" else "非ハンデ"
    if r["race_name"] in HANDICAP_RACES or re.search(r"荒れ度=[^/]*ハンデ\+[1-9]", r.get("notes") or ""):
        return "ハンデ"
    return "非ハンデ"


def week_key(date_str):
    d = datetime.date.fromisoformat(date_str)
    mon = d - datetime.timedelta(days=d.weekday())
    return mon.isoformat()


def load():
    races = {r["race_id"]: r for r in csv.DictReader(open(ROOT / "log/races.csv", encoding="utf-8"))}
    bets = [b for b in csv.DictReader(open(ROOT / "log/bets.csv", encoding="utf-8"))
            if b["race_id"] in races and b["hit"] != ""]
    preds = defaultdict(list)
    for p in csv.DictReader(open(ROOT / "log/predictions.csv", encoding="utf-8")):
        preds[p["race_id"]].append(p)
    return races, bets, preds


def pct(a, b):
    return f"{a / b:.0%}" if b else "—"


def kpi(per_race, races):
    """{race_id: (投資, 回収)} → 週次 KPI の dict"""
    wk = defaultdict(lambda: [0.0, 0.0])
    for rid, (c, r) in per_race.items():
        w = wk[week_key(races[rid]["date"])]
        w[0] += c
        w[1] += r
    ws = [v for _, v in sorted(wk.items()) if v[0] > 0]
    n = len(ws)
    roll = [(sum(x[0] for x in ws[i - ROLLING_WEEKS + 1:i + 1]), sum(x[1] for x in ws[i - ROLLING_WEEKS + 1:i + 1]))
            for i in range(ROLLING_WEEKS - 1, n)]
    cost = sum(x[0] for x in ws)
    ret = sum(x[1] for x in ws)
    top = max((r for _, r in per_race.values()), default=0)
    return {"weeks": n, "cost": cost, "ret": ret, "top": top,
            "week_ok": sum(1 for c, r in ws if r / c >= WEEK_TARGET),
            "zero": sum(1 for c, r in ws if r == 0),
            "roll_n": len(roll), "roll_ok": sum(1 for c, r in roll if r / c >= WEEK_TARGET)}


def section_weekly(races, bets):
    wk = defaultdict(lambda: {"cost": 0.0, "ret": 0.0, "races": set(), "h": 0})
    for b in bets:
        r = races[b["race_id"]]
        w = wk[week_key(r["date"])]
        w["cost"] += f(b["cost"])
        w["ret"] += f(b["return"])
        w["races"].add(r["race_name"])
    for w in wk.values():
        w["h"] = sum(1 for n in w["races"] if n in HANDICAP_RACES)
    weeks = [(k, v) for k, v in sorted(wk.items()) if v["cost"] > 0]
    print(f"■ (1) 週次回収率（実購入・月曜始まり）／目標ライン {WEEK_TARGET:.0%}")
    hit = 0
    for k, v in weeks:
        roi = v["ret"] / v["cost"]
        ok = roi >= WEEK_TARGET
        hit += ok
        print(f"  {k}週 {len(v['races'])}R 投資{v['cost']:>7,.0f} 回収{v['ret']:>7,.0f} {roi:>5.0%} "
              f"{'○' if ok else '×'}  {'・'.join(sorted(v['races']))}")
    n = len(weeks)
    tc = sum(v["cost"] for _, v in weeks)
    tr = sum(v["ret"] for _, v in weeks)
    zero = sum(1 for _, v in weeks if v["ret"] == 0)
    plus = sum(1 for _, v in weeks if v["ret"] >= v["cost"])
    rois = sorted(v["ret"] / v["cost"] for _, v in weeks)
    med = rois[n // 2] if n % 2 else (rois[n // 2 - 1] + rois[n // 2]) / 2
    by_race = defaultdict(float)
    for x in bets:
        by_race[x["race_id"]] += f(x["return"])
    top_id = max(by_race, key=by_race.get)
    top = by_race[top_id]
    print(f"  計 {n}週 投資{tc:,.0f} 回収{tr:,.0f} 通算{pct(tr, tc)}"
          f"（最高払戻レース {races[top_id]['race_name']} {top:,.0f}円 を除くと {pct(tr - top, tc)}）")
    print(f"  目標到達 {hit}/{n}週（{pct(hit, n)}）｜プラス収支 {plus}/{n}週｜回収0 {zero}/{n}週｜週次回収率の中央値 {med:.0%}")
    print()
    print(f"■ (2) 直近{ROLLING_WEEKS}週の移動回収率（購入のあった週）")
    ok_roll = 0
    rolls = 0
    for i in range(ROLLING_WEEKS - 1, n):
        win = weeks[i - ROLLING_WEEKS + 1:i + 1]
        c = sum(v["cost"] for _, v in win)
        t = sum(v["ret"] for _, v in win)
        rolls += 1
        ok_roll += (t / c) >= WEEK_TARGET
        print(f"  〜{weeks[i][0]}週 投資{c:>7,.0f} 回収{t:>7,.0f} {t / c:>5.0%} {'○' if t / c >= WEEK_TARGET else '×'}")
    if rolls:
        print(f"  目標到達 {ok_roll}/{rolls} 窓（{pct(ok_roll, rolls)}）")
    print()


def section_split(races, bets):
    print("■ (3) ハンデ戦／非ハンデ戦 × 券種 の実購入収支")
    t = defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0])  # 行数, 的中, 投資, 回収, 最高払戻（レース単位）
    rset = defaultdict(set)
    per_race = defaultdict(float)  # (区分, 券種, race_id) → 回収。1レースの複数行的中は1つの結果として除く
    for b in bets:
        g = weight_type(races[b["race_id"]])
        rset[g].add(b["race_id"])
        for key in ((g, "計"), (g, b["bet_type"])):
            o = t[key]
            o[0] += 1
            o[1] += b["hit"] == "1"
            o[2] += f(b["cost"])
            o[3] += f(b["return"])
            per_race[key + (b["race_id"],)] += f(b["return"])
    for (g, bt, _), v in per_race.items():
        t[(g, bt)][4] = max(t[(g, bt)][4], v)
    for g in ("ハンデ", "非ハンデ"):
        print(f"== {g}戦（{len(rset[g])}R）")
        keys = sorted((k for k in t if k[0] == g), key=lambda k: (k[1] != "計", -t[k][2]))
        for k in keys:
            n, h, c, r, mx = t[k]
            print(f"  {k[1]:<6} {n:>3}行 的中{h:>2} 投資{c:>7,.0f} 回収{r:>7,.0f} {pct(r, c):>5}"
                  f"（最高払戻レースを除く {pct(r - mx, c)}）")
    print()


def section_roughness(races, preds):
    print("■ (4) ハンデ戦／非ハンデ戦 の決着（結果入力済みレース）")
    t = defaultdict(lambda: defaultdict(int))
    for rid, r in races.items():
        if not r["result_1st"]:
            continue
        g = weight_type(r)
        o = t[g]
        o["n"] += 1
        o["big"] += f(r["payout_sanrentan"]) >= 100000
        ps = [p for p in preds.get(rid, []) if (p.get("finish_pos") or "").isdigit()]
        top3 = [p for p in ps if int(p["finish_pos"]) <= 3]
        if len(top3) == 3:
            o["cap_n"] += 1
            marked = sum(1 for p in top3 if (p.get("mark") or "-").strip() not in ("-", "", "✕", "x", "X", "×"))
            o["cap"] += marked
            o["cap3"] += marked == 3
        fav = [p for p in ps if (p.get("popularity") or "").strip() == "1"]
        if fav:
            o["fav_n"] += 1
            o["fav"] += int(fav[0]["finish_pos"]) <= 3
        for p in ps:
            m = (p.get("mark") or "").strip()
            if m in MARKS:
                o["m_n_" + m] += 1
                o["m_in_" + m] += int(p["finish_pos"]) <= 3
                o["m_win_" + m] += int(p["finish_pos"]) == 1
        pop_ok = [p for p in top3 if (p.get("popularity") or "").strip().isdigit()]
        if len(pop_ok) == 3:
            o["pop_n"] += 1
            o["longshot"] += sum(1 for p in pop_ok if int(p["popularity"]) >= 6)
    for g in ("ハンデ", "非ハンデ"):
        o = t[g]
        if not o["n"]:
            continue
        print(f"  {g}戦 {o['n']}R｜三連単10万円超 {o['big']}/{o['n']}（{pct(o['big'], o['n'])}）"
              f"｜1番人気の3着内 {o['fav']}/{o['fav_n']}（{pct(o['fav'], o['fav_n'])}）"
              f"｜3着内の6番人気以下 {o['longshot']}/{o['pop_n'] * 3}頭（{pct(o['longshot'], o['pop_n'] * 3)}）"
              f"｜印つき馬の3着内捕捉 {o['cap']}/{o['cap_n'] * 3}頭・3/3は {o['cap3']}/{o['cap_n']}R")
        print("    印別の3着内（1着）： " + "  ".join(
            f"{m} {o['m_in_' + m]}/{o['m_n_' + m]}（{o['m_win_' + m]}）" for m in MARKS if o["m_n_" + m]))
    print()


def section_expected(races, bets):
    print("■ (5) 期待的中数と実的中数（bets.notes に 概算的中率 の記録がある行だけ）")
    t = defaultdict(lambda: [0, 0.0, 0, 0.0, 0.0])  # 行数, 期待的中, 実的中, 投資, 回収
    for b in bets:
        m = HIT_RATE.search(b.get("notes") or "")
        if not m:
            continue
        g = weight_type(races[b["race_id"]])
        for key in (("全体", b["bet_type"]), (g, b["bet_type"]), ("全体", "計"), (g, "計")):
            o = t[key]
            o[0] += 1
            o[1] += float(m.group(1)) / 100
            o[2] += b["hit"] == "1"
            o[3] += f(b["cost"])
            o[4] += f(b["return"])
    if not t:
        print("  記録なし")
    for g in ("全体", "ハンデ", "非ハンデ"):
        keys = sorted((k for k in t if k[0] == g), key=lambda k: (k[1] != "計", -t[k][3]))
        if keys:
            print(f"== {g}")
        for k in keys:
            n, e, h, c, r = t[k]
            print(f"  {k[1]:<6} {n:>3}行 期待的中 {e:>5.2f} 実的中 {h:>2} （実÷期待 {h / e:.2f}）回収率 {pct(r, c)}" if e else
                  f"  {k[1]:<6} {n:>3}行 期待的中 0")
    print("  ※ 期待的中は市場オッズの Harville 近似（配分表 第5項）。実÷期待が1を超えれば市場より多く当てている。")
    print("    的中率の低い券種は行数が少ないうちは実的中0でも期待どおりで、単独では構造を変える根拠にならない")
    print()


def section_replay(races, bets, preds):
    print("■ (6) 三連複の構造リプレイを週次 KPI で比較（事前登録の候補・実購入のあったレース・100円単位）")
    bought = {b["race_id"] for b in bets}
    base = defaultdict(lambda: [0.0, 0.0])
    for b in bets:
        base[b["race_id"]][0] += f(b["cost"])
        base[b["race_id"]][1] += f(b["return"])
    rows = [("実績（全券種）", {k: tuple(v) for k, v in base.items()})]
    for name, policy in REPLAY_POLICIES:
        per = {}
        for rid in bought:
            r = races[rid]
            ps = [p for p in preds.get(rid, []) if (p.get("finish_pos") or "").isdigit()]
            top3 = {int(p["horse_no"]) for p in ps if int(p["finish_pos"]) <= 3}
            if len(top3) != 3 or not r["payout_sanrenpuku"]:
                continue
            kind, k, ns = policy(weight_type(r))
            axis, pool, pts = replay_build(ps, kind, k, ns)
            hit = top3 <= pool if axis is None else (axis in top3 and (top3 - {axis}) <= pool)
            per[rid] = (pts * 100, f(r["payout_sanrenpuku"]) if hit else 0.0)
        rows.append((name, per))
    for name, per in rows:
        k = kpi(per, races)
        if not k["cost"]:
            continue
        print(f"  {name}\n      投資{k['cost']:>8,.0f} 回収率{pct(k['ret'], k['cost']):>5}（最高払戻レースを除く {pct(k['ret'] - k['top'], k['cost'])}）"
              f"｜週{WEEK_TARGET:.0%}到達 {k['week_ok']}/{k['weeks']}｜回収0週 {k['zero']}"
              f"｜{ROLLING_WEEKS}週窓の到達 {k['roll_ok']}/{k['roll_n']}")
    print("  ※ 同じレース群で組み直した後付けの比較（in-sample）。差は数本の高配当で入れ替わるので、構造の採否は較正レビューで判定する")
    print()


def section_budget(races, bets):
    wk = defaultdict(lambda: defaultdict(float))
    for b in bets_all(races):
        r = races[b["race_id"]]
        if r["date"] >= WEEK_BUDGET_FROM:
            wk[week_key(r["date"])][r["race_name"]] += f(b["cost"])
    print(f"■ (7) 週予算の消化（{WEEK_BUDGET_FROM} 以降・週上限 {WEEK_BUDGET:,}円・1レース上限 {RACE_BUDGET:,}円）")
    if not wk:
        print("  対象週なし")
    for k in sorted(wk):
        tot = sum(wk[k].values())
        over = [n for n, c in wk[k].items() if c > RACE_BUDGET]
        print(f"  {k}週 {tot:>6,.0f}円／残り {WEEK_BUDGET - tot:>6,.0f}円 {'超過' if tot > WEEK_BUDGET or over else ''}  "
              + "・".join(f"{n} {c:,.0f}" for n, c in wk[k].items()))
    print()


def bets_all(races):
    """結果未確定の買い目も含む全行（週予算の消化は購入時点で数える）"""
    return [b for b in csv.DictReader(open(ROOT / "log/bets.csv", encoding="utf-8")) if b["race_id"] in races]


def main():
    races, bets, preds = load()
    if not bets:
        print("対象の実購入なし")
        return 1
    section_weekly(races, bets)
    section_split(races, bets)
    section_roughness(races, preds)
    section_expected(races, bets)
    section_replay(races, bets, preds)
    section_budget(races, bets)
    print("※ 週次回収率は少数の高配当に依存する。目標の達否は (1) の到達率と (2) の移動回収率で読み、1週の値で設計を変えない（R19）")
    print("※ ハンデ戦の n<10 は参考値")
    return 0


if __name__ == "__main__":
    sys.exit(main())
