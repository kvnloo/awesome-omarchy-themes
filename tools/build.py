"""Build the local Omarchy gallery. Theme repositories are read as data only."""
import argparse
import colorsys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import warnings
import urllib.request
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
MIB = 1024 ** 2
VIEW = 2560

IMG_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif', '.bmp', '.jxl'}
VID_EXT = {'.mp4', '.webm', '.mkv', '.mov', '.m4v'}
NAMES = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white']


def read_text(path, limit=400_000):
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        with open(path, 'rb') as f:
            return f.read(limit).decode('utf-8', 'replace')
    except OSError:
        return None


def hexc(v):
    """Normalise a colour token to #rrggbb or None."""
    if not isinstance(v, str):
        return None
    v = v.strip().strip('"\'').strip()
    m = re.fullmatch(r'rgba?\(\s*([0-9a-fA-F]{6})([0-9a-fA-F]{2})?\s*\)', v)
    if m:
        return '#' + m.group(1).lower()
    m = re.fullmatch(r'rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*[\d.]+)?\s*\)', v)
    if m:
        r, g, b = (min(255, int(x)) for x in m.groups())
        return '#%02x%02x%02x' % (r, g, b)
    m = re.fullmatch(r'(?:#|0x)?([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})', v)
    if not m:
        return None
    h = m.group(1).lower()
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return '#' + h[:6]


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def lum(h):
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(h)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    if la < lb:
        la, lb = lb, la
    return round((la + 0.05) / (lb + 0.05), 2)


def mix(a, b, t):
    ra, rb = rgb(a), rgb(b)
    return '#%02x%02x%02x' % tuple(round(ra[i] + (rb[i] - ra[i]) * t) for i in range(3))


def hue(h):
    r, g, b = (x / 255 for x in rgb(h))
    hh, s, v = colorsys.rgb_to_hsv(r, g, b)
    return round(hh * 360), round(s, 3)


# ---------- per-source parsers: each returns a partial dict of fields ----------
def flat_toml(text):
    try:
        d = tomllib.loads(text)
    except Exception:
        d = {}
        for m in re.finditer(r'^[ \t]*([A-Za-z0-9_.-]+)[ \t]*=[ \t]*["\']?([^"\'\n#]*#?[0-9A-Fa-fx(),. ]*)["\']?', text, re.M):
            d[m.group(1)] = m.group(2).strip()
    return d


def from_colors_toml(text):
    d = flat_toml(text)
    g = lambda *ks: next((hexc(d[k]) for k in ks if k in d and hexc(d[k])), None)
    out, kind = {}, None
    out['background'] = g('background', 'bg')
    out['foreground'] = g('foreground', 'fg')
    out['accent'] = g('accent')
    out['cursor'] = g('cursor')
    out['selection'] = g('selection_background', 'selection')
    out['selection_fg'] = g('selection_foreground')
    out['muted'] = g('muted')
    out['border_active'] = g('hyprland_active_border', 'active_border_color')
    out['border_inactive'] = g('hyprland_inactive_border')
    out['bg_light'] = g('lighter_background', 'lighter_bg')
    out['bg_dark'] = g('dark_background', 'dark_bg')
    out['fg_bright'] = g('bright_foreground', 'bright_fg')
    out['fg_dim'] = g('dark_foreground', 'dark_fg')
    out['orange'] = g('orange')
    if isinstance(d.get('mode'), str) and d['mode'].lower() in ('light', 'dark'):
        out['mode'] = d['mode'].lower()
    n, b = [None] * 8, [None] * 8
    if any(k in d for k in ('red', 'green', 'blue')):
        # Omarchy 4.x named scheme, mapped exactly as default/themed/alacritty.toml.tpl does
        kind = 'colors.toml (4.x named)'
        named = [out['background'], g('red'), g('green'), g('yellow'), g('blue'), g('magenta'), g('cyan'), out['foreground']]
        bright = [out['muted'], g('bright_red'), g('bright_green'), g('bright_yellow'), g('bright_blue'),
                  g('bright_magenta'), g('bright_cyan'), out['fg_bright']]
        for i in range(8):
            n[i] = n[i] or named[i]
            b[i] = b[i] or bright[i]
        if not out['cursor']:
            out['cursor'] = out['fg_bright']
    if any(('color%d' % i) in d for i in range(16)):
        kind = 'colors.toml (color0-15)' if kind is None else 'colors.toml (4.x named + color0-15)'
        for i in range(8):
            n[i] = n[i] or g('color%d' % i)
            b[i] = b[i] or g('color%d' % (i + 8))
    out['normal'], out['bright'] = n, b
    out['_kind'] = kind or 'colors.toml'
    return out


def from_alacritty(text):
    d = flat_toml(text)
    c = d.get('colors') if isinstance(d.get('colors'), dict) else {}
    def sec(name):
        s = c.get(name)
        return s if isinstance(s, dict) else {}
    out = {'background': hexc(sec('primary').get('background')), 'foreground': hexc(sec('primary').get('foreground')),
           'cursor': hexc(sec('cursor').get('cursor')), 'selection': hexc(sec('selection').get('background')),
           'selection_fg': hexc(sec('selection').get('text'))}
    out['normal'] = [hexc(sec('normal').get(k)) for k in NAMES]
    out['bright'] = [hexc(sec('bright').get(k)) for k in NAMES]
    return out


def from_kitty(text):
    d = {}
    for line in text.splitlines():
        m = re.match(r'^\s*([a-z_0-9]+)\s+(\S+)', line)
        if m and not line.lstrip().startswith('#'):
            d.setdefault(m.group(1), m.group(2))
    g = lambda k: hexc(d.get(k))
    out = {'background': g('background'), 'foreground': g('foreground'), 'cursor': g('cursor'),
           'selection': g('selection_background'), 'selection_fg': g('selection_foreground'),
           'border_active': g('active_border_color'), 'border_inactive': g('inactive_border_color')}
    out['normal'] = [g('color%d' % i) for i in range(8)]
    out['bright'] = [g('color%d' % i) for i in range(8, 16)]
    return out


def from_ghostty(text):
    d, pal = {}, {}
    for line in text.splitlines():
        if line.lstrip().startswith('#'):
            continue
        m = re.match(r'^\s*palette\s*=\s*(\d+)\s*=\s*(\S+)', line)
        if m:
            pal[int(m.group(1))] = hexc(m.group(2))
            continue
        m = re.match(r'^\s*([a-z-]+)\s*=\s*(\S+)', line)
        if m:
            d.setdefault(m.group(1), m.group(2))
    g = lambda k: hexc(d.get(k))
    out = {'background': g('background'), 'foreground': g('foreground'), 'cursor': g('cursor-color'),
           'selection': g('selection-background'), 'selection_fg': g('selection-foreground')}
    out['normal'] = [pal.get(i) for i in range(8)]
    out['bright'] = [pal.get(i) for i in range(8, 16)]
    return out


def from_hyprland(text):
    var = {}
    references = re.compile(r'\$([A-Za-z0-9_]+)')
    self_referencing = set()
    for m in re.finditer(r'^[ \t]*\$([A-Za-z0-9_]+)[ \t]*=[ \t]*(.+?)[ \t]*(?:#.*)?$', text, re.M):
        name, value = m.group(1), m.group(2)
        var[name] = value[:4096]
        self_referencing.discard(name)
        if name in references.findall(value):
            self_referencing.add(name)
    def expand(expr):
        parts, remaining, end = [], 4096, 0
        for m in references.finditer(expr):
            value = m.group(0) if m.group(1) in self_referencing else var.get(m.group(1), '')
            for part in (expr[end:m.start()], value):
                parts.append(part[:remaining])
                remaining -= len(parts[-1])
                if not remaining:
                    return ''.join(parts)
            end = m.end()
        parts.append(expr[end:][:remaining])
        return ''.join(parts)
    def first_colour(expr):
        expr = expr[:4096]
        for _ in range(4):
            expanded = expand(expr)
            if expanded == expr:
                break
            expr = expanded
        for tok in re.findall(r'rgba?\([^)]*\)|0x[0-9a-fA-F]{6,8}|#[0-9a-fA-F]{6}', expr):
            h = hexc(tok)
            if h:
                return h
        return None
    out = {}
    m = re.search(r'^[ \t]*col\.active_border[ \t]*=[ \t]*(.+)$', text, re.M)
    if m:
        out['border_active'] = first_colour(m.group(1))
    m = re.search(r'^[ \t]*col\.inactive_border[ \t]*=[ \t]*(.+)$', text, re.M)
    if m:
        out['border_inactive'] = first_colour(m.group(1))
    return out


SOURCES = [('colors.toml', from_colors_toml), ('alacritty.toml', from_alacritty), ('kitty.conf', from_kitty),
           ('ghostty.conf', from_ghostty), ('hyprland.conf', from_hyprland)]
CORE = ['background', 'foreground'] + ['normal%d' % i for i in range(8)] + ['bright%d' % i for i in range(8)]


