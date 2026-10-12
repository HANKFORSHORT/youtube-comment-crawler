#!/usr/bin/env python3
"""
youtube_collector.py

Collect YouTube comments (YouTube Data API v3) into a CSV file with exactly
three columns: ID, parent_ID, text.
Every run ADDS to the file: comments already stored (matched by ID)
are skipped, nothing is overwritten.

Usage:
    python youtube_collector.py                       # uses videos from config.yaml,
                                                      # or asks you to paste links
    python youtube_collector.py URL [URL ...]         # extra links on the command line
    python youtube_collector.py --urls-file links.txt # one link per line
    python youtube_collector.py --config other.yaml
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
import yaml

API_BASE = "https://www.googleapis.com/youtube/v3"
SCRIPT_DIR = Path(__file__).resolve().parent
PLACEHOLDER_KEY = "YOUR_API_KEY_HERE"
PLACEHOLDER_KEYS = {PLACEHOLDER_KEY, "api_key_here"}
CSV_FIELDS = ["ID", "parent_ID", "text"]

DEFAULT_CONFIG = {
    "api_key": PLACEHOLDER_KEY,
    "output_file": "data/comments.csv",
    "videos": [],
    "order": "relevance",            # relevance | time
    "max_comments_per_video": 500,   # NEW comments added per video per run
    "max_scanned_per_video": 5000,   # safety cap on comments looked at per video per run
    "include_replies": True,
    "min_chars": 5,
    "only_code_switch_candidates": False,
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class QuotaExceeded(Exception):
    pass


class VideoUnavailable(Exception):
    pass


# --------------------------------------------------------------------------- #
# Config / input handling
# --------------------------------------------------------------------------- #
def load_config(path: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(yaml.safe_load(f) or {})
    else:
        print(f"[warn] config file not found: {path} (using defaults)")
    return cfg


ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_video_id(text: str) -> str | None:
    """Accepts watch?v=, youtu.be/, /shorts/, /embed/, /live/ links or a bare 11-char ID."""
    s = text.strip()
    if not s:
        return None
    if ID_RE.match(s):
        return s
    u = urlparse(s if "://" in s else "https://" + s)
    host = (u.hostname or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    vid = ""
    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [""])[0]
        else:
            parts = [p for p in u.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
                vid = parts[1]
    return vid if ID_RE.match(vid) else None


def prompt_for_links() -> list[str]:
    print("Paste YouTube links (one per line). Empty line to finish:")
    links = []
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            break
        links.append(line)
    return links


# --------------------------------------------------------------------------- #
# YouTube API
# --------------------------------------------------------------------------- #
def api_get(endpoint: str, params: dict, retries: int = 4) -> dict:
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(f"{API_BASE}/{endpoint}", params=params, timeout=30)
        except requests.RequestException as e:
            last_err = e
            time.sleep(2 ** attempt)
            continue

        if r.status_code == 200:
            return r.json()

        reason = ""
        try:
            reason = r.json()["error"]["errors"][0]["reason"]
        except Exception:
            pass

        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            raise QuotaExceeded(reason)
        if reason in ("commentsDisabled", "videoNotFound", "forbidden", "processingFailure"):
            raise VideoUnavailable(reason)
        if reason in ("keyInvalid", "badRequest") or r.status_code == 400:
            sys.exit(f"[error] API rejected the request ({reason or r.status_code}). "
                     f"Check your API key and that YouTube Data API v3 is enabled.")
        if r.status_code >= 500:
            last_err = RuntimeError(f"HTTP {r.status_code}")
            time.sleep(2 ** attempt)
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    raise RuntimeError(f"Request failed after {retries} attempts: {last_err}")


def get_video_title(key: str, video_id: str) -> str:
    """Only used for console progress messages; the title is NOT stored."""
    data = api_get("videos", {"part": "snippet", "id": video_id, "key": key})
    items = data.get("items", [])
    if not items:
        raise VideoUnavailable("videoNotFound")
    return items[0]["snippet"].get("title", "")


def iter_all_replies(key: str, parent_id: str):
    params = {
        "part": "snippet",
        "parentId": parent_id,
        "maxResults": 100,
        "textFormat": "plainText",
        "key": key,
    }
    while True:
        data = api_get("comments", params)
        yield from data.get("items", [])
        token = data.get("nextPageToken")
        if not token:
            break
        params["pageToken"] = token


def iter_comments(key: str, video_id: str, cfg: dict):
    """Yield (comment_resource, parent_id_or_None)."""
    params = {
        "part": "snippet,replies" if cfg["include_replies"] else "snippet",
        "videoId": video_id,
        "maxResults": 100,
        "order": cfg["order"],
        "textFormat": "plainText",
        "key": key,
    }
    while True:
        data = api_get("commentThreads", params)
        for thread in data.get("items", []):
            top = thread["snippet"]["topLevelComment"]
            yield top, None
            if not cfg["include_replies"]:
                continue
            total = thread["snippet"].get("totalReplyCount", 0)
            if not total:
                continue
            inline = thread.get("replies", {}).get("comments", [])
            replies = inline if len(inline) >= total else iter_all_replies(key, top["id"])
            for rep in replies:
                yield rep, top["id"]
        token = data.get("nextPageToken")
        if not token:
            break
        params["pageToken"] = token


# --------------------------------------------------------------------------- #
# Rough code-switching heuristic (a PRE-FILTER only, not a label!)
# Only used when only_code_switch_candidates: true. The flag is not stored.
# --------------------------------------------------------------------------- #
VI_DIACRITICS = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE,
)
# Common Vietnamese words typed WITHOUT diacritics (many comments are written like this).
# Ambiguous ones that are also English words (e.g. "to", "me", "so", "can") are left out.
VI_UNACCENTED = {
    "khong", "duoc", "nhung", "cung", "vay", "roi", "nhe", "nha", "oi", "qua",
    "thi", "cua", "minh", "ban", "nguoi", "rat", "lam", "nay", "gi", "va",
    "cho", "voi", "nhieu", "hay", "that", "dung", "xem", "biet", "thich", "anh", "chi",
}
EN_COMMON = {
    "the", "and", "is", "are", "i", "you", "this", "that", "it", "but", "for",
    "with", "not", "very", "good", "love", "nice", "best", "bad", "really",
    "like", "just", "my", "your", "we", "they", "of", "in", "at", "be", "have",
    "what", "how", "why", "will", "wow", "omg", "lol", "vibe", "chill", "cool",
    "amazing", "awesome", "thanks", "thank", "please", "sorry", "bro", "cringe",
    "flex", "literally", "actually", "so", "too", "when", "who", "all", "was",
}
# "that", "hay", "so" etc. are ambiguous; they're only used for a rough flag.
EN_COMMON -= {"so"}
VI_UNACCENTED -= {"that", "hay"}
TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def is_code_switch_candidate(text: str) -> bool:
    tokens = TOKEN_RE.findall(text.lower())
    if len(tokens) < 4:
        return False
    vi = en = 0
    for t in tokens:
        if VI_DIACRITICS.search(t) or t in VI_UNACCENTED:
            vi += 1
        elif t in EN_COMMON:
            en += 1
    return vi >= 1 and en >= 1


# --------------------------------------------------------------------------- #
# CSV storage (append-only): columns ID, parent_ID, text
# --------------------------------------------------------------------------- #
def load_seen_ids(path: Path) -> set[str]:
    """Return the IDs already stored in the CSV (empty set if there is no file yet)."""
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != CSV_FIELDS:
            sys.exit(f"[error] {path} does not have the expected header {CSV_FIELDS} "
                     f"(found {reader.fieldnames}). Fix or move it; nothing was changed.")
        return {row["ID"] for row in reader}


def append_rows(path: Path, rows: list[dict]) -> None:
    """Append rows to the CSV, writing the header first if the file is new/empty."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    # A new file gets a UTF-8 BOM so Excel displays Vietnamese correctly;
    # when appending, plain utf-8 avoids a stray BOM in the middle of the file.
    with open(path, "a", encoding="utf-8-sig" if is_new else "utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())


def make_row(res: dict, parent_id: str | None) -> dict:
    sn = res["snippet"]
    text = (sn.get("textDisplay") or sn.get("textOriginal") or "").strip()
    return {"ID": res["id"], "parent_ID": parent_id or "", "text": text}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Collect YouTube comments into a CSV file.")
    ap.add_argument("urls", nargs="*", help="YouTube links or video IDs")
    ap.add_argument("--config", default=str(SCRIPT_DIR / "config.yaml"))
    ap.add_argument("--urls-file", help="text file with one link per line")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))

    key = os.environ.get("YOUTUBE_API_KEY") or cfg.get("api_key")
    if not key or key in PLACEHOLDER_KEYS:
        sys.exit("[error] No API key. Put it in config.yaml (api_key) "
                 "or set the YOUTUBE_API_KEY environment variable.")

    raw_links = list(cfg.get("videos") or []) + list(args.urls)
    if args.urls_file:
        raw_links += [ln.strip() for ln in Path(args.urls_file).read_text(encoding="utf-8").splitlines()
                      if ln.strip() and not ln.strip().startswith("#")]
    if not raw_links:
        raw_links = prompt_for_links()

    video_ids: list[str] = []
    for link in raw_links:
        vid = extract_video_id(str(link))
        if not vid:
            print(f"[skip] could not read a video ID from: {link}")
        elif vid not in video_ids:
            video_ids.append(vid)
    if not video_ids:
        sys.exit("[error] No valid videos to process.")

    out_path = Path(cfg["output_file"])
    if not out_path.is_absolute():
        out_path = SCRIPT_DIR / out_path

    seen = load_seen_ids(out_path)
    total_stored = len(seen)
    print(f"Output: {out_path}  ({total_stored} comments already stored)\n")

    total_new = 0
    for vid in video_ids:
        try:
            title = get_video_title(key, vid)
        except VideoUnavailable as e:
            print(f"[skip] {vid}: {e}")
            continue
        except QuotaExceeded:
            print("[stop] API quota exhausted. Progress is saved; try again tomorrow.")
            break

        print(f"== {title[:70]}  [{vid}]")
        new_rows: list[dict] = []
        scanned = 0
        quota_hit = False
        try:
            for res, parent_id in iter_comments(key, vid, cfg):
                scanned += 1
                if res["id"] not in seen:
                    row = make_row(res, parent_id)
                    keep = len(row["text"]) >= cfg["min_chars"]
                    if keep and cfg["only_code_switch_candidates"] \
                            and not is_code_switch_candidate(row["text"]):
                        keep = False
                    if keep:
                        new_rows.append(row)
                        seen.add(row["ID"])
                if len(new_rows) >= cfg["max_comments_per_video"] or scanned >= cfg["max_scanned_per_video"]:
                    break
        except VideoUnavailable as e:
            print(f"   [skip] comments unavailable: {e}")
        except QuotaExceeded:
            quota_hit = True

        append_rows(out_path, new_rows)  # save after every video (and on quota stop)
        total_new += len(new_rows)
        total_stored += len(new_rows)

        if quota_hit:
            print(f"   +{len(new_rows)} new (partial)")
            print("[stop] API quota exhausted. Progress is saved; try again tomorrow.")
            break
        print(f"   scanned {scanned}, +{len(new_rows)} new\n")

    print(f"Done. +{total_new} new comments, {total_stored} total in {out_path.name}")


if __name__ == "__main__":
    main()
