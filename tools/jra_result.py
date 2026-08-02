#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jra_result.py — JRA公式のレース結果を一次ソースとして取得・構造化する

背景:
  『振り返り手順.md』は着順・配当・馬場をJRA公式で確認することを要求するが、
  JRA公式の結果画面は /JRADB/accessS.html への遷移（POST の cname、または
  GET の ?CNAME=）で到達する動的画面で、GET専用だった polite_fetch.py では
  インデックスを辿れず「取得失敗」が続いていた。
  実測（2026-08-02）では jra.go.jp の robots.txt は全面許可であり、
  正直な User-Agent で 200 が返る。bot ブロックではなく到達手段の問題だった。

クロール手順（2段）:
  1. 開催選択ページ  POST accessS.html cname=pw01sli00/AF
     → 直近開催日の一覧（pw01srl…）と重賞への直リンク（pw01sde…）を得る
  2. 開催日レース一覧 POST accessS.html cname=pw01srl…
     → その日の各レース結果（pw01sde…）へのリンクを得る
  3. レース結果      GET  accessS.html?CNAME=pw01sde…
  CNAME 末尾の2桁はサイト側のチェックサムで自前生成できないため、
  必ずインデックスから辿る（推測でURLを組み立てない）。

CNAME の構造（インデックスから得た値の解釈用。生成には使わない）:
  pw01sde + [2]種別 + [2]場コード + [4]年 + [2]回 + [2]日 + [2]R + [8]YYYYMMDD
  pw01srl + [2]種別 + [2]場コード + [4]年 + [2]回 + [2]日 +       [8]YYYYMMDD
  場コード 01札幌 02函館 03福島 04新潟 05東京 06中山 07中京 08京都 09阪神 10小倉

使い方:
  python3 tools/jra_result.py --index                       # 取得可能な開催日を一覧
  python3 tools/jra_result.py --date 2026-08-02 --course 札幌 --race 11
  python3 tools/jra_result.py --date 2026-08-02 --course 札幌 --race 11 --json
  python3 tools/jra_result.py --cname pw01sde0101202601041120260802/F2

取得できる項目:
  開催日・場・R・レース名・グレード・距離/コース・天候・**発表馬場**・
  全着順（着順/枠/馬番/馬名/性齢/斤量/騎手/タイム/着差/通過順/上り3F/馬体重/人気）・
  **ハロンタイム（ラップ内訳）**・上り4F/3F・コーナー通過順位・全券種の配当

取得できない項目（別ソース）:
  含水率・クッション値 … JRA公式の「馬場情報」ページ（開催中のみ公開）。
  結果ページには載らないため、本ツールは出力しない（推測で埋めない）。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polite_fetch  # noqa: E402

# Windows のコンソール既定は cp932 で、馬名・レース名に化け／例外が出る。
# 出力は常に UTF-8 に固定する（リダイレクト先でも同じ結果になる）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

BASE_URL = "https://www.jra.go.jp/JRADB/accessS.html"
INDEX_CNAME = "pw01sli00/AF"
TTL_INDEX = 3600          # 開催選択は開催中に更新されうる（1時間）
TTL_RESULT = 31536000     # 確定結果は変わらない（1年）

COURSE_CODES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}
CODE_BY_COURSE = {v: k for k, v in COURSE_CODES.items()}


class FetchError(RuntimeError):
    """一次ソースに到達できなかった。呼び出し側は推測で埋めず「取得失敗」と記録する。"""


# ---------- 取得 ----------

def _decode(body):
    return polite_fetch._decode(body, "text/html; charset=Shift_JIS")


def _post(cname, ttl):
    body = polite_fetch.fetch(BASE_URL, ttl, data="cname=" + cname)
    return _decode(body)


def _get_result(cname, ttl=TTL_RESULT):
    """結果ページは ?CNAME= のGETで到達できる。失敗時はPOSTへフォールバック。"""
    url = BASE_URL + "?CNAME=" + cname
    try:
        html = _decode(polite_fetch.fetch(url, ttl))
        if "race_result_unit" in html:
            return html
    except Exception:
        pass
    return _post(cname, ttl)


# ---------- CNAME の解釈 ----------