def build_palette(tdir):
    merged, origin, used = {}, {}, []
    fmt_kind = None
    for fname, fn in SOURCES:
        text = read_text(os.path.join(tdir, fname))
        if text is None:
            continue
        try:
            part = fn(text)
        except Exception as e:  # malformed third-party file
            part = {}
        if fname == 'colors.toml':
            fmt_kind = part.pop('_kind', None)
        flat = {}
        for k, v in part.items():
            if k in ('normal', 'bright'):
                for i, c in enumerate(v):
                    if c:
                        flat['%s%d' % (k, i)] = c
            elif v:
                flat[k] = v
        got = False
        for k, v in flat.items():
            if k not in merged:
                merged[k] = v
                origin[k] = fname
                got = True
        if got:
            used.append(fname)
    guessed = []
    def need(k, fallback):
        if k not in merged:
            merged[k] = fallback()
            guessed.append(k)
    if 'background' not in merged and 'normal0' in merged:
        merged['background'] = merged['normal0']; guessed.append('background')
    if 'foreground' not in merged and 'normal7' in merged:
        merged['foreground'] = merged['normal7']; guessed.append('foreground')
    # nothing but a border colour (or nothing at all): synthesise a neutral dark scheme
    need('background', lambda: '#101014')
    need('foreground', lambda: '#d8d8d8')
    bg, fg = merged['background'], merged['foreground']
    dark = lum(bg) < 0.4
    generic = ['#000000', '#cc5555', '#55aa55', '#ccaa44', '#5588cc', '#aa66cc', '#55aaaa', '#cccccc']
    for i in range(8):
        def fb(i=i):
            if i == 0:
                return mix(bg, '#000000', 0.3) if dark else mix(bg, '#000000', 0.75)
            if i == 7:
                return fg
            if 'bright%d' % i in merged:
                return mix(merged['bright%d' % i], bg, 0.15)
            return generic[i]
        need('normal%d' % i, fb)
    for i in range(8):
        def fb(i=i):
            base = merged['normal%d' % i]
            if i == 0:
                return mix(bg, fg, 0.35)
            return mix(base, '#ffffff' if dark else '#000000', 0.18)
        need('bright%d' % i, fb)
    derived = []
    def soft(k, fallback):
        if k not in merged:
            merged[k] = fallback()
            derived.append(k)
    soft('border_active', lambda: merged.get('accent') or merged['normal4'])
    soft('accent', lambda: merged['border_active'])
    soft('cursor', lambda: fg)
    soft('selection', lambda: mix(bg, fg, 0.22))
    soft('selection_fg', lambda: fg if contrast(fg, merged['selection']) >= contrast(bg, merged['selection']) else bg)
    soft('muted', lambda: merged['bright0'])
    soft('border_inactive', lambda: mix(bg, fg, 0.28))
    soft('bg_light', lambda: mix(bg, fg, 0.09))
    soft('bg_dark', lambda: mix(bg, '#000000', 0.25))
    soft('fg_bright', lambda: merged['bright7'])
    soft('fg_dim', lambda: mix(fg, bg, 0.45))
    mode = merged.pop('mode', None)
    mode_src = 'colors.toml'
    if not mode:
        if os.path.isfile(os.path.join(tdir, 'light.mode')):
            mode, mode_src = 'light', 'light.mode'
        else:
            mode, mode_src = ('dark' if lum(bg) < 0.4 else 'light'), 'background luminance'
    core_guessed = [k for k in guessed if k in CORE]
    h, s = hue(merged['accent'])
    rec = {
        'background': bg, 'foreground': fg, 'cursor': merged['cursor'],
        'selection': merged['selection'], 'selection_foreground': merged['selection_fg'],
        'accent': merged['accent'],
        'normal': [merged['normal%d' % i] for i in range(8)],
        'bright': [merged['bright%d' % i] for i in range(8)],
        'mode': mode, 'mode_source': mode_src,
        'muted': merged['muted'], 'border_active': merged['border_active'], 'border_inactive': merged['border_inactive'],
        'background_light': merged['bg_light'], 'background_dark': merged['bg_dark'],
        'foreground_bright': merged['fg_bright'], 'foreground_dim': merged['fg_dim'],
        'source': used[0] if used else None, 'sources_used': used,
        'field_source': {k: origin[k] for k in sorted(origin)},
        'guessed': guessed, 'derived': derived, 'guessed_core': bool(core_guessed),
        'contrast_fg_bg': contrast(fg, bg), 'accent_hue': h, 'accent_saturation': s,
    }
    return rec, fmt_kind


def img_list(tdir):
    """previews, wallpapers, videos as paths relative to the theme dir."""
    def is_img(p):
        return os.path.splitext(p)[1].lower() in IMG_EXT and os.path.isfile(p) and not os.path.islink(p)
    walls, vids = [], []
    bdir = os.path.join(tdir, 'backgrounds')
    if os.path.isdir(bdir) and not os.path.islink(bdir):
        for dp, dn, fn in os.walk(bdir, followlinks=False):
            dn.sort()
            for f in sorted(fn, key=str.lower):
                p = os.path.join(dp, f)
                ext = os.path.splitext(f)[1].lower()
                if ext in VID_EXT:
                    vids.append(os.path.relpath(p, tdir))
                elif is_img(p):
                    walls.append(os.path.relpath(p, tdir))
    previews, psrc = [], None
    for f in ('preview.png', 'preview-unlock.png'):
        if is_img(os.path.join(tdir, f)):
            previews.append(f)
    if previews and previews[0] == 'preview.png':
        psrc = 'preview.png'
    else:
        previews = []
        cand = []
        try:
            top = sorted(os.listdir(tdir), key=str.lower)
        except OSError:
            top = []
        pat = re.compile(r'preview|screenshot|theme|desktop|showcase|demo|image|screen', re.I)
        for f in top:
            if is_img(os.path.join(tdir, f)) and pat.search(f):
                cand.append(f)
        for sub in ('preview', 'previews', 'screenshots', 'screenshot', 'media', 'images', 'assets', 'docs', '.github'):
            sd = os.path.join(tdir, sub)
            if os.path.isdir(sd) and not os.path.islink(sd):
                for dp, dn, fn in os.walk(sd, followlinks=False):
                    for f in sorted(fn, key=str.lower):
                        p = os.path.join(dp, f)
                        if is_img(p) and os.path.getsize(p) > 30_000:
                            cand.append(os.path.relpath(p, tdir))
        if cand:
            cand = list(dict.fromkeys(cand))
            weak = re.compile(r'logo|palett?e|pallete|icon|banner|badge|avatar', re.I)
            cand.sort(key=lambda c: (bool(weak.search(os.path.basename(c))), -os.path.getsize(os.path.join(tdir, c))))
            strong = [c for c in cand if not weak.search(os.path.basename(c))]
            previews, psrc = (strong or cand)[:3], 'other screenshot in repo'
    return previews, psrc, walls, vids


def licence(tdir):
    try:
        for f in sorted(os.listdir(tdir)):
            if re.match(r'(?i)^(licen[cs]e|copying|unlicense)(\.|$|-)', f) and os.path.isfile(os.path.join(tdir, f)):
                text = read_text(os.path.join(tdir, f), 3000) or ''
                kind = None
                for pat, name in ((r'MIT License|Permission is hereby granted, free of charge', 'MIT'),
                                  (r'GNU GENERAL PUBLIC LICENSE\s+Version 3', 'GPL-3.0'), (r'GNU GENERAL PUBLIC LICENSE\s+Version 2', 'GPL-2.0'),
                                  (r'Apache License', 'Apache-2.0'), (r'Mozilla Public License', 'MPL-2.0'),
                                  (r'Creative Commons|CC0', 'Creative Commons'), (r'unlicense|public domain', 'Unlicense'),
                                  (r'BSD', 'BSD'), (r'GNU AFFERO', 'AGPL'), (r'GNU LESSER', 'LGPL')):
                    if re.search(pat, text, re.I):
                        kind = name
                        break
                return f, kind
    except OSError:
        pass
    return None, None


def exec_like(tdir):
    n = 0
    for dp, dn, fn in os.walk(tdir, followlinks=False):
        if '.git' in dn:
            dn.remove('.git')
        for f in fn:
            if os.path.splitext(f)[1].lower() in ('.sh', '.lua', '.py', '.vsix', '.fish', '.js', '.qml', '.bash', '.zsh'):
                n += 1
    return n


def read_json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=True) + '\n', encoding='utf-8')


def tree_bytes(tdir):
    """Working-tree bytes, excluding Git metadata and symlinks."""
    total = 0
    for parent, dirs, files in os.walk(tdir, followlinks=False):
        dirs[:] = [d for d in dirs if d != '.git' and not (Path(parent) / d).is_symlink()]
        for name in files:
            path = Path(parent) / name
            try:
                info = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                # Other fetch workers may remove rejected clones during this scan.
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def within_size_cap(tdir, max_mb):
    return tree_bytes(tdir) <= max_mb * MIB


def git_command(cache):
    """Ignore inherited Git configuration and disable checkout-time execution."""
    empty = Path(cache).resolve() / 'empty-git-template'
    empty.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='', GIT_LFS_SKIP_SMUDGE='1')
    argv = ['git', '-c', 'core.hooksPath=' + str(empty), '-c', 'core.symlinks=false',
            '-c', 'init.templateDir=' + str(empty), '-c', 'protocol.file.allow=never',
            '-c', 'protocol.ext.allow=never', '-c', 'filter.lfs.smudge=',
            '-c', 'filter.lfs.process=', '-c', 'filter.lfs.required=false',
            '-c', 'submodule.recurse=false', '-c', 'credential.helper=', '-c', 'core.fsmonitor=false']
    return argv, env


def run_git(cache, args, timeout=60, input=None):
    argv, env = git_command(cache)
    return subprocess.run(argv + [str(a) for a in args], env=env,
                          capture_output=True, text=True, timeout=timeout, check=True, input=input)


def clone_repo(cache, url, dest, update=False, timeout=60, no_checkout=False, lean=False):
    dest = Path(dest)
    if dest.is_symlink():
        raise ValueError('cache repository must not be a symlink')
    try:
        if (dest / '.git').is_dir():
            if update:
                run_git(cache, ['-C', dest, 'fetch', '--depth', '1', '--no-recurse-submodules', 'origin', 'HEAD'],
                        timeout=timeout)
                run_git(cache, ['-C', dest, 'checkout', '--force', '--detach', 'FETCH_HEAD'], timeout=timeout)
            return False
        if dest.exists():
            raise ValueError('cache destination already exists without Git metadata')
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_git(cache, ['clone', '--quiet', '--depth', '1',
                            '--filter=blob:none' if lean else '--filter=blob:limit=25m',
                            *(['--no-checkout'] if no_checkout else []),
                            '--no-recurse-submodules', '--', url, dest], timeout=timeout)
        except (subprocess.SubprocessError, OSError):
            if dest.exists():
                shutil.rmtree(dest)
            raise
        return True
    except subprocess.TimeoutExpired as error:
        repo = urlsplit(url).path.strip('/')
        command = 'list' if update else 'fetch'
        raise ValueError('repository %s timed out after %s seconds; retry python3 tools/build.py %s '
                         'with the same options' % (repo, timeout, command)) from error


