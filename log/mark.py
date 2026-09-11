#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mark.py — 印 v4 の算出とガードレール（ロードマップ v2 §5-3・2026-09-12）

入力（すべてファイル。LLM は全頭シミュレーション表だけを書く）
  cache/profile/<slug>.json            … tools/build_profile.py の生成物（近5走・脚質・末脚指数・想定4角）
  _handoff/sim/<slug>.csv              … 全頭シミュレーション表（--template で雛形を出す）
  _handoff/sim/<slug>_race.json        … レース単位の入力（ペーススコア・ハナ争い型・馬場・バイアス・決着帯 等）
  _handoff/odds/<slug>.csv             … 当日オッズ（horse_no,win_odds,place_odds_max,popularity）。無ければ☆と R は「未取得」
出力
  標準出力：印・rank_score・違反一覧（ERROR が1件でもあれば印は確定しない）
  _handoff/mark_runs/<slug>/<時刻>.json … 実行履歴（現状からの再算出・回数上限なし）
  --write で log/predictions.csv に upsert（締切前の最終実行だけを書く）

使い方
  python3 log/mark.py --slug 2026_challenge_cup --template      # 雛形（S列の機械初期値入り）を出す
  python3 log/mark.py --slug 2026_challenge_cup                 # 判定（ドライラン）
  python3 log/mark.py --slug 2026_challenge_cup --write         # predictions.csv へ反映

全頭シミュレーション表の列（_handoff/sim/<slug>.csv）
  horse_no   馬番（枠順未確定なら雛形は仮番。確定後に build_profile を再実行して雛形を取り直す）
  horse_name 馬名（照合用・変更しない）
  base       地力の核 0〜100（主観・条件を含めない）                                  ← LLM 必須
  sim_pos    想定4角位置（雛形＝r_med×(頭数−1)+1。上書きは±2まで・tags に 上書き= 理由）
  s_front / s_mid / s_back  前残り／中立／前崩れ での結論 A/B/C/D（雛形＝機械初期値。上書きは tags に理由）
  win_path   来るならこう来る（1行）                                                      ← LLM 必須
  lose_path  負けるならこう負ける（1行）                                                  ← LLM 必須
  sim_score  今回評価 0〜100（本線・対抗の重みで統合した LLM の定性評価）               ← LLM 必須
  add_jockey / add_agari / add_weight / add_body  加算層（評価ルール第2・7項の表どおり。0 可）← LLM 必須
  myomi      妙味スコア（評価ルール第10項）                                              ← LLM 必須
  risk       不安材料（構造フィルタ候補と実測の所在または n。無ければ空）
  tags       key=value を ; 区切り。使うキー：
             R25精査=（R25・validate.py と同じマーカー）／血統適用=（R38）／理由=（R41 軽ハンデ未確保）／根拠=a|b（R34・R20・2根拠）／保全=／
             上書き=S前残り:B→A:理由（S5）／消し根拠=カテゴリ:詳細:所在（R40・R43）／R35=根拠（☆ R<4.0）
禁止：win_path・lose_path・risk・tags に人気・オッズ由来の語（人気／オッズ／倍／単勝／複勝）を書かない（G-O）
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM_DIR = os.path.join(BASE, "_handoff", "sim")
ODDS_DIR = os.path.join(BASE, "_handoff", "odds")
RUN_DIR = os.path.join(BASE, "_handoff", "mark_runs")

SIM_COLS = ["horse_no", "horse_name", "base", "sim_pos", "s_front", "s_mid", "s_back", "win_path", "lose_path",
            "sim_score", "add_jockey", "add_agari", "add_weight", "add_body", "myomi", "risk", "tags"]
ADD_KEYS = [("add_jockey", "騎手"), ("add_agari", "上がり"), ("add_weight", "斤量"), ("add_body", "馬体重")]
SCN = ["front", "mid", "back"]
SCN_JA = {"front": "前残り", "mid": "中立", "back": "前崩れ"}
FORBID = re.compile(r"人気|オッズ|倍|単勝|複勝")