def parse_cname(cname):
    """pw01sde…/pw01srl… を辞書に分解する。解釈できなければ None。"""
    m = re.match(r"pw01s(de|rl)(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})(\d{0,2})(\d{8})/",
                 cname + "/")
    if not m:
        return None
    kind, _sort, course, year, kai, day, race, date = m.groups()
    if kind == "rl" and race:      # srl は R が無いぶん桁がずれる
        date = race + date
        race = ""
        if len(date) != 8:
            return None
    return {
        "cname": cname,
        "kind": "race" if kind == "de" else "day",
        "course_code": course,
        "course": COURSE_CODES.get(course, course),
        "kai": int(kai),
        "day": int(day),
        "race_no": int(race) if race else None,
        "date": f"{date[0:4]}-{date[4:6]}-{date[6:8]}",
    }


def _links(html):
    """ページ内の accessS 遷移先 CNAME を、出現順・重複なしで返す。"""
    found, seen = [], set()
    pat = (r"accessS\.html\?CNAME=([^\"']+)"
           r"|doAction\(\s*'/JRADB/accessS\.html'\s*,\s*'([^']+)'\s*\)")
    for m in re.finditer(pat, html):
        c = m.group(1) or m.group(2)
        if c not in seen:
            seen.add(c)
            found.append(c)
    return found


# ---------- インデックス ----------

def load_index():
    """開催選択ページから、開催日リンクと重賞直リンクを解釈して返す。"""
    html = _post(INDEX_CNAME, TTL_INDEX)
    if "accessS" not in html:
        raise FetchError(
            "開催選択ページに遷移リンクが無い（レイアウト変更の可能性）。取得失敗。")
    days, races = [], []
    for c in _links(html):
        info = parse_cname(c)
        if not info:
            continue
        (days if info["kind"] == "day" else races).append(info)
    if not days and not races:
        raise FetchError("開催選択ページからCNAMEを1件も解釈できなかった。取得失敗。")
    return {"days": days, "races": races}


def find_race_cname(date, course, race_no):
    """日付・場・R番号から結果ページの CNAME を特定する。"""
    idx = load_index()
    code = CODE_BY_COURSE.get(course, course)

    for r in idx["races"]:          # 重賞は開催選択から直接リンクされている
        if r["date"] == date and r["course_code"] == code and r["race_no"] == race_no:
            return r["cname"]

    day = next((d for d in idx["days"]
                if d["date"] == date and d["course_code"] == code), None)
    if day is None:
        # 同日・別場の開催日リンクからでも同じ日のレース一覧に辿れる
        day = next((d for d in idx["days"] if d["date"] == date), None)
    if day is None:
        have = sorted({d["date"] for d in idx["days"]} |
                      {r["date"] for r in idx["races"]})
        raise FetchError(
            f"{date} は開催選択ページの収録範囲外（収録: {have[0]}〜{have[-1]}）。"
            "JRA公式は直近の開催のみ掲載するため、それ以前は取得失敗として扱う。")

    for c in _links(_post(day["cname"], TTL_INDEX)):
        info = parse_cname(c)
        if (info and info["kind"] == "race" and info["date"] == date
                and info["course_code"] == code and info["race_no"] == race_no):
            return info["cname"]
    raise FetchError(
        f"{date} {course}{race_no}R のリンクが一覧に見つからない。取得失敗。")


# ---------- 結果ページの解析 ----------

def _text(s):
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&#8544;", "I").replace("&#8545;", "II").replace("&#8546;", "III"))
    return " ".join(s.split())


def _section(html, cls, size):
    i = html.find('class="' + cls)
    return html[i:i + size] if i >= 0 else ""


