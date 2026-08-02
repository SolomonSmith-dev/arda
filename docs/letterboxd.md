# Letterboxd Import

Tom Bombadil can merge your Letterboxd export into `FILM_DATABASE` at startup
so the agent has real context — your ratings, reviews, favorites, watched
list — instead of only the three hardcoded seed films.

## How it works

If `LETTERBOXD_EXPORT_DIR` is set and points at a directory containing the
standard Letterboxd export CSVs, `FilmKnowledge.__init__` reads them and
merges into the in-memory `films` and `people` dicts via
`agents.tombombadil.letterboxd_loader`.

Files we read (any missing are skipped silently):

| File | Used for |
| --- | --- |
| `profile.csv` | Display name + favorites list |
| `ratings.csv` | Rating per film (0.5-5.0, doubled to 0-10) |
| `reviews.csv` | Review text + tags, attached to the rated entry |
| `watched.csv` | Films watched without an explicit rating |

Letterboxd's `diary.csv`, `watchlist.csv`, `lists/`, `likes/`, and
`comments.csv` are ignored for now. Add them to the loader if you want them.

## Bring-up on home-server

```bash
# 1. Unzip your export (you sent the zip via Tailscale already)
cd ~/Code/arda-stack/arda
mkdir -p data/letterboxd
unzip ~/letterboxd-export-*.zip -d data/letterboxd/

# Verify the CSVs landed at the top level (NOT inside a sub-directory):
ls data/letterboxd/
# expected: profile.csv  ratings.csv  reviews.csv  watched.csv  ...

# 2. Rebuild + restart the api so the new code loads
docker compose build api
docker compose up -d api
docker compose logs api | grep -i letterboxd
# expect: letterboxd_export_loaded  name=...  entries=N  rated=N  reviewed=N
#         letterboxd_merged         films=N  people=N
```

`data/` is gitignored — your CSVs stay on home-server only, never pushed.

## Verify Tom Bombadil sees it

```bash
curl -X POST -H "x-api-key: $ARDA_API_KEY" \
     -H "content-type: application/json" \
     -d '{"task_id":"t1","payload":{"message":"What did Solomon rate Inception?"}}' \
     http://localhost:5000/agents/tombombadil/run
```

Or via the Telegram bot once Gwaihir is up: send "what did I rate Inception" to
`@GwaihirsBot` and it'll route through Sauron → Tom Bombadil.

## Re-importing after new ratings

The loader re-runs every time the api container starts. Drop a fresh
export into `data/letterboxd/` (overwriting the old CSVs) and
`docker compose restart api`.

## Limitations

- The merge is in-memory; it does **not** write into Redis film stats
  (those are reserved for `Film: X / Rating: 8` Discord submissions).
- Themes come from Letterboxd `Tags` first, then a lightweight keyword
  scan of title + review text. Every imported film gets at least one
  theme (fallback: `cinema`). The viewer's `preferred_themes` is
  derived from those film themes (rating-weighted) so `/recommend`
  can rank against imported history without manual tagging.
- Existing watcher entries with the same name in the seed `FILM_DATABASE`
  are *updated* (not duplicated) by your import.
