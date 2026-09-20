#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py 層2（`欠落理由=` の内容照合）の回帰テスト。

層1（タグが存在するか）は gate.py が、層2（書かれた値が predictions.csv と
一致するか）は validate.check_reason_tags が担当する。層3（その cat で落とす
判断が正しかったか）は機械検証できないので analyze.py のカテゴリ別集計へ回す。
この区分は log/README.md 宣言タグ規約の `欠落理由=` 節が正本。

実行: python3 log/test_reason_tags.py   （claude_run.sh からも呼ばれる）
終了コード 0=全ケースPASS / 1=FAILあり
"""
import importlib.util
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("validate_mod", os.path.join(BASE, "validate.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

# オールカマー2026の実値を固定値として使う（13頭・#8 は 妙味4.8・final9位・s_mid=B）
PRED = [
    {"horse_no": "7",  "myomi_score": "3.8", "final_score": "104.6", "s_mid": "B"},
    {"horse_no": "9",  "myomi_score": "0.0", "final_score": "86.0",  "s_mid": "A"},
    {"horse_no": "13", "myomi_score": "2.4", "final_score": "82.4",  "s_mid": "A"},
    {"horse_no": "3",  "myomi_score": "6.2", "final_score": "81.0",  "s_mid": "A"},
    {"horse_no": "4",  "myomi_score": "2.4", "final_score": "76.4",  "s_mid": "A"},
    {"horse_no": "12", "myomi_score": "0.0", "final_score": "75.8",  "s_mid": "A"},
    {"horse_no": "6",  "myomi_score": "3.8", "final_score": "68.0",  "s_mid": "A"},
    {"horse_no": "2",  "myomi_score": "6.2", "final_score": "66.0",  "s_mid": "B"},
    {"horse_no": "8",  "myomi_score": "4.8", "final_score": "64.5",  "s_mid": "B"},
    {"horse_no": "1",  "myomi_score": "2.4", "final_score": "64.0",  "s_mid": "B"},
    {"horse_no": "5",  "myomi_score": "5.2", "final_score": "53.0",  "s_mid": "B"},
    {"horse_no": "10", "myomi_score": "4.8", "final_score": "47.0",  "s_mid": "C"},
    {"horse_no": "11", "myomi_score": "3.8", "final_score": "46.0",  "s_mid": "A"},
]

AFTER = "2026-09-27"   # REASON_TAG_FROM (2026-09-21) 以降
BEFORE = "2026-09-20"  # 施行日より前＝遡及しない

CASES = [
    ("施行日より前は一切見ない（遡及しない）", BEFORE,
     "欠落理由=中帯域の紐5〜6頭上限と予算3000円により限定した", 0, 0),
    ("正しい記録（事実タグが全一致）", AFTER,
     "欠落理由=#8:cat=妙味閾値未満:妙味4.8:final9位:本線B:中帯域の紐上限で溢れた"
     " ; #1-4:cat=予算上限:三連系に20点配分したため / EV=…", 0, 0),
    ("妙味が実データと違う → ERROR", AFTER,
     "欠落理由=#8:cat=妙味閾値未満:妙味5.8:本線B:…", 1, 0),
    ("final順位が違う → ERROR", AFTER,
     "欠落理由=#8:cat=紐頭数上限:final3位:…", 1, 0),
    ("本線の結論が違う → ERROR", AFTER,
     "欠落理由=#8:cat=展開不適合:本線C:…", 1, 0),
    ("3つ同時に違う → ERROR 3件", AFTER,
     "欠落理由=#8:cat=予算上限:妙味9.9:final1位:本線A:…", 3, 0),
    ("cat が閉じた語彙にない → WARN", AFTER,
     "欠落理由=#8:cat=なんとなく:妙味4.8:…", 0, 1),
    ("cat がない → WARN", AFTER,
     "欠落理由=#8:妙味4.8:予算の都合で…", 0, 1),
    ("先頭が #馬番 でない（旧来の自由文）→ WARN 2件", AFTER,
     "欠落理由=中帯域の紐5〜6頭上限と予算3000円により限定した", 0, 2),
    ("存在しない馬番 → WARN", AFTER,
     "欠落理由=#18:cat=予算上限:…", 0, 1),
    ("ペア欠落は事実タグを照合しない（誤ERRORを出さない）", AFTER,
     "欠落理由=#1-4:cat=予算上限:妙味9.9:final1位:…", 0, 0),
    ("無記入は何も言わない（層1は gate.py の担当）", AFTER,
     "band=中 / 予算=3000", 0, 0),
    ("bets.notes 側に書かれていても拾う", AFTER, "band=中", 1, 0,
     [{"notes": "欠落理由=#8:cat=予算上限:妙味9.9:… / side=穴"}]),
]


def main():
    print("=" * 78)
    print("■ validate.py 層2（欠落理由= の内容照合）回帰テスト")
    print("=" * 78)
    print("%-46s %-9s %-9s %s" % ("ケース", "ERROR", "WARN", "判定"))
    print("-" * 78)
    failed = 0
    for case in CASES:
        name, date, notes, e_exp, w_exp = case[:5]
        rbets = case[5] if len(case) > 5 else []
        V.errors.clear()
        V.warns.clear()
        V.check_reason_tags("TEST", {"date": date}, notes, rbets, PRED)
        ok = len(V.errors) == e_exp and len(V.warns) == w_exp
        if not ok:
            failed += 1
        print("%-46s %-9s %-9s %s" % (
            name, "%d/%d" % (len(V.errors), e_exp), "%d/%d" % (len(V.warns), w_exp),
            "PASS" if ok else "**FAIL**"))
        if not ok:
            for m in V.errors + V.warns:
                print("        " + m)
    print("-" * 78)
    if failed:
        print("**FAIL %d件**（log/README.md 宣言タグ規約 の `欠落理由=` 節が正本）" % failed)
        return 1
    print("全 %d ケース PASS" % len(CASES))
    print()
    print("※ 層3（cat の判断が正しかったか）は機械検証できない。")
    print("　 cat を閉じた語彙にしてあるのは analyze.py でカテゴリ別3着内率を測るため（TODO U18）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
