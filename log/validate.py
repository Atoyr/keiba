#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ログCSV検証スクリプト（P0-1）。log/ で python3 validate.py を実行。標準ライブラリのみ。

チェック内容（log/README.md の記録原則を機械化）:
  スキーマ / 数値ゲート / 印の頭数(R15) / 積み上げ検算 / 点数×単価(R16)
  ＋ 宣言と実装の突合（帯域・配分タグ ↔ bets、計画総額 ↔ 実購入、as_of ↔ 確定馬場）
  ＋ 印と買い目の整合（消し馬の採用 / 印を付けた馬の不在 / 消し記号の表記ゆれ）
  ＋ 係数・加算層の分解記録（coef_breakdown の積 ↔ composite_coef、
     additive_breakdown の和 ↔ additive_total。A-1・2026-08-03導入）
  ＋ rule_fires の capture 語彙と rules_master の direction 整合（A-2・同日導入）
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

# 分解記録スキーマ（A-1）と capture 語彙（A-2）の適用開始日。
# 制定日より前のレースは対象外＝遡及入力を強制しない（強制すると再構成＝捏造になる）
SCHEMA_V2_FROM = "2026-08-03"

# 適性タグ（A-3）の適用開始日。`5走距離帯=` は 2026-08-09 に記録原則として導入されたが
# 「運用で担保する」としていた結果、以降3レース（CBC賞・中京記念・札幌記念）すべてで
# 記入されず 187行中0件だった。log/README.md「チェックの置き場所の原則」に従い
# 機械判定できるものを validate.py へ移す（札幌記念2026の #設計 で決定）。
# 遡及は強制しない＝制定日より前のレースは対象外
SCHEMA_V3_FROM = "2026-08-17"
APTITUDE_TAGS = ("5走距離帯=", "父=", "母父=")