# S2 シナリオ重みの初期値（v2 §5-2・改訂は U6 較正レビュー）
def scenario_weights(race):
    ps = race.get("pace_score")
    hana = race.get("hana_type") or "なし"
    if ps is None:
        return None
    if ps <= 3 or hana == "単騎":
        w = {"front": 0.6, "mid": 0.3, "back": 0.1}
    elif ps <= 6:
        w = {"front": 0.3, "mid": 0.5, "back": 0.2}
    elif hana == "競り合い型":
        w = {"front": 0.1, "mid": 0.4, "back": 0.5}
    else:
        w = {"front": 0.3, "mid": 0.5, "back": 0.2}
    if race.get("going") in ("重", "不良") or race.get("straight"):
        w["mid"] = round(w["mid"] + w["back"], 2)
        w["back"] = 0.0
    if race.get("main_override") in SCN:  # 展開根拠2つを race json の reasons に書いた場合のみ
        w = dict(w)
        w[race["main_override"]] = max(w.values()) + 0.05
    return w


def finish_band(race):
    fb = race.get("finish_band") or {}
    ct = race.get("course_type") or "持続型"
    front = fb.get("front", 5 if ct == "持続型" else 6)
    mid = fb.get("mid", 6)
    shift = {"内前": -0, "フラット": 0, "外差し": 2}.get(race.get("bias") or "フラット", 0)
    return {"front": front + (0 if shift == 0 else -shift), "mid": mid + shift}


def initial_s(h, race):
    """S4 の機械初期値（位置×末脚の2軸）。"""
    pos = h.get("sim_pos")
    backed = bool(h.get("agari_backed"))
    front_type = h.get("style") in ("逃げ", "先行")
    structural = bool(h.get("structural_hit"))
    fb = finish_band(race)
    out = {}
    for s in SCN:
        if structural:
            out[s] = "D"
            continue
        if pos is None:
            out[s] = "B" if backed else "C"
            continue
        if s == "front":
            inband = pos <= fb["front"] + 0.5
            out[s] = "A" if (inband and (front_type or backed)) else ("B" if inband or (backed and pos <= fb["mid"] + 2.5) else "C")
        elif s == "mid":
            inband = pos <= fb["mid"] + 0.5
            out[s] = "A" if (inband and (front_type or backed)) else ("B" if inband or backed else "C")
        else:  # 前崩れ：上がり軸が主
            out[s] = "A" if (backed and pos > fb["mid"] - 2) else ("B" if backed or pos > fb["mid"] + 0.5 else "C")
    return out


def parse_tags(s):
    d = {}
    for t in (s or "").split(";"):
        t = t.strip()
        if "=" in t:
            k, v = t.split("=", 1)
            d.setdefault(k.strip(), []).append(v.strip())
    return d


def to_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_profile(slug):
    p = os.path.join(BASE, "cache", "profile", f"{slug}.json")
    if not os.path.exists(p):
        sys.exit(f"[ERROR] プロファイルがない: {p}（先に tools/build_profile.py）")
    return json.load(open(p, encoding="utf-8"))


def load_odds(slug):
    p = os.path.join(ODDS_DIR, f"{slug}.csv")
    if not os.path.exists(p):
        return None, None
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    return {int(r["horse_no"]): {"win": to_f(r.get("win_odds")), "place": to_f(r.get("place_odds_max")),
                                 "pop": to_f(r.get("popularity"))} for r in rows if r.get("horse_no")}, os.path.getmtime(p)


