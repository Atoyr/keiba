#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""polite_fetch.py — 相手サイトに負荷をかけない取得クライアント（標準ライブラリのみ）

設計方針（bot判定を「回避」するのではなく「bot扱いされない振る舞い」をする）:
  1. キャッシュ最優先。TTL内の同一URLはネットワークに出ない
  2. ホスト単位の最小アクセス間隔（既定8秒＋ジッタ）と1日あたり上限（既定60回）
  3. robots.txt を遵守。Disallow なら取得しない
  4. 連絡先入りの正直な User-Agent
  5. 429/503 は Retry-After を遵守して指数バックオフ（最大3回）
  6. 403/401 は「取得拒否」として即撤退。UA偽装・プロキシ等の回避策は取らない
     → 代替の動作実績ソース（JRA公式 keiba.go.jp / Yahoo競馬 denma）へ切り替える

使い方:
  python3 tools/polite_fetch.py <URL> [--ttl 21600] [--out file] [--force]
  python3 tools/polite_fetch.py --list urls.txt [--ttl 21600]

TTL の目安:
  出馬表(denma)      21600 (6時間)
  調教・過去統計     86400 (1日)
  確定結果・配当     31536000 (1年。確定後は変わらない)
  オッズ             300 が下限。締切直前でも5分以内の再取得はしない

