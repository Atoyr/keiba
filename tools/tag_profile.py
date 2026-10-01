#!/usr/bin/env python3
"""
tag_profile.py — predictions.csv の base_breakdown にプロファイル事実タグを付ける（ロードマップ v2 §11-3 K1）

  機械脚質=逃げ:r0.00:IQR0.04 / 距離帯経験=あり / 実距離経験=なし

機械脚質＝『脚質認定ルール.md』§3 の機械認定区分と r_med・IQR（小数2位）／
距離帯経験＝プロファイルの `当該距離帯経験=`（近5走に当該距離±200m の走があるか＝R38 の定義）／
実距離経験＝近5走に当該レース距離ちょうどの走があるか（ロードマップ §10-3 B6。R38 の判定には使わない）。

入力は `references/脚質認定_<日付>_<レース名>.md`（コミット済みの正本）だけで、`cache/` を読まない
（クラウドには cache/ が引き継がれず、validate の事実照合が動かなくなるため）。

**工程12 のログ追記後・validate の前に実行する。** 予想工程が「プロファイルに出ていた事実」と
食い違う判断（距離帯を未経験扱いにする等）を書いていないかを validate.py が照合するための入力で、
LLM が手で転記しない（手で書いた値は validate.py がプロファイル md と突き合わせて ERROR にする）。
プロファイルに馬番が無い／馬名が predictions.csv と一致しない馬は `取得失敗`（推測で埋めない）。

使い方:
  python3 tools/tag_profile.py --slug 2026_sprinters_stakes [--dry-run] [--profile <md>]
"""
import argparse
import csv
import io
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACT_KEYS = ("機械脚質", "距離帯経験", "実距離経験")
FAILED = "取得失敗"
# 付け直しのときに既存タグを外す（` / キー=値` の単位。`当該距離帯経験=` は ` / ` の直後に来ないので巻き込まない）
TAG_RE = re.compile(r"\s*/\s*(?:%s)=[^/\s]*" % "|".join(FACT_KEYS))

HEAD_RE = re.compile(r"^#(\d{1,2})\s+(.+?)\s*(?:[（(][^）)]*[）)])?\s*｜4角r\s*\[[^\]]*\]\s*r_med=(\S+)\s+IQR=(\S+)")
STYLE_RE = re.compile(r"機械=(未確定|逃げ|先行|差し|追込)")
RUN5_RE = re.compile(r"5走距離帯=(\S+)")
DIST_EXP_RE = re.compile(r"当該距離帯経験=(あり|なし)")
RACE_DIST_RE = re.compile(r"[芝ダ障]\s*(\d{3,4})\s*m")


def norm(s):
    return re.sub(r"\s+", "", s or "")


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def profile_path(date, race_name):
    return os.path.join(BASE, "references", f"脚質認定_{date}_{race_name}.md")


def parse_profile(path):
    """プロファイル md を {distance, horses{馬番: {...}}} に。build_profile.py の §6 書式が対象。"""
    lines = open(path, encoding="utf-8").read().splitlines()
    m = RACE_DIST_RE.search(lines[0]) if lines else None
    prof = {"distance": int(m.group(1)) if m else None, "horses": {}}
    cur = None
    for ln in lines:
        h = HEAD_RE.match(ln)
        if h:
            cur = {"name": h.group(2).strip(), "r_med": _num(h.group(3)), "iqr": _num(h.group(4)),
                   "style": None, "dists": None, "dist_exp": None}
            prof["horses"][h.group(1)] = cur
            continue
        if cur is None:
            continue
        if ln.startswith("---") or ln.startswith("## "):
            cur = None
            continue
        if cur["style"] is None:
            s = STYLE_RE.search(ln)
            if s:
                cur["style"] = s.group(1)
        if cur["dists"] is None:
            r = RUN5_RE.search(ln)
            if r:
                cur["dists"] = [int(x.split(":")[0]) for x in r.group(1).split(";")
                                if x.split(":")[0].isdigit()]
        if cur["dist_exp"] is None:
            d = DIST_EXP_RE.search(ln)
            if d:
                cur["dist_exp"] = d.group(1)
    return prof


