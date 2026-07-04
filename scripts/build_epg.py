#!/usr/bin/env python3
"""
build_epg.py

Downloads the US channel playlist (same iptv-org source the Roku app
already uses) and a set of plain (non-gzipped) XMLTV EPG files, matches
channels by name, and writes out a small JSON file containing just the
currently-airing program title for each matched channel.

This script is designed to run on a schedule via GitHub Actions -- see
.github/workflows/update-epg.yml -- so all the heavy lifting (downloading
several MB of XML, parsing it, matching channels) happens here on GitHub's
servers, not on the Roku device. The Roku app only ever needs to fetch the
small output file this script produces.
"""

import gzip
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/us.m3u"

# EPGTalk pulls its channel database from the same iptv-org/database
# project our playlist comes from, so its channel IDs have a real chance
# of matching tvg-id directly -- unlike globetvapp's EPG source, which
# uses unrelated call-sign-based IDs with essentially zero overlap.
EPG_URLS = [
    "https://raw.githubusercontent.com/acidjesuz/EPGTalk/master/US_guide.xml.gz",
]

OUTPUT_PATH = "docs/now-playing.json"

REQUEST_HEADERS = {
    "User-Agent": "USOpenTV-EPG-Builder/1.0 (personal, non-commercial Roku app)"
}


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8", errors="replace")


def fetch_gzip_text(url: str) -> str:
    """Fetches a .gz URL and decompresses it in Python -- sidesteps the
    on-device decompression problems we hit trying to do this directly
    on the Roku."""
    compressed = fetch_bytes(url)
    return gzip.decompress(compressed).decode("utf-8", errors="replace")


def normalize_name(name: str) -> str:
    """Lowercases and strips punctuation/whitespace for fuzzy matching."""
    name = name.lower()
    name = re.sub(r"\(.*?\)", "", name)  # drop parenthetical quality tags e.g. "(1080p)"
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def extract_attribute(line: str, attr_name: str) -> str:
    marker = f'{attr_name}="'
    start = line.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = line.find('"', start)
    if end == -1:
        return ""
    return line[start:end]


def parse_playlist_channels(m3u_text: str) -> list:
    """Returns a list of {"name": ..., "tvg_id": ...} dicts."""
    channels = []
    for line in m3u_text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            comma_pos = line.rfind(",")
            name = line[comma_pos + 1:].strip() if comma_pos != -1 else ""
            tvg_id = extract_attribute(line, "tvg-id")
            if name:
                channels.append({"name": name, "tvg_id": tvg_id})
    return channels


def parse_epg_file(xml_text: str) -> tuple:
    """Returns (channel_id_to_display_name, channel_id_to_programmes)."""
    channel_names = {}
    programmes_by_channel = {}

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        print(f"  WARNING: failed to parse XML ({error}); skipping this file", file=sys.stderr)
        return channel_names, programmes_by_channel

    for channel_el in root.findall("channel"):
        channel_id = channel_el.get("id", "")
        display_name_el = channel_el.find("display-name")
        if channel_id and display_name_el is not None and display_name_el.text:
            channel_names[channel_id] = display_name_el.text.strip()

    for programme_el in root.findall("programme"):
        channel_id = programme_el.get("channel", "")
        start_raw = programme_el.get("start", "")
        stop_raw = programme_el.get("stop", "")
        title_el = programme_el.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else None

        if channel_id and start_raw and stop_raw and title:
            programmes_by_channel.setdefault(channel_id, []).append({
                "start": start_raw,
                "stop": stop_raw,
                "title": title,
            })

    return channel_names, programmes_by_channel


def parse_xmltv_time(raw: str) -> datetime:
    """
    XMLTV times look like "20260703160000 +0000". Parses into an aware
    datetime for comparison against "now".
    """
    raw = raw.strip()
    main_part = raw[:14]
    offset_part = raw[14:].strip() or "+0000"

    dt = datetime.strptime(main_part, "%Y%m%d%H%M%S")

    sign = 1 if offset_part[0] == "+" else -1
    offset_hours = int(offset_part[1:3])
    offset_minutes = int(offset_part[3:5])
    total_offset_minutes = sign * (offset_hours * 60 + offset_minutes)

    dt = dt.replace(tzinfo=timezone.utc)
    from datetime import timedelta
    dt = dt - timedelta(minutes=total_offset_minutes)
    return dt