def parse_result(html):
    r = {}
    head = _section(html, "race_result_unit", 6000)

    m = re.search(r'class="cell date">([^<]+)<', head)
    r["date_line"] = _text(m.group(1)) if m else None
    m = re.search(r'発走時刻：<strong>([^<]+)</strong>', head)
    r["post_time"] = _text(m.group(1)) if m else None

    m = re.search(r'class="race_name">(.*?)(?:<span class="grade_icon|</span>)',
                  head, re.S)
    r["race_name"] = _text(m.group(1)) if m else None
    m = re.search(r'icon_grade_(g\d|jg\d)\.png', head)
    r["grade"] = m.group(1).upper().replace("J", "J-") if m else None
    m = re.search(r'race_num_(\d+)\.png', head)
    r["race_no"] = int(m.group(1)) if m else None

    m = re.search(r'class="cell course">(.*?)</div>', head, re.S)
    r["course"] = _text(m.group(1)).replace("コース：", "") if m else None

    # 天候・発表馬場（芝/ダート別）
    # クラス名は turf / durt（※JRA側の綴りは "dirt" ではない）と揺れるため、
    # クラス名ではなく <span class="cap"> のラベル（天候/芝/ダート）で判別する。
    r["weather"] = None
    r["going"] = {}
    i = head.find('class="cell baba')
    baba = head[i:head.find("</div>", i)] if i >= 0 else ""
    for inner in re.findall(r"<li[^>]*>(.*?)</li>", baba, re.S):
        cap = re.search(r'<span class="cap">(.*?)</span>', inner, re.S)
        txt = re.search(r'<span class="txt">(.*?)</span>', inner, re.S)
        if not (cap and txt):
            continue
        label, val = _text(cap.group(1)), _text(txt.group(1))
        if label == "天候":
            r["weather"] = val
        else:
            r["going"][label] = val

    r["horses"] = _parse_horses(html)
    r["lap"] = _parse_lap(html)
    r["corner"] = _parse_corner(html)
    r["payouts"] = _parse_payouts(html)
    return r


def _parse_horses(html):
    i = html.find('class="race_result_unit')
    tb = html.find("<tbody", i)
    end = html.find("</tbody>", tb)
    if tb < 0 or end < 0:
        return []
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", html[tb:end], re.S):
        cells = dict((c, v) for c, v in
                     re.findall(r'<td class="([a-z_]+)"[^>]*>(.*?)</td>', tr, re.S))
        if "place" not in cells or not _text(cells["place"]):
            continue
        corner = [_text(x) for x in
                  re.findall(r"<li[^>]*>(.*?)</li>", cells.get("corner", ""), re.S)]
        hw = _text(cells.get("h_weight", ""))
        m = re.match(r"(\d+)\s*\(([-+]?\d+)\)", hw)
        waku = re.search(r"/waku/(\d+)\.png", cells.get("waku", ""))
        rows.append({
            "place": _text(cells["place"]),
            "waku": int(waku.group(1)) if waku else None,
            "horse_no": int(_text(cells.get("num", "0")) or 0),
            "horse": _text(cells.get("horse", "")),
            "age": _text(cells.get("age", "")),
            "weight": _text(cells.get("weight", "")),
            "jockey": _text(cells.get("jockey", "")),
            "time": _text(cells.get("time", "")),
            "margin": _text(cells.get("margin", "")),
            "corner": corner,
            "last_3f": _text(cells.get("f_time", "")),
            "horse_weight": int(m.group(1)) if m else None,
            "horse_weight_diff": int(m.group(2)) if m else None,
            "trainer": _text(cells.get("trainer", "")),
            "popularity": int(_text(cells.get("pop", "0")) or 0) or None,
        })
    return rows


def _parse_lap(html):
    sec = _section(html, "result_time_data", 2500)
    out = {}
    for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", sec, re.S):
        key = _text(th)
        val = _text(td)
        if key == "ハロンタイム":
            out["furlong"] = [x.strip() for x in val.split("-")]
        elif key == "上り":
            out["last"] = val
            for m in re.finditer(r"(\d)F\s*([\d.]+)", val):
                out["last_%sf" % m.group(1)] = float(m.group(2))
    return out


def _parse_corner(html):
    sec = _section(html, "result_corner_place", 3000)
    out = {}
    for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", sec, re.S):
        out[_text(th)] = _text(td)
    return out


def _parse_payouts(html):
    i = html.find('class="refund_area')
    if i < 0:
        return {}
    sec = html[i:]
    out = {}
    for li in re.findall(r'<li class="[a-z0-9_]+">\s*<dl>(.*?)</dl>', sec, re.S):
        m = re.search(r"<dt>(.*?)</dt>", li, re.S)
        if not m:
            continue
        name = _text(m.group(1))
        lines = []
        for ln in re.findall(r'<div class="line">(.*?)</div>\s*</div>', li, re.S):
            num = re.search(r'<div class="num">(.*?)</div>', ln, re.S)
            yen = re.search(r'<div class="yen">([\d,]+)', ln, re.S)
            pop = re.search(r'<div class="pop">(\d+)', ln, re.S)
            if num and yen:
                lines.append({
                    "combination": _text(num.group(1)),
                    "yen": int(yen.group(1).replace(",", "")),
                    "popularity": int(pop.group(1)) if pop else None,
                })
        if lines:
            out.setdefault(name, []).extend(lines)
    return out


