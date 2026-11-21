# Release Kit

A local app for releasing music everywhere from one place. It runs on your computer, opens in your browser, and uses ffmpeg to render audio and video.

## What it does

| Tab | What you get |
|---|---|
| **Release** | One form for title, artist, genre, mood, dates, (P)/(C) lines, UPC, and per-track ISRC, credits, BPM, key and lyrics. **Analyze** checks loudness (LUFS), true peak, sample rate, lossy sources, and estimates BPM. |
| **Artwork** | Pick a photo folder. Photos are ranked by resolution, sharpness and contrast. Make a 3000×3000 cover with smart crop, center crop, or fit plus blurred fill. You can add title text and an explicit label. You can also **generate** art in 6 styles, optionally using colors from one of your photos. Export sizes for every platform, and make a contact sheet of a folder. |
| **YouTube & video** | Release videos (static, waveform, frequency bars, line, spectrum). Full-album videos with chapters. **10-hour (or 1–12 h) loop videos** with a seamless crossfaded loop point: one track or a playlist, static, slow-zoom or photo-slideshow visuals, plus an optional rain or vinyl ambient layer. Shorts, Reels and TikTok clips that auto-pick the strongest section. Spotify Canvas loops. |
| **Packages** | **Distributor** (WAV, cover, metadata.csv/json, lyrics, pre-submit checklist). **Bandcamp + SoundCloud** (FLAC, covers, paste-ready text). **Stock music** (WAVs, 60/30/15 s cut-downs, Shutterstock MP3s, keyword CSV). **Stock photo pack** (cleaned JPGs plus a Shutterstock-format CSV). |
| **Upload** | Writes the YouTube title, description (with your links, credits, chapters and hashtags) and tags, with length counters. Uploads through the YouTube API with scheduling, thumbnail and playlist (optional setup). |
| **Plan** | A release timeline worked back from your date, downloadable as a calendar (.ics). |
| **Sign-ups** | Links to every distributor, artist-profile claim, store, stock library, royalty organization (ASCAP/BMI, The MLC, SoundExchange) and the YouTube API setup. Includes checkboxes and a notes field for your login emails. |

## Install

1. **Python 3.10+**: python.org (on Windows, tick "Add to PATH").
2. **ffmpeg**:
   - Mac: `brew install ffmpeg`
   - Windows: `winget install Gyan.FFmpeg`
   - Linux: `sudo apt install ffmpeg`
3. **Start the app**:
   - Mac/Linux: run `./start-mac-linux.sh`
   - Windows: double-click `start-windows.bat`

   The first start installs Flask, Pillow and numpy into a private `.venv` folder. Your browser then opens http://127.0.0.1:5577.

Your projects, settings and renders live in `~/ReleaseKit` (change the output folder in Settings).

## Optional: one-click YouTube uploads

1. `.venv/bin/pip install -r requirements-youtube.txt` (Windows: `.venv\Scripts\pip ...`)
2. Follow **Sign-ups, section 7**: enable YouTube Data API v3, then create an OAuth client of type **Desktop app**. Add yourself as a test user and download the JSON as `~/ReleaseKit/client_secret.json`.
3. The first upload opens a Google sign-in window. After that it remembers you.

Things to know: API projects that Google hasn't audited upload as **Private only**, so flip them to Public in YouTube Studio or apply for an audit with the form linked in the app. Uploads are also capped per day. Custom thumbnails need a verified channel.

## Important platform facts

- **Spotify, Apple Music, Amazon Music, YouTube Music, TikTok, Deezer and Tidal have no direct upload for independent artists.** You upload once to a distributor, and it delivers everywhere. The Distributor package is made for that.
- **Content ID vs stock libraries**: if you license a track on stock sites or use it as a free background loop, opt it **out** of YouTube Content ID at your distributor. Otherwise buyers' videos get claimed.
- **Shutterstock Music** buys all rights outright. A track you sell there can't be used or sold anywhere else.
- Most stock libraries and PROs don't accept fully AI-generated music.

## How the 10-hour videos stay fast

The audio loop is built once, with its tail crossfaded into its own start so there's no click at each repeat. It's then looped and encoded to AAC in a single pass. The picture is a short clip rendered once and stream-copied, with no re-encoding. Time is mostly the audio encode: a 10-hour video takes roughly 5–20 minutes depending on your CPU, and about 1–2 GB of disk.

## Files

```
releasekit/
  app.py        web server and job queue
  audio.py      analysis, conversions, cut-downs, seamless loops
  art.py        photo ranking, crops, generated art, text, variants
  video.py      all video renderers
  packages.py   platform export folders
  meta.py       titles, descriptions, tags, keywords, timeline, .ics
  links.py      sign-up directory (plain data: edit it freely)
  youtube.py    YouTube API uploader
  templates/index.html   the interface
```
