#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py「ゲートの本丸」のユーザー指示例外（R14・`ユーザー指示=`）の回帰テスト。

未決レースで fired=1・followed=0 の行があり bets があると ERROR（gate_followed0）。
ユーザー本人の指示で予想確定後に印を変えた R14 だけは、races.notes の `ユーザー指示=` と
R14 行の `ユーザー指示=<日付>` が揃い、中身が predictions.csv と合うときに WARN へ落ちる。
書式の正本は log/README.md「判断タグ」節。

実行: python3 log/test_user_override.py   （claude_run.sh からも呼ばれる）
終了コード 0=全ケースPASS / 1=FAILあり
"""
import importlib.util
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("validate_mod", os.path.join(BASE, "validate.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

# 秋華賞2026 の実値（#5 タイセイボーグを確定後のユーザー指示で 無印→☆）
TAG = "ユーザー指示=2026-10-17:#5:-→☆:☆に追加し紐5頭"
PROWS = [{"horse_no": "13", "mark": "◎"}, {"horse_no": "3", "mark": "○"},
         {"horse_no": "4", "mark": "☆"}, {"horse_no": "5", "mark": "☆"}, {"horse_no": "6", "mark": "-"}]
FIRE_NOTE = "#5を無印→☆に変更。独立した新根拠2つは無い / ユーザー指示=2026-10-17 / as_of=良"


def race(notes="band=堅 / " + TAG + " / 帯域逸脱=紐5頭", date="2026-10-18", result=""):
    return {"date": date, "notes": notes, "result_1st": result}


def fire(rule="R14", notes=FIRE_NOTE, fired="1", followed="0"):
    return {"rule_id": rule, "fired": fired, "followed": followed, "notes": notes}


# (名前, race, prows, fire, ERROR期待, WARN期待)
GATE_CASES = [
    ("記録が揃った R14 → WARN（秋華賞2026 の形）", race(), PROWS, fire(), 0, 1),
    ("ユーザー指示= が無い R14 → ERROR（従来どおり）", race(notes="band=堅"), PROWS, fire(notes="as_of=良"), 1, 0),
    ("races.notes だけで R14 行に日付が無い → ERROR", race(), PROWS, fire(notes="as_of=良"), 1, 0),
    ("R14 行だけで races.notes に項目が無い → ERROR", race(notes="band=堅"), PROWS, fire(), 1, 0),
    ("R14 行の日付が races.notes の項目と違う → ERROR", race(), PROWS,
     fire(notes="ユーザー指示=2026-10-16 / as_of=良"), 1, 0),
    ("新印が predictions.csv の印と不一致 → ERROR", race(notes=TAG.replace("→☆", "→△")), PROWS, fire(), 1, 0),
    ("馬番が predictions.csv にない → ERROR", race(notes=TAG.replace("#5", "#15")), PROWS, fire(), 1, 0),
    ("旧印と新印が同じ → ERROR", race(notes="ユーザー指示=2026-10-17:#5:☆→☆:念のため"), PROWS, fire(), 1, 0),
    ("指示日がレース日より後 → ERROR", race(notes=TAG.replace("2026-10-17", "2026-10-19")), PROWS,
     fire(notes="ユーザー指示=2026-10-19"), 1, 0),
    ("指示の要旨が空 → ERROR", race(notes="ユーザー指示=2026-10-17:#5:-→☆"), PROWS, fire(), 1, 0),
    ("R14 以外のルールに ユーザー指示= → ERROR（R14 限定）", race(), PROWS,
     fire(rule="R13", notes="ユーザー指示=2026-10-17 / 1番人気を紐から外した"), 1, 0),
    ("R14 以外のルール（タグ無し）→ ERROR（従来どおり）", race(), PROWS, fire(rule="R48", notes="as_of=良"), 1, 0),
    ("同じ馬を2回変えたら最後の項目の新印で照合", race(
        notes="ユーザー指示=2026-10-17:#5:-→△:紐に追加 ; 2026-10-18:#5:△→☆:☆に格上げ"), PROWS,
     fire(notes="ユーザー指示=2026-10-18"), 0, 1),
    ("2頭を変えた（消し馬の正規化 ✕→x を含む）", race(
        notes="ユーザー指示=2026-10-17:#5:-→☆:☆に追加 ; 2026-10-17:#6:✕→-:消しを外す"), PROWS, fire(), 0, 1),
]

# (名前, race, prows, rfires, gated, ERROR期待, WARN期待)
TAG_CASES = [
    ("タグ無しのレースは見ない", race(notes="band=堅"), PROWS, [], set(), 0, 0),
    ("ゲート判定済みのレースは二重に出さない", race(), PROWS, [fire()], {"TEST"}, 0, 0),
    ("結果確定後・記録が揃っている → 何も出ない", race(result="13"), PROWS, [fire()], set(), 0, 0),
    ("ユーザー指示= があるのに R14 の followed=0 行が無い → WARN", race(), PROWS,
     [fire(followed="1")], set(), 0, 1),
    ("結果確定後・書式外の項目 → WARN", race(notes="ユーザー指示=2026-10-17:5番を☆", result="13"), PROWS,
     [fire()], set(), 0, 2),
]


def show(name, e_exp, w_exp):
    ok = len(V.errors) == e_exp and len(V.warns) == w_exp
    print("%-58s %-7s %-7s %s" % (name, "%d/%d" % (len(V.errors), e_exp),
                                  "%d/%d" % (len(V.warns), w_exp), "PASS" if ok else "**FAIL**"))
    if not ok:
        for m in V.errors + V.warns:
            print("        " + m)
    return not ok


def run_cases():
    print("=" * 86)
    print("■ validate.py ゲートの本丸・ユーザー指示例外（R14 `ユーザー指示=`）回帰テスト")
    print("=" * 86)
    print("%-58s %-7s %-7s %s" % ("ケース", "ERROR", "WARN", "判定"))
    print("-" * 86)
    failed = 0
    for name, r, prows, f_, e_exp, w_exp in GATE_CASES:
        V.errors.clear()
        V.warns.clear()
        V.gate_followed0("TEST", r, prows, f_)
        failed += show(name, e_exp, w_exp)
    for name, r, prows, rfires, gated, e_exp, w_exp in TAG_CASES:
        V.errors.clear()
        V.warns.clear()
        V.check_user_override_tags("TEST", r, prows, rfires, gated)
        failed += show(name, e_exp, w_exp)
    print("-" * 86)
    if failed:
        print("**FAIL %d件**（log/README.md「判断タグ」節が正本）" % failed)
        return 1
    print("全 %d ケース PASS" % (len(GATE_CASES) + len(TAG_CASES)))
    print()
    print("※ 照合できるのは「記録の対と中身が predictions.csv と合うか」まで。")
    print("　 ユーザーが本当にそう指示したかは機械検証できない（指示の要旨を残し、analyze.py が別集計で頻度と成績を見せる）。")
    return 0


if __name__ == "__main__":
    sys.exit(run_cases())
