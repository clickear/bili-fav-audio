import json
import os
import subprocess
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import requests

UID = os.environ["BILI_UID"]
FID = os.environ["BILI_FID"]
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TAG = os.environ.get("AUDIO_TAG", "audio")
COOKIE = os.environ.get("BILI_COOKIE", "").strip()
SITE_BASE = os.environ.get("SITE_BASE", f"https://github.com/{REPO}/releases/download/{TAG}")

AUDIO_DIR = Path("audio")
STATE_FILE = Path("state.json")
FEED_FILE = Path("feed.xml")
COOKIE_FILE = Path("cookies.txt")
API = "https://api.bilibili.com/x/v3/fav/resource/list"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/",
}
if COOKIE:
    HEADERS["Cookie"] = COOKIE


def write_cookies():
    if not COOKIE:
        return None
    lines = ["# Netscape HTTP Cookie File"]
    for part in COOKIE.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        lines.append("\t".join([".bilibili.com", "TRUE", "/", "TRUE", "0", name, value]))
    COOKIE_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return COOKIE_FILE


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"info": {}, "items": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_list():
    items, info, pn = [], {}, 1
    while True:
        params = {"media_id": FID, "pn": pn, "ps": 20, "order": "mtime", "type": 0, "tid": 0, "jsonp": "jsonp"}
        data = requests.get(API, params=params, headers=HEADERS, timeout=30).json().get("data") or {}
        if not info:
            info = data.get("info") or {}
        items.extend(data.get("medias") or [])
        if not data.get("has_more"):
            break
        pn += 1
    return items, info


def download_audio(bvid):
    out = AUDIO_DIR / f"{bvid}.m4a"
    if out.exists():
        return out
    cmd = [
        "yt-dlp", "--no-update", "-f", "ba", "-x", "--audio-format", "m4a",
        "--no-playlist", "--referer", "https://www.bilibili.com/",
        "-o", str(AUDIO_DIR / f"{bvid}.%(ext)s"),
        f"https://www.bilibili.com/video/{bvid}",
    ]
    if COOKIE_FILE.exists():
        cmd += ["--cookies", str(COOKIE_FILE)]
    subprocess.run(cmd, check=True)
    return out


def ensure_release():
    if not REPO:
        return
    subprocess.run(
        ["gh", "release", "create", TAG, "--title", "audio", "--notes", "audio assets", "--latest=false"],
        capture_output=True,
    )


def upload_asset(path):
    if not REPO:
        return
    subprocess.run(["gh", "release", "upload", TAG, str(path), "--clobber"], check=True)


def rfc822(ts):
    return format_datetime(datetime.fromtimestamp(ts, tz=timezone.utc))


def build_feed(state):
    info = state["info"]
    title = info.get("title", "bilibili 收藏夹")
    cover = info.get("cover", "")
    home = f"https://space.bilibili.com/{UID}/favlist?fid={FID}"
    now = format_datetime(datetime.now(tz=timezone.utc))

    ordered = sorted(state["items"].values(), key=lambda x: x["ctime"], reverse=True)
    entries = []
    for it in ordered:
        url = f"{SITE_BASE}/{it['bvid']}.m4a"
        entries.append(f"""    <item>
      <title>{escape(it['title'])}</title>
      <link>https://www.bilibili.com/video/{it['bvid']}</link>
      <guid isPermaLink="false">{it['bvid']}</guid>
      <pubDate>{rfc822(it['ctime'])}</pubDate>
      <author>{escape(it.get('author', ''))}</author>
      <description>{escape(it.get('intro', ''))}</description>
      <itunes:image href="{escape(it.get('cover', ''))}"/>
      <enclosure url="{escape(url)}" type="audio/mp4" length="{it.get('size', 0)}"/>
    </item>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(title)}</title>
    <link>{home}</link>
    <description>{escape(title)} - bilibili 收藏夹音频</description>
    <language>zh-cn</language>
    <lastBuildDate>{now}</lastBuildDate>
    <itunes:image href="{escape(cover)}"/>
    <itunes:author>bilibili</itunes:author>
{chr(10).join(entries)}
  </channel>
</rss>
"""
    FEED_FILE.write_text(xml, encoding="utf-8")


def main():
    AUDIO_DIR.mkdir(exist_ok=True)
    write_cookies()
    state = load_state()
    medias, info = fetch_list()
    if info:
        state["info"] = info
    ensure_release()

    for m in medias:
        bvid = m["bvid"]
        if m.get("attr", 0) != 0:
            continue
        if bvid in state["items"]:
            continue
        try:
            path = download_audio(bvid)
        except subprocess.CalledProcessError:
            continue
        upload_asset(path)
        state["items"][bvid] = {
            "bvid": bvid,
            "title": m.get("title", ""),
            "intro": m.get("intro", ""),
            "cover": m.get("cover", ""),
            "author": (m.get("upper") or {}).get("name", ""),
            "ctime": m.get("ctime", 0),
            "size": path.stat().st_size if path.exists() else 0,
        }
        save_state(state)

    build_feed(state)
    save_state(state)


if __name__ == "__main__":
    main()