def find_now_playing(programmes: list, now: datetime) -> dict:
    for programme in programmes:
        try:
            start = parse_xmltv_time(programme["start"])
            stop = parse_xmltv_time(programme["stop"])
        except (ValueError, IndexError):
            continue
        if start <= now <= stop:
            return {"title": programme["title"]}
    return None


def main():
    print("Fetching channel playlist...")
    playlist_text = fetch_text(PLAYLIST_URL)
    playlist_channels = parse_playlist_channels(playlist_text)
    print(f"  Found {len(playlist_channels)} channels in playlist")
    print(f"  Sample playlist channels: {playlist_channels[:5]}")

    all_epg_channel_names = {}
    all_epg_programmes = {}

    for url in EPG_URLS:
        print(f"Fetching EPG file: {url}")
        try:
            if url.endswith(".gz"):
                xml_text = fetch_gzip_text(url)
            else:
                xml_text = fetch_text(url)
        except Exception as error:
            print(f"  WARNING: failed to fetch ({error}); skipping", file=sys.stderr)
            continue

        names, programmes = parse_epg_file(xml_text)
        all_epg_channel_names.update(names)
        all_epg_programmes.update(programmes)
        print(f"  Parsed {len(names)} channels, {len(programmes)} channels with listings")

    sample_ids = list(all_epg_channel_names.items())[:10]
    print(f"  Sample EPG channel id -> display name: {sample_ids}")

    # Build a normalized-name -> epg channel id lookup, used only as a
    # fallback when a direct tvg-id match isn't found.
    normalized_epg_lookup = {}
    for channel_id, display_name in all_epg_channel_names.items():
        normalized_epg_lookup[normalize_name(display_name)] = channel_id

    # Exact-id lookup set, for fast direct matching against tvg-id.
    epg_ids_lowercase = {cid.lower(): cid for cid in all_epg_channel_names.keys()}

    now = datetime.now(timezone.utc)
    print(f"Current time (UTC): {now.isoformat()}")

    output = {}
    matched_by_id = 0
    matched_by_name = 0

    for channel in playlist_channels:
        playlist_name = channel["name"]
        tvg_id = channel["tvg_id"]
        normalized_playlist_name = normalize_name(playlist_name)

        epg_channel_id = None
        match_method = None

        # Try 1: direct tvg-id match (case-insensitive) -- the correct,
        # reliable approach when both sources share an ID scheme.
        if tvg_id:
            epg_channel_id = epg_ids_lowercase.get(tvg_id.lower())
            if epg_channel_id is not None:
                match_method = "id"

        # Try 2: normalized display-name substring match, as a fallback.
        if epg_channel_id is None and len(normalized_playlist_name) >= 3:
            epg_channel_id = normalized_epg_lookup.get(normalized_playlist_name)
            if epg_channel_id is not None:
                match_method = "name-exact"
            else:
                for candidate_name, candidate_id in normalized_epg_lookup.items():
                    if len(candidate_name) < 3:
                        continue
                    if (normalized_playlist_name in candidate_name
                            or candidate_name in normalized_playlist_name):
                        epg_channel_id = candidate_id
                        match_method = "name-substring"
                        break

        if epg_channel_id is None:
            continue

        programmes = all_epg_programmes.get(epg_channel_id, [])
        now_playing = find_now_playing(programmes, now)

        if now_playing is not None:
            output[normalized_playlist_name] = now_playing
            if match_method == "id":
                matched_by_id += 1
            else:
                matched_by_name += 1

    total_matched = matched_by_id + matched_by_name
    print(f"Matched now-playing data for {total_matched} / {len(playlist_channels)} channels "
          f"({matched_by_id} by tvg-id, {matched_by_name} by name)")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generatedAt": now.isoformat(),
            "channels": output,
        }, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
