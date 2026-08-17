#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""買い目構成ゲート（設計セッション 2026-08-17 新設）。log/ で python3 gate.py を実行。

rules_master の買い目系ルールのうち、bets.structure と predictions の数値から
機械判定できる条文を買い目確定前に一括判定する（チェックの置き場所の原則）:

  R23  荒帯域で全券種が同一1頭の複勝圏に依存する構成の禁止（band=荒のみ・簡易判定）
  R24  妙味閾値到達馬（荒帯域4以上・他5以上）の紐は全券種で統一
  R31  軸抜けで全券種が同時失効する構成の検出（軸選定理由 or 併設券種を要求）
  R32  券種間の紐リスト突合（欠落には 欠落理由= の記録を要求）
  R35  R<4.0 の☆は全券種の3着紐に保全（＋predictions.notes にR35根拠）
  R36  final上位2頭の直積を持つ券種が1つ以上あること
  R37  軸を含まない紐同士のペアが全券種合計2点以上（＋軸と人気同層に固めない）

適用対象は APPLY_FROM（2026-08-17）以降のレース。それ以前は --race 指定時のみ
参考表示する（遡及裁定はしない・R17）。structure の解釈はヒューリスティックであり、
読めない書式は「判定不能」と明示して人間судへ返す（推測でPASSにしない）。
本スクリプトのPASSは rule_fires の記録を置き換えない——LLM側の仕事は裁量ルールの
判定と、ここでFAILした行の理由記録に縮小される。

  [GATE-FAIL] = 該当条文の違反状態。買い目を確定しない
  [GATE-WARN] = 記録不備・注意喚起