def commit_id(cache, tdir):
    if not (Path(tdir) / '.git').is_dir():
        return None
    return run_git(cache, ['-C', tdir, 'rev-parse', 'HEAD']).stdout.strip() or None


def make_list(site, omarchy):
    """Original catalog and slug rules from mklist.py."""
    themes = read_json(Path(site) / 'src/data/themes.json')
    builtin = sorted(p.name for p in (Path(omarchy) / 'themes').iterdir() if p.is_dir() and not p.is_symlink())
    used, rows = set(builtin), []
    for pos, theme in enumerate(themes, 1):
        url = re.sub(r'\.git$', '', theme['repo'].strip().rstrip('/'))
        match = re.match(r'https://(github\.com|gitlab\.com|codeberg\.org)/([^/]+)/([^/]+)', url)
        if not match:
            rows.append(dict(name=theme['name'], repo=url, slug=None, author=None, skip='unsupported url'))
            continue
        author, repo = match.group(2), match.group(3)
        slug = re.sub(r'[^a-z0-9._-]+', '-', repo.lower())
        slug = re.sub(r'^omarchy-', '', slug)
        slug = re.sub(r'-theme$', '', slug)
        slug = re.sub(r'^theme-', '', slug) or repo.lower()
        if slug in used:
            slug += '-' + re.sub(r'[^a-z0-9]+', '-', author.lower())
        used.add(slug)
        rows.append(dict(name=theme['name'], repo=url, slug=slug, author=author, site_image=theme.get('image'), official_pos=pos))
    return dict(builtin=builtin, community=rows)


def list_themes(cache):
    for name, url in [('site', 'https://github.com/omacom/omarchy-site'),
                      ('omarchy', 'https://github.com/omacom/omarchy')]:
        clone_repo(cache, url, cache / 'src' / name, update=True, timeout=900)
    catalog = make_list(cache / 'src/site', cache / 'src/omarchy')
    write_json(cache / 'list.json', catalog)
    print('listed', len(catalog['builtin']), 'built-in and', len(catalog['community']), 'community themes')


CURATED_LISTS = ('manual', 'awesome-a', 'awesome-w', 'hub-l')
LIST_LABELS = {'official': 'Official list', 'manual': 'Manual', 'awesome-a': 'awesome-omarchy',
               'awesome-w': 'awesome-omarchy (Wheel-Smith)', 'hub-l': 'omarchytheme.com',
               'search': 'GitHub search'}
CORE_APP_FILES = ('alacritty.toml', 'kitty.conf', 'ghostty.conf', 'hyprland.conf', 'hyprlock.conf',
                  'waybar.css', 'mako.ini', 'btop.theme', 'neovim.lua')
KNOWN_APP_FILES = set(CORE_APP_FILES) | {
    'chromium.theme', 'foot.ini', 'icons.theme', 'keyboard.rgb', 'obsidian.css', 'swayosd.css',
    'vscode.json', 'walker.css', 'wofi.css', 'gtk.css', 'gtk-3.0.css', 'gtk-4.0.css',
    'rofi.rasi', 'fish.theme', 'fzf.fish', 'tmux.conf', 'cava.conf', 'zathura.conf',
    'fuzzel.ini', 'yazi.toml', 'starship.toml', 'hyprpaper.conf', 'sddm.conf',
    'sublime.theme', 'wezterm.lua', 'quickshell.qml', 'dunst.conf', 'electron.theme'}
PALETTE_FILES = ('colors.toml', 'alacritty.toml', 'kitty.conf', 'ghostty.conf')
DISTRO_PATHS = {'install.sh', 'bin', 'themes', 'config', 'default', 'migrations', 'applications'}
MANUAL_URL = 'https://learn.omacom.io/2/the-omarchy-manual/90/extra-themes.md'


def oklab(value):
    channels = [c / 255 for c in rgb(hexc(value))]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    l = (0.4122214708*r + 0.5363325363*g + 0.0514459929*b) ** (1/3)
    m = (0.2119034982*r + 0.6806995451*g + 0.1073969566*b) ** (1/3)
    s = (0.0883024619*r + 0.2817188376*g + 0.6299787005*b) ** (1/3)
    return (0.2104542553*l + 0.7936177850*m - 0.0040720468*s,
            1.9779984951*l - 2.4285922050*m + 0.4505937099*s,
            0.0259040371*l + 0.7827717662*m - 0.8086757660*s)


def oklch(value):
    L, a, b = oklab(value)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def hue_family(h, chroma):
    if chroma < 0.04:
        return 'neutral'
    for family, low, high in [('red', 15, 45), ('orange', 45, 80), ('yellow', 80, 118),
                               ('green', 118, 165), ('teal', 165, 225), ('blue', 225, 285),
                               ('purple', 285, 330)]:
        if low <= h < high:
            return family
    return 'pink'


ACCENT_ANCHORS = dict(zip(
    'red crimson coral orange amber gold yellow lime green emerald mint teal cyan sky blue cobalt indigo lavender violet purple magenta pink rose peach sand brown olive white silver grey black'.split(),
    'e5484d c2254b ff7f6a f5842a f5b83d d4a72c f2e04a a6d936 4caf50 2fbf8f 8de0c0 1f9e9e 3fd0e0 6cb8f5 3b7de9 2a52be 5a5fd6 b4a7f5 8b5cf6 9b3fc4 e04fd0 f58fc0 f0607f f7b79a d8c4a0 9a6b45 8a8f3a f2f2f2 b8b8c0 808088 1c1c20'.split()))


def palette_fields(value):
    pal = value.get('palette', value.get('p', value))
    return (pal.get('background', pal.get('bg')), pal.get('foreground', pal.get('fg')),
            pal.get('accent', pal.get('ac')), pal.get('normal', pal.get('n')),
            pal.get('bright', pal.get('b')))


def palette_vector(palette):
    bg, fg, ac, normal, bright = palette_fields(palette)
    colours = [bg, fg, ac] + normal + bright
    weights = [4, 2, 2] + [1] * 16
    return tuple(channel * 100 * math.sqrt(weight)
                 for colour, weight in zip(colours, weights) for channel in oklab(colour))


def root_entries(paths):
    """Keep file modes when callers have them; never accept a symlink palette."""
    files, roots = set(), set()
    for entry in paths:
        if isinstance(entry, str):
            name, regular = entry, not entry.endswith('/')
        else:
            name = entry.get('path', entry.get('name', ''))
            mode = str(entry.get('mode', '100644'))
            regular = entry.get('type', 'blob') == 'blob' and mode not in ('120000', '40960', '160000')
        name = name.lstrip('/')
        if not name:
            continue
        roots.add(name.split('/')[0])
        if regular:
            files.add(name)
    return files, roots


def is_theme(paths, read):
    files, roots = root_entries(paths)
    if not roots:
        return False, 'empty'
    if len(roots & DISTRO_PATHS) >= 3:
        return False, 'distro or dotfiles tree'
    if 'colors.toml' in roots:
        if 'colors.toml' not in files:
            return False, 'colors.toml is not a regular file'
        text = read('colors.toml')
        if text is None:
            return False, 'unparseable colors.toml'
        try:
            part = from_colors_toml(text)
        except (ValueError, TypeError):
            return False, 'unparseable colors.toml'
        core = [part.get('background'), part.get('foreground')] + part['normal'] + part['bright']
        if core[0] and core[1] and sum(bool(c) for c in core) >= 6:
            return True, 'colors.toml'
        return False, 'unparseable colors.toml'
    if len(files & KNOWN_APP_FILES) < 2:
        return False, 'fewer than two per-app files'
    if not any(p.startswith('backgrounds/') and Path(p).suffix.lower() in IMG_EXT for p in files):
        return False, 'no background image'
    for name, parser in SOURCES[1:4]:
        if name not in files:
            continue
        text = read(name)
        if text is None:
            continue
        try:
            part = parser(text)
        except (ValueError, TypeError):
            continue
        if part.get('background') and sum(bool(c) for c in part['normal'] + part['bright']) >= 6:
            return True, 'legacy'
    return False, 'not a theme'


def repo_identity(url):
    match = re.fullmatch(r'https://(github\.com|gitlab\.com|codeberg\.org)/([^/\s]+)/([^/\s]+?)(?:\.git)?/?', url.strip(), re.I)
    if not match:
        return None
    host, owner, name = match.groups()
    return host.lower(), owner + '/' + name


def github_name(url):
    identity = repo_identity(url)
    return identity[1].lower() if identity and identity[0] == 'github.com' else None


def repo_links(text):
    return list(dict.fromkeys(re.findall(
        r'https://(?:github\.com|gitlab\.com|codeberg\.org)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', text)))


def resolve_token():
    for key in ('GITHUB_TOKEN', 'GH_TOKEN'):
        if os.environ.get(key):
            return os.environ[key]
    if shutil.which('gh'):
        try:
            result = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True,
                                    timeout=10, check=True)
            return result.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            pass
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_graphql(query, token):
    request = Request('https://api.github.com/graphql', data=json.dumps({'query': query}).encode(),
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                               'User-Agent': 'omarchy-theme-gallery'})
    opener = urllib.request.build_opener(NoRedirect)
    for attempt in range(3):
        try:
            with opener.open(request, timeout=30) as response:
                result = json.load(response)
            break
        except HTTPError as error:
            if error.code not in (429, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))
    errors = result.get('errors', [])
    # Missing repositories have a null alias plus NOT_FOUND, not a failed batch.
    if errors and (not result.get('data') or any(error.get('type') != 'NOT_FOUND' for error in errors)):
        raise ValueError('GitHub GraphQL response contains errors')
    return result['data']