def write_template(slug, prof):
    os.makedirs(SIM_DIR, exist_ok=True)
    race = prof["race"]
    rj = os.path.join(SIM_DIR, f"{slug}_race.json")
    if not os.path.exists(rj):
        known = [h for h in prof["horses"] if h.get("style") != "未確定"]
        ps = 2 * sum(1 for h in known if h["style"] == "逃げ") + sum(1 for h in known if h["style"] == "先行")
        json.dump({
            "slug": slug, "pace_score": ps, "hana_type": "", "going": "", "bias": "フラット",
            "course_type": "持続型", "straight": False, "handicap": bool(race.get("handicap")),
            "grade": "", "band": "", "finish_band": {}, "patterns": [], "reasons": [], "main_override": None,
            "_help": "hana_type=単騎|競り合い型|隊列確定型|なし／going=良|稍重|重|不良／bias=内前|フラット|外差し／"
                     "course_type=持続型|瞬発型／patterns=展開2パターンの見出し（R39・R04）／reasons=前崩れ本線の根拠（R29・2つ）",
        }, open(rj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"→ {rj}（レース入力の雛形。hana_type / going / bias / band を埋める）")
    racej = json.load(open(rj, encoding="utf-8"))
    out = os.path.join(SIM_DIR, f"{slug}.csv")
    if os.path.exists(out):
        print(f"[skip] 既に存在: {out}（作り直すなら削除してから）")
        return
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(SIM_COLS)
        for h in prof["horses"]:
            s = initial_s(h, racej)
            tags = []
            if not h.get("dist_exp"):
                tags.append("血統適用=")
            if racej.get("handicap") and h.get("weight_carried") is not None and 52.0 <= h["weight_carried"] <= 53.0:
                tags.append("軽ハンデ=該当")
            w.writerow([h.get("no") or f"仮{h['row']}", h["name"], "", h.get("sim_pos", ""), s["front"], s["mid"], s["back"],
                        "", "", "", 0, 0, 0, 0, "", "", ";".join(tags)])
    print(f"→ {out}（全頭シミュレーション表の雛形。base / win_path / lose_path / sim_score / 加算 / myomi を埋める）")


def main():
    ap = argparse.ArgumentParser(description="印 v4 の算出とガードレール")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--log-dir", default=os.path.join(BASE, "log"))
    ap.add_argument("--sim-dir", default=None, help="試験用：全頭表・race.json・odds の置き場を差し替える")
    a = ap.parse_args()
    global SIM_DIR, ODDS_DIR, RUN_DIR
    if a.sim_dir:
        SIM_DIR = a.sim_dir
        ODDS_DIR = os.path.join(a.sim_dir, "odds")
        RUN_DIR = os.path.join(a.sim_dir, "runs")
    prof = load_profile(a.slug)
    if a.template:
        write_template(a.slug, prof)
        return

    sim_p = os.path.join(SIM_DIR, f"{a.slug}.csv")
    race_p = os.path.join(SIM_DIR, f"{a.slug}_race.json")
    for p in (sim_p, race_p):
        if not os.path.exists(p):
            sys.exit(f"[ERROR] 入力がない: {p}（--template で雛形を出す）")
    race = json.load(open(race_p, encoding="utf-8"))
    sim = list(csv.DictReader(open(sim_p, encoding="utf-8")))
    odds, odds_mtime = load_odds(a.slug)
    sim_mtime = os.path.getmtime(sim_p)
    byname = {h["name"]: h for h in prof["horses"]}
    n_field = len(sim)
    errors, warns, flags = [], [], []

    # ---- 結合と G-A（必須列）----
    H = []
    for r in sim:
        h = byname.get((r.get("horse_name") or "").strip())
        if not h:
            errors.append(f"G-A #{r.get('horse_no')} {r.get('horse_name')}: プロファイルに無い馬名")
            continue
        no = r.get("horse_no") or ""
        no_i = int(no) if str(no).isdigit() else None
        x = {"no": no_i, "no_s": str(no), "name": h["name"], "prof": h, "tags": parse_tags(r.get("tags")),
             "risk": r.get("risk") or "", "win": r.get("win_path") or "", "lose": r.get("lose_path") or "",
             "base": to_f(r.get("base")), "sim_pos": to_f(r.get("sim_pos")), "sim": to_f(r.get("sim_score")),
             "myomi": to_f(r.get("myomi")), "s": {s: (r.get(f"s_{s}") or "").strip().upper() for s in SCN},
             "add": {ja: to_f(r.get(k)) for k, ja in ADD_KEYS}}
        miss = [k for k in ("base", "sim_pos", "sim", "myomi") if x[k] is None]
        miss += [f"s_{s}" for s in SCN if x["s"][s] not in ("A", "B", "C", "D")]
        miss += [ja for ja, v in x["add"].items() if v is None]
        miss += [k for k in ("win", "lose") if not x[k].strip()]
        if not h.get("sire") or not h.get("bms") or h.get("weight_carried") is None:
            miss.append("父/母父/斤量(プロファイル)")
        if miss:
            errors.append(f"G-A #{x['no_s']} {x['name']}: 空欄 {miss}")
        if odds and no_i in odds:
            o = odds[no_i]
            x["win_odds"], x["place_odds"], x["pop"] = o["win"], o["place"], o["pop"]
            x["R"] = round(o["win"] / o["place"], 2) if o["win"] and o["place"] else None
        else:
            x["win_odds"] = x["place_odds"] = x["pop"] = x["R"] = None
        H.append(x)
    if not H:
        sys.exit("[ERROR] 全頭表が空")
    if len(H) != len(prof["horses"]):
        warns.append(f"G-A 全頭表 {len(H)} 頭 ≠ プロファイル {len(prof['horses'])} 頭（取消・追加は build_profile を再実行）")

    # ---- シナリオ重み・本線 ----
    w = scenario_weights(race)
    if w is None:
        errors.append("G-A race.json の pace_score が無い")
        w = {"front": 0.3, "mid": 0.5, "back": 0.2}
    main_s = max(w, key=w.get)
    for x in H:
        x["rank"] = round(x["sim"] + sum(x["add"].values()), 1) if x["sim"] is not None and all(v is not None for v in x["add"].values()) else None

    # ---- 序列と印 ----
    ranked = sorted([x for x in H if x["rank"] is not None], key=lambda x: (-x["rank"], -(x["sim"] or 0), -(x["base"] or 0)))
    marks = {}
    top4 = ranked[:4]
    if len(top4) >= 2 and top4[0]["s"][main_s] != "A" and top4[1]["s"][main_s] == "A" and abs(top4[0]["rank"] - top4[1]["rank"]) <= 3.0:
        top4[0], top4[1] = top4[1], top4[0]  # 本線シナリオで A の馬を上に（同水準のとき）
    for m, x in zip("◎○▲△", top4):
        marks[x["name"]] = m
    # ☆（人気6番以下・妙味→R→sim）
    stars = []
    if odds:
        thr = 4 if (race.get("band") == "荒") else 5
        cand = [x for x in H if x["name"] not in marks and x["pop"] is not None and x["pop"] >= 6 and x["myomi"] is not None]
        cand = [x for x in cand if x["myomi"] >= 3 or (x["R"] or 0) >= 4.0]
        cand.sort(key=lambda x: (-(x["myomi"] >= thr), -(x["myomi"]), -((x["R"] or 0) >= 4.0), -(x["R"] or 0), -(x["sim"] or 0)))
        stars = cand[:2]
        for x in stars:
            marks[x["name"]] = "☆"
    else:
        warns.append("☆: オッズ未取得（_handoff/odds/<slug>.csv）のため未確定。締切前に取り直して再実行")
    # ✕（消しプール内の rank 最下位1頭）
    base_rank = {x["name"]: i + 1 for i, x in enumerate(sorted(H, key=lambda x: -(x["base"] or 0)))}
    pool = []
    for x in H:
        all_cd = all(x["s"][s] in ("C", "D") for s in SCN)
        keshi = x["tags"].get("消し根拠", [])
        has_src = any(len(v.split(":")) >= 3 for v in keshi)
        if (all_cd or (keshi and has_src)) and x["name"] not in marks:
            pool.append(x)
        if keshi and not has_src:
            errors.append(f"G-B/R43 #{x['no_s']} {x['name']}: 消し根拠= に実測の所在または n が無い（カテゴリ:詳細:所在）")
    if pool:
        x = min(pool, key=lambda x: (x["rank"] if x["rank"] is not None else 1e9))
        if base_rank[x["name"]] <= n_field / 2:
            errors.append(f"G-C #{x['no_s']} {x['name']}: ✕候補だが base 順位 {base_rank[x['name']]}/{n_field} が上位半分（プールの他の馬を見直す）")
        else:
            marks[x["name"]] = "x"
    else:
        errors.append("G-B ✕候補なし（全シナリオ C/D の馬も消し根拠つきの馬も無い）＝分離度が低い。S列を見直すか races.notes に 信頼度=低 を記録（§8-3 決定3）")

    # ---- ガードレール ----
    for x in H:
        h, t, lab = x["prof"], x["tags"], f"#{x['no_s']} {x['name']}"
        # G-D R25
        if x["base"] is not None and x["base"] < 70:
            hit = any(r.get("grade") in ("G1", "G2", "G3") and (r.get("finish") or "99").isdigit() and int(r["finish"]) <= 4
                      for r in h.get("runs", []))  # validate.py A-4 と同じ近似（近5走に重賞4着以内）
            if hit and not t.get("R25精査"):
                errors.append(f"G-D/R25 {lab}: base{x['base']:g}<70 だが近5走に重賞4着以内あり。tags に R25精査=（結論と根拠2走）を書く")
        # G-E R38
        if not h.get("dist_exp") and not any(v for v in t.get("血統適用", [])):
            errors.append(f"G-E/R38 {lab}: 当該距離帯±200m 未経験。tags に 血統適用=（±0 も可・理由つき）")
        # G-F R41
        if race.get("handicap") and h.get("weight_carried") is not None and 52.0 <= h["weight_carried"] <= 53.0:
            if x["name"] not in marks and not t.get("理由"):
                flags.append(f"G-F/R41 {lab}: 52.0〜53.0kg が無印。買い目で3着紐に確保するか tags に 理由= を書く（買い目ゲートで再判定）")
        # G-H 向きの整合
        if x["base"] is not None and x["sim"] is not None:
            ms = x["s"][main_s]
            if x["sim"] > x["base"] and ms != "A":
                errors.append(f"G-H {lab}: sim_score{x['sim']:g}＞base{x['base']:g} なのに本線（{SCN_JA[main_s]}）が {ms}")
            if x["sim"] < x["base"] and ms not in ("C", "D"):
                errors.append(f"G-H {lab}: sim_score{x['sim']:g}＜base{x['base']:g} なのに本線（{SCN_JA[main_s]}）が {ms}")
        # G-I R35
        if marks.get(x["name"]) == "☆" and x["R"] is not None and x["R"] < 4.0 and not (t.get("R35") or t.get("妙味根拠")):
            errors.append(f"G-I/R35 {lab}: ☆で R={x['R']}<4.0。tags に R35=根拠 を書き、買い目で全券種の3着紐に保全")
        # G-J R09
        if x["R"] is not None and x["R"] < 1.5:
            flags.append(f"G-J/R09 {lab}: R={x['R']}<1.5 → 頭固定回避（複勝圏除外にはしない）")
        # G-K R20
        if main_s == "front" and h.get("style") in ("差し", "追込") and marks.get(x["name"]) in ("◎", "○", "▲") and not t.get("根拠"):
            errors.append(f"G-K/R20 {lab}: 前残り本線で {h['style']} を{marks[x['name']]}。tags に 根拠=（なぜ前残りでも届くか）")
        # G-N 上書き
        for v in t.get("上書き", []):
            if len(v.split(":")) < 3 or not v.split(":")[-1].strip():
                errors.append(f"G-N {lab}: 上書き= に理由が無い（S前残り:B→A:理由 の形）")
        # G-O 人気・オッズ由来の語
        for fld in ("win", "lose", "risk"):
            if FORBID.search(x[fld]):
                errors.append(f"G-O {lab}: {fld} に人気・オッズ由来の語（{FORBID.search(x[fld]).group()}）")
        if FORBID.search(";".join(sum(t.values(), []))):
            errors.append(f"G-O {lab}: tags に人気・オッズ由来の語")
        # sim_pos の上書き幅
        if x["sim_pos"] is not None and h.get("sim_pos") is not None and abs(x["sim_pos"] - h["sim_pos"]) > 2.0 and not t.get("上書き"):
            errors.append(f"G-N {lab}: sim_pos を機械初期値 {h['sim_pos']} から {x['sim_pos']} へ動かしたが 上書き= が無い（±2超）")
    # G-G R34
    if ranked:
        b1 = max(H, key=lambda x: (x["base"] or 0))
        if marks.get(b1["name"]) not in ("◎", "○", "▲", "☆"):
            t = b1["tags"]
            if not (len(t.get("根拠", [""])[0].split("|")) >= 2 or t.get("保全")):
                errors.append(f"G-G/R34 #{b1['no_s']} {b1['name']}: base 1位が ◎○▲☆圏外。tags に 根拠=a|b（独立2つ）か 保全= を書く")
    # G-L R33 / R29
    if race.get("straight") and main_s == "back":
        errors.append("G-L/R33 直線競走で前崩れが本線")
    if race.get("going") in ("重", "不良") and race.get("main_override") == "back" and len(race.get("reasons") or []) < 2:
        errors.append("G-L/R29 重・不良で前崩れ本線にするには race.json の reasons に根拠2つ")
    # G-M R39 / R04
    hana_horses = [h for h in prof["horses"] if h.get("style") == "逃げ" or (h.get("hana_count") or 0) >= 1]
    if len(hana_horses) >= 2 and len(race.get("patterns") or []) < 2:
        errors.append(f"G-M/R39 ハナ実績馬が {len(hana_horses)} 頭。race.json の patterns に競り合い型／隊列確定型の2パターンを記録")
    if race.get("hana_type") == "単騎" and race.get("going") == "良" and race.get("pace_score", 0) in (4, 5, 6) and not any("前残り" in p for p in race.get("patterns") or []):
        errors.append("G-M/R04 中立×良×単騎。patterns にスロー前残りシナリオを並列で置く")
    # 上書きの多さ
    n_ov = sum(1 for x in H if x["tags"].get("上書き"))
    if n_ov > n_field / 3:
        warns.append(f"G-N 上書きが {n_ov}/{n_field} 頭（1/3 超）＝機械初期値を全面的に書き換えている")
    # G-O' 見えたの記録
    if odds_mtime and odds_mtime < sim_mtime and not race.get("blind_declared"):
        warns.append("G-O' オッズファイルが全頭表より先に存在（見えた可能性の記録・race.json に blind_declared=true で宣言可）")
    # 分離度
    if all(x["s"][main_s] in ("A", "B") for x in H):
        warns.append("信頼度=低 本線シナリオで全頭が A/B（分離していない）")

    # ---- 出力 ----
    print(f"=== 印 v4 {a.slug}  本線={SCN_JA[main_s]}  重み={w}  決着帯={finish_band(race)} ===")
    print(f"{'印':2s} {'番':>3s} {'馬名':14s} {'base':>5s} {'sim':>5s} {'rank':>6s} {'想定4角':>5s} 前/中/崩 {'R':>5s} {'人気':>3s}")
    for x in sorted(H, key=lambda x: -(x["rank"] if x["rank"] is not None else -1)):
        m = marks.get(x["name"], "-")
        print(f"{m:2s} {x['no_s']:>3s} {x['name']:14s} {x['base'] if x['base'] is not None else '':>5} {x['sim'] if x['sim'] is not None else '':>5} "
              f"{x['rank'] if x['rank'] is not None else '':>6} {x['sim_pos'] if x['sim_pos'] is not None else '':>5} "
              f"{x['s']['front']}/{x['s']['mid']}/{x['s']['back']}   {x['R'] if x['R'] is not None else '-':>5} {int(x['pop']) if x['pop'] else '-':>3}")
    for e in errors:
        print(f"[ERROR] {e}")
    for wv in warns:
        print(f"[WARN] {wv}")
    for fl in flags:
        print(f"[FLAG] {fl}")
    print(f"ERROR {len(errors)} / WARN {len(warns)} / FLAG {len(flags)} → " + ("印は確定しない（表を直して再実行）" if errors else "印確定可"))

    os.makedirs(os.path.join(RUN_DIR, a.slug), exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = {"slug": a.slug, "time": ts, "main": main_s, "weights": w, "errors": errors, "warns": warns, "flags": flags,
           "marks": {x["no_s"]: marks.get(x["name"], "-") for x in H},
           "rank": {x["no_s"]: x["rank"] for x in H}, "odds_file": bool(odds)}
    prev = sorted(glob.glob(os.path.join(RUN_DIR, a.slug, "*.json")))
    if prev:
        last = json.load(open(prev[-1], encoding="utf-8"))
        diff = {k: (last["marks"].get(k), v) for k, v in run["marks"].items() if last["marks"].get(k) != v}
        if diff:
            print("前回との差分（印）: " + ", ".join(f"#{k} {o or '-'}→{n}" for k, (o, n) in diff.items()))
        else:
            print("前回との差分（印）: なし")
    json.dump(run, open(os.path.join(RUN_DIR, a.slug, f"{ts}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if a.write:
        if errors:
            sys.exit("[ERROR] が残っているので predictions.csv には書かない")
        write_predictions(a, prof, H, marks, race, main_s)


def write_predictions(a, prof, H, marks, race, main_s):
    pp = os.path.join(a.log_dir, "predictions.csv")
    rp = os.path.join(a.log_dir, "races.csv")
    races = list(csv.DictReader(open(rp, encoding="utf-8")))
    if not any(r["race_id"] == a.slug for r in races):
        sys.exit(f"[ERROR] races.csv に {a.slug} の行が無い（先に宣言タグつきで作る）")
    raw = open(pp, "rb").read()
    crlf = b"\r\n" in raw
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    cols = list(rows[0].keys()) if rows else None
    if not cols:
        sys.exit("[ERROR] predictions.csv が空")
    rows = [r for r in rows if r["race_id"] != a.slug]
    for x in H:
        h = x["prof"]
        if x["no"] is None:
            sys.exit(f"[ERROR] #{x['no_s']} は仮番。枠順確定後に build_profile と --template を取り直す")
        adds = {ja: x["add"][ja] for _, ja in ADD_KEYS}
        scn = round(x["sim"] - x["base"], 1)
        add_total = round(scn + sum(adds.values()), 1)
        tag5 = ";".join(f"{r.get('distance') or '不明'}:{r.get('going') or '不明'}:{(r.get('finish') + '着') if (r.get('finish') or '').isdigit() else (r.get('finish') or '不明')}:{r.get('grade') or '不明'}" for r in h.get("runs", [])) or "なし"
        notes = [f"v4=paper", f"本線={SCN_JA[main_s]}", f"想定4角初期={h.get('sim_pos')}"]
        for k, vs in x["tags"].items():
            for v in vs:
                notes.append(f"{k}={v}")
        if x["risk"]:
            notes.append(f"不安材料={x['risk']}")
        notes.append(f"勝ち筋={x['win']}")
        notes.append(f"崩れ筋={x['lose']}")
        row = {c: "" for c in cols}
        row.update({
            "race_id": a.slug, "horse_no": x["no"], "horse_name": x["name"], "mark": marks.get(x["name"], "-"),
            "base_score": f"{x['base']:g}", "base_breakdown": f"v4 / 5走距離帯={tag5} / 父={h.get('sire')} / 母父={h.get('bms')}",
            "composite_coef": "1.0", "coef_breakdown": "v4係数なし1.00",
            "additive_total": f"{add_total:g}",
            "additive_breakdown": f"シナリオ{scn:+.1f};" + ";".join(f"{ja}{adds[ja]:+.1f}" for _, ja in ADD_KEYS),
            "r_adj": "0", "final_score": f"{round(x['base'] + add_total, 1):g}", "myomi_score": f"{x['myomi']:g}",
            "popularity": f"{int(x['pop'])}" if x["pop"] else "", "win_odds": f"{x['win_odds']:g}" if x["win_odds"] else "",
            "place_odds_max": f"{x['place_odds']:g}" if x["place_odds"] else "", "r_value": f"{x['R']}" if x["R"] is not None else "",
            "sim_pos": f"{x['sim_pos']:g}", "s_front": x["s"]["front"], "s_mid": x["s"]["mid"], "s_back": x["s"]["back"],
            "sim_score": f"{x['sim']:g}", "agari_diff": f"{h['agari_diff_med']:g}" if h.get("agari_diff_med") is not None else "",
            "notes": " / ".join(notes),
        })
        rows.append(row)
    with open(pp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\r\n" if crlf else "\n")
        w.writeheader()
        w.writerows(rows)
    print(f"→ {pp} に {len(H)} 行を反映（validate.py で確認）")


if __name__ == "__main__":
    main()