EXPECTED_HEADERS = {
    "races.csv": ["race_id", "date", "race_name", "grade", "course", "field_size",
                  "going", "cushion", "pace_score_pre", "pace_flag_pre", "pace_actual",
                  "pace_match", "bias_actual", "result_1st", "result_2nd", "result_3rd",
                  "payout_sanrentan", "payout_sanrenpuku", "notes"],
    "predictions.csv": ["race_id", "horse_no", "horse_name", "mark", "base_score",
                        "base_breakdown", "composite_coef", "coef_breakdown",
                        "additive_total", "additive_breakdown", "r_adj",
                        "final_score", "myomi_score", "popularity", "win_odds",
                        "place_odds_max", "r_value", "finish_pos", "in_place",
                        "last_3f", "corner4_pos", "notes"],
    "bets.csv": ["race_id", "bet_type", "structure", "points", "unit", "cost",
                 "hit", "return", "notes"],
    "rules_master.csv": ["rule_id", "rule_name", "origin_race", "added_date", "status",
                         "direction", "scope"],
    "rule_fires.csv": ["race_id", "rule_id", "fired", "followed", "capture", "outcome",
                       "notes"],
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

# --- A-2: rule_fires.capture（予測方向の当否）と rules_master.direction ---
# outcome が損益寄与しか測れず「中立」に68%が滞留してR19の降格条件が発動しえなかった
# 構造への対策（P1-3）。capture は損益と独立に、結果に照らした向きの当否を記録する。
VALID_CAPTURE = {"的中", "空振り", "逆行", "方向なし", ""}
VALID_DIRECTION = {"上向き", "下向き", "双方向", "手続き"}

# --- T9: scope（そのルールをチェックする工程）。ゲート②の対象行判定を機械化する ---
# 指示v2 工程11は「買い目・配分・頭固定に作用する行＋status=暫定の全行」をゲート②の
# 対象と定義しているが、どの行が該当するかは毎回人が判断していた。scope で固定する。
VALID_SCOPE = {"印確定前", "買い目", "両方", "振り返り", "照会"}
GATE2_SCOPES = {"買い目", "両方"}  # ＋ status=暫定 の全行（指示v2の定義）
# 馬を指さないルール（検算・記録・工程順序・シナリオ制約）は capture の判定対象外
NO_DIRECTION = "手続き"

# --- A-1: 係数・加算層の分解記録 ---
# coef_breakdown   例) 枠0.85;適性1.10;バイアス1.00   → 積が composite_coef
# additive_breakdown 例) R+2.0;騎手+1.0;馬場-1.0      → 和が additive_total
# 加算層が空なら "なし"（additive_total=0 のときのみ）
COEF_ITEM = re.compile(r"^\s*(.*?)\s*([0-9]*\.?[0-9]+)\s*$")
ADD_ITEM = re.compile(r"^\s*(.*?)\s*([+-][0-9]*\.?[0-9]+)\s*$")
BREAKDOWN_SEP = re.compile(r"[;；]")


def parse_breakdown(text, pattern):
    """`名前値;名前値` を [(名前, 値)] に。1項でも解釈できなければ None を返す。"""
    items = [s for s in BREAKDOWN_SEP.split(text or "") if s.strip()]
    if not items:
        return None
    out = []
    for it in items:
        m = pattern.match(it)
        if not m or not m.group(1):
            return None
        out.append((m.group(1), float(m.group(2))))
    return out

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

        # --- T1: 結果側の観測列（last_3f / corner4_pos）---
        # jra_result.py が全頭ぶん取得済みの値。「着順と上がりの乖離＝展開不利の好走」
        # （評価ルール第169項の次走加点材料）を機械抽出するための入力で、
        # これが無いと不利の反映が主観メモ頼みの一本経路になる。
        # 競走中止・除外は空欄で可（finish_pos も空欄になるため条件から外れる）
        if fin is not None and rid in race_by_id \
                and (race_by_id[rid].get("date") or "") >= SCHEMA_V2_FROM:
            for c, label in (("last_3f", "上がり3F"), ("corner4_pos", "4角通過順位")):
                if not (p.get(c) or "").strip():
                    warn(f"predictions.csv [{rid} #{p.get('horse_no')}]: 着順記入済みだが {c}（{label}）が空"
                         "（tools/jra_result.py の出力から転記する）")
        l3 = to_f(p.get("last_3f"))
        if l3 is not None and not (30.0 <= l3 <= 50.0):
            err(f"predictions.csv [{rid} #{p.get('horse_no')}]: last_3f={l3} が想定範囲(30.0-50.0秒)外")
        c4 = to_f(p.get("corner4_pos"))
        if c4 is not None and not (1 <= c4 <= 18):
            err(f"predictions.csv [{rid} #{p.get('horse_no')}]: corner4_pos={c4} が想定範囲(1-18)外")
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

            # --- A-1: 係数・加算層の分解記録 ---
            # 「合成後の1値」しか残らないと、どの項が最終点を動かしたかを事後に復元できず、
            # Phase 3 の係数アブレーション（1着馬の最終点順位が捉えられない課題B）が
            # 実行不能になる。アイビスSD2026で⑥に適用した内枠ミスマッチ0.85が
            # reflection md にしか残っていない事故が起点。
            new_schema = (race_by_id[rid].get("date") or "") >= SCHEMA_V2_FROM
            cb, ab = (p.get("coef_breakdown") or "").strip(), (p.get("additive_breakdown") or "").strip()
            tagname = f"predictions.csv [{rid} #{p.get('horse_no')}]"
            if cf is not None:
                if not cb:
                    if new_schema:
                        warn(f"{tagname}: coef_breakdown 未記入"
                             "（合成係数の内訳を 枠0.85;適性1.10 形式で。アブレーション不能になる）")
                else:
                    items = parse_breakdown(cb, COEF_ITEM)
                    if items is None:
                        err(f"{tagname}: coef_breakdown を解釈できない '{cb}'"
                            "（書式は 名前値;名前値 例 枠0.85;適性1.10;バイアス1.00）")
                    else:
                        prod = 1.0
                        for _, v in items:
                            prod *= v
                        # 合成係数はクリップ後の値を記録するため、クリップに当たった行は
                        # 積と一致しない。その場合のみ notes の clip タグで容認する
                        if abs(prod - cf) > 0.02 and "clip" not in (p.get("notes") or ""):
                            err(f"{tagname}: coef_breakdown の積 {prod:.3f} ≠ composite_coef {cf}"
                                "（内訳を直すか、クリップに当たったなら notes に clip タグ）")
            if ad is not None:
                if not ab:
                    if new_schema:
                        warn(f"{tagname}: additive_breakdown 未記入"
                             "（加算層の内訳を R+2.0;騎手+1.0 形式で。加算層なしなら なし と書く）")
                elif ab == "なし":
                    if abs(ad) > 0.01:
                        err(f"{tagname}: additive_breakdown='なし' だが additive_total={ad}")
                else:
                    items = parse_breakdown(ab, ADD_ITEM)
                    if items is None:
                        err(f"{tagname}: additive_breakdown を解釈できない '{ab}'"
                            "（書式は 名前±値;名前±値 例 R+2.0;騎手+1.0;馬場-1.0。符号は必須）")
                    else:
                        s = sum(v for _, v in items)
                        if abs(s - ad) > 0.1:
                            err(f"{tagname}: additive_breakdown の和 {s:+.1f} ≠ additive_total {ad}")
                        # r_adj は加算層の一項なので、内訳の R 項と一致していなければ整合しない
                        r_adj = to_f(p.get("r_adj"))
                        r_items = [v for k, v in items if k.startswith("R")]
                        if r_adj is not None and r_items and abs(sum(r_items) - r_adj) > 0.1:
                            warn(f"{tagname}: additive_breakdown の R項 {sum(r_items):+.1f} ≠ r_adj {r_adj}")

            # --- A-3: 適性タグ（5走距離帯・父・母父）---
            # 未経験の距離帯・コースをどう扱うかは血統でしか埋められないが、その判定入力が
            # predictions.csv に存在しないため、母父ルールを作っても検証不能になる
            # （仮説17の教訓＝入力が実在するかを先に確認する）。R38 の発火判定と
            # 仮説20-d・仮説22 の集計入力を兼ねる。値は増やさず base_breakdown に併記する
            if (race_by_id[rid].get("date") or "") >= SCHEMA_V3_FROM:
                bb = (p.get("base_breakdown") or "")
                miss = [x for x in APTITUDE_TAGS if x not in bb]
                if miss:
                    warn(f"{tagname}: base_breakdown に適性タグ {miss} がない"
                         "（書式は log/README.md の base_breakdown 節。"
                         "5走距離帯= は R38 の未経験判定と仮説20-d、父=/母父= は仮説22 の集計入力）")

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

        # (7) 一次ソース未確認のまま確定列に入った going の検出
        #     関屋記念2026で going=重 と記録したがJRA公式の発表馬場は不良だった事故への対策。
        #     notes に取得失敗系の語が残っているレースは、馬場を何で確定したかを
        #     `馬場ソース=` タグで明示させる（タグがあれば確認済みとみなす）。
        if r.get("going") and not tag(notes, "馬場ソース") \
                and re.search(r"(取得失敗|ブロック|据え置き|未確認)", notes):
            warn(f"races.csv [{rid}]: going={r.get('going')} が一次ソース未確認の疑い"
                 "（notesに取得失敗/ブロック等の記述あり）。"
                 "tools/jra_result.py で発表馬場を確認し notes に 馬場ソース= を付ける")

    # ---- rules_master ----
    rule_ids = set()
    rule_dir = {}
    rule_scope = {}
    gate2_rules = set()
    for i, r in enumerate(rules, 2):
        rid = (r.get("rule_id") or "").strip()
        if rid in rule_ids:
            err(f"rules_master.csv 行{i}: rule_id 重複 [{rid}]")
        rule_ids.add(rid)
        if not VALID_STATUS.match((r.get("status") or "").strip()):
            err(f"rules_master.csv [{rid}]: 不正な status '{r.get('status')}'")
        d = (r.get("direction") or "").strip()
        rule_dir[rid] = d
        if d not in VALID_DIRECTION:
            err(f"rules_master.csv [{rid}]: 不正な direction '{d}'"
                f"（{'/'.join(sorted(VALID_DIRECTION))} のいずれか。capture 判定の入力・A-2）")
        sc = (r.get("scope") or "").strip()
        if sc not in VALID_SCOPE:
            err(f"rules_master.csv [{rid}]: 不正な scope '{sc}'"
                f"（{'/'.join(sorted(VALID_SCOPE))} のいずれか。ゲート②の対象行判定・T9）")
        st = (r.get("status") or "").strip()
        if st.startswith(("現行", "暫定")):
            rule_scope[rid] = sc
            if sc in GATE2_SCOPES or st.startswith("暫定"):
                gate2_rules.add(rid)

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
        # --- A-2: capture（予測方向の当否）。outcome（損益寄与）と独立の第2軸 ---
        cap = (f_.get("capture") or "").strip()
        if cap not in VALID_CAPTURE:
            err(f"rule_fires.csv 行{i}: 不正な capture '{cap}'"
                f"（{'/'.join(x for x in sorted(VALID_CAPTURE) if x)} のいずれか）")
        d = rule_dir.get(rl, "")
        if d == NO_DIRECTION and cap and cap != "方向なし":
            warn(f"rule_fires.csv [{rid} {rl}]: direction={NO_DIRECTION} のルールに capture='{cap}'"
                 "（馬を指さないルールは capture=方向なし。判定は遵守率で行う）")
        if d and d != NO_DIRECTION and f_.get("fired") == "1" and not cap \
                and rid in race_by_id and race_by_id[rid].get("result_1st") \
                and (race_by_id[rid].get("date") or "") >= SCHEMA_V2_FROM:
            warn(f"rule_fires.csv [{rid} {rl}]: 結果確定済み・direction={d} なのに capture 未記入"
                 "（R19の降格/昇格判定の入力・振り返りV4で埋める）")
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

    # ---- ゲート②の網羅チェック（T9・scope列の実効化） ----
    # 指示v2 工程11：買い目確定直前に「買い目・配分・頭固定に作用する行＋status=暫定の全行」を
    # 再チェックする。どの行が該当するかを scope 列で固定し、rule_fires に記録が無い行を検出する。
    # 買い目のあるレース（＝実際に確定まで進んだレース）だけを対象にする。
    fired_by_race = defaultdict(set)
    for f_ in fires:
        fired_by_race[(f_.get("race_id") or "").strip()].add((f_.get("rule_id") or "").strip())
    for rid in sorted(bets_by_race):
        if rid in EXEMPT_RACES or rid not in race_by_id:
            continue
        # ルールは追加日以降にしか適用できない。レース日より後に生まれた行は対象外
        race_date = (race_by_id[rid].get("date") or "")
        target = {r["rule_id"].strip() for r in rules
                  if r["rule_id"].strip() in gate2_rules
                  and (r.get("added_date") or "9999") <= race_date}
        missing = sorted(target - fired_by_race.get(rid, set()))
        if missing:
            warn(f"rule_fires.csv [{rid}]: ゲート②の対象ルールに記録が無い {missing}"
                 "（scope=買い目/両方 と status=暫定 の行は買い目確定直前に再評価して記録する・指示v2工程11）")

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
