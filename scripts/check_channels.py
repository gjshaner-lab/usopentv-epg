#!/usr/bin/env python3
"""
check_channels.py

Downloads the same US channel playlist the Roku app uses, and checks
whether each channel's stream URL is actually reachable right now.
Writes a small JSON file mapping each channel (by the same normalized
name key used elsewhere in this pipeline) to whether it appears to be
working, so the Roku app can skip dead channels without needing to try
each one itself.

This is a "best effort, right now" check, not a guarantee -- a stream
can go up or down between check runs, and some servers block automated
checks even though they work fine in a real player. See the workflow's
schedule for how often this re-runs.
"""

import json
import re
import ssl
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/us.m3u"
OUTPUT_PATH = "docs/channel-status.json"

# How many channels to check at once. Higher = faster overall, but more
# load on both GitHub's runner and the many third-party stream servers
# being checked. 30 is a reasonable middle ground for ~1,500 channels.
MAX_CONCURRENCY = 30

# Per-request timeout. Live stream servers that are actually up usually
# respond quickly; anything hanging past this is treated as not working.
REQUEST_TIMEOUT_SECONDS = 8

# Presenting as a normal browser-like client, since some stream servers
# block requests that don't look like a real player/browser. This
# matches what an actual Roku device's HTTP client looks like to a
# server, for an accurate "would this work in the app" signal -- not
# intended to bypass any real access control.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; USOpenTV-HealthCheck/1.0)"
}

# Many small/independent IPTV stream servers run with self-signed or
# misconfigured certificates. For this specific reachability check
# (not the EPG/playlist fetches elsewhere in this pipeline, which use
# trusted sources), we care whether the stream responds at all, not
# whether its TLS setup is perfect.
INSECURE_SSL_CONTEXT = ssl.create_default_context()
INSECURE_SSL_CONTEXT.check_hostname = False
INSECURE_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def parse_playlist_channels(m3u_text: str) -> list:
    """Returns a list of {"name": ..., "url": ...} dicts."""
    channels = []
    pending_name = None

    for raw_line in m3u_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            comma_pos = line.rfind(",")
            pending_name = line[comma_pos + 1:].strip() if comma_pos != -1 else None
        elif line.startswith("#"):
            continue
        elif line.startswith("http"):
            if pending_name:
                channels.append({"name": pending_name, "url": line})
            pending_name = None

    return channels


def check_channel(channel: dict) -> dict:
    name = channel["name"]
    url = channel["url"]
    normalized = normalize_name(name)

    try:
        request = urllib.request.Request(url, headers=REQUEST_HEADERS, method="HEAD")
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS, context=INSECURE_SSL_CONTEXT
        ) as response:
            status = response.status
            if 200 <= status < 400:
                return {"key": normalized, "working": True}
    except urllib.error.HTTPError as error:
        # Some stream servers reject HEAD requests specifically (405) but
        # work fine for a real GET -- fall through and try that instead.
        if error.code != 405:
            return {"key": normalized, "working": False}
    except Exception:
        return {"key": normalized, "working": False}

    # Fall back to a small GET, reading only a few hundred bytes before
    # closing, to confirm reachability without downloading a live stream.
    try:
        request = urllib.request.Request(url, headers=REQUEST_HEADERS, method="GET")
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS, context=INSECURE_SSL_CONTEXT
        ) as response:
            response.read(512)
            status = response.status
            return {"key": normalized, "working": 200 <= status < 400}
    except Exception:
        return {"key": normalized, "working": False}


def main():
    print("Fetching channel playlist...")
    playlist_text = fetch_text(PLAYLIST_URL)
    channels = parse_playlist_channels(playlist_text)
    print(f"  Found {len(channels)} channels to check")

    now = datetime.now(timezone.utc)
    print(f"Current time (UTC): {now.isoformat()}")
    print(f"Checking channels with concurrency={MAX_CONCURRENCY}, "
          f"timeout={REQUEST_TIMEOUT_SECONDS}s per request...")

    results = {}
    working_count = 0
    checked_count = 0

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures = {executor.submit(check_channel, ch): ch for ch in channels}

        for future in as_completed(futures):
            checked_count += 1
            result = future.result()
            results[result["key"]] = result["working"]
            if result["working"]:
                working_count += 1

            if checked_count % 100 == 0:
                print(f"  Checked {checked_count} / {len(channels)} "
                      f"({working_count} working so far)")

    print(f"Done: {working_count} / {len(channels)} channels responding")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generatedAt": now.isoformat(),
            "channels": results,
        }, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