def search_repositories(query, token, start=None, end=None):
    """Use GraphQL search, so the token only ever goes to the GraphQL endpoint."""
    start = start or date(2008, 1, 1)
    end = end or date.today()
    terms = query + ' created:' + start.isoformat() + '..' + end.isoformat()
    def page(after=None):
        cursor = ', after:' + json.dumps(after) if after else ''
        return github_graphql('query { search(type:REPOSITORY, query:' + json.dumps(terms) +
                              ', first:100' + cursor + ') { repositoryCount pageInfo { hasNextPage endCursor } '
                              'nodes { ... on Repository { nameWithOwner isFork } } } }', token)['search']
    result = page()
    if result['repositoryCount'] > 1000:
        if start >= end:
            raise ValueError('GitHub search exceeds 1000 repositories on one creation date')
        middle = start + (end - start) // 2
        return (search_repositories(query, token, start, middle) +
                search_repositories(query, token, middle + timedelta(days=1), end))
    names = []
    while True:
        names.extend(node['nameWithOwner'] for node in result['nodes'] if node and not node['isFork'])
        if not result['pageInfo']['hasNextPage']:
            break
        result = page(result['pageInfo']['endCursor'])
    return names


def repository_query(names, blobs=False):
    fields = ('nameWithOwner isFork isEmpty stargazerCount forkCount createdAt pushedAt isArchived '
              'issues(states:OPEN) { totalCount } licenseInfo { spdxId }')
    if blobs:
        fields += (' root:object(expression:"HEAD:") { ... on Tree { entries { name type mode } } } '
                   'backgrounds:object(expression:"HEAD:backgrounds") { ... on Tree { entries { name type mode } } }')
        for i, name in enumerate(PALETTE_FILES):
            fields += ' p%d:object(expression:%s) { ... on Blob { text } }' % (i, json.dumps('HEAD:' + name))
    aliases = []
    for i, name in enumerate(names):
        owner, repo = name.split('/', 1)
        aliases.append('r%d:repository(owner:%s,name:%s) { %s }' %
                       (i, json.dumps(owner), json.dumps(repo), fields))
    return 'query { ' + ' '.join(aliases) + ' }'


def stats_row(node):
    return dict(stars=node['stargazerCount'], forks=node['forkCount'], created=(node.get('createdAt') or '')[:10] or None,
                pushed=(node.get('pushedAt') or '')[:10] or None, issues=node['issues']['totalCount'],
                licence=(node.get('licenseInfo') or {}).get('spdxId'), archived=node['isArchived'])


def discover(cache, no_search=False, no_api=False, max_age=7, refresh=False):
    catalog = read_json(cache / 'list.json')
    candidates = {}
    def add(url, list_id, pos=None, name=None, site_image=None, slug=None):
        identity = repo_identity(url)
        if not identity:
            return
        host, repo = identity
        key = host + '/' + repo.lower()
        row = candidates.setdefault(key, dict(repo='https://' + host + '/' + repo, host=host,
            lists=[], official_pos=None, tier='found', accepted=None, reason='not probed', queried_as=[]))
        if 'https://' + host + '/' + repo not in row['queried_as']:
            row['queried_as'].append('https://' + host + '/' + repo)
        if list_id not in row['lists']:
            row['lists'].append(list_id)
        if pos is not None:
            row['official_pos'] = pos
        if slug:
            row['slug'] = slug
        if name:
            row['name'] = name
        if site_image:
            row['site_image'] = site_image
        row['tier'] = 'official' if 'official' in row['lists'] else 'curated' if any(x in CURATED_LISTS for x in row['lists']) else 'found'
    for pos, row in enumerate(catalog['community'], 1):
        add(row['repo'], 'official', row.get('official_pos', pos), row.get('name'), row.get('site_image'), row.get('slug'))
    for list_id, name in [('awesome-a', 'aorumbayev/awesome-omarchy'),
                           ('awesome-w', 'Wheel-Smith/awesome-omarchy'),
                           ('hub-l', 'limehawk/omarchy-theme-website')]:
        dest = cache / 'src' / list_id
        try:
            clone_repo(cache, 'https://github.com/' + name, dest, update=True)
            if list_id == 'hub-l':
                rows = read_json(dest / 'src/data/themes-data.json')
                if isinstance(rows, dict):
                    rows = rows.get('themes', rows.get('data', []))
                urls = [row['github_url'] for row in rows if str(row.get('is_builtin')) != '1' and row.get('github_url')]
            else:
                text = read_text(dest / 'README.md') or ''
                if list_id == 'awesome-a':
                    text = re.split(r'^##\s+Themes\s*$', text, flags=re.M)[-1]
                    text = re.split(r'^##\s+Contributing\s*$', text, flags=re.M)[0]
                urls = repo_links(text)
            for url in urls:
                add(url, list_id)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print('discover warning:', list_id, type(error).__name__)
    try:
        request = Request(MANUAL_URL, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; OmarchyThemeGallery/1.0)'})
        with urlopen(request, timeout=10) as response:
            text = response.read(2_000_000).decode('utf-8', 'replace')
        for url in repo_links(text):
            add(url, 'manual')
    except Exception as error:
        print('discover warning: manual', type(error).__name__)
    fresh_cache = False
    if (cache / 'stats.json').exists() and (cache / 'candidates.json').exists() and not refresh and not no_api:
        saved_stats = read_json(cache / 'stats.json')
        try:
            age = (date.today() - date.fromisoformat(saved_stats['fetched'][:10])).days
            fresh_cache = saved_stats.get('source') == 'github-graphql' and 0 <= age < max_age
        except (KeyError, TypeError, ValueError):
            pass
        if fresh_cache:
            saved_candidates = read_json(cache / 'candidates.json')
            current = list(candidates.values())
            aliases = {url.lower(): row['repo'] for row in saved_candidates
                       for url in row.get('queried_as', [])}
            for row in current:
                row['repo'] = aliases.get(row['repo'].lower(), row['repo'])
            # Reuse remote probes and search membership, but retain freshly read curation.
            current_keys = {host + '/' + name.lower() for host, name in
                            (repo_identity(row['repo']) for row in current)}
            cached = []
            for row in saved_candidates:
                host, name = repo_identity(row['repo'])
                search_lists = [source for source in row.get('lists', []) if source == 'search']
                if host + '/' + name.lower() in current_keys or search_lists:
                    cached.append(dict(row, lists=search_lists, official_pos=None))
            rows = merge_candidates(current + cached)
            candidates = {repo_identity(row['repo'])[0] + '/' + repo_identity(row['repo'])[1].lower(): row for row in rows}
            print('discover: using cached GitHub probes and search results')
    token = None if no_api or fresh_cache else resolve_token()
    if token:
        if not no_search:
            for query in ('omarchy theme in:name,description', '"omarchy-theme-install" in:readme'):
                try:
                    for name in search_repositories(query, token):
                        add('https://github.com/' + name, 'search')
                except Exception as error:
                    print('discover search warning:', type(error).__name__)
        names = [repo_identity(row['repo'])[1] for row in candidates.values() if row['host'] == 'github.com']
        for offset in range(0, len(names), 30):
            print('discover: probing repositories', offset + 1, 'to', min(offset + 30, len(names)), 'of', len(names), flush=True)
            batch = names[offset:offset + 30]
            try:
                response = github_graphql(repository_query(batch, blobs=True), token)
                for i, name in enumerate(batch):
                    row = candidates['github.com/' + name.lower()]
                    node = response.get('r%d' % i)
                    if not node or node['isFork'] or node['isEmpty']:
                        row.update(accepted=False, reason='missing or private' if not node else 'fork' if node['isFork'] else 'empty')
                        continue
                    row['repo'] = 'https://github.com/' + node['nameWithOwner']
                    row['stats'] = stats_row(node)
                    paths = [dict(entry, path=entry['name']) for entry in (node.get('root') or {}).get('entries', [])]
                    paths += [dict(entry, path='backgrounds/' + entry['name']) for entry in (node.get('backgrounds') or {}).get('entries', [])]
                    texts = {filename: (node.get('p%d' % j) or {}).get('text') for j, filename in enumerate(PALETTE_FILES)}
                    accepted, reason = is_theme(paths, texts.get)
                    row.update(accepted=accepted, reason=reason)
            except Exception as error:
                print('discover probe warning:', type(error).__name__)
    rows = merge_candidates(candidates.values())
    write_json(cache / 'candidates.json', rows)
    print('discovered', len(rows), 'repositories;', sum(row['accepted'] is True for row in rows), 'accepted remotely')


def stats(cache, max_age=7, refresh=False, no_api=False):
    path = cache / 'stats.json'
    if path.exists() and not refresh and not no_api:
        existing = read_json(path)
        try:
            age = (date.today() - date.fromisoformat(existing['fetched'][:10])).days
            if 0 <= age < max_age and existing.get('source') == 'github-graphql':
                print('stats: using cached GitHub data')
                return existing
        except (KeyError, ValueError, TypeError):
            pass
    result = dict(fetched=date.today().isoformat(), source='none', repos={})
    token = None if no_api else resolve_token()
    if token:
        if (cache / 'candidates.json').exists():
            rows = read_json(cache / 'candidates.json')
        else:
            rows = read_json(cache / 'list.json')['community']
        names = sorted({github_name(row['repo']) for row in rows if github_name(row['repo']) and row.get('accepted') is not False})
        try:
            for offset in range(0, len(names), 100):
                print('stats: fetching repositories', offset + 1, 'to', min(offset + 100, len(names)), 'of', len(names), flush=True)
                batch = names[offset:offset + 100]
                response = github_graphql(repository_query(batch), token)
                for i, name in enumerate(batch):
                    node = response.get('r%d' % i)
                    if node:
                        result['repos'][node['nameWithOwner'].lower()] = stats_row(node)
            result['source'] = 'github-graphql'
        except Exception as error:
            print('stats warning:', type(error).__name__)
            result['repos'] = {}
    write_json(path, result)
    print('stats:', result['source'], len(result['repos']), 'repositories')
    return result


