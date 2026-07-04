# US Open TV - EPG Data Pipeline

Generates a small `now-playing.json` file for the US Open TV Roku app,
so the channel guide can show real "now playing" program titles instead
of a static placeholder.

## How it works

1. `.github/workflows/update-epg.yml` runs `scripts/build_epg.py` on a
   schedule (every 30 minutes) via GitHub Actions.
2. The script downloads the same channel playlist the Roku app uses,
   plus a set of plain (non-gzipped) XMLTV EPG files, matches channels
   by name, and figures out what's airing right now on each one.
3. The result is written to `docs/now-playing.json` -- a small file,
   typically just a few KB, regardless of how large the source EPG data
   is.
4. GitHub Pages serves the `docs/` folder, so the file is reachable at:
   `https://<your-username>.github.io/usopentv-epg/now-playing.json`

## Setup (one-time)

1. Create a new **public** GitHub repository and upload this folder's
   contents to it (or push via git).
2. Go to the repo's **Settings -> Pages**, set Source to "Deploy from a
   branch", branch `main`, folder `/docs`, then Save.
3. Go to the repo's **Settings -> Actions -> General**, scroll to
   "Workflow permissions", and make sure **Read and write permissions**
   is selected (required so the workflow can commit the updated JSON
   file back to the repo).
4. Go to the **Actions** tab, click into "Update EPG data", and click
   **Run workflow** to trigger a first manual run rather than waiting up
   to 30 minutes for the schedule.

## Data format

```json
{
  "generatedAt": "2026-07-04T18:30:00+00:00",
  "channels": {
    "abcnewslive": { "title": "World News Tonight" },
    "wfla": { "title": "News Channel 8 at Six" }
  }
}
```

Keys are normalized channel names (lowercased, punctuation/spaces
stripped) so the Roku app can look up a match using the same
normalization on the channel names it already has from the playlist.
Channels with no confident match, or nothing currently airing according
to the EPG data, are simply omitted -- the app falls back to a generic
placeholder for those.

## Known limitations

- Matching is by channel **name**, not a shared ID -- the EPG source
  uses call-sign-based IDs (e.g. `KABCDT.us`) while the playlist uses a
  different scheme, so there's no reliable common identifier. Name
  matching is normalized but not fuzzy/typo-tolerant, so some channels
  with slightly different naming between the two sources won't match.
- EPG coverage itself is whatever the upstream source provides -- niche
  or newer channels may simply have no listings available.