def facts_for(h, distance):
    """1頭ぶんの事実タグ {キー: 値}。md に無い項は 取得失敗（推測で埋めない）。"""
    if h is None:
        return {k: FAILED for k in FACT_KEYS}
    r = f"r{h['r_med']:.2f}" if h["r_med"] is not None else "r不明"
    q = f"IQR{h['iqr']:.2f}" if h["iqr"] is not None else "IQR不明"
    out = {"機械脚質": f"{h['style']}:{r}:{q}" if h["style"] else FAILED}
    out["距離帯経験"] = h["dist_exp"] or FAILED
    if h["dists"] is None or not distance:
        out["実距離経験"] = FAILED
    else:
        out["実距離経験"] = "あり" if distance in h["dists"] else "なし"
    return out


def facts_str(f):
    return " / ".join(f"{k}={f[k]}" for k in FACT_KEYS)


def read_facts(bb):
    """base_breakdown から事実タグを {キー: 値} で取り出す（無いキーは含めない）。"""
    out = {}
    for k in FACT_KEYS:
        m = re.search(r"(?:^|/\s*)%s=([^/\s]+)" % k, bb or "")
        if m:
            out[k] = m.group(1)
    return out


def expected_facts(prof, horse_no, horse_name):
    """predictions.csv の1行に付くべき事実タグと、取得失敗にした理由。"""
    h = prof["horses"].get(str(horse_no).strip())
    if h is None:
        return facts_for(None, None), "プロファイルに馬番なし"
    if norm(h["name"]) != norm(horse_name):
        return facts_for(None, None), f"馬名不一致（プロファイル={h['name']}）"
    return facts_for(h, prof["distance"]), ""


def main():
    ap = argparse.ArgumentParser(description="predictions.csv にプロファイル事実タグを付ける（§11-3 K1）")
    ap.add_argument("--slug", required=True, help="log/races.csv の race_id")
    ap.add_argument("--profile", help="プロファイル md のパス（省略時は races.csv の date と race_name から決める）")
    ap.add_argument("--dry-run", action="store_true", help="書き込まずに表示だけ")
    a = ap.parse_args()

    races = {r["race_id"]: r for r in csv.DictReader(open(os.path.join(BASE, "log", "races.csv"), encoding="utf-8"))}
    race = races.get(a.slug)
    if race is None:
        sys.exit(f"[ERROR] log/races.csv に {a.slug} の行が無い（工程12 のログ追記後に実行する）")
    path = a.profile or profile_path(race["date"], race["race_name"])
    if not os.path.exists(path):
        sys.exit(f"[取得失敗] {path} が無い（#収録 <レース名> 種別:出走馬プロファイル が先。--profile で指定も可）")
    prof = parse_profile(path)
    if not prof["horses"]:
        sys.exit(f"[取得失敗] {path} から馬を読み取れない（build_profile.py の §6 書式でない・または枠順未確定の仮番）")
    m = re.search(r"(\d{3,4})", race.get("course") or "")
    if prof["distance"] and m and int(m.group(1)) != prof["distance"]:
        sys.exit(f"[ERROR] 距離が食い違う：races.csv course={race['course']} ／ プロファイル {prof['distance']}m（別レースの md を読んでいる）")

    pp = os.path.join(BASE, "log", "predictions.csv")
    raw = open(pp, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)
    header = next(csv.reader([lines[0]]))
    out, n = [], 0
    for ln in lines:
        if not ln.startswith(a.slug + ","):
            out.append(ln)
            continue
        vals = next(csv.reader([ln]))
        if len(vals) != len(header):
            sys.exit(f"[ERROR] predictions.csv の {a.slug} 行を1行として読めない（列数 {len(vals)}≠{len(header)}）")
        rec = dict(zip(header, vals))
        f, why = expected_facts(prof, rec["horse_no"], rec["horse_name"])
        rec["base_breakdown"] = TAG_RE.sub("", rec["base_breakdown"]) + " / " + facts_str(f)
        buf = io.StringIO()
        csv.writer(buf, lineterminator="").writerow([rec[k] for k in header])
        out.append(buf.getvalue())
        n += 1
        print(f"#{rec['horse_no']:>2} {rec['horse_name']}: {facts_str(f)}" + (f"（{why}）" if why else ""))
    if n == 0:
        sys.exit(f"[ERROR] predictions.csv に {a.slug} の行が無い")
    if a.dry_run:
        print("（--dry-run のため書き込みなし）")
        return
    with open(pp, "w", encoding="utf-8", newline="") as fh:
        fh.write(nl.join(out))
    print(f"→ {pp} の {n} 行に事実タグを付与（validate.py で確認）")


if __name__ == "__main__":
    main()
