# Omarchy theme preview gallery

A local, static fullscreen gallery for Omarchy desktop themes. Browse screenshots,
wallpapers, a mock desktop drawn from each theme's palette, a mock lock screen,
and colour sheets with contrast ratios. Search, compare themes, preview two
monitors, and keep a starred list in your browser.

![The gallery's own palette and contrast view](docs/screenshots/05-palette.png)

## Quick start

Install Python 3.11 or newer and Git, then run from the repository root:

```sh
python3 tools/build.py all
```

Open `app/index.html` in your browser. No web server is needed. The build needs
network access; the generated gallery makes no network requests.

For a smaller first build:

```sh
python3 tools/build.py all --only tokyo-night,catppuccin,gruvbox --cache ./cache
```

Python's standard library is enough to build. If Pillow is installed, it makes
480x270 thumbnails and display copies up to 2560 pixels, using the original image
for small or animated files. ImageMagick or ffmpeg with ffprobe can provide an
alternative image backend. Without a decoder, the gallery uses local original
images and lets the browser decode them; there is no resizing. Pillow preserves
the original pipeline's image transforms most closely.

## Build commands

`tools/build.py` is the single entry point. Its functions can also be imported
without running the CLI.

| Command | Result |
| --- | --- |
| `list` | Shallow-clone or update `omacom/omarchy-site` and `omacom/omarchy`; write the catalog to the cache |
| `fetch` | Select and shallow-clone community themes; select built-in themes from the cached Omarchy tree |
| `palettes` | Read the fetch receipt; write palette, provenance and image job metadata to the cache |
| `images` | Copy chosen images and generate optional thumbnails in `app/img/` |
| `data` | Write the gallery's local `app/data.js` script |
| `check` | Fail if a referenced image is missing or non-local, or an app HTML/CSS/JS source contains an HTTP(S) URL |
| `all` | Run all six stages above, in order |

The cache defaults to `./cache`, relative to your current directory. Set
`THEME_GALLERY_CACHE` or pass `--cache DIR` before or after the command. Output
always goes to this repository's `app/`. Every browser image reference lives in
`app/img/`, so the gallery works with caches in other locations.

`fetch` and `all` accept `--only slug,slug`, `--limit N`, `--max-mb 150`, and
`--jobs 6`. Selection applies to built-ins and community themes, then the limit
applies in catalog order. Unknown slugs are errors. Only the selected themes
appear in the fetch receipt and subsequent gallery, even if older themes remain
cached. Built-ins come from the required Omarchy catalog clone; they do not each
need a separate clone.

The per-theme cap measures working-tree bytes in MiB, excluding `.git` and
symlinks. Oversized community clones are removed after cloning. This cap does
not bound the network transfer or Git metadata. Clones have a 60-second timeout;
community fetching also skips new work when the theme cache exceeds 6,000 MiB.
Previously fetched community repositories are reused; `list` updates the two
catalog repositories. Failed or oversized themes are recorded as skipped. A
fetch with no successful themes fails.

The default cache, `app/data.js`, and `app/img/` are git-ignored. If you choose a
custom cache inside this repository, add that directory to `.gitignore` before
staging anything. Theme repositories and generated files are not included here.

## Keys

| Key | Action |
| --- | --- |
| Right / J / Page Down; Left / K / Page Up | Next; previous theme, following the grid's filter and sort |
| Down / Up; Space / Shift+Space | Next; previous slide |
| 1 / 2 / 3 / 4 / 5 | Screenshot / wallpaper / mock desktop / lock / palette; repeat 1 or 2 to advance that kind |
| W / Shift+W | Next / previous wallpaper, including behind the mock desktop |
| L | Toggle the launcher's and notifications' mock overlays |
| M | Toggle landscape plus portrait dual-monitor view |
| C | Cycle comparison with the previously viewed theme: split, mouse-controlled wipe, off |
| X | Swap compared themes |
| G / Esc | Open the grid; Esc closes it or first clears its search |
| F | Star or unstar the current theme |
| S | Open the starred slug list for copying or downloading |
| Enter / F11 | Browser fullscreen; Enter opens the selected theme in the grid |
| O | Open the current original screenshot or wallpaper in a new tab |
| R | Random theme |
| Home / End | First / last filtered theme |
| H | Hide or show the HUD |
| ? | Help |

In the grid, type to search and use arrows and Enter to select. Esc closes
modals. Wheel, horizontal swipes and screen edges change themes; Shift+wheel
and vertical swipes change slides. Clicking a palette swatch copies its hex
colour. Favourites and HUD visibility use localStorage when available.

## Theme data and extraction

Theme repositories are fetched as data, never executed, sourced or installed.
Git hooks, checkout symlinks, LFS smudge/process filters and recursive submodules
are disabled. Git system and global configuration are ignored for build Git
commands. The parser reads known colour files; it does not load theme shell,
Python, Lua, JavaScript, extensions or application configuration as code.

Palette files fill gaps in this exact order: `colors.toml`, `alacritty.toml`,
`kitty.conf`, `ghostty.conf`, `hyprland.conf`. Hyprland supplies border colours
only. Omarchy 4.x named colours follow its Alacritty template: black is the
background, white the foreground, bright black muted, and bright white
`bright_foreground`. Named values win over `color0` through `color15`.
Missing core colours use the original fallbacks; secondary colours retain the
original mix and contrast rules. The cache records source files, guessed and
derived fields, repository commits, detected licences and skipped themes.

Image selection also follows the original pipeline. Prefer `preview.png` and
then `preview-unlock.png`. Otherwise look for screenshot-like names at the root
and images larger than 30,000 bytes in the known preview/media/docs directories.
Prefer non-logo/non-palette candidates and larger files, keeping up to three.
Only when no repository preview exists, use its cached Omarchy site image.
Wallpapers come from `backgrounds/`, in directory and filename order. Video
files are noted, never copied into the app. Originals and display copies stay
local; repository URLs in `data.js` are attribution metadata. `check` scans app
source files for HTTP(S) URLs and validates image URLs in generated data.

## Limits and verification

The mock desktop is an approximation. It cannot reproduce each theme's real
application settings, font choices, extensions or window manager behaviour.
Preview verification is limited to static rendering; this project does not
install or test desktop themes. Optional decoders and browser image support can
differ, and individual theme fetches or image decodes may fail.

Run the offline tests without fetching any themes:

```sh
python3 -m unittest discover -s tests -v
```

CI runs these tests on Python 3.11 and 3.13 for pushes and pull requests. It does
not deploy the gallery or fetch theme repositories.

All eight source gallery screenshots were inspected. Only `05-palette.png`
is included: it shows the gallery's own colour and contrast view. The omitted
`01-mock-1920x1080.png`, `02-dual-1920x1080.png`,
`03-mock-portrait-1080x1920.png`, and `06-compare-split.png` contain personal mock
session labels and wallpaper backgrounds. `04-grid.png` contains third-party
preview thumbnails, `07-screenshot-slide.png` mainly shows an author's desktop,
and `08-lock.png` contains a personal label over a wallpaper. The app uses generic
mock session labels instead.

## Credits

Thanks to [Omarchy](https://github.com/omacom/omarchy) and the theme authors.
The catalog comes from [Omarchy's official theme list](https://omarchy.org/themes/)
and [its site repository](https://github.com/omacom/omarchy-site). Theme names,
authors, repository links and source revisions are retained in generated data.

## NOTICE

Themes, wallpapers and screenshots belong to their authors. The build downloads
them into a git-ignored cache and creates local, git-ignored image copies for
viewing. They are not redistributed in this repository and are not covered by
this repository's MIT licence. Refer to each theme's own licence and author for
its terms. The documentation PNG is a capture of this gallery's own palette UI,
not a theme author's screenshot or wallpaper.
