"""Build the local Omarchy gallery. Theme repositories are read as data only."""
import argparse
import colorsys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
from urllib.parse import quote, unquote, urlsplit

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
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
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


def run_git(cache, args, timeout=60):
    argv, env = git_command(cache)
    return subprocess.run(argv + [str(a) for a in args], env=env,
                          capture_output=True, text=True, timeout=timeout, check=True)


def clone_repo(cache, url, dest, update=False, timeout=60):
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
            run_git(cache, ['clone', '--quiet', '--depth', '1', '--filter=blob:limit=25m',
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
    for theme in themes:
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
        rows.append(dict(name=theme['name'], repo=url, slug=slug, author=author, site_image=theme.get('image')))
    return dict(builtin=builtin, community=rows)


def list_themes(cache):
    for name, url in [('site', 'https://github.com/omacom/omarchy-site'),
                      ('omarchy', 'https://github.com/omacom/omarchy')]:
        clone_repo(cache, url, cache / 'src' / name, update=True, timeout=900)
    catalog = make_list(cache / 'src/site', cache / 'src/omarchy')
    write_json(cache / 'list.json', catalog)
    print('listed', len(catalog['builtin']), 'built-in and', len(catalog['community']), 'community themes')


def catalog_entries(catalog):
    entries = [dict(slug=s, name=' '.join(w.capitalize() for w in s.split('-')),
                    author='Omarchy (built-in)', repo='https://github.com/omacom/omarchy/tree/master/themes/' + s,
                    builtin=True, commit=None, site_image=None) for s in catalog['builtin']]
    entries.extend(dict(slug=r['slug'], name=r['name'], author=r['author'], repo=r['repo'],
                        builtin=False, commit=None, site_image=r.get('site_image')) for r in catalog['community'])
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


def fetch_themes(cache, only=None, limit=None, max_mb=150, jobs=6):
    entries = select_entries(catalog_entries(read_json(cache / 'list.json')), only, limit)
    # Create the shared template before starting concurrent Git processes.
    git_command(cache)
    def fetch(entry):
        slug = entry['slug']
        if slug is None:
            return dict(entry=entry, status='skip', reason='unsupported url')
        tdir = theme_path(cache, entry)
        cloned = False
        if not entry['builtin']:
            if tree_bytes(cache / 'themes') > 6000 * MIB:
                return dict(entry=entry, status='skip', reason='cache-over-6GB')
            try:
                cloned = clone_repo(cache, entry['repo'], tdir)
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                reason = str(error) if isinstance(error.__cause__, subprocess.TimeoutExpired) else type(error).__name__
                return dict(entry=entry, status='fail', reason=reason)
        if not tdir.is_dir() or tdir.is_symlink():
            return dict(entry=entry, status='missing', reason='theme directory unavailable')
        if not within_size_cap(tdir, max_mb):
            if not entry['builtin']:
                shutil.rmtree(tdir)
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
               videos_not_copied=vids, script_like_files_not_executed=exec_like(tdir))
    job = dict(slug=entry['slug'], previews=previews, walls=walls,
               site_img=site_img if not previews else None, bg=pal['background'])
    return rec, pal, job


def palettes(cache):
    receipts = read_json(cache / 'fetch.json')
    omarchy_commit = commit_id(cache, cache / 'src/omarchy')
    site_commit = commit_id(cache, cache / 'src/site')
    index, pals, jobs, skipped = [], {}, [], []
    for receipt in receipts:
        entry = dict(receipt['entry'])
        tdir = theme_path(cache, entry) if entry['slug'] else None
        if receipt['status'] != 'ok' or not tdir.is_dir():
            skipped.append(dict(slug=entry['slug'], name=entry['name'], repo=entry['repo'],
                                reason=receipt['status'] + ' ' + receipt['reason']))
            continue
        entry['commit'] = omarchy_commit if entry['builtin'] else commit_id(cache, tdir)
        rec, pal, job = process_theme(entry, tdir, cache / 'src/site')
        job['tdir'] = tdir.relative_to(cache).as_posix()
        rec['path'] = job['tdir']
        index.append(rec)
        pals[entry['slug']] = dict(name=entry['name'], **pal)
        jobs.append(job)
    generated = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    write_json(cache / 'index.json', dict(generated=generated,
               sources=dict(gallery_list='https://github.com/omacom/omarchy-site src/data/themes.json @ ' + (site_commit or '?'),
                            builtin='https://github.com/omacom/omarchy themes/ @ ' + (omarchy_commit or '?')),
               counts=dict(listed=len(receipts), collected=len(index), skipped=len(skipped)), skipped=skipped, themes=index))
    write_json(cache / 'palettes.json', dict(generated=generated, palettes=pals))
    write_json(cache / 'jobs.json', jobs)
    print('palettes', len(pals), 'skipped', len(skipped))

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
    out = []
    for theme in idx['themes']:
        pal, im = pals[theme['slug']], imgs[theme['slug']]
        out.append(dict(
            slug=theme['slug'], name=theme['name'], author=theme['author'], repo=theme['repo'],
            builtin=theme['builtin'], format=theme['format'], src=pal['source'], srcs=pal['sources_used'],
            guessed=pal['guessed'], derived=pal['derived'], mode=pal['mode'],
            lic=theme['licence_detected'] or ('file: ' + theme['licence_file'] if theme['licence_file'] else None),
            commit=(theme['commit'] or '')[:10], videos=len(theme['videos_not_copied']), psrc=theme['preview_source'],
            thumb=im['thumb'], wthumb=im.get('wthumb'), previews=im['previews'], walls=im['walls'],
            hue=pal['accent_hue'], sat=pal['accent_saturation'], contrast=pal['contrast_fg_bg'],
            p=dict(bg=pal['background'], fg=pal['foreground'], cur=pal['cursor'], sel=pal['selection'],
                   selfg=pal['selection_foreground'], ac=pal['accent'], mu=pal['muted'], ab=pal['border_active'],
                   ib=pal['border_inactive'], bg2=pal['background_light'], bg0=pal['background_dark'],
                   fgb=pal['foreground_bright'], fgd=pal['foreground_dim'], n=pal['normal'], b=pal['bright'])))
    out.sort(key=lambda theme: theme['name'].lower())
    write_data(app / 'data.js', dict(generated=idx['generated'], sources=idx['sources'], skipped=idx['skipped'], themes=out))
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
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('list', 'fetch', 'palettes', 'images', 'data', 'check', 'all'):
        sub = commands.add_parser(name)
        sub.add_argument('--cache', default=argparse.SUPPRESS, help='cache directory')
        if name in ('fetch', 'all'):
            sub.add_argument('--only', help='comma-separated theme slugs')
            sub.add_argument('--limit', type=nonnegative, help='first N themes after --only')
            sub.add_argument('--max-mb', type=positive_mb, default=150, help='maximum working-tree MiB per theme')
            sub.add_argument('--jobs', type=positive_int, default=6, help='concurrent community clones')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not shutil.which('git'):
        print('build: git is required', file=sys.stderr)
        return 1
    cache = Path(args.cache).expanduser().resolve()
    try:
        if args.command in ('list', 'all'):
            list_themes(cache)
        if args.command in ('fetch', 'all'):
            fetch_themes(cache, args.only, args.limit, args.max_mb, args.jobs)
        if args.command in ('palettes', 'all'):
            palettes(cache)
        if args.command in ('images', 'all'):
            images(cache)
        if args.command in ('data', 'all'):
            data(cache)
        if args.command in ('check', 'all'):
            check()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Avoid leaking local filenames or credentials through persisted receipts.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print('build:', message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