終了コード: 適用対象レースに GATE-FAIL が1件でもあれば 1
"""
import csv
import os
import re
import sys
from collections import defaultdict
from itertools import combinations

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
APPLY_FROM = "2026-08-17"  # 制定日。以前のレースは --race 指定時のみ参考表示

_NOISE = [re.compile(r"[（(][^）)]*点[^）)]*[）)]"), re.compile(r"\d+\s*点"),
          re.compile(r"軸\d+頭"), re.compile(r"\d+\s*着")]
AXIS_RE = re.compile(r"軸\d*頭?\s*[（(]\s*([\d・、,\s]+?)\s*[）)]")
PARTNER_RE = re.compile(r"相手\d*頭?\s*[（(]\s*([\d\-・、,\s]+?)\s*[）)]")
PAIR_TOKEN = re.compile(r"^\s*(\d{1,2})-(\d{1,2})\s*$")


def load(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def nums(text):
    return {int(x) for x in re.findall(r"\d+", text or "") if 1 <= int(x) <= 18}


def horses_of(structure):
    s = structure or ""
    for pat in _NOISE:
        s = pat.sub(" ", s)
    return nums(s)


def parse_structure(structure):
    """structure を解釈して (pairs, axis, partners, horses, parseable) を返す。
    pairs   : 「a-b/c-d/…」形式の明示ペア（frozensetのリスト・それ以外は None）
    axis    : 「軸1頭(3)流し」等の軸馬集合（無ければ空）
    partners: 「相手6頭(…)」の相手集合（無ければ空）
    """
    s = (structure or "").strip()
    tokens = [t for t in s.split("/") if t.strip()]
    if tokens and all(PAIR_TOKEN.match(t) for t in tokens):
        pairs = [frozenset({int(m.group(1)), int(m.group(2))})
                 for m in (PAIR_TOKEN.match(t) for t in tokens)]
        pairs = [p for p in pairs if len(p) == 2 and all(1 <= x <= 18 for x in p)]
        hs = set().union(*pairs) if pairs else set()
        return pairs, set(), set(), hs, True
    axis = nums(AXIS_RE.search(s).group(1)) if AXIS_RE.search(s) else set()
    partners = nums(PARTNER_RE.search(s).group(1)) if PARTNER_RE.search(s) else set()
    parseable = bool(axis or partners)
    return None, axis, partners, horses_of(s), parseable


def co_pairs(row):
    """その券種の同一買い目内で同居しうる馬番ペアの集合（R36用・楽観側の近似）。"""
    pairs, axis, partners, horses, _ = row["_p"]
    if pairs is not None:
        return set(pairs)
    pool = (axis | partners) if (axis or partners) else horses
    return {frozenset(c) for c in combinations(sorted(pool), 2)}


def required_horses(row):
    """その券種の全買い目が的中に必要とする馬（軸依存の検出・保守側の近似）。"""
    pairs, axis, partners, horses, parseable = row["_p"]
    if axis:
        return set(axis) if len(axis) == 1 else set()  # 軸2頭流しは片軸抜けでも残らないため両方必要
    if pairs:
        common = set(pairs[0])
        for p in pairs[1:]:
            common &= p
        return common
    return set()


def main():
    only = None
    if "--race" in sys.argv:
        only = sys.argv[sys.argv.index("--race") + 1]

    races = {r["race_id"]: r for r in load("races.csv")}
    preds = defaultdict(list)
    for p in load("predictions.csv"):
        preds[p["race_id"]].append(p)
    bets = defaultdict(list)
    for b in load("bets.csv"):
        bets[b["race_id"]].append(b)

    fails = warns = 0
    targets = [rid for rid, r in races.items()
               if (only and rid == only) or (not only and (r.get("date") or "") >= APPLY_FROM)]
    if not targets:
        print(f"買い目構成ゲート: 適用対象レースなし（{APPLY_FROM}以降が対象。"
              "過去レースは --race <race_id> で参考表示）")
        return 0

    for rid in targets:
        r = races[rid]
        enforced = (r.get("date") or "") >= APPLY_FROM
        rows = bets.get(rid, [])
        ps = preds.get(rid, [])
        mode = "適用" if enforced else "参考（制定日前・裁定しない）"
        print(f"\n=== {rid} [{mode}] ===")
        if not rows:
            print("  買い目なし（paper等）→ 対象外")
            continue
        for b in rows:
            b["_p"] = parse_structure(b.get("structure"))
        unparsed = [b for b in rows if not b["_p"][4] and not b["_p"][3]]
        for b in unparsed:
            print(f"  [GATE-WARN] structure を解釈できない: 「{b.get('structure')}」→ 該当行は判定不能（人間が判定する）")
            warns += 1

        notes_all = (r.get("notes") or "") + " " + " ".join(b.get("notes") or "" for b in rows)
        band = (re.search(r"band=(\S+)", r.get("notes") or "") or [None, ""])[1]
        pop = {int(to_f(p["horse_no"])): to_f(p.get("popularity")) for p in ps if to_f(p.get("horse_no"))}
        final = {int(to_f(p["horse_no"])): to_f(p.get("final_score")) for p in ps if to_f(p.get("horse_no"))}
        myomi = {int(to_f(p["horse_no"])): to_f(p.get("myomi_score")) for p in ps if to_f(p.get("horse_no"))}

        def report(ok, rule, msg):
            nonlocal fails, warns
            if ok is True:
                print(f"  [PASS] {rule}: {msg}")
            elif ok is False:
                print(f"  [GATE-FAIL] {rule}: {msg}")
                fails += enforced
            else:
                print(f"  [GATE-WARN] {rule}: {msg}")
                warns += 1

        # --- R24 / R32: 券種間の紐突合 ---
        sets = {i: b["_p"][3] for i, b in enumerate(rows) if b["_p"][3]}
        union = set().union(*sets.values()) if sets else set()
        thr = 4.0 if band == "荒" else 5.0
        miss24, miss32 = [], []
        for i, b in enumerate(rows):
            if i not in sets:
                continue
            for h in sorted(union - sets[i]):
                (miss24 if (myomi.get(h) or 0) >= thr else miss32).append((h, b.get("bet_type")))
        has_reason = "欠落理由=" in notes_all
        if miss24:
            report(has_reason and None, "R24",
                   f"妙味{thr:g}以上の馬が券種間で欠落 {miss24}" + ("（欠落理由あり）" if has_reason else "（欠落理由なし）"))
        if miss32 and not miss24:
            report(None if has_reason else False, "R32",
                   f"紐が券種間で不一致 {miss32}" + ("（欠落理由あり→WARN止まり）" if has_reason else "（欠落理由の記録がない）"))
        if not miss24 and not miss32 and sets:
            report(True, "R24/R32", "全券種の紐リストが一致")

        # --- R35: R<4.0 の☆の3着紐保全 ---
        for p in ps:
            if (p.get("mark") or "").strip() != "☆":
                continue
            no = int(to_f(p["horse_no"])); rv = to_f(p.get("r_value"))
            if rv is None or rv >= 4.0:
                continue
            everywhere = all(no in s for s in sets.values()) if sets else False
            tagged = "R35" in (p.get("notes") or "")
            report(everywhere and tagged, "R35",
                   f"☆#{no}（R={rv:g}<4.0）全券種保全={'○' if everywhere else '×'}・妙味根拠タグ={'○' if tagged else '×'}")

        # --- R36: final上位2頭の直積 ---
        top2 = [h for h, _ in sorted(final.items(), key=lambda kv: -(kv[1] or 0))[:2]]
        if len(top2) == 2:
            pair = frozenset(top2)
            hit = any(pair in co_pairs(b) for b in rows)
            report(hit or (None if has_reason else False), "R36",
                   f"final上位2頭 {sorted(top2)} の直積" + ("あり" if hit else "が全券種に不在" + ("（欠落理由あり）" if has_reason else "")))

        # --- 軸の特定（三連複系の軸 → なければ◎） ---
        axis = None
        for b in rows:
            a = b["_p"][1]
            if len(a) == 1:
                axis = next(iter(a)); break
        if axis is None:
            for p in ps:
                if (p.get("mark") or "").strip() == "◎":
                    axis = int(to_f(p["horse_no"])); break

        # --- R37: 非軸ペア≧2 ＋ 人気同層回避 ---
        if axis is not None:
            cnt, same_layer = 0, []
            for b in rows:
                pairs = b["_p"][0]
                if pairs is None:
                    continue  # ペア列挙型以外の券種は「軸を含まない買い目」を数えられない
                for p2 in pairs:
                    if axis not in p2:
                        cnt += 1
                        ap = pop.get(axis)
                        if ap and all(abs((pop.get(h) or 99) - ap) <= 2 for h in p2):
                            same_layer.append(sorted(p2))
            ok = cnt >= 2
            report(ok or (None if has_reason else False), "R37",
                   f"軸#{axis}を含まないペア {cnt}点（要2点以上）")
            if ok and same_layer and len(same_layer) == cnt:
                report(None, "R37", f"非軸ペアが全て軸と人気±2以内の同層 {same_layer}（分散の趣旨に反する）")

        # --- R23: 荒帯域の1頭依存禁止 ／ R31: 軸抜け全滅 ---
        req_sets = [required_horses(b) for b in rows if b["_p"][4] or b["_p"][3]]
        if req_sets:
            common = set.intersection(*[s if s else set(range(1, 19)) for s in req_sets])
            common = {h for h in common if all(h in (s or {h}) for s in req_sets)}
            all_dependent = bool(req_sets) and all(s for s in req_sets) and bool(set.intersection(*req_sets))
            if band == "荒":
                report(not all_dependent, "R23",
                       f"荒帯域の1頭依存: " + (f"全券種が {sorted(set.intersection(*req_sets))} に依存" if all_dependent else "依存なし"))
            if all_dependent:
                has_sel = "軸選定=" in notes_all
                report(None if has_sel else False, "R31",
                       "軸抜けで全券種が同時失効する構成" + ("（軸選定=の理由記録あり→容認）" if has_sel else "（軸選定理由の記録も併設券種もない）"))
            elif axis is not None:
                report(True, "R23/R31", "軸抜けでも生存する券種あり")

    print(f"\nGATE-FAIL {fails}件 / GATE-WARN {warns}件"
          + ("（参考モードのFAILは裁定・終了コードに数えない）" if only else "")
          + ("" if fails == 0 else " → 買い目を確定しない（FAILを解消するか理由タグを記録する）"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