def licence_category(value, has_file=False):
    if not value or value == 'NOASSERTION':
        return 'other' if has_file else 'none'
    if re.search(r'GPL|MPL', value, re.I):
        return 'copyleft'
    if re.search(r'^MIT$|^BSD|^Apache|^ISC$|^Unlicense$|^CC0|^0BSD$|^Zlib$', value, re.I):
        return 'permissive'
    return 'other'


def metrics(themes, stats):
    ordered = sorted(themes, key=lambda theme: theme['slug'])
    now = date.fromisoformat((stats.get('fetched') or date.today().isoformat())[:10])
    repos = {key.lower(): value for key, value in stats.get('repos', {}).items()}
    has_stats = stats.get('source') == 'github-graphql'
    anchors = {name: oklab('#' + value) for name, value in ACCENT_ANCHORS.items()}
    vectors = []
    def months(value):
        try:
            return max(0, (now - date.fromisoformat(value[:10])).days) / 30.44
        except (TypeError, ValueError):
            return None
    for theme in ordered:
        pal = theme.get('palette', theme.get('p', theme))
        bg, fg, accent, normal, bright = palette_fields(theme)
        terminal = normal + bright
        chromatic = terminal[1:7] + terminal[9:15]
        L, C, h = oklch(accent)
        labs = oklab(accent)
        m = dict(L=round(oklab(bg)[0], 4), ah=round(h, 1), ac=round(C, 4), fam=hue_family(h, C),
                 an=min(anchors, key=lambda name: math.dist(labs, anchors[name])),
                 leg=min(contrast(bg, colour) for colour in [fg] + chromatic),
                 col=round(100 * sum(oklch(colour)[1] for colour in [accent] + chromatic) / 13, 2))
        missing = set(pal.get('guessed', theme.get('guessed', []))) | set(pal.get('derived', theme.get('derived', [])))
        keys = CORE + ['accent', 'cursor', 'selection', 'selection_fg']
        P = sum(key not in missing for key in keys) / 22
        files = set(theme.get('app_files', []))
        if not files:
            apps = set(theme.get('apps', []))
            files = {name for name in KNOWN_APP_FILES if Path(name).stem in apps}
        A = 1 if str(theme.get('format', '')).startswith('4.x') else len(files & set(CORE_APP_FILES)) / 9
        E = min(1, len(files - set(CORE_APP_FILES)) / 6)
        preview = bool(theme.get('previews') or theme.get('preview_source') or theme.get('psrc'))
        wall = bool(theme.get('wallpapers_count', theme.get('wn', len(theme.get('wallpapers', [])))))
        lic = bool(theme.get('licence_file') or theme.get('licence_detected') or theme.get('lic'))
        m['comp'] = round(40*P + 30*A + 10*E + 10*preview + 5*wall + 5*lic, 2)
        row = None if theme.get('builtin') else repos.get(github_name(theme.get('repo', '')))
        if row and not lic and row.get('licence') not in (None, 'NOASSERTION'):
            m['comp'] = round(m['comp'] + 5, 2)
        theme['stats'] = row
        lists = set(theme.get('lists', theme.get('ls', [])))
        official = theme.get('tier') == 'official' or 'official' in lists
        M = min(1, (2*official + len(lists & set(CURATED_LISTS))) / 6)
        age = months(row.get('pushed') if row else theme.get('last_commit', theme.get('lc')))
        R = 0.5 ** (age / 6) if age is not None else 0
        trend = None
        if theme.get('builtin'):
            pop = 100
        elif has_stats:
            S = min(1, math.log1p(row['stars']) / math.log(151)) if row else 0
            F = min(1, math.log1p(row['forks']) / math.log(21)) if row else 0
            pop = 100 * (0.5*S + 0.1*F + 0.2*M + 0.2*R)
            born = months(row.get('created')) if row else None
            if born is not None:
                trend = (row['stars'] + 1) / (born + 3)
        else:
            pop = 100 * (0.6*M + 0.4*R)
        theme['m'], theme['r'] = m, dict(pop=round(pop, 1), trend=round(trend, 1) if trend is not None else None)
        vectors.append(palette_vector(theme))
    nearest = [math.inf] * len(vectors)
    for i, vector in enumerate(vectors):
        for j in range(i):
            distance = math.dist(vector, vectors[j])
            nearest[i] = min(nearest[i], distance)
            nearest[j] = min(nearest[j], distance)
    for theme, value in zip(ordered, nearest):
        theme['m']['uniq'] = round(value, 1) if math.isfinite(value) else None
    return palette_map(ordered, vectors)


def palette_map(themes, vectors):
    n = len(themes)
    grid = max(1, math.ceil(math.sqrt(1.5*n)))
    positions = [[0.5, 0.5] for _ in themes]
    explained = [0.5, 0.5]
    if n >= 3:
        width = len(vectors[0])
        means = [sum(row[i] for row in vectors) / n for i in range(width)]
        centred = [[value - mean for value, mean in zip(row, means)] for row in vectors]
        covariance = [[sum(row[i]*row[j] for row in centred) / (n-1) for j in range(width)] for i in range(width)]
        trace = sum(covariance[i][i] for i in range(width))
        projections, explained = [], []
        for axis in range(2):
            vector = [1/(i+1) for i in range(width)]
            norm = math.sqrt(sum(value*value for value in vector))
            vector = [value/norm for value in vector]
            for iteration in range(300):
                product = [sum(a*b for a, b in zip(row, vector)) for row in covariance]
                norm = math.sqrt(sum(value*value for value in product))
                if norm <= 1e-12:
                    break
                vector = [value/norm for value in product]
            largest = max(range(width), key=lambda i: abs(vector[i]))
            if vector[largest] < 0:
                vector = [-value for value in vector]
            eigenvalue = max(0, sum(vector[i] * sum(covariance[i][j]*vector[j] for j in range(width)) for i in range(width)))
            explained.append(round(eigenvalue/trace, 2) if trace > 1e-12 else 0)
            scores = [sum(a*b for a, b in zip(row, vector)) for row in centred]
            lo, hi = min(scores), max(scores)
            projections.append([round((value-lo)/(hi-lo), 4) if hi-lo > 1e-10 else 0.5 for value in scores])
            covariance = [[covariance[i][j] - eigenvalue*vector[i]*vector[j] for j in range(width)] for i in range(width)]
        positions = list(zip(*projections))
    occupied = set()
    for theme, (x, y) in zip(themes, positions):
        wanted = round(x*(grid-1)), round((1-y)*(grid-1))
        chosen = None
        for radius in range(grid):
            for cy in range(max(0, wanted[1]-radius), min(grid, wanted[1]+radius+1)):
                for cx in range(max(0, wanted[0]-radius), min(grid, wanted[0]+radius+1)):
                    if max(abs(cx-wanted[0]), abs(cy-wanted[1])) == radius and (cx, cy) not in occupied:
                        chosen = cx, cy
                        break
                if chosen is not None:
                    break
            if chosen is not None:
                break
        occupied.add(chosen)
        theme['m'].update(x=x, y=y, cx=chosen[0], cy=chosen[1])
    return dict(pca=explained, grid=grid)


def dedupe_themes(themes):
    def priority(theme):
        lists = theme.get('lists', theme.get('ls', []))
        tier = 0 if theme.get('builtin') else 1 if theme.get('tier') == 'official' or 'official' in lists else 2 if theme.get('tier') == 'curated' or any(x in CURATED_LISTS for x in lists) else 3
        row = theme.get('stats') or theme.get('st') or {}
        return tier, -row.get('stars', row.get('s', 0)), row.get('created', row.get('c')) or '9999', theme['slug']
    duplicates, keys = [], {}
    for theme in sorted(themes, key=priority):
        bg, fg, accent, normal, bright = palette_fields(theme)
        key = tuple([bg, fg] + normal + bright)
        theme.pop('twin_of', None)
        if key in keys:
            theme['twin_of'] = keys[key]['slug']
            duplicates.append(dict(slug=theme['slug'], name=theme.get('name'), repo=theme.get('repo'), duplicate_of=keys[key]['slug']))
        else:
            keys[key] = theme
    return sorted(themes, key=lambda theme: theme['slug']), sorted(duplicates, key=lambda theme: theme['slug'])


def merge_candidates(rows):
    merged = {}
    for row in rows:
        identity = repo_identity(row['repo'])
        if not identity:
            continue
        key = identity[0] + '/' + identity[1].lower()
        if key not in merged:
            merged[key] = dict(row, lists=list(row.get('lists', [])), queried_as=list(row.get('queried_as', [])))
            continue
        current = merged[key]
        current['lists'] = list(dict.fromkeys(current['lists'] + row.get('lists', [])))
        current['queried_as'] = list(dict.fromkeys(current['queried_as'] + row.get('queried_as', [])))
        if row.get('official_pos') is not None and (current.get('official_pos') is None or row['official_pos'] < current['official_pos']):
            current.update(official_pos=row['official_pos'])
            for field in ('name', 'site_image', 'slug'):
                if row.get(field):
                    current[field] = row[field]
        if row.get('accepted') is not None:
            current.update(accepted=row['accepted'], reason=row.get('reason'))
        if row.get('stats'):
            current['stats'] = row['stats']
    for row in merged.values():
        row['tier'] = 'official' if 'official' in row['lists'] else 'curated' if any(x in CURATED_LISTS for x in row['lists']) else 'found'
    return sorted(merged.values(), key=lambda row: row['repo'].lower())


