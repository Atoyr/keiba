#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py 遵守検証 K3（判断タグの事実照合）の回帰テスト。

K1＝プロファイル由来の事実タグ（tools/tag_profile.py）／K2＝判断タグ（`10-7=` `適性行=` `減点根拠=`）／
K3＝validate.check_compliance が両者を突き合わせる。書式の正本は log/README.md「判断タグ」節、
設計はロードマップ v2 §11-3。

実行:
  python3 log/test_compliance.py            固定ケース（claude_run.sh からも呼ばれる）
  python3 log/test_compliance.py --retro    過去ログに (b) を後付けで走らせて検出を一覧する（書き込まない）
終了コード 0=全ケースPASS / 1=FAILあり
"""
import csv
import importlib.util
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("validate_mod", os.path.join(BASE, "validate.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

AFTER = "2026-10-04"   # COMPLIANCE_FROM (2026-10-03) 以降
BEFORE = "2026-09-27"  # 施行日より前＝遡及しない

# スプリンターズS2026 の実値（#6 ワールズエンド・#16 ピューロマジック・#2 アイサンサン）
F6 = "機械脚質=逃げ:r0.00:IQR0.04 / 距離帯経験=あり / 実距離経験=なし"
F16 = "機械脚質=逃げ:r0.00:IQR0.00 / 距離帯経験=あり / 実距離経験=あり"
F2 = "機械脚質=逃げ:r0.04:IQR0.26 / 距離帯経験=あり / 実距離経験=なし"
F_NONE = "機械脚質=先行:r0.31:IQR0.53 / 距離帯経験=なし / 実距離経験=なし"
ROW_OK = "適性行=先行〜好位の持続型:機械逃げ:r0.00:距離帯経験あり:番手で運べる"

# 過去ログの実文面（(b) の検出対象3件と、別次元の「未経験」）
N6 = ("R34該当=base1位(80)が最終点8位で◎○▲☆圏外。減点根拠の独立2要素=(1)芝1200mはキャリア11戦で出走0"
      "(netkeiba全キャリア確認)で1200m G1のテンで位置を取れるか未検証のため係数1.00に保留(2)㉘の5歳以上年齢-1")
B2 = "R25精査=近5走全確認・base64(1200m実走歴なしは係数側で扱い base には入れない)/5走距離帯=1600:良:13着:G1"
N2 = "芝1200mはキャリア13戦で出走0(netkeiba全キャリア確認)。近5走1400×3で±200m判定上R38は非発火"
N12 = "不安材料=中27週（休養196日）・近5走すべて1600mで1800m自体が未経験"
OTHER = ("道悪未経験(馬場別 良[0-0-1-3]) / 阪神の坂は未経験で係数×1.10どまり / 重賞は未経験 / "
         "R40の『未経験は消し根拠にしない』には抵触しない(1200未経験ではなく実測の決め手不在が根拠) / "
         "未経験は不利でなく未知 / 1200mは実距離経験なし（距離帯経験あり） / 1200mは実距離未経験 / "
         "古馬重賞1200の出走0（スプリンターズS2026 #12 の実文面）")


def race(date=AFTER, course="中山芝1200外", ps="16", flag="前崩れ警戒(競り合い型)/隊列確定型 同確率",
         notes="10-7=不適用:同確率のためコース標準", result=""):
    return {"date": date, "course": course, "pace_score_pre": ps, "pace_flag_pre": flag,
            "notes": notes, "result_1st": result}


def row(no="16", facts=F16, notes=ROW_OK, coef="適性1.10;バイアス1.00", base="71", mark="-", bb="地力メモ"):
    return {"horse_no": no, "base_breakdown": bb + (" / " + facts if facts else ""),
            "coef_breakdown": coef, "base_score": base, "mark": mark, "notes": notes}


R34 = [{"rule_id": "R34", "fired": "1", "followed": "1"}]
BETS_WITHOUT_6 = [{"bet_type": "三連複", "structure": "軸1頭 14→5,4,13"}, {"bet_type": "ワイド", "structure": "14-5 / 5-4"}]
BETS_WITH_6 = [{"bet_type": "三連複", "structure": "軸1頭 14→5,6,13"}, {"bet_type": "ワイド", "structure": "14-6 / 5-4"}]
TOP6 = dict(no="6", facts=F6, base="80", mark="-")
DED = "適性行=標準・位置取り保留:機械逃げ:距離帯経験あり / 減点根拠="

# (名前, race, prows, rbets, rfires, expected, ERROR期待, WARN期待)
CASES = [
    ("施行日より前は一切見ない（遡及しない）", race(date=BEFORE), [row(facts="", notes="")], [], [], None, 0, 0),
    ("正しい記録（事実タグが全一致）", race(), [row()], [], [], None, 0, 0),
    ("K1 事実タグが無い → ERROR", race(), [row(facts="", notes="適性行=標準")], [], [], None, 1, 0),
    ("K1 事実タグがプロファイル md と違う（手書き）→ ERROR", race(), [row()], [], [],
     {"16": {"機械脚質": "逃げ:r0.00:IQR0.00", "距離帯経験": "あり", "実距離経験": "なし"}}, 1, 0),
    ("(a) 機械脚質が違う → ERROR", race(), [row(notes="適性行=上がり上位の差し型:機械差し")], [], [], None, 1, 0),
    ("(a) 距離帯経験なし と書いたがプロファイルは あり → ERROR", race(),
     [row(notes="適性行=標準・位置取り保留:機械逃げ:距離帯経験なし")], [], [], None, 1, 0),
    ("(a) r が違う → ERROR", race(), [row(notes="適性行=先行〜好位の持続型:r0.30")], [], [], None, 1, 0),
    ("(b) スプリンターズS2026 #6 の実文面 → ERROR", race(),
     [row(no="6", facts=F6, notes=ROW_OK + " / " + N6, mark="◎")], [], [], None, 1, 0),
    ("(b) 同 #2（notes と base_breakdown の2句）→ ERROR 2件", race(),
     [row(no="2", facts=F2, notes="適性行=標準・位置取り保留:機械逃げ / " + N2, bb=B2)], [], [], None, 2, 0),
    ("(b) ローズS2026 #12 の実文面（1800m）→ ERROR", race(course="阪神芝1800外", ps="6", notes=""),
     [row(no="12", facts="機械脚質=差し:r0.47:IQR0.19 / 距離帯経験=あり / 実距離経験=なし",
          notes="適性行=上がり上位の差し型:機械差し / " + N12)], [], [], None, 1, 0),
    ("(b) 道悪・坂・重賞・R40言及・否定・実距離の表記は拾わない", race(),
     [row(notes=ROW_OK + " / " + OTHER)], [], [], None, 0, 0),
    ("(b) 距離帯経験=なし の馬が距離未経験と書くのは正しい", race(),
     [row(facts=F_NONE, notes="適性行=標準:機械先行:距離帯経験なし / 1200mは未経験で未知扱い")], [], [], None, 0, 0),
    ("適性係数を使ったのに 適性行= がない → ERROR", race(), [row(notes="")], [], [], None, 1, 0),
    ("印v4（係数層なし）は 適性行= 不要", race(), [row(notes="", coef="v4係数なし1.00")], [], [], None, 0, 0),
    ("(c) 10-7=適用・同確率・反転根拠なし → ERROR", race(notes="10-7=適用:ペーススコア16"), [row()], [], [], None, 1, 0),
    ("(c) 同・反転根拠あり", race(notes="10-7=適用:ペーススコア16 / 反転根拠=当日8Rまで前傾かつ内が荒れて外差し3連続"),
     [row()], [], [], None, 0, 0),
    ("10-7=適用・前崩れ単独本線・反転根拠なし → WARN（R46 の記録）",
     race(flag="前崩れ(競り合い型A本線)/隊列確定型(B対抗)", notes="10-7=適用:競り合い型が本線"), [row()], [], [], None, 0, 1),
    ("(e) ペーススコア≧7 で 10-7= なし → WARN", race(notes="band=荒"), [row()], [], [], None, 0, 1),
    ("ペーススコア6 は 10-7= 不要", race(ps="6", flag="中立", notes="band=堅"), [row()], [], [], None, 0, 0),
    ("(d) R34 followed=1・未経験＋実測（有効1/2）・紐外 → ERROR", race(),
     [row(**TOP6, notes=DED + "#6:未経験:1200m G1のテンが未検証 ; #6:実測:㉘5歳以上の年齢-1"), row()],
     BETS_WITHOUT_6, R34, None, 1, 0),
    ("(d) 実測＋自己実績n=3（有効2/2）", race(),
     [row(**TOP6, notes=DED + "#6:実測:㉘5歳以上の年齢-1 ; #6:自己実績3:坂1200で3戦着外"), row()],
     BETS_WITHOUT_6, R34, None, 0, 0),
    ("(d) 自己実績n=2 は有効に数えない → ERROR", race(),
     [row(**TOP6, notes=DED + "#6:実測:㉘5歳以上の年齢-1 ; #6:自己実績2:坂1200で13着16着"), row()],
     BETS_WITHOUT_6, R34, None, 1, 0),
    ("(d) 全券種の紐に残した（減点根拠なし）", race(),
     [row(**TOP6, notes="適性行=標準・位置取り保留:機械逃げ"), row()], BETS_WITH_6, R34, None, 0, 0),
    ("(d) base1位が印圏内なら対象外", race(),
     [row(no="6", facts=F6, base="80", mark="◎", notes="適性行=標準・位置取り保留:機械逃げ"), row()],
     BETS_WITHOUT_6, R34, None, 0, 0),
    ("結果確定後に 逸脱= を記録済み → WARN へ落ちる（文面は直さない）",
     race(result="16", notes="10-7=不適用:同確率 / 逸脱=コース別_脚質枠_補正表.md㉕:#6:距離帯経験ありの馬を未経験扱いで保留"),
     [row(no="6", facts=F6, notes=ROW_OK + " / " + N6, mark="◎")], [], [], None, 0, 1),
    ("結果確定後でも 逸脱= が無ければ ERROR のまま", race(result="16"),
     [row(no="6", facts=F6, notes=ROW_OK + " / " + N6, mark="◎")], [], [], None, 1, 0),
]


def run_cases():
    print("=" * 86)
    print("■ validate.py 遵守検証 K3（判断タグの事実照合）回帰テスト")
    print("=" * 86)
    print("%-58s %-7s %-7s %s" % ("ケース", "ERROR", "WARN", "判定"))
    print("-" * 86)
    failed = 0
    for name, r, prows, rbets, rfires, expected, e_exp, w_exp in CASES:
        V.errors.clear()
        V.warns.clear()
        V.check_compliance("TEST", r, prows, rbets, rfires, expected)
        ok = len(V.errors) == e_exp and len(V.warns) == w_exp
        failed += not ok
        print("%-58s %-7s %-7s %s" % (name, "%d/%d" % (len(V.errors), e_exp),
                                      "%d/%d" % (len(V.warns), w_exp), "PASS" if ok else "**FAIL**"))
        if not ok:
            for m in V.errors + V.warns:
                print("        " + m)
    # §11-5 要判断1：COMPLIANCE_LEVEL を WARN にすると全項目が WARN へ落ちる
    V.errors.clear()
    V.warns.clear()
    V.COMPLIANCE_LEVEL = "WARN"
    V.check_compliance("TEST", race(), [row(facts="", notes="")], [], [], None)
    V.COMPLIANCE_LEVEL = "ERROR"
    ok = not V.errors and len(V.warns) == 2
    failed += not ok
    print("%-58s %-7s %-7s %s" % ("COMPLIANCE_LEVEL=WARN で ERROR が出ない", "%d/0" % len(V.errors),
                                  "%d/2" % len(V.warns), "PASS" if ok else "**FAIL**"))
    print("-" * 86)
    if failed:
        print("**FAIL %d件**（log/README.md「判断タグ」節が正本）" % failed)
        return 1
    print("全 %d ケース PASS" % (len(CASES) + 1))
    print()
    print("※ 照合できるのは「書かれた事実がプロファイルと合うか」まで（層2）。")
    print("　 その行を選んだ判断が正しかったか（層3）は analyze.py の一貫性表で n レース後に測る（TODO U24）。")
    return 0


def run_retro():
    """過去ログに (b) を後付けで走らせる。事実タグはメモリ上で付け、CSV には書かない（遡及入力しない）。"""
    tp = V._load_tag_profile()
    races = list(csv.DictReader(open(os.path.join(BASE, "races.csv"), encoding="utf-8")))
    preds = list(csv.DictReader(open(os.path.join(BASE, "predictions.csv"), encoding="utf-8")))
    print("■ (b) 距離を未経験と書いたのに 距離帯経験=あり — 過去ログへの後付け実行（書き込みなし）")
    hits, n_race, n_horse = 0, 0, 0
    for r in races:
        path = tp.profile_path(r["date"], r["race_name"])
        prof = tp.parse_profile(path) if os.path.exists(path) else {"horses": {}}
        prows = [dict(p) for p in preds if p["race_id"] == r["race_id"]]
        if not prof["horses"] or not prows:
            print(f"  [対象外] {r['race_id']}: " + ("predictions なし" if not prows else "プロファイル md なし／書式差で読めない"))
            continue
        n_race += 1
        n_horse += len(prows)
        for p in prows:
            f, _ = tp.expected_facts(prof, p["horse_no"], p["horse_name"])
            p["base_breakdown"] = tp.TAG_RE.sub("", p["base_breakdown"]) + " / " + tp.facts_str(f)
        V.errors.clear()
        V.warns.clear()
        V.check_compliance(r["race_id"], dict(r, date=AFTER, result_1st=""), prows, [], [], None)
        for m in V.errors:
            if "(b)" in m:
                hits += 1
                print("  " + m.split("（(b)")[0])
    print(f"→ 対象 {n_race} レース・{n_horse} 頭／検出 {hits} 句")
    return 0


if __name__ == "__main__":
    sys.exit(run_retro() if "--retro" in sys.argv else run_cases())
