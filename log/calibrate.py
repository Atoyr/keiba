#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calibrate.py — final_score → 複勝確率 p のロジスティック較正（Phase 2・群2-1）

「点数の序列」から「確率」への昇格。ロードマップ v1.2 で単位を訂正した項目で、
**単位はレースではなく頭**（predictions.csv の行）。n=30レース待ちは設定ミスだった。

設計上の制約（ロードマップ Phase 2）:
  - 説明変数は r_adj を引いた「モデル素点」= final_score − r_adj。
    オッズ由来の加算を除くことで、モデルが市場をなぞっているだけの循環参照を避ける
  - **パラメータは2個まで**（切片＋傾き）。レース内相関が強く実効サンプルは行数より小さい
  - 汎化は**レース単位の1レース抜きクロスバリデーション**で見る。行単位のCVは
    同一レースの馬が学習側と検証側に分かれるため楽観に偏る

比較する3モデル（いずれも2パラメータ）:
  A 素点（絶対値）      … 点数をそのまま確率に変換する素朴版
  B 素点（レース内z）   … レース内で標準化。点数の意味は相手関係に依存するという仮説
  C 参考: log(単勝オッズ) … 市場そのものの較正。モデルが市場を超えているかの基準線

実行: python3 log/calibrate.py
標準ライブラリのみ使用。
"""
import csv
import math
import os
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
RIDGE = 1e-6  # 完全分離時の発散止め


def to_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_rows():
    """モデル素点・複勝結果・単勝オッズが揃った行だけを返す（推測で埋めない）"""
    path = os.path.join(BASE, "predictions.csv")
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        fs, ra, odds = (to_f(r.get(k)) for k in ("final_score", "r_adj", "win_odds"))
        ip = r.get("in_place")
        if None in (fs, ra) or ip not in ("0", "1"):
            continue
        out.append({"race_id": r["race_id"], "raw": fs - ra, "y": int(ip),
                    "odds": odds, "name": r.get("horse_name", "")})
    return out


def sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def fit(xs, ys, iters=200):
    """2パラメータのロジスティック回帰（Newton-Raphson／微小リッジ付き）"""
    a = b = 0.0
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            p = sigmoid(a + b * x)
            w = max(p * (1 - p), 1e-9)
            g0 += y - p
            g1 += (y - p) * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        h00 += RIDGE
        h11 += RIDGE
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        a += da
        b += db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return a, b


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def logloss(ps, ys):
    eps = 1e-12
    return -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
                for p, y in zip(ps, ys)) / len(ys)


# ---------- 特徴量の作り方（モデルごと） ----------

def feat_abs(rows):
    """A: 素点そのもの（学習時に標準化して数値安定化）"""
    return {r_id(r): r["raw"] for r in rows}


def r_id(r):
    return (r["race_id"], r["name"], r["raw"], r["y"])


def build(rows, kind):
    """(x, y, race_id) のリストを返す。Noneを含む行は落とす（推測しない）"""
    out = []
    if kind == "B":
        by_race = defaultdict(list)
        for r in rows:
            by_race[r["race_id"]].append(r)
        for rid, rs in by_race.items():
            vals = [r["raw"] for r in rs]
            m = sum(vals) / len(vals)
            sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
            if sd < 1e-9:
                continue
            for r in rs:
                out.append(((r["raw"] - m) / sd, r["y"], rid))
    else:
        for r in rows:
            if kind == "A":
                x = r["raw"]
            else:  # C
                if not r["odds"] or r["odds"] <= 0:
                    continue
                x = math.log(r["odds"])
            out.append((x, r["y"], r["race_id"]))
    return out


def standardize(xs):
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5 or 1.0
    return m, sd


def evaluate(data, label, note):
    xs = [d[0] for d in data]
    ys = [d[1] for d in data]
    rids = [d[2] for d in data]
    races = sorted(set(rids))
    m, sd = standardize(xs)
    zs = [(x - m) / sd for x in xs]

    a, b = fit(zs, ys)
    ps_in = [sigmoid(a + b * z) for z in zs]

    # レース単位の1レース抜きCV
    ps_cv = [None] * len(ys)
    for held in races:
        tr = [(z, y) for z, y, rid in zip(zs, ys, rids) if rid != held]
        if not tr or len({y for _, y in tr}) < 2:
            continue
        a_, b_ = fit([t[0] for t in tr], [t[1] for t in tr])
        for i, (z, rid) in enumerate(zip(zs, rids)):
            if rid == held:
                ps_cv[i] = sigmoid(a_ + b_ * z)
    idx = [i for i, p in enumerate(ps_cv) if p is not None]
    ys_cv = [ys[i] for i in idx]
    ps_cv2 = [ps_cv[i] for i in idx]

    base = sum(ys) / len(ys)
    ps_base = [base] * len(ys)

    print(f"\n--- {label} ---")
    print(f"  {note}")
    print(f"  n={len(ys)}頭 / {len(races)}レース ／ 複勝圏 {sum(ys)}頭（基準率 {base * 100:.1f}%）")
    print(f"  係数: intercept={a:+.3f} slope={b:+.3f}（標準化後1SDあたりのオッズ比 {math.exp(b):.2f}倍）")
    print(f"  in-sample : Brier {brier(ps_in, ys):.4f} ／ LogLoss {logloss(ps_in, ys):.4f}")
    if ps_cv2:
        print(f"  レース抜きCV: Brier {brier(ps_cv2, ys_cv):.4f} ／ LogLoss {logloss(ps_cv2, ys_cv):.4f}"
              f"（n={len(ys_cv)}）")
    print(f"  ベースライン(定数{base * 100:.1f}%): Brier {brier(ps_base, ys):.4f} ／ "
          f"LogLoss {logloss(ps_base, ys):.4f}")
    if ps_cv2:
        b_cv, b_bl = brier(ps_cv2, ys_cv), brier([base] * len(ys_cv), ys_cv)
        verdict = "ベースライン超え" if b_cv < b_bl else "**ベースライン未満（予測力なし）**"
        print(f"  → CV Brier の対ベースライン: {b_cv:.4f} vs {b_bl:.4f} … {verdict}")
    return (brier(ps_cv2, ys_cv) if ps_cv2 else None), ps_cv, ys, rids


def calibration_table(ps, ys, bins=4):
    """予測確率の帯ごとに実測率を並べる（較正のずれを見る）"""
    pairs = sorted((p, y) for p, y in zip(ps, ys) if p is not None)
    if not pairs:
        return
    size = max(1, len(pairs) // bins)
    print("  予測帯 → 実測（較正のずれ）")
    for i in range(0, len(pairs), size):
        chunk = pairs[i:i + size]
        if len(chunk) < 3:
            continue
        pm = sum(p for p, _ in chunk) / len(chunk)
        ym = sum(y for _, y in chunk) / len(chunk)
        print(f"    予測 {pm * 100:5.1f}% → 実測 {ym * 100:5.1f}%  (n={len(chunk)})")


def main():
    rows = load_rows()
    print("=" * 64)
    print("■ 複勝確率の較正（Phase 2 / 群2-1・単位＝頭）")
    print("=" * 64)
    if len(rows) < 30:
        print(f"有効行 {len(rows)} 件。30件未満のためフィットしない（過学習）。")
        return
    print(f"有効行 {len(rows)} 件（final_score・r_adj・in_place が揃った行のみ。推測補完なし）")
    print("説明変数はすべて『モデル素点 = final_score − r_adj』系統。Cのみ市場基準線。")

    results = {}
    for kind, label, note in (
        ("A", "モデルA 素点（絶対値）", "点数をそのまま確率へ。レースの相手関係を見ない"),
        ("B", "モデルB 素点（レース内z）", "レース内で標準化。点数の意味は相手関係に依存するという仮説"),
        ("C", "参考C 市場（log単勝オッズ）", "市場そのものの較正＝モデルが超えるべき基準線"),
    ):
        data = build(rows, kind)
        if not data:
            print(f"\n--- {label} --- データ不足でスキップ")
            continue
        cv, ps_cv, ys, rids = evaluate(data, label, note)
        results[kind] = cv
        calibration_table(ps_cv, ys)

    print("\n" + "=" * 64)
    print("■ 読み方と留保")
    print("=" * 64)
    print(" ・Brier は小さいほど良い。CV値だけを比較する（in-sample は必ず良く出る）")
    print(f" ・{len({r['race_id'] for r in rows})}レース分しかなく**レース内相関が強い**ため、")
    print("   実効サンプルは行数よりかなり小さい。パラメータを増やさない（ロードマップの制約）")
    print(" ・複勝は1レースにちょうど3頭。頭単位の独立ロジスティックはこの制約を無視しており、")
    print("   確率の合計が3にならない。順位モデル化は Phase 3 の課題として残す")
    print(" ・A/B が C（市場）に届かない場合、それは『モデルが市場を超えていない』ことの直接の証拠")
    print(" ・馬券は自己責任。較正は的中を保証しない")


if __name__ == "__main__":
    main()
