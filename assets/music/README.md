# Music library

Drop tracks (mp3/wav/m4a/ogg) directly into this folder — one is picked at random
per video and mixed under the narration at low volume, loudness-normalized.

## Legal free music sources

**Read the license on each site before use.** Most require attribution — paste the
required credit line into `config.yaml` style or add it to the video description
manually. "Free" ≠ "no conditions": monetized YouTube use is allowed by all of the
sites below under their stated terms, but attribution/subscription conditions vary.

| Source | License notes (verify on-site — terms change) |
|---|---|
| https://soundimage.org/ | Eric Matyas; free with attribution ("Music by Eric Matyas, soundimage.org") |
| https://incompetech.filmmusic.io/search/ | Kevin MacLeod; CC-BY 4.0 — attribution required in description |
| https://www.soundclick.com/artist/default.cfm?bandid=1277008&content=songs | Per-artist licensing — read the artist's terms |
| https://no-copyright-music.com/ | Free with attribution; some tracks need a license for monetized use — check each track |
| https://uppbeat.io/ | Free tier: 3 downloads/month with credit tag; paid removes limits. Copyright-safe for monetized YT |
| https://sfx.productioncrate.com/royalty-free-music-categories.html | Free account tier; royalty-free with account; check daily limits |
| https://www.scottbuckley.com.au/library/ | CC-BY 4.0 — attribution required; excellent cinematic/emotional beds |
| https://www.streambeats.com/ | Harris Heller; 100% copyright-free, no attribution required — safest for automation |
| https://www.bensound.com/ | Free tier requires attribution + has usage limits; paid license for full rights |
| https://www.purple-planet.com/ | Free with attribution link; paid license removes it |
| https://imuno.sourceaudio.com/#!albums | Check per-album license terms |
| https://ncs.io/ | NoCopyrightSounds; free for content creators WITH the exact credit format they specify (electronic — better for shorts than kids' stories) |

**Recommended for a hands-off kids channel:** StreamBeats (zero conditions) and
Scott Buckley / Kevin MacLeod / soundimage.org (CC-BY — put one fixed attribution
block in your description template).

## Automating attribution

If you use attribution-required tracks, name the files like
`Artist - Track (CC-BY soundimage.org).mp3` and add a permanent credits block to
the channel/video description. The pipeline records which track it picked in each
project's `ledger.jsonl`-adjacent logs; a per-track credit line in the generated
description is a small future enhancement (see README roadmap).
