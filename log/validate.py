#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ログCSV検証スクリプト（P0-1）。log/ で python3 validate.py を実行。標準ライブラリのみ。

チェック内容（log/README.md の記録原則を機械化）:
  スキーマ / 数値ゲート / 印の頭数(R15) / 積み上げ検算 / 点数×単価(R16)
  ＋ 宣言と実装の突合（帯域・配分タグ ↔ bets、計画総額 ↔ 実購入、as_of ↔ 確定馬場）
  ＋ 印と買い目の整合（消し馬の採用 / 印を付けた馬の不在 / 消し記号の表記ゆれ）
  [ERROR] = ゲートFAIL級。予想・買い目を確定してはいけない状態
  [WARN]  = 記録不備。次の追記時に直す
終了コード: ERRORが1件でもあれば 1、なければ 0
"""
import csv
import os
import re
import sys
from collections import defaultdict

# Windows既定コンソール(cp932)では罫線・✕等でUnicodeEncodeErrorを起こし途中で落ちるため強制UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))

# README記載のバックフィル3レース（数値欠損を例外として容認・集計参考外）
EXEMPT_RACES = {"2026_sakitama_hai", "2026_hakodate_kinen", "2026_radio_nikkei"}

# 予算ガード（log/README.md 記録の原則9）。制定日より前のレースは対象外
RACE_BUDGET_CAP = 3000
BUDGET_GUARD_FROM = "2026-07-27"

EXPECTED_HEADERS = {
    "races.csv": ["race_id", "date", "race_name", "grade", "course", "field_size",
                  "going", "cushion", "pace_score_pre", "pace_flag_pre", "pace_actual",
                  "pace_match", "bias_actual", "result_1st", "result_2nd", "result_3rd",
                  "payout_sanrentan", "payout_sanrenpuku", "notes"],
    "predictions.csv": ["race_id", "horse_no", "horse_name", "mark", "base_score",
                        "base_breakdown", "composite_coef", "additive_total", "r_adj",
                        "final_score", "myomi_score", "popularity", "win_odds",
                        "place_odds_max", "r_value", "finish_pos", "in_place", "notes"],
    "bets.csv": ["race_id", "bet_type", "structure", "points", "unit", "cost",
                 "hit", "return", "notes"],
    "rules_master.csv": ["rule_id", "rule_name", "origin_race", "added_date", "status"],
    "rule_fires.csv": ["race_id", "rule_id", "fired", "followed", "outcome", "notes"],
}

# 予想確定時に全頭必須の数値列（記録原則6）。r_adj / r_value はオッズ未確定なら暫定空欄可
NUM_COLS_REQUIRED = ["base_score", "composite_coef", "additive_total", "final_score", "myomi_score"]
NUM_COLS_ODDS_DEPENDENT = ["r_adj", "r_value"]

VALID_MARKS = {"◎", "○", "▲", "△", "☆", "✕", "x", "-", ""}
SINGLE_MARKS = ["◎", "○", "▲", "△"]  # 各1頭（R15）。✕/xは消しとして同枠

# 消し＝買い目に一切採用しない馬。log/README.md 記録原則4により正規形は "x"
KESHI_MARKS = {"✕", "x", "×", "X"}
CANONICAL_KESHI = "x"
# 買い目に現れることが期待される印（複勝圏以上を想定して付けた印）
BOUGHT_MARKS = {"◎", "○", "▲", "△", "☆"}

# bets.structure から馬番を取り出すためのノイズ除去（"軸1頭" "3着" "（新規5点）" 等）
_NOISE_PATTERNS = [
    re.compile(r"[（(][^）)]*点[^）)]*[）)]"),  # （新規5点）
    re.compile(r"\d+\s*点"),                    # 15点
    re.compile(r"軸\d+頭"),                     # 軸1頭 / 軸2頭
    re.compile(r"\d+\s*着"),                    # 1着 / 2着 / 3着
]


def bet_horses(structure):
    """買い目の structure 文字列から馬番の集合を返す（1〜18のみ採用）。"""
    s = structure or ""
    for pat in _NOISE_PATTERNS:
        s = pat.sub(" ", s)
    return {int(x) for x in re.findall(r"\d+", s) if 1 <= int(x) <= 18}
VALID_OUTCOMES = {"効いた", "逆効果", "中立", "違反して失敗", "違反したが結果OK", ""}
VALID_STATUS = re.compile(r"^(現行|暫定.*|包含済.*|廃止.*)$")

errors, warns = [], []


def err(msg):
    errors.append(msg)


def warn(msg):
    warns.append(msg)


def to_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def tag(text, key):
    """notes から `key=値` を取り出す。区切りは / ／ 空白 読点。無ければ None。"""
    m = re.search(key + r"\s*[=＝:：]\s*([^/／、,\s]+)", text or "")
    return m.group(1).strip() if m else None


def load(name):
    path = os.path.join(BASE, name)
    if not os.path.exists(path):
        err(f"{name}: ファイルが存在しない")
        return None, []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    exp = EXPECTED_HEADERS[name]
    if header != exp:
        missing = [c for c in exp if c not in header]
        extra = [c for c in header if c not in exp]
        detail = []
        if missing:
            detail.append(f"欠損列 {missing}")
        if extra:
            detail.append(f"未知列 {extra}（スキーマ変更ならREADME/validate.pyを同期）")
        if not missing and not extra:
            detail.append("列順が定義と不一致")
        err(f"{name}: ヘッダー不一致 → {' / '.join(detail)}")
    return header, rows


def is_paper(race_row):
    return (race_row.get("notes") or "").strip().lower().startswith("paper")


def main():
    _, races = load("races.csv")
    _, preds = load("predictions.csv")
    _, bets = load("bets.csv")
    _, rules = load("rules_master.csv")
    _, fires = load("rule_fires.csv")

    race_by_id = {}
    for i, r in enumerate(races, 2):
        rid = (r.get("race_id") or "").strip()
        if not rid:
            err(f"races.csv 行{i}: race_id が空")
            continue
        if rid in race_by_id:
            err(f"races.csv 行{i}: race_id 重複 [{rid}]")
        race_by_id[rid] = r
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", r.get("date") or ""):
            warn(f"races.csv [{rid}]: date がISO形式でない ({r.get('date')})")
        if r.get("pace_score_pre") and to_f(r["pace_score_pre"]) is None:
            err(f"races.csv [{rid}]: pace_score_pre が数値でない")
        if (r.get("pace_match") or "") not in ("", "0", "1"):
            err(f"races.csv [{rid}]: pace_match は 0/1/空 のみ ({r.get('pace_match')})")
        # 結果済みなのに配当欠損
        if r.get("result_1st") and not r.get("payout_sanrenpuku"):
            warn(f"races.csv [{rid}]: 結果入力済みだが payout_sanrenpuku が空")
        # ペース密度の併記（検証待ち仮説6）
        if r.get("pace_score_pre") and "密度" not in (r.get("notes") or ""):
            warn(f"races.csv [{rid}]: notes にペース密度の併記なし（仮説6）")

    # ---- predictions ----
    preds_by_race = defaultdict(list)
    for i, p in enumerate(preds, 2):
        rid = (p.get("race_id") or "").strip()
        if rid not in race_by_id:
            err(f"predictions.csv 行{i}: 未知の race_id [{rid}]")
            continue
        preds_by_race[rid].append(p)
        mark = (p.get("mark") or "").strip()
        if mark not in VALID_MARKS:
            err(f"predictions.csv [{rid} #{p.get('horse_no')}]: 不正な印 '{mark}'")
        if (p.get("in_place") or "") not in ("", "0", "1"):
            err(f"predictions.csv [{rid} #{p.get('horse_no')}]: in_place は 0/1/空のみ")
        fin = to_f(p.get("finish_pos"))
        inp = p.get("in_place")
        if fin is not None and inp in ("0", "1"):
            expect = "1" if fin <= 3 else "0"
            if inp != expect:
                err(f"predictions.csv [{rid} #{p.get('horse_no')}]: finish_pos={int(fin)} と in_place={inp} が矛盾")
        # 数値列ゲート（バックフィル3レースは例外）
        if rid not in EXEMPT_RACES:
            for c in NUM_COLS_REQUIRED:
                if to_f(p.get(c)) is None:
                    err(f"predictions.csv [{rid} #{p.get('horse_no')}]: 必須数値列 {c} が未記入（ゲートFAIL・記録原則6）")
            for c in NUM_COLS_ODDS_DEPENDENT:
                if to_f(p.get(c)) is None:
                    if to_f(p.get("win_odds")) is not None:
                        err(f"predictions.csv [{rid} #{p.get('horse_no')}]: オッズ記入済みなのに {c} が空（暫定確定の解除漏れ）")
                    else:
                        warn(f"predictions.csv [{rid} #{p.get('horse_no')}]: {c} 空欄（オッズ未確定の暫定として容認・確定後に記入）")
            # 積み上げ式の検算: base×coef+additive ≒ final（許容±0.5）
            b, cf, ad, fs = (to_f(p.get(k)) for k in ("base_score", "composite_coef", "additive_total", "final_score"))
            if None not in (b, cf, ad, fs):
                calc = b * cf + ad
                if abs(calc - fs) > 1.0:  # 小数1位記録・丸め規約（評価ルール運用上の注意）
                    msg = f"predictions.csv [{rid} #{p.get('horse_no')}]: 検算不一致 {b}×{cf}+{ad}={calc:.1f} ≠ final {fs}"
                    if "calc_gap" in (p.get("notes") or ""):
                        warn(msg + "（calc_gapタグ済・監査痕跡として保持）")
                    else:
                        err(msg + "（修正するか notes に calc_gap タグを追記）")
            # 合成係数キャップ 0.7-1.5
            if cf is not None and not (0.7 <= cf <= 1.5):
                err(f"predictions.csv [{rid} #{p.get('horse_no')}]: composite_coef={cf} がクリップ範囲(0.7-1.5)外")

    # 印の頭数制約（R15）と全頭記録（記録原則5）
    for rid, rows in preds_by_race.items():
        if rid in EXEMPT_RACES:
            continue  # R15(2026-07-03)制定前のバックフィル分は対象外
        cnt = defaultdict(int)
        for p in rows:
            cnt[(p.get("mark") or "").strip()] += 1
        for m in SINGLE_MARKS:
            if cnt[m] > 1:
                err(f"predictions.csv [{rid}]: {m} が{cnt[m]}頭（R15: 各1頭）")
        if cnt["☆"] > 2:
            err(f"predictions.csv [{rid}]: ☆ が{cnt['☆']}頭（R15: 最大2頭）")
        keshi_n = sum(cnt[m] for m in KESHI_MARKS)
        if keshi_n > 1:
            warn(f"predictions.csv [{rid}]: 消しが{keshi_n}頭（指示は各1頭・意図的なら容認）")
        for m in KESHI_MARKS - {CANONICAL_KESHI}:
            if cnt[m]:
                warn(f"predictions.csv [{rid}]: 消し記号 '{m}' が{cnt[m]}件"
                     f"（log/README.md 記録原則4の正規形は '{CANONICAL_KESHI}'・表記を揃える）")
        fs = to_f(race_by_id[rid].get("field_size"))
        if fs is not None and len(rows) < fs and rid not in EXEMPT_RACES:
            err(f"predictions.csv [{rid}]: 記録{len(rows)}頭 < 出走{int(fs)}頭（記録原則5: 全頭記録）")

    # ---- bets ----
    bets_by_race = defaultdict(list)
    for i, b in enumerate(bets, 2):
        rid = (b.get("race_id") or "").strip()
        if rid not in race_by_id:
            err(f"bets.csv 行{i}: 未知の race_id [{rid}]")
            continue
        bets_by_race[rid].append(b)
        c = to_f(b.get("cost"))
        if c is not None and c == 0:
            err(f"bets.csv [{rid}]: cost=0 行は禁止（ペーパーは bets 行を作らない・記録原則8）")
        if is_paper(race_by_id[rid]):
            err(f"bets.csv [{rid}]: paperレースに bets 行がある（記録原則8）")
        if (b.get("hit") or "") not in ("", "0", "1"):
            err(f"bets.csv [{rid}]: hit は 0/1/空のみ")
        if b.get("hit") == "1" and to_f(b.get("return")) is None:
            warn(f"bets.csv [{rid}]: 的中なのに return 未入力")
        pts, unit = to_f(b.get("points")), to_f(b.get("unit"))
        if None not in (pts, unit, c) and abs(pts * unit - c) > 0.01:
            err(f"bets.csv [{rid}]: 点数×単価≠金額 ({pts}×{unit}≠{c}) R16検算")

    # ---- 印と買い目の整合 ----
    # 『競馬予想_評価ルール.md』第8項の区分は 軸/対抗/押さえ/消し。消し＝買い目に採用しない馬。
    # R09（R<1.5は頭固定回避のみで複勝圏除外にしない）で紐に残す馬は「押さえ＝△」であり消しではない。
    # 2026_ibis_sd で ✕ を付けた馬を三連複の相手に採用する不整合が発生したため機械検出に移した。
    for rid, rows in preds_by_race.items():
        if rid in EXEMPT_RACES:
            continue
        rbets = bets_by_race.get(rid, [])
        if not rbets:
            continue  # ペーパー等、買い目のないレースは対象外
        bought = set()
        for b in rbets:
            bought |= bet_horses(b.get("structure"))
        if not bought:
            warn(f"bets.csv [{rid}]: structure から馬番を読み取れない（印との整合チェックを実施できず）")
            continue
        for p in rows:
            mark = (p.get("mark") or "").strip()
            no = to_f(p.get("horse_no"))
            if no is None:
                continue
            no = int(no)
            if mark in KESHI_MARKS and no in bought:
                err(f"predictions.csv [{rid} #{no} {p.get('horse_name')}]: mark='{mark}'（消し）"
                    f"だが買い目に採用されている（消しは買い目不採用の馬に限る。"
                    f"複勝圏に残すならR09に従い押さえ='△'または無印）")
            if mark in BOUGHT_MARKS and no not in bought:
                warn(f"predictions.csv [{rid} #{no} {p.get('horse_name')}]: mark='{mark}'"
                     f"だが全券種の買い目に不在（印と買い目の対応を確認）")

    # ---- 宣言と実装の突合（races.notes の宣言タグ ↔ bets） ----
    # 宣言タグ書式（log/README.md が正本）:
    #   band=中 / 荒れ度=5 / 券種=三連複+三連単F / 堅実穴=40:60 / 予算=2500 / 計画総額=2400
    # 乖離を許容する場合は notes に 乖離理由=... を書く（無断の乖離は ERROR）
    for rid, r in race_by_id.items():
        notes = r.get("notes") or ""
        rbets = bets_by_race.get(rid, [])
        decided = bool(r.get("result_1st"))
        band = tag(notes, "band")
        plan_total = to_f(tag(notes, "計画総額"))
        reason = tag(notes, "乖離理由")

        # (1) 未決レースで買い目があるのに宣言タグが無い＝宣言行なしの買い目（確定禁止）
        if rbets and not decided and (band is None or plan_total is None):
            err(f"races.csv [{rid}]: 買い目があるのに宣言タグ不足（band= / 計画総額= が必要・宣言行なしの買い目は確定禁止）")

        if not rbets:
            continue
        actual_total = sum(to_f(b.get("cost")) or 0 for b in rbets)

        # (2) 券種構成：宣言と実購入の集合差（券種の無断欠落＝過去2レースの失敗パターン）
        kenshu = tag(notes, "券種")
        if kenshu:
            declared = {k.strip() for k in kenshu.split("+") if k.strip()}
            actual = {(b.get("bet_type") or "").strip() for b in rbets}
            missing = declared - actual
            extra = actual - declared
            if missing or extra:
                detail = (f"未購入 {sorted(missing)}" if missing else "") + \
                         (" / " if missing and extra else "") + \
                         (f"宣言外 {sorted(extra)}" if extra else "")
                msg = f"races.csv [{rid}]: 宣言券種と実購入が不一致 → {detail}"
                (warn if reason else err)(msg + ("（乖離理由あり）" if reason else "（乖離理由= を notes に記載するか買い目を直す）"))

        # (3) 計画総額 ↔ 実購入額
        if plan_total is not None and abs(plan_total - actual_total) > 0.01:
            msg = f"races.csv [{rid}]: 計画総額 {plan_total:.0f}円 ≠ 実購入 {actual_total:.0f}円"
            (warn if reason else err)(msg + ("（乖離理由あり）" if reason else "（乖離理由= を notes に記載する）"))

        # (4) 予算超過
        budget = to_f(tag(notes, "予算"))
        if budget is not None and actual_total > budget + 0.01:
            err(f"races.csv [{rid}]: 実購入 {actual_total:.0f}円 が宣言予算 {budget:.0f}円 を超過")

        # (4-b) 予算ガード：1レース上限3,000円（2026-07-27制定・制定前レースは対象外）
        if (r.get("date") or "") >= BUDGET_GUARD_FROM:
            if actual_total > RACE_BUDGET_CAP + 0.01:
                err(f"races.csv [{rid}]: 実購入 {actual_total:.0f}円 が1レース上限 {RACE_BUDGET_CAP}円 を超過（予算ガード）")
            if budget is not None and budget > RACE_BUDGET_CAP + 0.01:
                err(f"races.csv [{rid}]: 宣言予算 {budget:.0f}円 が1レース上限 {RACE_BUDGET_CAP}円 を超過（予算ガード）")

        # (5) 堅実:穴の配分（bets.notes の side=堅実/穴 タグが全行に揃っている場合のみ判定）
        ratio = tag(notes, "堅実穴")
        sides = [tag(b.get("notes") or "", "side") for b in rbets]
        if ratio and ":" in ratio and all(x in ("堅実", "穴") for x in sides):
            try:
                d_kata, d_ana = (float(x) for x in ratio.split(":", 1))
            except ValueError:
                d_kata = d_ana = None
            if d_kata is not None and actual_total > 0 and (d_kata + d_ana) > 0:
                a_kata = sum((to_f(b.get("cost")) or 0) for b, s_ in zip(rbets, sides) if s_ == "堅実")
                act_pct = a_kata / actual_total * 100
                dec_pct = d_kata / (d_kata + d_ana) * 100
                if abs(act_pct - dec_pct) > 10:
                    warn(f"races.csv [{rid}]: 堅実:穴の宣言 {dec_pct:.0f}% と実配分 {act_pct:.0f}% が10ポイント超乖離")

        # (6) 馬場の確定表記と notes の未確定表記の食い違い
        if r.get("going") and re.search(r"(未確定|暫定)", notes) and not decided:
            warn(f"races.csv [{rid}]: going={r.get('going')} を確定値で記録しつつ notes に未確定/暫定の記述あり（どちらかに揃える）")

    # ---- rules_master ----
    rule_ids = set()
    for i, r in enumerate(rules, 2):
        rid = (r.get("rule_id") or "").strip()
        if rid in rule_ids:
            err(f"rules_master.csv 行{i}: rule_id 重複 [{rid}]")
        rule_ids.add(rid)
        if not VALID_STATUS.match((r.get("status") or "").strip()):
            err(f"rules_master.csv [{rid}]: 不正な status '{r.get('status')}'")

    # ---- rule_fires ----
    for i, f_ in enumerate(fires, 2):
        rid, rl = (f_.get("race_id") or "").strip(), (f_.get("rule_id") or "").strip()
        if rid not in race_by_id:
            err(f"rule_fires.csv 行{i}: 未知の race_id [{rid}]")
        if rl not in rule_ids:
            err(f"rule_fires.csv 行{i}: 台帳にない rule_id [{rl}]")
        for c in ("fired", "followed"):
            if (f_.get(c) or "") not in ("", "0", "1"):
                err(f"rule_fires.csv 行{i}: {c} は 0/1/空のみ")
        if (f_.get("outcome") or "") not in VALID_OUTCOMES:
            warn(f"rule_fires.csv 行{i}: 未知の outcome '{f_.get('outcome')}'")
        # 鮮度: 参照した馬場宣言(as_of=)が確定馬場と食い違ったまま残っていないか
        if rid in race_by_id:
            as_of = tag(f_.get("notes") or "", "as_of")
            going = (race_by_id[rid].get("going") or "").strip()
            # as_of は「重(暫定)」「良(確定)」のように但し書きを伴うことがある。
            # 比較対象は馬場そのものなので、先頭の馬場語だけを取り出して突合する
            m_going = re.match(r"\s*(不良|稍重|重|良)", as_of or "")
            as_of_going = m_going.group(1) if m_going else as_of
            if as_of and going and as_of_going != going:
                warn(f"rule_fires.csv [{rid} {rl}]: as_of={as_of} が確定馬場 {going} と不一致（旧前提のnotesが残存）")
        # ゲートの本丸: followed=0 のまま買おうとしていないか。
        # 結果確定済みレースの違反は「記録された過去」であり outcome 列で追跡済み → スキップ
        if (f_.get("fired") == "1" and f_.get("followed") == "0"
                and bets_by_race.get(rid)
                and rid in race_by_id
                and not race_by_id[rid].get("result_1st")):
            err(f"rule_fires.csv [{rid} {rl}]: followed=0 のまま bets が存在（未決レース・ゲート突破）")

    # ---- report ----
    print("=" * 60)
    print("■ validate.py 検証結果")
    print("=" * 60)
    for m in errors:
        print(f"[ERROR] {m}")
    for m in warns:
        print(f"[WARN]  {m}")
    print("-" * 60)
    print(f"ERROR {len(errors)}件 / WARN {len(warns)}件"
          + ("" if not EXEMPT_RACES else f"（バックフィル例外: {len(EXEMPT_RACES)}レース）"))
    if errors:
        print("→ ゲートFAIL級の不整合あり。修正するまで新規の印・買い目を確定しない")
        sys.exit(1)
    print("→ OK（構造チェック通過）")
    sys.exit(0)


if __name__ == "__main__":
    main()
