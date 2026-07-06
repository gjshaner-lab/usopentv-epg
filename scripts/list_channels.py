#!/usr/bin/env python3
"""
list_channels.py

Generates a human-readable list of channels the app would actually show
right now: the full US playlist, with the same English-language
heuristic filter the Roku app applies, and with channels the health
checker has flagged as not responding removed too.

This mirrors (in Python) the filtering logic that actually lives in the
Roku app's BrightScript code (ChannelLoaderTask.brs), so what you see
here should match what's really in the guide. If you change the filter
rules in one place, keep the other in sync.
"""

import json
import re
import sys
import urllib.request

PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/us.m3u"
STATUS_PATH = "docs/channel-status.json"
OUTPUT_PATH = "docs/channel-list.txt"

REQUEST_HEADERS = {
    "User-Agent": "USOpenTV-EPG-Builder/1.0 (personal, non-commercial Roku app)"
}

NON_ENGLISH_ID_SUFFIXES = [
    "@spanish", "@español", "@portuguese", "@french", "@arabic",
    "@chinese", "@korean", "@vietnamese", "@hindi", "@russian",
    "@german", "@italian", "@polish", "@tagalog", "@filipino",
    "@punjabi", "@urdu", "@persian", "@farsi", "@turkish",
    "@japanese", "@thai", "@indonesian", "@greek", "@hebrew",
    "@armenian", "@somali", "@amharic", "@ukrainian", "@romanian",
    "@bengali", "@gujarati", "@tamil", "@telugu",
]

NON_ENGLISH_KEYWORDS = [
    "univision", "telemundo", "unimas", "unimás", "caracol",
    "azteca", "estrella tv", "en espanol", "en español", "cinelatino",
    "galavision", "galavisión", "bandamax", "ritmoson", "pasiones",
    "de pelicula", "de película", "ntn24", "vme", "wapa", "tlnovelas",
    "distrito comedia", "discovery familia", "canal sur",
    "tv chile", "america tve", "américa tve", "antena 3", "hola tv",
    "mega tv", "latele novela", "mtv tres", "sur peru", "sur perú",
    "cnn en espanol", "cnn en español", "fox deportes", "tudn",
    "bein sports espanol", "bein sports en espanol",
]

NON_ENGLISH_LANGUAGE_WORDS = [
    "french", "latino", "latina", "spanish", "portuguese", "arabic",
    "german", "italian", "polish", "russian", "chinese", "mandarin",
    "cantonese", "korean", "vietnamese", "hindi", "tagalog",
    "filipino", "punjabi", "urdu", "persian", "farsi", "turkish",
    "japanese", "thai", "indonesian", "greek", "hebrew", "armenian",
    "somali", "amharic", "ukrainian", "romanian", "bengali",
    "gujarati", "tamil", "telugu", "creole", "haitian",
]

ACCENTED_CHARS = ["á", "é", "í", "ó", "ú", "ñ", "ü", "ã", "õ", "ç",
                  "à", "â", "ê", "î", "ô", "û", "ë", "ï"]


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\(.*?\)", "", name)
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
    channels = []
    pending_name = None
    pending_tvg_id = None

    for raw_line in m3u_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            comma_pos = line.rfind(",")
            pending_name = line[comma_pos + 1:].strip() if comma_pos != -1 else None
            pending_tvg_id = extract_attribute(line, "tvg-id")
        elif line.startswith("#"):
            continue
        elif line.startswith("http"):
            if pending_name:
                channels.append({"name": pending_name, "tvg_id": pending_tvg_id or ""})
            pending_name = None
            pending_tvg_id = None

    return channels


def is_likely_english(name: str, tvg_id: str) -> bool:
    name_lower = name.lower()
    id_lower = tvg_id.lower()

    for suffix in NON_ENGLISH_ID_SUFFIXES:
        if id_lower.endswith(suffix):
            return False

    for keyword in NON_ENGLISH_KEYWORDS:
        if keyword in name_lower:
            return False

    for word in NON_ENGLISH_LANGUAGE_WORDS:
        if word in name_lower:
            return False

    for ch in ACCENTED_CHARS:
        if ch in name_lower:
            return False

    return True


def load_channel_status() -> dict:
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("channels", {})
    except Exception as error:
        print(f"  WARNING: could not read {STATUS_PATH} ({error}); "
              f"treating all channels as unknown status", file=sys.stderr)
        return {}


def main():
    print("Fetching channel playlist...")
    playlist_text = fetch_text(PLAYLIST_URL)
    all_channels = parse_playlist_channels(playlist_text)
    print(f"  Found {len(all_channels)} channels total")

    status_map = load_channel_status()
    print(f"  Loaded status for {len(status_map)} channels from {STATUS_PATH}")

    kept_channels = []
    excluded_language = 0
    excluded_dead = 0

    for channel in all_channels:
        name = channel["name"]
        tvg_id = channel["tvg_id"]

        if not is_likely_english(name, tvg_id):
            excluded_language += 1
            continue

        key = normalize_name(name)
        if status_map.get(key) is False:
            excluded_dead += 1
            continue

        kept_channels.append(name)

    kept_channels.sort(key=str.lower)

    print(f"Kept {len(kept_channels)} channels "
          f"(excluded {excluded_language} non-English, {excluded_dead} not responding)")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(f"US Open TV -- Channel List\n")
        f.write(f"{len(kept_channels)} channels currently shown in the app\n")
        f.write(f"(excluded: {excluded_language} non-English, {excluded_dead} not responding)\n")
        f.write("=" * 60 + "\n\n")
        for name in kept_channels:
            f.write(name + "\n")

    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