def catalog_entries(catalog, candidates=None, stats_data=None, tier='curated', min_stars=2):
    if isinstance(catalog, (str, Path)):
        cache = Path(catalog)
        catalog = read_json(cache / 'list.json')
        candidates = read_json(cache / 'candidates.json') if (cache / 'candidates.json').exists() else None
        stats_data = read_json(cache / 'stats.json') if (cache / 'stats.json').exists() else None
    stats_data = stats_data or dict(source='none', repos={})
    repos = {key.lower(): value for key, value in stats_data.get('repos', {}).items()}
    entries = [dict(slug=s, name=' '.join(w.capitalize() for w in s.split('-')),
                    author='Omarchy (built-in)', repo='https://github.com/omacom/omarchy/tree/master/themes/' + s,
                    builtin=True, commit=None, site_image=None, lists=[], official_pos=None, tier='builtin')
               for s in catalog['builtin']]
    if candidates is None:
        candidates = [dict(row, lists=['official'], tier='official', official_pos=row.get('official_pos', i))
                      for i, row in enumerate(catalog['community'], 1)]
    def stars(row):
        return (repos.get(github_name(row['repo'])) or row.get('stats') or {}).get('stars', 0)
    def ordering(row):
        group = {'official': 0, 'curated': 1, 'found': 2}.get(row.get('tier'), 2)
        return (group, row.get('official_pos') or math.inf if group == 0 else
                -len(set(row.get('lists', [])) & set(CURATED_LISTS)) if group == 1 else -stars(row),
                (row.get('name') or row['repo']).lower())
    official = {row.get('official_pos', i): row for i, row in enumerate(catalog['community'], 1)}
    used = set(catalog['builtin'])
    for row in sorted(candidates, key=ordering):
        if row.get('accepted') is False:
            continue
        row_tier = row.get('tier', 'official')
        if tier == 'official' and row_tier != 'official':
            continue
        if row_tier == 'found' and (tier == 'curated' or tier == 'starred' and stars(row) < min_stars):
            continue
        identity = repo_identity(row['repo'])
        if not identity:
            continue
        author, repo = identity[1].split('/', 1)
        original = repo_identity((row.get('queried_as') or [row['repo']])[0])
        original_author, original_repo = original[1].split('/', 1) if original else (author, repo)
        slug = re.sub(r'[^a-z0-9._-]+', '-', original_repo.lower())
        slug = re.sub(r'^omarchy-', '', slug)
        slug = re.sub(r'-theme$', '', slug)
        slug = re.sub(r'^theme-', '', slug)
        base = slug if slug and slug not in ('theme', 'omarchy') else original_repo.lower()
        slug = slug or original_repo.lower()
        if row.get('official_pos') is not None:
            slug = (official.get(row['official_pos']) or row).get('slug') or slug
        if slug in used:
            slug += '-' + re.sub(r'[^a-z0-9]+', '-', original_author.lower())
        while slug in used:
            slug += '-2'
        used.add(slug)
        entries.append(dict(slug=slug, name=row.get('name') or ' '.join(w.capitalize() for w in base.split('-')),
            author=author, repo=row['repo'], builtin=False, commit=None, site_image=row.get('site_image'),
            tier=row_tier, lists=row.get('lists', []), official_pos=row.get('official_pos')))
    return entries


def select_entries(entries, only=None, limit=None):
    if only is not None:
        wanted = {s.strip() for s in only.split(',') if s.strip()}
        unknown = wanted - {e['slug'] for e in entries}
        if unknown:
            raise ValueError('unknown theme slug(s): ' + ', '.join(sorted(unknown)))
        if not wanted:
            raise ValueError('--only must name at least one slug')
        entries = [e for e in entries if e['slug'] in wanted]
    if limit is not None:
        if limit < 0:
            raise ValueError('--limit must be non-negative')
        entries = entries[:limit]
    return entries


def safe_slug(slug):
    if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9._-]+', slug) or slug in ('.', '..'):
        raise ValueError('invalid theme slug')
    return slug


def theme_path(cache, entry):
    slug = safe_slug(entry['slug'])
    return cache / ('src/omarchy/themes' if entry['builtin'] else 'themes') / slug


def sparse_pattern(path):
    if '\n' in path or '\r' in path:
        return None
    return '/' + re.sub(r'([\\*?\[\]!#])', r'\\\1', path)


def repository_paths(cache, tdir):
    # --name-only avoids blob reads in a partial clone.
    names = run_git(cache, ['-C', tdir, 'ls-tree', '-r', '--name-only', '-z', 'HEAD']).stdout.split('\0')
    return [name for name in names if name]


def root_tree(cache, tdir):
    entries = []
    for item in run_git(cache, ['-C', tdir, 'ls-tree', '-r', '-t', '-z', 'HEAD']).stdout.split('\0'):
        if not item:
            continue
        metadata, name = item.split('\t', 1)
        mode, kind, oid = metadata.split()
        entries.append(dict(path=name, type=kind, mode=mode))
    return entries


def checkout_theme(cache, tdir, paths, lean=False, wallpapers=6):
    if lean:
        selected = [p for p in paths if p in {name for name, _ in SOURCES} | {'light.mode', 'preview.png', 'preview-unlock.png'}
                    or '/' not in p and re.match(r'(?i)^(licen[cs]e|copying|unlicense)(\.|$|-)', p)]
        images = sorted(p for p in paths if p.startswith('backgrounds/') and Path(p).suffix.lower() in IMG_EXT)
        selected += images[:wallpapers] if wallpapers else images
        patterns = [sparse_pattern(path) for path in selected]
        run_git(cache, ['-C', tdir, 'sparse-checkout', 'set', '--no-cone', '--stdin'],
                input='\n'.join(pattern for pattern in patterns if pattern is not None) + '\n')
    run_git(cache, ['-C', tdir, 'checkout', '--quiet', '--detach', 'HEAD'])


def fetch_themes(cache, only=None, limit=None, max_mb=150, jobs=6, tier='curated', min_stars=2,
                 wallpapers=6, cache_max_mb=6000, no_api=False):
    if tier in ('starred', 'all') and (no_api or not resolve_token()):
        print('fetch: no GitHub token; falling back to curated tier')
        tier = 'curated'
    entries = select_entries(catalog_entries(cache, tier=tier, min_stars=min_stars), only, limit)
    git_command(cache)
    def fetch(entry):
        slug = entry['slug']
        if slug is None:
            return dict(entry=entry, status='skip', reason='unsupported url')
        tdir = theme_path(cache, entry)
        cloned = False
        if not entry['builtin']:
            if tree_bytes(cache / 'themes') > cache_max_mb * MIB:
                return dict(entry=entry, status='skip', reason='cache-over-size-cap')
            try:
                lean = entry['tier'] not in ('builtin', 'official')
                cloned = clone_repo(cache, entry['repo'], tdir, no_checkout=True, lean=lean)
                paths = repository_paths(cache, tdir)
                root = root_tree(cache, tdir)
                def read(name):
                    if not any(e['path'] == name and e['type'] == 'blob' and e['mode'] in ('100644', '100755') for e in root):
                        return None
                    return run_git(cache, ['-C', tdir, 'show', 'HEAD:' + name]).stdout[:400_000]
                accepted, reason = is_theme(root, read)
                if not accepted:
                    shutil.rmtree(tdir)
                    (tdir.parent / (slug + '.paths.json')).unlink(missing_ok=True)
                    return dict(entry=entry, status='skip', reason=reason)
                # Existing full checkouts are narrowed too when moved to a lean tier.
                checkout_theme(cache, tdir, paths, lean, wallpapers)
                write_json(tdir.parent / (slug + '.paths.json'), paths)
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                reason = str(error) if isinstance(error.__cause__, subprocess.TimeoutExpired) else type(error).__name__
                return dict(entry=entry, status='fail', reason=reason)
        if not tdir.is_dir() or tdir.is_symlink():
            return dict(entry=entry, status='missing', reason='theme directory unavailable')
        if not within_size_cap(tdir, max_mb):
            if not entry['builtin']:
                shutil.rmtree(tdir)
                (tdir.parent / (slug + '.paths.json')).unlink(missing_ok=True)
            return dict(entry=entry, status='skip', reason='over-size-cap')
        return dict(entry=entry, status='ok', reason='cloned' if cloned else 'cached', bytes=tree_bytes(tdir))
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(fetch, entries))
    write_json(cache / 'fetch.json', results)
    for result in results:
        print(result['entry']['slug'], result['status'], result['reason'])
    if not any(r['status'] == 'ok' for r in results):
        raise ValueError('no themes fetched; inspect the catalog, filters and fetch receipt')


def process_theme(entry, tdir, site):
    """Parse known text and image files; never import, execute or source a theme."""
    pal, kind = build_palette(tdir)
    has_ct = (Path(tdir) / 'colors.toml').is_file()
    if has_ct and kind and kind.startswith('colors.toml (4.x'):
        fmt = '4.x colors.toml'
    elif has_ct:
        fmt = 'colors.toml (3.x color0-15)' if kind and 'color0-15' in kind else 'colors.toml (other keys)'
    else:
        fmt = 'legacy per-app files'
    previews, psrc, walls, vids = img_list(tdir)
    paths_file = Path(tdir).parent / (entry['slug'] + '.paths.json')
    if not entry['builtin'] and paths_file.exists():
        paths = read_json(paths_file)
    else:
        paths = [p.relative_to(tdir).as_posix() for p in Path(tdir).rglob('*')
                 if p.is_file() and not p.is_symlink() and '.git' not in p.relative_to(tdir).parts]
    app_files = sorted(set(paths) & KNOWN_APP_FILES)
    wallpapers_count = sum(p.startswith('backgrounds/') and Path(p).suffix.lower() in IMG_EXT for p in paths)
    vids = sorted(p for p in paths if p.startswith('backgrounds/') and Path(p).suffix.lower() in VID_EXT)
    site_img = None
    if entry.get('site_image'):
        rel = Path(entry['site_image'].lstrip('/'))
        if '..' not in rel.parts:
            candidate = Path(site) / rel
            if candidate.is_file() and not candidate.is_symlink():
                site_img = rel.as_posix()
    lic, lic_kind = licence(tdir)
    if entry['builtin']:
        lic, lic_kind = 'LICENSE (omacom/omarchy repo root)', 'MIT'
    rec = dict(slug=entry['slug'], name=entry['name'], author=entry['author'], repo=entry['repo'],
               commit=entry.get('commit'), builtin=entry['builtin'], licence_file=lic, licence_detected=lic_kind,
               format=fmt, colors_toml_variant=kind, palette_source=pal['source'], mode=pal['mode'],
               size_mb=math.ceil(tree_bytes(tdir) / MIB), previews=previews,
               preview_source=psrc or ('omarchy-site gallery image' if site_img else None), wallpapers=walls,
               videos_not_copied=vids, script_like_files_not_executed=sum(Path(p).suffix.lower() in
               ('.sh', '.lua', '.py', '.vsix', '.fish', '.js', '.qml', '.bash', '.zsh') for p in paths),
               tier=entry.get('tier', 'builtin' if entry['builtin'] else 'official'), lists=entry.get('lists', []),
               official_pos=entry.get('official_pos'), app_files=app_files,
               apps=sorted({Path(p).stem for p in app_files}), wallpapers_count=wallpapers_count,
               palette=pal, last_commit=entry.get('last_commit'))
    job = dict(slug=entry['slug'], previews=previews, walls=walls,
               site_img=site_img if not previews else None, bg=pal['background'])
    return rec, pal, job


