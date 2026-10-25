# My Nuvio Collection Art

Rotating artwork for your Nuvio **collection folders**, organized like Kaptain's Collection: genres, moods, networks, streaming services and more, plus folders for your own Simkl lists. Each folder gets a cover built from TMDB backdrops or posters, in one of four designs, with your title on top. A GitHub Action rebuilds the covers every night and serves them from GitHub at links that never change.

This only makes the folder covers. Posters for the movies and shows inside each folder still load from TMDB in Nuvio as normal.

Each folder gets two images:

| File | Size | Use for |
| --- | --- | --- |
| `art/<category>/<folder>/cover.jpg` | 1000×1500 (2:3) | poster-shaped folders |
| `art/<category>/<folder>/wide.jpg`  | 1280×720 (16:9) | landscape folders |
| `art/<category>/<folder>/<design>/…` | same two sizes | the folder in each design |

## What's included

218 folders, in Kaptain's categories:

| Category | Folders | Design |
| --- | --- | --- |
| My Lists (Simkl) | Continue Watching, Plan to Watch, Anime Watchlist | fan / mosaic |
| Discover | For You, Trending Movies and TV, New Movies and Series, Top Streaming Movies and Series | fan |
| Genres | 25, including K-Drama, Anime, Superhero and Stand-Up | backdrop |
| Moods & Vibes | 10, such as Feel-Good, Mind-Bending, Slow Burn and Tearjerkers | backdrop |
| Film Collections | Franchise films in 11 genres | fan |
| Based On | Books, Comics, True Stories, Video Games and 8 more | fan |
| By Decade | The '50s to the 2020s | mosaic |
| Anime | Discover, Isekai, Shōnen, Slice of Life, Sports, Studios | fan |
| Documentaries | 7 topics, from History & War to True Crime | backdrop |
| Kids & Family | 6, from Little Kids to Saturday Morning Cartoons | fan |
| Reality TV | 6, from Cooking Shows to Dating & Drama | mosaic |
| Awards | Academy Awards, Cannes, Golden Globes | spotlight |
| Spotlights | Retro Rewind, The Powerhouse, The Prestige Darling and 5 more | spotlight |
| Studios | DreamWorks, Focus Features, Searchlight, Walt Disney Pictures | mosaic |
| Streaming Services | 22, from Netflix and Max to Crunchyroll and MUBI | mosaic |
| Networks | 80, from A&E to WE tv | mosaic |

## Designs

| Design | Looks like | Built from |
| --- | --- | --- |
| `backdrop` | One full-bleed scene with the title over it | 1 backdrop |
| `spotlight` | One poster on a blurred copy of itself. On the wide card the poster sits on the right and the title on the left. | 1 poster |
| `fan` | Three posters fanned out, the middle one in front | 3 posters |
| `mosaic` | A tilted wall of posters behind the title | up to 18 posters |
| `logo` | Just the network's, service's or studio's logo on a card in its brand colour | the logo |
| `topic` | Just the folder's name on a colourful card, with no movie art | nothing |

`logo` is made only for folders about one network, streaming service or studio: the Networks, Streaming Services and Studios categories, 106 folders. Logos come from TMDB. Dark or grey wordmarks are turned white so they show up, and a service that TMDB only has an app icon for is shown as a rounded tile. To use a logo TMDB doesn't have, add `logo: https://...png` to that folder. `topic` works for every folder and never changes, since it isn't built from any movie art.

The four poster and backdrop designs rotate nightly, never repeating the previous day's main pick.

**Every folder is built in every design.** A folder's `design:` is its main look, and that's what its plain links show:

```
.../art/genres/horror/cover.jpg              ← main design
.../art/genres/horror/mosaic/cover.jpg       ← the same folder as a mosaic
.../art/genres/horror/fan/wide.jpg           ← the same folder as a fan, landscape
```