# ---------- 出力 ----------

def render(r, cname):
    L = []
    L.append(f"■ {r.get('date_line')} {r.get('race_no')}R "
             f"{r.get('race_name')} {r.get('grade') or ''}".rstrip())
    L.append(f"  コース: {r.get('course')} ／ 発走: {r.get('post_time')}")
    going = " ".join(f"{k}{v}" for k, v in (r.get("going") or {}).items())
    L.append(f"  天候: {r.get('weather')} ／ 発表馬場: {going or '取得失敗'}")
    L.append(f"  CNAME: {cname}")
    L.append("")
    L.append("【着順】")
    L.append("着 枠 番 馬名                 性齢 斤量 騎手         タイム 着差   "
             "通過        上3F 馬体重     人気")
    for h in r["horses"]:
        L.append("{:>2} {:>2} {:>2} {:<20} {:<4} {:<4} {:<12} {:<6} {:<6} {:<11} "
                 "{:<4} {:<4}{:<5} {:>3}".format(
                     h["place"], h["waku"] or "", h["horse_no"], h["horse"][:20],
                     h["age"], h["weight"], h["jockey"][:12], h["time"],
                     h["margin"][:6], "-".join(h["corner"]), h["last_3f"],
                     h["horse_weight"] or "",
                     f"({h['horse_weight_diff']:+d})" if h["horse_weight_diff"]
                     is not None else "", h["popularity"] or ""))
    lap = r.get("lap") or {}
    L.append("")
    L.append("【ラップ】ハロンタイム: " +
             (" - ".join(lap.get("furlong", [])) or "取得失敗"))
    L.append("          上り: " + (lap.get("last") or "取得失敗"))
    L.append("")
    L.append("【コーナー通過順位】")
    for k, v in (r.get("corner") or {}).items():
        L.append(f"  {k}: {v}")
    L.append("")
    L.append("【払戻金】")
    for name, lines in (r.get("payouts") or {}).items():
        for ln in lines:
            pop = f"（{ln['popularity']}番人気）" if ln["popularity"] else ""
            L.append(f"  {name:<6} {ln['combination']:<12} "
                     f"{ln['yen']:>8,}円 {pop}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="JRA公式のレース結果を一次ソースとして取得する")
    ap.add_argument("--date", help="開催日 YYYY-MM-DD")
    ap.add_argument("--course", help="競馬場名（札幌/函館/福島/新潟/東京/中山/中京/京都/阪神/小倉）")
    ap.add_argument("--race", type=int, help="レース番号")
    ap.add_argument("--cname", help="結果ページのCNAMEを直接指定")
    ap.add_argument("--index", action="store_true",
                    help="取得可能な開催日・重賞リンクを一覧表示して終了")
    ap.add_argument("--json", action="store_true", help="JSONで出力")
    args = ap.parse_args()

    try:
        if args.index:
            idx = load_index()
            if args.json:
                print(json.dumps(idx, ensure_ascii=False, indent=1))
                return 0
            print("【開催日リンク】")
            for d in idx["days"]:
                print(f"  {d['date']} {d['course']} "
                      f"{d['kai']}回{d['course']}{d['day']}日  {d['cname']}")
            print("【重賞への直リンク】")
            for r in idx["races"]:
                print(f"  {r['date']} {r['course']}{r['race_no']}R  {r['cname']}")
            return 0

        if args.cname:
            cname = args.cname
        else:
            if not (args.date and args.course and args.race):
                ap.error("--cname か、--date/--course/--race の3点を指定")
            cname = find_race_cname(args.date, args.course, args.race)

        html = _get_result(cname)
        if "race_result_unit" not in html:
            raise FetchError(
                f"結果表が見つからない（未確定 or CNAME不正）: {cname}。取得失敗。")
        r = parse_result(html)
        if not r["horses"]:
            raise FetchError(f"着順を1頭も抽出できなかった: {cname}。取得失敗。")
        print(json.dumps(r, ensure_ascii=False, indent=1) if args.json
              else render(r, cname))
        return 0
    except (FetchError, PermissionError, RuntimeError) as e:
        print(f"[取得失敗] {e}", file=sys.stderr)
        print("→ 推測で埋めず「取得失敗」と記録すること。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