def last_commit(cache, tdir):
    return run_git(cache, ['-C', tdir, 'log', '-1', '--format=%cI']).stdout.strip()[:10] or None


def palettes(cache):
    receipts = read_json(cache / 'fetch.json')
    omarchy_commit = commit_id(cache, cache / 'src/omarchy')
    site_commit = commit_id(cache, cache / 'src/site')
    stats_data = read_json(cache / 'stats.json') if (cache / 'stats.json').exists() else dict(fetched=date.today().isoformat(), source='none', repos={})
    repos = {key.lower(): value for key, value in stats_data.get('repos', {}).items()}
    index, pals, jobs, skipped = [], {}, [], []
    for receipt in receipts:
        entry = dict(receipt['entry'])
        tdir = theme_path(cache, entry) if entry['slug'] else None
        if receipt['status'] != 'ok' or not tdir.is_dir():
            skipped.append(dict(slug=entry['slug'], name=entry['name'], repo=entry['repo'],
                                reason=receipt['status'] + ' ' + receipt['reason']))
            continue
        repository = cache / 'src/omarchy' if entry['builtin'] else tdir
        entry['commit'] = omarchy_commit if entry['builtin'] else commit_id(cache, tdir)
        entry['last_commit'] = last_commit(cache, repository)
        rec, pal, job = process_theme(entry, tdir, cache / 'src/site')
        rec['stats'] = None if entry['builtin'] else repos.get(github_name(entry['repo']))
        job['tdir'] = tdir.relative_to(cache).as_posix()
        rec['path'] = job['tdir']
        index.append(rec)
        pals[entry['slug']] = dict(name=entry['name'], **pal)
        jobs.append(job)
    index, duplicates = dedupe_themes(index)
    viz = metrics(index, stats_data)
    for theme in index:
        del theme['palette']
    generated = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    write_json(cache / 'index.json', dict(generated=generated,
               sources=dict(gallery_list='https://github.com/omacom/omarchy-site src/data/themes.json @ ' + (site_commit or '?'),
                            builtin='https://github.com/omacom/omarchy themes/ @ ' + (omarchy_commit or '?')),
               stats={key: stats_data[key] for key in ('fetched', 'source')}, viz=viz, lists=LIST_LABELS,
               counts=dict(listed=len(receipts), collected=len(index), skipped=len(skipped), duplicates=len(duplicates)),
               duplicates=duplicates, skipped=skipped, themes=index))
    write_json(cache / 'palettes.json', dict(generated=generated, palettes=pals))
    write_json(cache / 'jobs.json', jobs)
    print('palettes', len(pals), 'skipped', len(skipped), 'duplicates', len(duplicates))


class ImageBackend:
    """Optional decoders. Without one, keep original image bytes for the browser."""
    def __init__(self):
        self.Image = self.ImageOps = None
        try:
            from PIL import Image, ImageOps
            Image.MAX_IMAGE_PIXELS = 100_000_000
            self.Image, self.ImageOps = Image, ImageOps
        except ImportError:
            pass
        self.magick = shutil.which('magick')
        self.convert = shutil.which('convert') if shutil.which('identify') else None
        self.identify = shutil.which('identify')
        self.ffmpeg, self.ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')

    @contextmanager
    def open_image(self, src):
        with warnings.catch_warnings():
            warnings.simplefilter('error', self.Image.DecompressionBombWarning)
            with self.Image.open(src) as im:
                yield im

    def dimensions(self, src):
        if self.Image:
            with self.open_image(src) as im:
                return im.width, im.height, getattr(im, 'is_animated', False)
        if self.magick or self.identify:
            cmd = [self.magick, 'identify'] if self.magick else [self.identify]
            result = subprocess.run(cmd + ['-ping', '-format', '%w %h %n\n', str(src)],
                                    capture_output=True, text=True, check=True, timeout=60)
            w, h, frames = map(int, result.stdout.splitlines()[0].split())
            return w, h, frames > 1
        if self.ffmpeg and self.ffprobe:
            result = subprocess.run([self.ffprobe, '-v', 'error', '-select_streams', 'v:0',
                                     '-show_entries', 'stream=width,height,nb_frames', '-of', 'json', str(src)],
                                    capture_output=True, text=True, check=True, timeout=60)
            stream = json.loads(result.stdout)['streams'][0]
            return stream['width'], stream['height'], int(stream.get('nb_frames', '1').replace('N/A', '1')) > 1
        return None, None, False

    def display(self, src, dst, orig_url):
        w, h, animated = self.dimensions(src)
        ext = src.suffix.lower()
        if w is None or animated or (src.stat().st_size < 700_000 and max(w, h) <= VIEW
                                     and ext in ('.png', '.jpg', '.jpeg', '.webp')):
            return orig_url, w, h
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            return None, w, h
        if self.Image:
            with self.open_image(src) as im:
                im = self.ImageOps.exif_transpose(im)
                if im.mode not in ('RGB', 'RGBA'):
                    im = im.convert('RGBA' if ('A' in im.mode or 'transparency' in im.info) else 'RGB')
                im.thumbnail((VIEW, VIEW), self.Image.Resampling.LANCZOS)
                im.save(dst, 'WEBP', quality=84, method=4)
        elif self.magick or self.convert:
            cmd = [self.magick] if self.magick else [self.convert]
            subprocess.run(cmd + [str(src) + '[0]', '-auto-orient', '-resize', '2560x2560>',
                                  '-quality', '84', str(dst)], capture_output=True, check=True, timeout=60)
        elif self.ffmpeg and self.ffprobe:
            subprocess.run([self.ffmpeg, '-v', 'error', '-y', '-i', str(src), '-frames:v', '1',
                            '-vf', r'scale=w=min(2560\,iw):h=min(2560\,ih):force_original_aspect_ratio=decrease',
                            '-quality', '84', str(dst)], capture_output=True, check=True, timeout=60)
        else:
            return orig_url, w, h
        return None, w, h

    def thumbnail(self, src, dst, bg):
        if self.Image:
            with self.open_image(src) as im:
                im.seek(0)
                im = self.ImageOps.exif_transpose(im).convert('RGBA')
                base = self.Image.new('RGBA', im.size, bg)
                base.alpha_composite(im)
                thumb = self.ImageOps.fit(base.convert('RGB'), (480, 270), self.Image.Resampling.LANCZOS,
                                          centering=(0.5, 0.5))
                thumb.save(dst, 'WEBP', quality=72, method=5)
        elif self.magick or self.convert:
            cmd = [self.magick] if self.magick else [self.convert]
            subprocess.run(cmd + [str(src) + '[0]', '-auto-orient', '-background', bg,
                                  '-alpha', 'remove', '-alpha', 'off', '-resize', '480x270^',
                                  '-gravity', 'center', '-extent', '480x270', '-quality', '72', str(dst)],
                           capture_output=True, check=True, timeout=60)
        elif self.ffmpeg and self.ffprobe:
            subprocess.run([self.ffmpeg, '-v', 'error', '-y', '-i', str(src), '-frames:v', '1',
                            '-vf', 'scale=480:270:force_original_aspect_ratio=increase,crop=480:270',
                            '-quality', '72', str(dst)], capture_output=True, check=True, timeout=60)
        else:
            return False
        return True


def image_job(job, cache, app, backend):
    slug = safe_slug(job['slug'])
    tdir, out = cache / job['tdir'], app / 'img' / slug
    out.mkdir(parents=True, exist_ok=True)
    result = dict(slug=slug, previews=[], walls=[], thumb=None, errors=[])
    def url(path):
        return quote(path.relative_to(app).as_posix())
    def process(kind, i, rel):
        src = tdir / rel
        original = out / 'orig' / rel
        try:
            # Preserve original bytes inside app so cache placement cannot break O.
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, original)
            dst = out / ('%s%02d.webp' % (kind, i))
            display, w, h = backend.display(src, dst, url(original))
            return dict(src=display or url(dst), orig=url(original), w=w, h=h, file=src.name), src
        except Exception as error:  # Third-party decoders also raise their own exception types.
            original.unlink(missing_ok=True)
            result['errors'].append(rel + ': ' + type(error).__name__)
            return None, None
    first = wfirst = None
    for i, rel in enumerate(job['previews']):
        record, src = process('p', i, rel)
        if record:
            result['previews'].append(record)
            first = first or src
    if job.get('site_img'):
        try:
            src = cache / 'src/site' / job['site_img']
            dst = out / ('site' + src.suffix.lower())
            shutil.copy2(src, dst)
            w, h, _ = backend.dimensions(dst)
            result['previews'].append(dict(src=url(dst), orig=url(dst), w=w, h=h,
                                            file='omarchy-site gallery image'))
            first = first or dst
        except Exception as error:  # Third-party decoders also raise their own exception types.
            result['errors'].append('site image: ' + type(error).__name__)
    for i, rel in enumerate(job['walls']):
        record, src = process('w', i, rel)
        if record:
            result['walls'].append(record)
            wfirst = wfirst or src
    for kind, src in [('thumb', first or wfirst), ('wthumb', wfirst)]:
        if src:
            try:
                dst = out / (kind + '.webp')
                if backend.thumbnail(src, dst, job['bg']):
                    result[kind] = url(dst)
                else:
                    candidates = result['walls'] if kind == 'wthumb' else result['previews'] or result['walls']
                    result[kind] = candidates[0]['src']
            except Exception as error:  # Third-party decoders also raise their own exception types.
                result['errors'].append(kind + ': ' + type(error).__name__)
    return result


