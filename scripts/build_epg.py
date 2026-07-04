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

import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/us.m3u"

# Plain (non-gzipped) EPG source files. globetvapp splits US channels
# across several numbered files; fetching all of them for the widest
# possible match coverage.
EPG_URLS = [
    "https://raw.githubusercontent.com/globetvapp/epg/main/Usa/usa1.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Usa/usa2.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Usa/usa3.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Usa/usa4.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Usa/usa5.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Usa/usa6.xml",
]

OUTPUT_PATH = "docs/now-playing.json"

# Being a good citizen of the free data sources this script depends on.
REQUEST_HEADERS = {
    "User-Agent": "USOpenTV-EPG-Builder/1.0 (personal, non-commercial Roku app)"
}


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_name(name: str) -> str:
    """Lowercases and strips punctuation/whitespace for fuzzy matching."""
    name = name.lower()
    name = re.sub(r"\(.*?\)", "", name)  # drop parenthetical quality tags e.g. "(1080p)"
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def parse_playlist_channel_names(m3u_text: str) -> list:
    names = []
    for line in m3u_text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            comma_pos = line.rfind(",")
            if comma_pos != -1:
                names.append(line[comma_pos + 1:].strip())
    return names


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
    channel_names = parse_playlist_channel_names(playlist_text)
    print(f"  Found {len(channel_names)} channels in playlist")
    print(f"  Sample playlist names: {channel_names[:10]}")

    all_epg_channel_names = {}
    all_epg_programmes = {}

    for url in EPG_URLS:
        print(f"Fetching EPG file: {url}")
        try:
            xml_text = fetch_text(url)
        except Exception as error:
            print(f"  WARNING: failed to fetch ({error}); skipping", file=sys.stderr)
            continue

        names, programmes = parse_epg_file(xml_text)
        all_epg_channel_names.update(names)
        all_epg_programmes.update(programmes)
        print(f"  Parsed {len(names)} channels, {len(programmes)} channels with listings")

    # Build a normalized-name -> epg channel id lookup for matching.
    normalized_epg_lookup = {}
    for channel_id, display_name in all_epg_channel_names.items():
        normalized_epg_lookup[normalize_name(display_name)] = channel_id

    sample_epg_names = list(all_epg_channel_names.values())[:10]
    print(f"  Sample EPG display names: {sample_epg_names}")
    sample_normalized_epg = list(normalized_epg_lookup.keys())[:10]
    print(f"  Sample normalized EPG names: {sample_normalized_epg}")
    sample_normalized_playlist = [normalize_name(n) for n in channel_names[:10]]
    print(f"  Sample normalized playlist names: {sample_normalized_playlist}")

    now = datetime.now(timezone.utc)
    print(f"Current time (UTC): {now.isoformat()}")

    output = {}
    matched_count = 0

    for playlist_name in channel_names:
        normalized_playlist_name = normalize_name(playlist_name)
        if len(normalized_playlist_name) < 3:
            continue

        epg_channel_id = normalized_epg_lookup.get(normalized_playlist_name)

        # Exact match failed -- try substring matching in both directions.
        # This is intentionally a broad heuristic: e.g. "abc" (from an
        # entry like "ABC (East)") matching within "kabcdtlosangeles"
        # (from a local affiliate's display name), or vice versa for
        # longer, more specific playlist names.
        if epg_channel_id is None:
            for candidate_normalized_name, candidate_id in normalized_epg_lookup.items():
                if len(candidate_normalized_name) < 3:
                    continue
                if (normalized_playlist_name in candidate_normalized_name
                        or candidate_normalized_name in normalized_playlist_name):
                    epg_channel_id = candidate_id
                    break

        if epg_channel_id is None:
            continue

        programmes = all_epg_programmes.get(epg_channel_id, [])
        now_playing = find_now_playing(programmes, now)

        if now_playing is not None:
            output[normalized_playlist_name] = now_playing
            matched_count += 1

    print(f"Matched now-playing data for {matched_count} / {len(channel_names)} channels")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generatedAt": now.isoformat(),
            "channels": output,
        }, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