In `manifest.json`, each folder has a `designs` section listing the cover and wide links for every design, so you can use a different look for any folder in Nuvio without touching the config. To build fewer designs, edit `designs:` under `settings:` in `collections.yaml`. A folder or category can also set its own list, or switch its main look; for example, add `design: logo` to the Networks category so their plain links show logos. Backdrop is skipped for folders whose titles have no backdrops (for example, anime TMDB can't match).

## Setup (about 15 minutes, once)

You need a GitHub account and Python on your computer. Python is only needed for the one-time Simkl sign-in.

1. **Get a TMDB key.** Sign up at [themoviedb.org](https://www.themoviedb.org/signup), then go to *Settings → API*. Either the "API Key" or the "API Read Access Token" works.
2. **Get your Simkl token.** On [simkl.com/settings/developer](https://simkl.com/settings/developer/), register a new app with the type **"TV, devices & command line"** and copy its **Client ID**. Then, in a terminal in this folder:
   ```bash
   pip install requests
   SIMKL_CLIENT_ID=your-client-id python simkl_login.py
   ```
   On Windows, run `set SIMKL_CLIENT_ID=your-client-id` first, then `python simkl_login.py`. Open the link it prints and approve. It then prints a **refresh token**. Don't save the token in this folder.
3. **Put this folder on GitHub.** Create a new **public** repository with the README box unticked, then from a terminal in this folder:
   ```bash
   git init -b main
   git add .
   git commit -m "Collection art"
   git remote add origin https://github.com/YOUR-USERNAME/my-nuvio-art.git
   git push -u origin main
   ```
   The repo must be public, because Nuvio can't load images from a private one. If you upload by drag and drop instead, check that the hidden `.github` folder made it.
4. **Add three secrets.** In the repo, go to *Settings → Secrets and variables → Actions → New repository secret*:
   - `TMDB_API_KEY`
   - `SIMKL_CLIENT_ID`
   - `SIMKL_REFRESH_TOKEN`
5. **Run it.** Go to *Actions → Rotate collection art → Run workflow*, and enable workflows if GitHub asks. The first run takes a few minutes. Any folder that fails shows its error in the log, and the others still build.
6. **Copy the links into Nuvio.** Switch the branch picker from `main` to `art` and open `manifest.json`, which lists every folder's two links. They look like this:
   ```
   https://raw.githubusercontent.com/YOUR-USERNAME/my-nuvio-art/art/art/genres/horror/cover.jpg
   ```
   Paste each one wherever Nuvio, or Kaptain's Cinematic Editor, asks for that folder's image. The links never change, so you only do this once per folder.

From then on it rebuilds by itself every night at about 2 AM Pacific, and right away whenever you edit `collections.yaml`.

## Adding, removing and changing folders

All folders live in `collections.yaml`, one line each, grouped by category. You can edit it directly on GitHub (open the file and click the pencil):

```yaml
  - name: genres                 # the folder in the link: art/genres/...
    subtitle: Genre              # these apply to every folder in the category...
    design: backdrop
    folders:
      - { slug: horror, title: Horror, source: { genres: [Horror] }, accent: "#E53935" }   # ...unless a folder sets its own
```

- **Remove a folder:** delete its line.
- **Add one:** copy a line and change the slug, title and source.
- **Don't rename a slug** once its link is in Nuvio, because the link would change.

**Sources use names, not ID numbers.** Names are looked up on TMDB each run:

| Source | Meaning |
| --- | --- |
| `genres: [Crime, Thriller]` | has **all** of these genres |
| `any_genres: [Action, Thriller]` | has **any** of these genres |
| `keywords: [heist, time travel]` | tagged with **any** of these TMDB keywords |
| `all_keywords: [anime, sports]` | tagged with **all** of them |
| `companies: [Pixar, A24]` | from any of these studios |
| `networks: [HBO]` | aired on any of these TV networks |
| `providers: [Netflix]` | streaming on any of these, in `region:` (default `US`) |
| `countries: [KR, JP]` | made in any of these countries |
| `language: ja` | original language |
| `years: [1980, 1989]` | released in these years |
| `recent_days: 90` | released in the last 90 days |
| `sort: rating` | `popularity` (default), `rating` or `revenue` |
| `media: movie` | `movie`, `tv` or `both` (default) |
| `min_votes: 300` | skip obscure titles; shows need a third of this |
| `{ type: trending, media: movie }` | TMDB's trending list this week |
| `{ type: titles, items: ["movie/603", "tv/1399"] }` | a hand-picked list |
| `{ type: simkl, status: plantowatch, media: anime }` | one of your Simkl lists (see below) |

You can combine filters: `{ genres: [Documentary], keywords: [true crime] }` means true-crime documentaries. A name like `"HBO Max | Max"` means "whichever of these TMDB knows", which helps when services rename themselves. Genres translate to TV automatically, so Sci-Fi includes Sci-Fi & Fantasy shows. Genres that TV doesn't have, like Horror and Romance, are movie-only.

**Checking what names matched.** Run `python generate.py --check` with your TMDB key set. It lists every folder, how many titles it found, the top few, and what each name was matched to. The Actions log shows the same thing on each run. TMDB keywords are tagged by the community and can be patchy, especially for moods and awards, so if a folder comes up thin or odd, try a different keyword.

## Your Simkl lists

The three folders under My Lists read your actual Simkl lists, so their covers show what's in them. The most recently watched or added titles come first, so Continue Watching leads with the show you're on now. Add `rotate: true` to a folder to have it cycle through the whole list instead.

- **Statuses:** `watching`, `plantowatch`, `hold`, `completed`, `dropped` or `all`.
- **Media:** `media:` takes `movie`, `tv`, `anime`, `both` (movies and shows) or `all`.
- **Anime:** anime that TMDB can't match uses Simkl's own poster. Those titles have no backdrops, so use a poster design for anime folders.
- **Empty lists:** an empty list gets an abstract cover with its title instead of an error.

The token is read-only. It stays valid as long as the nightly job keeps using it: 180 days, renewed on every use. If the job stops for more than six months, run `simkl_login.py` again. Never put the token in the repo itself, because Simkl automatically revokes tokens that appear on GitHub.

**Privacy.** The art branch is public, and these covers show posters from your lists, so anyone with a link can see a few titles on them. The titles are left out of `manifest.json` and the gallery page. Add `show_titles: true` to a folder if you don't mind them appearing. Your full lists are never published: they're kept in a private Actions cache, and Simkl is only asked for them again when something on your account changed.

**Faster updates.** To have Continue Watching catch up within a few hours of you starting something, change the schedule in `.github/workflows/rotate.yml` to `"17 */4 * * *"` (every 4 hours).

## Trying it on your computer

```bash
pip install -r requirements.txt
python generate.py --demo                          # no key needed: abstract images, to check the layout
TMDB_API_KEY=xxxx python generate.py --check        # what each folder matches on TMDB
TMDB_API_KEY=xxxx python generate.py --only genres  # build one category (or folders: genres/horror,networks/hbo)
TMDB_API_KEY=xxxx python generate.py                # build everything
```

Then open `index.html` to see every cover, grouped by category.

## Good to know

- **Nuvio caches images.** A new cover may not appear on your TV until Nuvio refreshes its image cache. GitHub's raw links also cache for about 5 minutes.
- **If a folder fails** (for example a keyword TMDB doesn't know, or a TMDB outage), it keeps its previous image and the others still update. The error shows in the Actions log and in `manifest.json`.
- **Changing the look.** Each design is one `design_*()` function in `generate.py`, and the title text is drawn by `draw_title()`. The fonts are Inter, bundled under the SIL Open Font License (`fonts/OFL.txt`).
- **No animated hovers.** Kaptain's folders also have animated hover GIFs. This kit makes still covers only.
- **Using it elsewhere.** The images are plain JPGs, so the same links work as collection art in Stremio addons, Plex, Jellyfin and similar apps.

## Credits

Backdrops and posters come from [TMDB](https://www.themoviedb.org), and from [Simkl](https://simkl.com) for Simkl titles TMDB can't match. *This product uses the TMDB API but is not endorsed or certified by TMDB.* The images belong to their studios. Keep this for personal use, and follow [TMDB's API terms](https://www.themoviedb.org/api-terms-of-use). The category structure follows [Kaptain's Collection](https://kaptain.stream).