キャッシュ・状態は repo直下の cache/ に置く（.gitignore 推奨）。
"""
import argparse
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlparse

USER_AGENT = (
    "keiba-kb/1.0 (personal research; polite low-rate fetcher; "
    "+https://github.com/atoyr/keiba)"
)
MIN_INTERVAL = 8.0          # 同一ホストへの最小間隔（秒）
JITTER = (1.0, 3.0)         # 間隔に加えるゆらぎ
DAILY_HOST_LIMIT = 60       # 同一ホスト1日あたりの実リクエスト上限
MIN_TTL = 300               # これ未満のTTLは受け付けない（オッズ連打防止）
MAX_RETRY = 3
TIMEOUT = 30

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
CACHE_DIR = os.path.join(BASE, "cache")
STATE_PATH = os.path.join(CACHE_DIR, "_host_state.json")


# ---------- 状態（ホスト別の最終アクセス時刻・当日カウント） ----------

def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def _throttle(host):
    """最小間隔の待機と1日上限のチェック。上限超過なら False。"""
    state = _load_state()
    h = state.get(host, {})
    today = time.strftime("%Y-%m-%d")
    if h.get("day") != today:
        h = {"day": today, "count": 0, "last": 0.0}
    if h["count"] >= DAILY_HOST_LIMIT:
        print(f"[STOP] {host}: 本日の上限 {DAILY_HOST_LIMIT} 回に到達。"
              "キャッシュを使うか明日以降に。", file=sys.stderr)
        return False
    wait = h["last"] + MIN_INTERVAL + random.uniform(*JITTER) - time.time()
    if wait > 0:
        print(f"[wait] {host} へ {wait:.1f}s 待機（負荷防止）", file=sys.stderr)
        time.sleep(wait)
    h["last"] = time.time()
    h["count"] += 1
    state[host] = h
    _save_state(state)
    return True


# ---------- robots.txt ----------

_robots_cache = {}

def _robots_ok(url):
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    rp = _robots_cache.get(origin)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            req = urllib.request.Request(origin + "/robots.txt",
                                         headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                rp.parse(r.read().decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                rp.disallow_all = True
            else:
                rp.allow_all = True  # robots.txt なし → 通常アクセスは可（礼儀は維持）
        except OSError:
            rp.allow_all = True
        _robots_cache[origin] = rp
    return rp.can_fetch(USER_AGENT, url)


# ---------- キャッシュ ----------

def _cache_paths(url):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return (os.path.join(CACHE_DIR, key + ".meta.json"),
            os.path.join(CACHE_DIR, key + ".body"))


def _read_cache(url, ttl, force):
    meta_p, body_p = _cache_paths(url)
    if not (os.path.exists(meta_p) and os.path.exists(body_p)):
        return None, None
    try:
        with open(meta_p, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None, None
    age = time.time() - meta.get("fetched_at", 0)
    if not force and age < ttl:
        with open(body_p, "rb") as f:
            return meta, f.read()   # 新鮮 → ネットワークに出ない
    return meta, None               # 期限切れ → 条件付きGETに使う


def _write_cache(url, meta, body):
    os.makedirs(CACHE_DIR, exist_ok=True)
    meta_p, body_p = _cache_paths(url)
    with open(body_p, "wb") as f:
        f.write(body)
    with open(meta_p, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


# ---------- 取得本体 ----------

def fetch(url, ttl, force=False):
    """キャッシュ→robots→レート制限→条件付きGET の順で取得。bytes を返す。"""
    ttl = max(ttl, MIN_TTL)
    meta, fresh = _read_cache(url, ttl, force)
    if fresh is not None:
        print(f"[cache] {url}（TTL内。実アクセスなし）", file=sys.stderr)
        return fresh

    if not _robots_ok(url):
        raise PermissionError(
            f"robots.txt が {url} の取得を許可していない。取得しない（回避もしない）。"
            "JRA公式(keiba.go.jp)・Yahoo競馬(denma)など代替ソースを使うこと。")

    host = urlparse(url).netloc
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if meta:  # 条件付きGET（変更なしなら 304 で転送量ほぼゼロ）
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]

    delay = 10
    for attempt in range(1, MAX_RETRY + 1):
        if not _throttle(host):
            raise RuntimeError(f"{host}: 1日上限到達")
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read()
                new_meta = {
                    "url": url,
                    "fetched_at": time.time(),
                    "status": r.status,
                    "etag": r.headers.get("ETag"),
                    "last_modified": r.headers.get("Last-Modified"),
                    "content_type": r.headers.get("Content-Type", ""),
                }
                _write_cache(url, new_meta, body)
                print(f"[GET {r.status}] {url} ({len(body)} bytes)", file=sys.stderr)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 304 and meta:
                meta["fetched_at"] = time.time()
                _, body_p = _cache_paths(url)
                with open(body_p, "rb") as f:
                    body = f.read()
                _write_cache(url, meta, body)
                print(f"[304] {url} 変更なし。キャッシュ再利用", file=sys.stderr)
                return body
            if e.code in (401, 403):
                raise PermissionError(
                    f"HTTP {e.code}: {host} が取得を拒否。回避策は取らない。"
                    "代替ソース（JRA公式 / Yahoo競馬 denma）へ切り替えること。")
            if e.code in (429, 503) and attempt < MAX_RETRY:
                ra = e.headers.get("Retry-After")
                try:
                    wait = min(float(ra), 120) if ra else delay
                except ValueError:
                    wait = delay
                print(f"[{e.code}] {wait:.0f}s 待機して再試行 "
                      f"({attempt}/{MAX_RETRY})", file=sys.stderr)
                time.sleep(wait)
                delay *= 3
                continue
            raise
        except OSError as e:
            if attempt < MAX_RETRY:
                print(f"[net error] {e} → {delay}s 後に再試行", file=sys.stderr)
                time.sleep(delay)
                delay *= 3
                continue
            raise
    raise RuntimeError("取得失敗（リトライ上限）。推測で埋めず「取得失敗」と記録する。")


def _decode(body, content_type=""):
    for enc in ("utf-8", "cp932", "euc-jp"):
        if enc in content_type.lower().replace("shift_jis", "cp932"):
            try:
                return body.decode(enc)
            except UnicodeDecodeError:
                pass
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser(description="負荷をかけない取得クライアント")
    ap.add_argument("url", nargs="?", help="取得するURL")
    ap.add_argument("--list", help="URLリストファイル（1行1URL。順次・間隔つきで取得）")
    ap.add_argument("--ttl", type=int, default=21600,
                    help="キャッシュ有効秒数（既定6時間・下限300）")
    ap.add_argument("--out", help="本文の保存先（省略時は標準出力にテキスト表示）")
    ap.add_argument("--force", action="store_true",
                    help="TTLを無視して再確認（条件付きGETは維持）")
    args = ap.parse_args()

    urls = []
    if args.list:
        with open(args.list, encoding="utf-8") as f:
            urls = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    elif args.url:
        urls = [args.url]
    else:
        ap.error("URL か --list を指定")

    for i, url in enumerate(urls):
        try:
            body = fetch(url, args.ttl, args.force)
        except (PermissionError, RuntimeError, urllib.error.HTTPError) as e:
            print(f"[取得失敗] {url}: {e}", file=sys.stderr)
            continue
        if args.out:
            out = args.out if len(urls) == 1 else f"{args.out}.{i:02d}"
            with open(out, "wb") as f:
                f.write(body)
            print(f"→ {out}")
        else:
            meta_p, _ = _cache_paths(url)
            ct = ""
            try:
                with open(meta_p, encoding="utf-8") as f:
                    ct = json.load(f).get("content_type", "")
            except (OSError, ValueError):
                pass
            print(_decode(body, ct))


if __name__ == "__main__":
    main()