def images(cache, app=APP):
    backend = ImageBackend()
    jobs = read_json(cache / 'jobs.json')
    results = [image_job(job, cache, app, backend) for job in jobs]
    write_json(cache / 'images.json', results)
    print('images', len(results), 'themes;', sum(len(r['errors']) for r in results), 'decode errors')
    for result in results:
        for error in result['errors']:
            print('image warning:', result['slug'], error)


def write_data(path, value):
    """JSON in a script file, also safe to embed in an HTML script element."""
    text = json.dumps(value, separators=(',', ':'), ensure_ascii=True)
    text = text.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text('/* Generated by tools/build.py; data only. */\nwindow.GALLERY = ' + text + ';\n',
                          encoding='utf-8')


def read_data(path):
    text = Path(path).read_text(encoding='utf-8')
    match = re.search(r'window\.GALLERY\s*=\s*(.*?)\s*;\s*$', text, re.S)
    if not match:
        raise ValueError('data.js must contain a window.GALLERY JSON assignment')
    return json.loads(match.group(1))


def data(cache, app=APP):
    idx = read_json(cache / 'index.json')
    pals = read_json(cache / 'palettes.json')['palettes']
    imgs = {r['slug']: r for r in read_json(cache / 'images.json')}
    stats_data = read_json(cache / 'stats.json') if (cache / 'stats.json').exists() else dict(fetched=idx['generated'][:10], source='none', repos={})
    for theme in idx['themes']:
        theme['palette'] = pals[theme['slug']]
    viz = metrics(idx['themes'], stats_data)
    out = []
    for theme in idx['themes']:
        pal, im = pals[theme['slug']], imgs[theme['slug']]
        row = theme.get('stats')
        api_licence = (row or {}).get('licence')
        if api_licence == 'NOASSERTION':
            api_licence = None
        detected = api_licence or theme['licence_detected']
        st = dict(s=row['stars'], f=row['forks'], c=row.get('created'), u=row.get('pushed'),
                  i=row.get('issues', row.get('open_issues', 0)), a=int(row.get('archived', False))) if row else None
        wmp = max((image['w'] * image['h'] / 1_000_000 for image in im['walls']
                   if image.get('w') and image.get('h')), default=0)
        out.append(dict(
            slug=theme['slug'], name=theme['name'], author=theme['author'], repo=theme['repo'],
            builtin=theme['builtin'], format=theme['format'], src=pal['source'], srcs=pal['sources_used'],
            guessed=pal['guessed'], derived=pal['derived'], mode=pal['mode'],
            lic=detected or ('file: ' + theme['licence_file'] if theme['licence_file'] else None),
            commit=(theme['commit'] or '')[:10], videos=len(theme['videos_not_copied']), psrc=theme['preview_source'],
            thumb=im['thumb'], wthumb=im.get('wthumb'), previews=im['previews'], walls=im['walls'],
            hue=pal['accent_hue'], sat=pal['accent_saturation'], contrast=pal['contrast_fg_bg'],
            tier=theme.get('tier', 'builtin' if theme['builtin'] else 'official'), ls=theme.get('lists', []),
            pos=theme.get('official_pos'), st=st, lc=theme.get('last_commit'), tw=theme.get('twin_of'),
            licc=licence_category(detected, bool(theme['licence_file'])),
            wn=theme.get('wallpapers_count', len(theme['wallpapers'])), wmp=round(wmp, 2),
            apps=theme.get('apps', []), m=theme['m'], r=theme['r'],
            p=dict(bg=pal['background'], fg=pal['foreground'], cur=pal['cursor'], sel=pal['selection'],
                   selfg=pal['selection_foreground'], ac=pal['accent'], mu=pal['muted'], ab=pal['border_active'],
                   ib=pal['border_inactive'], bg2=pal['background_light'], bg0=pal['background_dark'],
                   fgb=pal['foreground_bright'], fgd=pal['foreground_dim'], n=pal['normal'], b=pal['bright'])))
    out.sort(key=lambda theme: theme['name'].lower())
    write_data(app / 'data.js', dict(generated=idx['generated'], sources=idx['sources'], skipped=idx['skipped'],
               duplicates=idx.get('duplicates', []), stats={key: stats_data[key] for key in ('fetched', 'source')},
               viz=viz, lists=LIST_LABELS, themes=out))
    print('data', len(out), 'themes')


def check_app(app=APP):
    errors = []
    app = Path(app)
    for path in sorted(app.rglob('*')):
        if path.is_file() and path.suffix.lower() in ('.html', '.css', '.js') and path != app / 'data.js':
            # Repository URLs in the generated catalog are metadata, not remote app resources.
            if re.search(r'https?://', path.read_text(encoding='utf-8'), re.I):
                errors.append('external URL in ' + path.relative_to(app).as_posix())
    try:
        gallery = read_data(app / 'data.js')
    except (OSError, ValueError) as error:
        return errors + ['data.js missing or invalid: ' + type(error).__name__]
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, child in value.items():
                yield from strings(key)
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)
    for value in strings(gallery):
        if re.match(r'^/(?:home|mnt|Users|tmp)(?:/|$)', value):
            errors.append('local absolute path in data.js')
        if re.search(r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_', value):
            errors.append('credential pattern in data.js')
    if not gallery['themes']:
        errors.append('data.js contains no themes')
    for theme in gallery['themes']:
        refs = [theme.get('thumb'), theme.get('wthumb')]
        refs += [im[key] for im in theme['previews'] + theme['walls'] for key in ('src', 'orig')]
        for ref in refs:
            if not ref:
                continue
            parts = urlsplit(ref)
            path = app / unquote(parts.path)
            if parts.scheme or parts.netloc or not path.resolve().is_relative_to(app.resolve()) or not path.is_file():
                errors.append('missing or non-local image: ' + theme['slug'] + ' ' + ref)
    return errors


def check(app=APP):
    errors = check_app(app)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        raise ValueError('gallery check failed with %d error(s)' % len(errors))
    print('check: all images exist; app HTML, CSS and JS have no external URLs')


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('must be non-negative')
    return number


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be at least 1')
    return number


def positive_mb(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be a positive finite number')
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', default=os.environ.get('THEME_GALLERY_CACHE', 'cache'),
                        help='cache directory, relative to the current directory by default')
    parser.add_argument('--output', default=str(APP), help='generated app directory')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('list', 'discover', 'stats', 'fetch', 'palettes', 'images', 'data', 'check', 'all'):
        sub = commands.add_parser(name)
        sub.add_argument('--cache', default=argparse.SUPPRESS, help='cache directory')
        sub.add_argument('--output', default=argparse.SUPPRESS, help='generated app directory')
        if name in ('fetch', 'all'):
            sub.add_argument('--only', help='comma-separated theme slugs')
            sub.add_argument('--limit', type=nonnegative, help='first N themes after --only')
            sub.add_argument('--max-mb', type=positive_mb, default=150, help='maximum working-tree MiB per theme')
            sub.add_argument('--jobs', type=positive_int, default=6, help='concurrent community clones')
            sub.add_argument('--tier', choices=('official', 'curated', 'starred', 'all'), default='curated')
            sub.add_argument('--min-stars', type=nonnegative, default=2)
            sub.add_argument('--wallpapers', type=nonnegative, default=6, help='lean wallpapers to copy; 0 copies all')
            sub.add_argument('--cache-max-mb', type=positive_mb, default=6000)
        if name in ('discover', 'all'):
            sub.add_argument('--no-search', action='store_true')
        if name in ('discover', 'stats', 'fetch', 'all'):
            sub.add_argument('--no-api', action='store_true')
        if name in ('discover', 'stats', 'all'):
            sub.add_argument('--stats-max-age', type=nonnegative, default=7)
            sub.add_argument('--refresh-stats', action='store_true')
    return parser.parse_args(argv)


def prepare_output(app):
    app = Path(app)
    if app.resolve() == APP.resolve():
        return
    app.mkdir(parents=True, exist_ok=True)
    for source in APP.iterdir():
        if source.name in ('data.js', 'img'):
            continue
        if source.is_file():
            shutil.copy2(source, app / source.name)
        elif source.is_dir():
            shutil.copytree(source, app / source.name, dirs_exist_ok=True)


def main(argv=None):
    args = parse_args(argv)
    if not shutil.which('git'):
        print('build: git is required', file=sys.stderr)
        return 1
    cache = Path(args.cache).expanduser().resolve()
    app = Path(args.output).expanduser().resolve()
    try:
        if args.command in ('list', 'all'):
            list_themes(cache)
        if args.command in ('discover', 'all'):
            discover(cache, args.no_search, args.no_api, args.stats_max_age, args.refresh_stats)
        if args.command in ('stats', 'all'):
            stats(cache, args.stats_max_age, args.refresh_stats, args.no_api)
        if args.command in ('fetch', 'all'):
            fetch_themes(cache, args.only, args.limit, args.max_mb, args.jobs, args.tier,
                         args.min_stars, args.wallpapers, args.cache_max_mb, args.no_api)
        if args.command in ('palettes', 'all'):
            palettes(cache)
        if args.command in ('images', 'data', 'all'):
            prepare_output(app)
        if args.command in ('images', 'all'):
            images(cache, app)
        if args.command in ('data', 'all'):
            data(cache, app)
        if args.command in ('check', 'all'):
            check(app)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print('build:', message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
