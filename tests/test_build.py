import contextlib
import copy
from datetime import date, timedelta
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import struct
import time
import types
import warnings
import zlib
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build


NAMED = '''background = "#101020"
foreground = "#d0d0e0"
accent = "#5588cc"
muted = "#505060"
bright_foreground = "#eeeeff"
red = "#cc1111"
green = "#22aa22"
yellow = "#bbbb33"
blue = "#4444cc"
magenta = "#aa33aa"
cyan = "#33aaaa"
bright_red = "#ff2222"
bright_green = "#33dd33"
bright_yellow = "#dddd44"
bright_blue = "#6666ff"
bright_magenta = "#dd44dd"
bright_cyan = "#44dddd"
mode = "dark"
color1 = "#ffffff"
'''
LEGACY = '''[colors.primary]
background = "0x202020"
foreground = "#eeeeee"
[colors.cursor]
cursor = "#abcdef"
[colors.selection]
background = "#333333"
text = "#ffffff"
[colors.normal]
''' + ''.join(f'{name} = "#{i + 1:06x}"\n' for i, name in enumerate(
    ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'])) + '''[colors.bright]
''' + ''.join(f'{name} = "#{i + 9:06x}"\n' for i, name in enumerate(
    ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white']))


class BuildTests(unittest.TestCase):
    def setUp(self):
        # Keep every test artifact inside the project, including in CI.
        (ROOT / '.work').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.work')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.theme = self.root / 'theme'
        self.theme.mkdir()

    def git_fixture(self, argv, **kwargs):
        if 'clone' in argv:
            dest = Path(argv[-1])
            (dest / '.git').mkdir(parents=True)
            (dest / 'colors.toml').write_text(NAMED)
        output = ''
        if 'ls-tree' in argv:
            output = 'colors.toml\0' if '--name-only' in argv else '100644 blob ' + 'a' * 40 + '\tcolors.toml\0'
        elif 'show' in argv:
            output = NAMED
        return subprocess.CompletedProcess(argv, 0, output, '')

    def test_named_palette_and_precedence(self):
        (self.theme / 'colors.toml').write_text(NAMED)
        (self.theme / 'alacritty.toml').write_text(LEGACY)
        pal, kind = build.build_palette(self.theme)
        self.assertEqual(kind, 'colors.toml (4.x named + color0-15)')
        self.assertEqual(pal['normal'], ['#101020', '#cc1111', '#22aa22', '#bbbb33',
                                       '#4444cc', '#aa33aa', '#33aaaa', '#d0d0e0'])
        self.assertEqual(pal['bright'], ['#505060', '#ff2222', '#33dd33', '#dddd44',
                                       '#6666ff', '#dd44dd', '#44dddd', '#eeeeff'])
        self.assertEqual(pal['cursor'], '#eeeeff')
        self.assertEqual(pal['selection'], '#333333')
        self.assertEqual(pal['sources_used'], ['colors.toml', 'alacritty.toml'])
        self.assertEqual(pal['field_source']['normal1'], 'colors.toml')
        self.assertFalse(pal['guessed_core'])

    def test_legacy_palette(self):
        (self.theme / 'alacritty.toml').write_text(LEGACY)
        pal, kind = build.build_palette(self.theme)
        self.assertIsNone(kind)
        self.assertEqual(pal['background'], '#202020')
        self.assertEqual(pal['foreground'], '#eeeeee')
        self.assertEqual(pal['normal'], [f'#{i:06x}' for i in range(1, 9)])
        self.assertEqual(pal['bright'], [f'#{i:06x}' for i in range(9, 17)])
        self.assertEqual(pal['cursor'], '#abcdef')
        self.assertEqual(pal['selection_foreground'], '#ffffff')

    def test_derived_keys(self):
        (self.theme / 'colors.toml').write_text('background=" #000000 "\nforeground="#ffffff"\n')
        pal, _ = build.build_palette(self.theme)
        self.assertEqual(pal['accent'], '#5588cc')
        self.assertEqual(pal['border_active'], pal['accent'])
        self.assertEqual(pal['cursor'], '#ffffff')
        self.assertEqual(pal['selection'], '#383838')
        self.assertEqual(pal['selection_foreground'], '#ffffff')
        self.assertEqual(pal['border_inactive'], '#474747')
        self.assertEqual(pal['background_light'], '#171717')
        self.assertEqual(pal['background_dark'], '#000000')
        self.assertEqual(pal['foreground_dim'], '#8c8c8c')
        self.assertEqual(pal['muted'], '#595959')
        self.assertEqual(pal['foreground_bright'], pal['bright'][7])
        self.assertEqual(pal['derived'], ['border_active', 'accent', 'cursor', 'selection',
                                         'selection_fg', 'muted', 'border_inactive', 'bg_light',
                                         'bg_dark', 'fg_bright', 'fg_dim'])
        self.assertTrue(pal['guessed_core'])
        self.assertEqual(pal['contrast_fg_bg'], 21.0)

    def test_theme_scripts_are_never_executed(self):
        marker = self.root / 'executed'
        (self.theme / 'colors.toml').write_text(NAMED)
        (self.theme / 'install.sh').write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        (self.theme / 'install.sh').chmod(0o755)
        (self.theme / 'theme.lua').write_text('io.open("' + str(marker) + '", "w"):close()')
        entry = dict(slug='safe', name='Safe', author='Example', repo='https://github.com/example/safe',
                     builtin=False, commit=None, site_image=None)
        rec, pal, job = build.process_theme(entry, self.theme, self.root)
        self.assertEqual(rec['script_like_files_not_executed'], 2)
        self.assertEqual(pal['background'], '#101020')
        self.assertEqual(job['slug'], 'safe')
        self.assertFalse(marker.exists())

    def test_safe_slug_and_name_serialization(self):
        value = 'quotes " and <angle> </script> & \u2028\u2029'
        path = self.root / 'data.js'
        build.write_data(path, dict(themes=[dict(slug=value, name=value)]))
        text = path.read_text()
        self.assertNotIn('</script>', text.lower())
        self.assertNotIn('<', text)
        self.assertNotIn('>', text)
        self.assertNotIn('\u2028', text)
        self.assertEqual(build.read_data(path)['themes'][0], dict(slug=value, name=value))

    def test_size_cap_excludes_git(self):
        (self.theme / '.git').mkdir()
        (self.theme / '.git' / 'objects').write_bytes(b'x' * 200)
        (self.theme / 'payload').write_bytes(b'x' * 10)
        self.assertEqual(build.tree_bytes(self.theme), 10)
        self.assertTrue(build.within_size_cap(self.theme, 10 / 1024**2))
        self.assertFalse(build.within_size_cap(self.theme, 9 / 1024**2))

    def test_only_filter_then_limit(self):
        entries = [dict(slug=s, builtin=b) for s, b in
                   [('tokyo-night', True), ('catppuccin', True), ('other', False), ('gruvbox', True)]]
        self.assertEqual([e['slug'] for e in build.select_entries(entries, 'gruvbox,tokyo-night')],
                         ['tokyo-night', 'gruvbox'])
        self.assertEqual(build.select_entries(entries, 'gruvbox,tokyo-night', 1), entries[:1])
        self.assertEqual(build.select_entries(entries, limit=0), [])
        with self.assertRaises(ValueError):
            build.select_entries(entries, 'unknown')

    def test_image_selection_preserves_preview_priority(self):
        (self.theme / 'preview.png').write_bytes(b'x')
        (self.theme / 'preview-unlock.png').write_bytes(b'x')
        (self.theme / 'screenshot.png').write_bytes(b'x' * 50000)
        (self.theme / 'backgrounds').mkdir()
        (self.theme / 'backgrounds' / 'b.jpg').write_bytes(b'x')
        (self.theme / 'backgrounds' / 'a.png').write_bytes(b'x')
        (self.theme / 'backgrounds' / 'video.mp4').write_bytes(b'x')
        p, source, walls, videos = build.img_list(self.theme)
        self.assertEqual(p, ['preview.png', 'preview-unlock.png'])
        self.assertEqual(source, 'preview.png')
        self.assertEqual(walls, ['backgrounds/a.png', 'backgrounds/b.jpg'])
        self.assertEqual(videos, ['backgrounds/video.mp4'])
        (self.theme / 'preview.png').unlink()
        p, source, _, _ = build.img_list(self.theme)
        self.assertEqual(p, ['screenshot.png', 'preview-unlock.png'])
        self.assertEqual(source, 'other screenshot in repo')

    def test_check_missing_images_and_external_app_urls(self):
        app = self.root / 'app'
        app.mkdir()
        for name in ['index.html', 'gallery.css', 'gallery.js']:
            (app / name).write_text('')
        data = dict(themes=[dict(slug='x', thumb='img/x.png', previews=[], walls=[])])
        build.write_data(app / 'data.js', data)
        self.assertTrue(build.check_app(app))
        (app / 'img').mkdir()
        (app / 'img' / 'x.png').write_bytes(b'x')
        self.assertEqual(build.check_app(app), [])
        (app / 'gallery.js').write_text('https://example.org/image.png')
        self.assertTrue(build.check_app(app))
        (app / 'gallery.js').write_text('')
        data['themes'][0]['previews'] = [dict(src='img/x.png', orig='img/missing.png')]
        build.write_data(app / 'data.js', data)
        self.assertTrue(build.check_app(app))

    def test_cache_option_before_or_after_command(self):
        a = build.parse_args(['--cache', 'custom', 'all', '--only', 'tokyo-night', '--jobs', '2'])
        b = build.parse_args(['all', '--cache', 'custom', '--only', 'tokyo-night', '--jobs', '2'])
        self.assertEqual(vars(a), vars(b))
        with patch.dict(os.environ, {'THEME_GALLERY_CACHE': 'from-env'}):
            self.assertEqual(build.parse_args(['palettes']).cache, 'from-env')

    def test_fetch_size_cap_removes_oversized_community_clone(self):
        cache = self.root / 'cache'
        for slug, size in [('small', 4), ('large', 6)]:
            tdir = cache / 'themes' / slug
            (tdir / '.git').mkdir(parents=True)
            (tdir / 'colors.toml').write_text(NAMED)
            (tdir / 'payload').write_bytes(b'x' * size)
        catalog = dict(builtin=[], community=[dict(slug=s, name=s, author='Example',
                       repo='https://github.com/example/' + s) for s in ['small', 'large']])
        build.write_json(cache / 'list.json', catalog)
        with contextlib.redirect_stdout(io.StringIO()), \
             patch.object(build.subprocess, 'run', side_effect=self.git_fixture):
            build.fetch_themes(cache, max_mb=(len(NAMED.encode()) + 5) / 1024**2, jobs=2)
        receipts = build.read_json(cache / 'fetch.json')
        self.assertEqual([r['status'] for r in receipts], ['ok', 'skip'])
        self.assertEqual(receipts[1]['reason'], 'over-size-cap')
        self.assertTrue((cache / 'themes/small').is_dir())
        self.assertFalse((cache / 'themes/large').exists())

    def test_selected_pipeline_with_custom_cache_and_no_image_tools(self):
        cache, app = self.root / 'custom-cache', self.root / 'app'
        catalog = dict(builtin=['tokyo-night', 'catppuccin', 'gruvbox', 'stale'], community=[])
        build.write_json(cache / 'list.json', catalog)
        # A valid synthetic PNG; no theme image is copied into the tests.
        def chunk(kind, payload):
            return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', 2, 2, 8, 2, 0, 0, 0))
        png += chunk(b'IDAT', zlib.compress(b'\0' + b'\xff\0\0' * 2 + b'\0' + b'\xff\0\0' * 2))
        png += chunk(b'IEND', b'')
        backgrounds = dict(tokyo_night='#101020', catppuccin='#202030', gruvbox='#303040', stale='#404050')
        for slug in catalog['builtin']:
            tdir = cache / 'src/omarchy/themes' / slug
            tdir.mkdir(parents=True)
            bg = backgrounds[slug.replace('-', '_')]
            (tdir / 'colors.toml').write_text(NAMED.replace('#101020', bg))
            (tdir / 'preview.png').write_bytes(png)
            (tdir / 'backgrounds').mkdir()
            (tdir / 'backgrounds/wall.png').write_bytes(png)
        backend = build.ImageBackend()
        for attr in ('Image', 'ImageOps', 'magick', 'convert', 'identify', 'ffmpeg', 'ffprobe'):
            setattr(backend, attr, None)
        with contextlib.redirect_stdout(io.StringIO()), patch.object(build, 'ImageBackend', return_value=backend):
            build.fetch_themes(cache)  # Simulate an older, wider selection first.
            build.fetch_themes(cache, only='tokyo-night,catppuccin,gruvbox')
            build.stats(cache, no_api=True)
            build.palettes(cache)
            build.images(cache, app)
            build.data(cache, app)
        for name in ('index.html', 'gallery.css', 'gallery.js'):
            (app / name).write_text('')
        gallery = build.read_data(app / 'data.js')
        self.assertEqual({t['slug'] for t in gallery['themes']}, {'tokyo-night', 'catppuccin', 'gruvbox'})
        self.assertEqual(gallery['stats']['source'], 'none')
        self.assertEqual(build.check_app(app), [])
        for theme in gallery['themes']:
            self.assertEqual(theme['p']['bg'], backgrounds[theme['slug'].replace('-', '_')])
            self.assertTrue(theme['thumb'].startswith('img/'))
            for im in theme['previews'] + theme['walls']:
                self.assertTrue(im['src'].startswith('img/'))
                self.assertTrue(im['orig'].startswith('img/'))
        self.assertNotIn(str(cache), (app / 'data.js').read_text())
        self.assertNotIn(str(cache), (cache / 'jobs.json').read_text())

    def test_all_cli_stage_order_and_options(self):
        calls = []
        with patch.object(build, 'list_themes', side_effect=lambda c: calls.append('list')), \
             patch.object(build, 'discover', side_effect=lambda *a: calls.append(('discover', a[1:]))), \
             patch.object(build, 'stats', side_effect=lambda *a: calls.append(('stats', a[1:]))), \
             patch.object(build, 'fetch_themes', side_effect=lambda *a: calls.append(('fetch', a[1:]))), \
             patch.object(build, 'palettes', side_effect=lambda c: calls.append('palettes')), \
             patch.object(build, 'images', side_effect=lambda c, a: calls.append('images')), \
             patch.object(build, 'data', side_effect=lambda c, a: calls.append('data')), \
             patch.object(build, 'check', side_effect=lambda a: calls.append('check')):
            status = build.main(['all', '--cache', str(self.root / 'custom'), '--only', 'gruvbox',
                                 '--limit', '1', '--max-mb', '2', '--jobs', '3', '--tier', 'starred',
                                 '--min-stars', '5', '--wallpapers', '4', '--cache-max-mb', '1234',
                                 '--no-api', '--no-search', '--stats-max-age', '9', '--refresh-stats'])
        self.assertEqual(status, 0)
        self.assertEqual(calls, ['list', ('discover', (True, True, 9, True)), ('stats', (9, True, True)),
                                ('fetch', ('gruvbox', 1, 2.0, 3, 'starred', 5, 4, 1234, True)),
                                'palettes', 'images', 'data', 'check'])

    def test_cache_size_scan_tolerates_concurrent_removal(self):
        victim = self.theme / 'disappearing'
        victim.write_bytes(b'gone')
        (self.theme / 'survivor').write_bytes(b'abc')
        original_stat, original_is_file = Path.stat, Path.is_file
        def stat(path, *args, **kwargs):
            if path == victim:
                raise FileNotFoundError('removed by another worker')
            return original_stat(path, *args, **kwargs)
        def is_file(path):
            # The earlier file check observed the file before concurrent removal.
            return True if path == victim else original_is_file(path)
        with patch.object(Path, 'stat', stat), patch.object(Path, 'is_file', is_file):
            self.assertEqual(build.tree_bytes(self.theme), 3)

    def test_decoder_specific_errors_are_recorded_per_image(self):
        class DecoderError(Exception):
            pass
        class Decoder:
            def display(self, src, dst, original):
                if src.name == 'bomb.png':
                    raise DecoderError('image rejected by decoder')
                return original, 1, 1
            def thumbnail(self, src, dst, bg):
                return False
        (self.theme / 'bomb.png').write_bytes(b'not decoded')
        (self.theme / 'good.png').write_bytes(b'fixture')
        job = dict(slug='fixture', tdir='theme', previews=['bomb.png', 'good.png'],
                   walls=[], site_img=None, bg='#000000')
        result = build.image_job(job, self.root, self.root / 'app', Decoder())
        self.assertEqual(result['errors'], ['bomb.png: DecoderError'])
        self.assertEqual(len(result['previews']), 1)
        self.assertEqual(result['previews'][0]['file'], 'good.png')
        self.assertTrue(result['thumb'].endswith('good.png'))
        self.assertFalse((self.root / 'app/img/fixture/orig/bomb.png').exists())
        self.assertEqual((self.root / 'app/img/fixture/orig/good.png').read_bytes(), b'fixture')

    def test_git_disables_hooks_filters_and_submodules(self):
        argv, env = build.git_command(self.root)
        options = ' '.join(argv)
        self.assertIn('core.hooksPath=', options)
        self.assertIn('core.symlinks=false', options)
        self.assertIn('filter.lfs.smudge=', options)
        self.assertIn('filter.lfs.process=', options)
        self.assertIn('submodule.recurse=false', options)
        self.assertEqual(env['GIT_LFS_SKIP_SMUDGE'], '1')
        self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(env['GIT_CONFIG_NOSYSTEM'], '1')

    def parse_with_deadline(self, parser, filename, text):
        path = self.theme / filename
        path.write_text(text)
        script = '''
import json
import sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))
except ImportError:
    pass
sys.path.insert(0, sys.argv[1])
import build
print(json.dumps(getattr(build, sys.argv[2])(build.read_text(sys.argv[3]))))
'''
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT / 'tools'),
                                     parser, str(path)], capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired:
            self.fail(parser + ' did not finish within 2 seconds')
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_hyprland_self_reference_finishes_under_two_seconds(self):
        text = '$a = ' + '$a' * 1000 + '\ncol.active_border = $a\n'
        self.assertIsNone(self.parse_with_deadline('from_hyprland', 'hyprland.conf', text)['border_active'])

    def test_hyprland_expansion_is_capped(self):
        text = '$a = ' + 'x' * 4096 + ' rgb(123456)\ncol.active_border = $a\n'
        self.assertIsNone(build.from_hyprland(text)['border_active'])
        text = '$a = ' + 'x' * 1000 + '\n$b = ' + '$a' * 10 + ' rgb(123456)\n'
        self.assertIsNone(build.from_hyprland(text + 'col.active_border = $b\n')['border_active'])

    def test_hyprland_self_reference_is_not_expanded(self):
        text = '$a = rgb(123456)$a\ncol.active_border = $a\n'
        self.assertIsNone(build.from_hyprland(text)['border_active'])

    def test_hyprland_indirect_cycle_finishes_under_two_seconds(self):
        text = '$a = ' + '$b' * 500 + '\n$b = ' + '$a' * 500 + '\ncol.active_border = $a\n'
        self.assertIsNone(self.parse_with_deadline('from_hyprland', 'hyprland.conf', text)['border_active'])

    def test_hyprland_regular_variables_and_borders(self):
        text = '$base = rgba(123456ff)\n$alias = $base\n'
        text += 'col.active_border = $alias\ncol.inactive_border = rgb(abcdef)\n'
        self.assertEqual(build.from_hyprland(text),
                         dict(border_active='#123456', border_inactive='#abcdef'))
        self.assertEqual(build.from_hyprland('$ab = rgb(123456)\n$a = $ab\ncol.active_border = $a\n'),
                         dict(border_active='#123456'))

    def test_colors_toml_blank_lines_finish_under_two_seconds(self):
        # Invalid TOML forces the fallback, which otherwise is not used for blank lines.
        for text in ('\n' * 400_000, 'invalid\n' + '\n' * 399_992):
            with self.subTest(fallback=text.startswith('invalid')):
                result = self.parse_with_deadline('from_colors_toml', 'colors.toml', text)
                self.assertIsNone(result['background'])

    def test_alacritty_blank_lines_finish_under_two_seconds(self):
        for text in ('\n' * 400_000, 'invalid\n' + '\n' * 399_992):
            with self.subTest(fallback=text.startswith('invalid')):
                result = self.parse_with_deadline('from_alacritty', 'alacritty.toml', text)
                self.assertIsNone(result['background'])

    def test_hyprland_blank_lines_finish_under_two_seconds(self):
        self.assertEqual(self.parse_with_deadline('from_hyprland', 'hyprland.conf', '\n' * 400_000), {})

    def test_kitty_blank_lines_finish_under_two_seconds(self):
        result = self.parse_with_deadline('from_kitty', 'kitty.conf', '\n' * 400_000)
        self.assertIsNone(result['background'])

    def test_ghostty_blank_lines_finish_under_two_seconds(self):
        result = self.parse_with_deadline('from_ghostty', 'ghostty.conf', '\n' * 400_000)
        self.assertIsNone(result['background'])

    def test_toml_fallback_blank_lines_finish_under_two_seconds(self):
        result = self.parse_with_deadline('flat_toml', 'colors.toml', 'invalid\n' + '\n' * 399_992)
        self.assertEqual(result, {})

    def test_git_timeout_parameter_and_clone_status(self):
        dest = self.root / 'clone'
        def run(argv, **kwargs):
            self.assertEqual(kwargs['timeout'], 900)
            if 'clone' in argv:
                (dest / '.git').mkdir(parents=True)
            return subprocess.CompletedProcess(argv, 0, '', '')
        with patch.object(build.subprocess, 'run', side_effect=run):
            self.assertTrue(build.clone_repo(self.root, 'theme-repo', dest, timeout=900))
            self.assertFalse(build.clone_repo(self.root, 'theme-repo', dest, update=True, timeout=900))

    def test_catalog_clones_use_long_timeout(self):
        seen = []
        def clone(cache, url, dest, **kwargs):
            seen.append((dest.name, kwargs))
            if dest.name == 'site':
                build.write_json(dest / 'src/data/themes.json', [])
            else:
                (dest / 'themes').mkdir(parents=True)
        with patch.object(build, 'clone_repo', side_effect=clone), contextlib.redirect_stdout(io.StringIO()):
            build.list_themes(self.root)
        self.assertEqual(seen, [('site', dict(update=True, timeout=900)),
                                ('omarchy', dict(update=True, timeout=900))])
        self.assertEqual(build.read_json(self.root / 'list.json'), dict(builtin=[], community=[]))

    def test_clone_timeout_identifies_repository_and_retry(self):
        dest = self.root / 'clone'
        def run(argv, **kwargs):
            dest.mkdir()
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        with patch.object(build.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'theme-repo.*timed out.*retry'):
                build.clone_repo(self.root, 'theme-repo', dest)
        self.assertFalse(dest.exists())

    def test_catalog_timeout_cli_gives_retry_command(self):
        stderr = io.StringIO()
        with patch.object(build.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 900)), \
             contextlib.redirect_stderr(stderr):
            status = build.main(['list', '--cache', str(self.root)])
        self.assertEqual(status, 1)
        self.assertIn('omacom/omarchy-site', stderr.getvalue())
        self.assertIn('timed out', stderr.getvalue())
        self.assertIn('retry', stderr.getvalue())
        self.assertIn('python3 tools/build.py list', stderr.getvalue())

    def test_fetch_timeout_identifies_repository_and_retry(self):
        cache = self.root / 'cache'
        build.write_json(cache / 'list.json', dict(builtin=[], community=[
            dict(slug=s, name=s, author='Example', repo='https://github.com/example/' + s) for s in ('slow', 'cached')]))
        (cache / 'themes/cached/.git').mkdir(parents=True)
        (cache / 'themes/cached/colors.toml').write_text(NAMED)
        def run(argv, **kwargs):
            if 'clone' in argv:
                Path(argv[-1]).mkdir()
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
            return self.git_fixture(argv, **kwargs)
        output = io.StringIO()
        with patch.object(build.subprocess, 'run', side_effect=run), contextlib.redirect_stdout(output):
            build.fetch_themes(cache, jobs=1)
        self.assertIn('example/slow timed out after 60 seconds', output.getvalue())
        self.assertIn('retry python3 tools/build.py fetch', output.getvalue())
        self.assertIn('cached ok cached', output.getvalue())
        self.assertFalse((cache / 'themes/slow').exists())

    def test_fetch_reports_cloned_then_cached(self):
        cache = self.root / 'cache'
        build.write_json(cache / 'list.json', dict(builtin=[], community=[
            dict(slug='fixture', name='Fixture', author='Example', repo='https://github.com/example/fixture')]))
        fresh, cached = io.StringIO(), io.StringIO()
        with patch.object(build.subprocess, 'run', side_effect=self.git_fixture):
            with contextlib.redirect_stdout(fresh):
                build.fetch_themes(cache)
            with contextlib.redirect_stdout(cached):
                build.fetch_themes(cache)
        self.assertEqual(fresh.getvalue(), 'fixture ok cloned\n')
        self.assertEqual(cached.getvalue(), 'fixture ok cached\n')

    def test_missing_git_fails_before_every_command(self):
        for command in ('list', 'fetch', 'palettes', 'images', 'data', 'check', 'all'):
            with self.subTest(command=command):
                cache = self.root / 'absent'
                stderr = io.StringIO()
                with patch.object(build.shutil, 'which', return_value=None), \
                     patch.object(build.subprocess, 'run', side_effect=FileNotFoundError('git')), \
                     contextlib.redirect_stderr(stderr):
                    status = build.main([command, '--cache', str(cache)])
                self.assertEqual(status, 1)
                self.assertEqual(stderr.getvalue(), 'build: git is required\n')
                self.assertFalse(cache.exists())

    def test_image_bomb_warning_is_a_per_image_failure(self):
        class BombWarning(UserWarning):
            pass
        fake_image = types.SimpleNamespace(DecompressionBombWarning=BombWarning)
        def open_image(src):
            if src.name == 'bomb.png':
                warnings.warn('too many pixels', BombWarning)
            return contextlib.nullcontext(types.SimpleNamespace(width=1, height=1, is_animated=False))
        fake_image.open = open_image
        fake_pil = types.ModuleType('PIL')
        fake_pil.Image, fake_pil.ImageOps = fake_image, types.SimpleNamespace()
        with patch.dict(sys.modules, {'PIL': fake_pil}):
            backend = build.ImageBackend()
        (self.theme / 'bomb.png').write_bytes(b'fixture')
        (self.theme / 'good.png').write_bytes(b'fixture')
        job = dict(slug='fixture', tdir='theme', previews=['bomb.png', 'good.png'], walls=[], bg='#000000')
        with warnings.catch_warnings(record=True) as caught, \
             patch.object(backend, 'thumbnail', return_value=False):
            warnings.simplefilter('always', BombWarning)
            result = build.image_job(job, self.root, self.root / 'app', backend)
        self.assertEqual(caught, [])
        self.assertEqual(result['errors'], ['bomb.png: BombWarning'])
        self.assertEqual([p['file'] for p in result['previews']], ['good.png'])
        self.assertFalse((self.root / 'app/img/fixture/orig/bomb.png').exists())
        self.assertEqual(fake_image.MAX_IMAGE_PIXELS, 100_000_000)
        src, dst = self.theme / 'bomb.png', self.root / 'display.webp'
        with self.assertRaises(BombWarning):
            backend.thumbnail(src, dst, '#000000')
        with patch.object(backend, 'dimensions', return_value=(3000, 3000, False)), \
             self.assertRaises(BombWarning):
            backend.display(src, dst, 'original.png')

    def test_pillow_rejects_over_one_hundred_million_pixels(self):
        backend = build.ImageBackend()
        if backend.Image is None:
            self.skipTest('optional Pillow is unavailable')
        # A tiny BMP header declares a large image without allocating its pixels.
        header = struct.pack('<2sIHHI', b'BM', 54, 0, 0, 54)
        header += struct.pack('<IiiHHIIiiII', 40, 10001, 10000, 1, 24, 0, 0, 0, 0, 0, 0)
        src = self.theme / 'bomb.bmp'
        src.write_bytes(header)
        with self.assertRaises(backend.Image.DecompressionBombWarning):
            backend.dimensions(src)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to exercise gallery HTML')
    def test_gallery_escapes_theme_values_in_html(self):
        script = r'''
const fs = require('fs'), vm = require('vm');
function node(tag) {
  return { tag, innerHTML: '', children: [], dataset: {}, style: { setProperty() {} },
           setAttribute() {},
           appendChild(child) { this.children.push(child); } };
}
const cards = node('cards');
const document = { createElement: node, createDocumentFragment: () => node('fragment'),
                   getElementById: () => cards };
const attack = '#112233" data-injected="yes"><img data-injected="yes">';
const palette = Object.fromEntries(['bg', 'fg', 'ac', 'cur', 'sel', 'selfg', 'mu', 'ab', 'ib',
                                   'bg2', 'bg0', 'fgb', 'fgd'].map(k => [k, attack]));
palette.n = palette.b = Array(8).fill(attack);
const theme = { slug: 'fixture', name: attack, author: attack, thumb: attack, mode: attack,
                videos: attack, p: palette, format: attack, repo: attack, srcs: [attack],
                lic: attack, commit: attack, guessed: [attack], derived: [attack], walls: [] };
const window = { GALLERY: { themes: [theme] } };
const source = fs.readFileSync(process.argv[1], 'utf8');
const prefix = source.slice(0, source.indexOf('  /* ---------- shared chart anatomy and interactions ---------- */'));
vm.runInNewContext(prefix + '\nwindow.test = {cardNode, paletteView};})();', {window, document});
cards.appendChild(window.test.cardNode(theme));
const paletteView = node('view');
window.test.paletteView(paletteView, theme);
const html = [];
function collect(n) { if (n.innerHTML) html.push(n.innerHTML); n.children.forEach(collect); }
collect(cards); collect(paletteView);
console.log(JSON.stringify(html));
'''
        result = subprocess.run(['node', '-e', script, str(ROOT / 'app/gallery.js')],
                                capture_output=True, text=True, check=True, timeout=5)
        class Markup(HTMLParser):
            def handle_starttag(self, tag, attrs):
                self_outer.assertNotIn('data-injected', dict(attrs))
        self_outer = self
        for html in json.loads(result.stdout):
            Markup().feed(html)


class DiscoveryMetricsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to exercise gallery filters')
    def test_gallery_generated_formats_and_incremental_typed_filters(self):
        script = r'''
const fs = require('fs'), vm = require('vm');
const document = {createElement: () => ({style: {}, dataset: {}})};
const window = {GALLERY: {themes: [], stats: {source: 'github-graphql'}}};
const source = fs.readFileSync(process.argv[1], 'utf8');
const prefix = source.slice(0, source.indexOf('  /* ---------- shared chart anatomy and interactions ---------- */'));
vm.runInNewContext(prefix + '\nwindow.test = {qualifies, liftWords, rankValue, S};})();', {window, document});
const {qualifies, liftWords, rankValue, S} = window.test;
const formats = ['4.x colors.toml', 'colors.toml (3.x color0-15)', 'colors.toml (other keys)', 'legacy per-app files'];
const matches = formats.map(format => ['4x', '3x', 'legacy'].map(fmt => qualifies({format}, {fmt})));
const sequences = ['stars:10', 'hue:blue,purple'].map(word => {
  S.filter = {}; let text = '', prematurelyConsumed = false;
  for (const character of word) {text += character; const next = liftWords(text, false); if(next !== text) prematurelyConsumed = true; text = next;}
  const before = JSON.parse(JSON.stringify(S.filter));
  text = liftWords(text + ' ', false);
  return {prematurelyConsumed, before, after: S.filter, text: text.trim()};
});
S.filter = {}; const explicit = liftWords('stars:10 unknown:value', true);
console.log(JSON.stringify({matches, sequences, explicit, explicitFilters:S.filter, hue:rankValue({hue:359,m:{ah:28}},"hue")}));
'''
        result = subprocess.run(['node', '-e', script, str(ROOT / 'app/gallery.js')],
                                capture_output=True, text=True, check=True, timeout=5)
        value = json.loads(result.stdout)
        self.assertEqual(value['matches'], [[True, False, False], [False, True, False],
                                           [False, True, False], [False, False, True]])
        for row, expected in zip(value['sequences'], [{'stars': '10'}, {'hue': 'blue,purple'}]):
            self.assertFalse(row['prematurelyConsumed'])
            self.assertEqual(row['before'], {})
            self.assertEqual(row['after'], expected)
            self.assertEqual(row['text'], '')
        self.assertEqual(value['explicit'], 'unknown:value')
        self.assertEqual(value['explicitFilters'], {'stars': '10'})
        self.assertEqual(value['hue'], 359)  # Keep the pre-existing HSV ordering.

    def setUp(self):
        (ROOT / '.work').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.work')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        network = patch.object(build, 'urlopen', side_effect=AssertionError('tests must stay offline'))
        self.network = network.start()
        self.addCleanup(network.stop)

    def palette(self, accent='#ff0000'):
        return dict(background='#000000', foreground='#ffffff', accent=accent,
                    cursor='#ffffff', selection='#ffffff', selection_foreground='#000000',
                    normal=['#ffffff'] * 8, bright=['#ffffff'] * 8,
                    guessed=[], derived=[])

    def themes(self):
        core = ['alacritty.toml', 'kitty.conf', 'ghostty.conf', 'hyprland.conf',
                'hyprlock.conf', 'waybar.css', 'mako.ini', 'btop.theme', 'neovim.lua']
        extra = ['cava.conf', 'swayosd.css', 'vscode.json', 'rofi.rasi', 'fuzzel.ini', 'foot.ini']
        result = []
        for slug, accent, tier in [('builtin', '#ff0000', 'builtin'),
                                   ('official', '#00ff00', 'official'),
                                   ('found', '#0000ff', 'found')]:
            result.append(dict(slug=slug, name=slug.title(), author='Fixture',
                               repo='https://github.com/example/' + slug,
                               builtin=tier == 'builtin', tier=tier,
                               lists=['official', 'manual', 'awesome-a', 'awesome-w', 'hub-l']
                                     if tier == 'official' else [], official_pos=1 if tier == 'official' else None,
                               last_commit='2026-10-12', format='4.x colors.toml',
                               palette=self.palette(accent), app_files=core + extra,
                               apps=[Path(p).stem for p in core + extra], previews=['preview.png'],
                               wallpapers_count=6, wallpaper_megapixels=8.3,
                               licence_file='LICENSE', licence_detected='MIT'))
        result[1]['palette']['guessed'] = ['normal0', 'bright0']
        result[1]['palette']['derived'] = ['cursor', 'selection']
        result[1]['app_files'] = core + extra[:3]
        result[2].update(format='legacy per-app files', app_files=core[:3] + extra[:1],
                         previews=[], wallpapers_count=0, licence_file=None, licence_detected=None)
        result[2]['palette']['guessed'] = ['normal%d' % i for i in range(8)]
        result[2]['palette']['derived'] = ['accent', 'cursor', 'selection']
        return result

    def statistics(self):
        return dict(fetched='2026-10-12', source='github-graphql', repos={
            'example/official': dict(stars=150, forks=20, created='2026-10-12', pushed='2026-10-12',
                                     issues=3, licence='MIT', archived=False),
            'example/found': dict(stars=0, forks=0, created='2026-10-12', pushed='2026-10-12',
                                  issues=0, licence=None, archived=True)})

    def test_oklab_reference_colours(self):
        for colour, expected in [('#ffffff', (1, 0, 0)), ('#ff0000', (.628, .225, .126)),
                                 ('#000000', (0, 0, 0))]:
            with self.subTest(colour=colour):
                for actual, wanted in zip(build.oklab(colour), expected):
                    self.assertAlmostEqual(actual, wanted, places=3)

    def test_acceptance_path_fixtures(self):
        legacy = {'alacritty.toml': LEGACY, 'hyprland.conf': '', 'backgrounds/wall.png': ''}
        fixtures = [
            ('modern', {'colors.toml': NAMED}, True),
            ('legacy', legacy, True),
            ('few-colours', {'colors.toml': 'background="#000000"\nforeground="#ffffff"\n'}, False),
            ('invalid-modern', dict(legacy, **{'colors.toml': 'this is invalid TOML'}), False),
            ('distro', dict(legacy, **{'colors.toml': NAMED, 'install.sh': '', 'bin/tool': '', 'themes/x': ''}), False),
            ('one-app', {'alacritty.toml': LEGACY, 'backgrounds/wall.png': ''}, False),
            ('no-wallpaper', {'alacritty.toml': LEGACY, 'hyprland.conf': ''}, False),
            ('empty', {}, False),
            ('collection-only', {'themes/example/colors.toml': NAMED}, False),
        ]
        for name, files, accepted in fixtures:
            with self.subTest(name=name):
                actual, reason = build.is_theme(list(files), files.get)
                self.assertEqual(actual, accepted)
                self.assertIsInstance(reason, str)
                if not accepted:
                    self.assertTrue(reason)
        files = {'colors.toml': NAMED}
        self.assertFalse(build.is_theme([dict(path='colors.toml', type='blob', mode='120000')], files.get)[0])
        self.assertTrue(build.is_theme([dict(path='colors.toml', type='blob', mode=33188)], files.get)[0])
        paths = [dict(path=name, type='blob', mode='100644') for name in legacy]
        paths[-1]['mode'] = '120000'
        self.assertFalse(build.is_theme(paths, legacy.get)[0], 'a symlink cannot satisfy the wallpaper requirement')

    def test_acceptance_threshold_counts_real_colours_only(self):
        base = 'background="#000000"\nforeground="#ffffff"\n'
        for count, accepted in [(3, False), (4, True)]:
            text = base + ''.join('color%d="#ff0000"\n' % i for i in range(count))
            self.assertEqual(build.is_theme(['colors.toml'], lambda _: text)[0], accepted)
        for parser_file, contents in [
            ('kitty.conf', 'background #000000\n' + ''.join('color%d #ffffff\n' % i for i in range(6))),
            ('ghostty.conf', 'background = #000000\n' + ''.join('palette = %d=#ffffff\n' % i for i in range(6))),
        ]:
            files = {parser_file: contents, 'hyprland.conf': '', 'backgrounds/a.jpg': ''}
            self.assertTrue(build.is_theme(list(files), files.get)[0])

    def test_palette_vector_weights_and_slot_order(self):
        pal = self.palette()
        vector = build.palette_vector(pal)
        self.assertEqual(len(vector), 57)
        self.assertEqual(tuple(vector[:3]), (0, 0, 0))
        self.assertAlmostEqual(vector[3], 100 * math.sqrt(2), places=5)
        self.assertAlmostEqual(vector[6], 62.795536 * math.sqrt(2), places=5)
        self.assertAlmostEqual(vector[7], 22.4863061 * math.sqrt(2), places=5)
        self.assertAlmostEqual(vector[9], 100, places=5)
        pal['background'] = '#ffffff'
        self.assertAlmostEqual(build.palette_vector(pal)[0], 200, places=5)
        pal['normal'][1] = '#000000'
        self.assertEqual(tuple(build.palette_vector(pal)[12:15]), (0, 0, 0))

    def test_ranking_formulas_and_builtin_missing_stats(self):
        themes = self.themes()
        build.metrics(themes, self.statistics())
        builtin, official, found = themes
        self.assertEqual(builtin['r']['pop'], 100)
        self.assertIsNone(builtin['r']['trend'])
        self.assertAlmostEqual(official['r']['pop'], 100, places=1)
        self.assertAlmostEqual(official['r']['trend'], 151 / 3, delta=.06)
        self.assertAlmostEqual(found['r']['pop'], 20, places=1)
        self.assertAlmostEqual(found['r']['trend'], 1 / 3, delta=.06)
        self.assertIsNone(builtin['stats'])
        self.assertEqual(official['stats']['stars'], 150)
        self.assertEqual(official['stats']['created'], '2026-10-12')
        self.assertEqual(official['stats']['pushed'], '2026-10-12')
        self.assertEqual(builtin['last_commit'], '2026-10-12')
        for theme, comp, colour, unique in zip(themes, [100, 87.7272727, 31.6666667],
                                              [1.9821793, 2.2679018, 2.4093413],
                                              [73.5, 73.5, 76.0]):
            with self.subTest(theme=theme['slug']):
                self.assertAlmostEqual(theme['m']['comp'], comp, delta=.06)
                self.assertEqual(theme['m']['leg'], 21)
                self.assertAlmostEqual(theme['m']['col'], colour, delta=.06)
                self.assertAlmostEqual(theme['m']['uniq'], unique, places=1)
                self.assertEqual(theme['m']['L'], 0)
        self.assertEqual([t['m']['fam'] for t in themes], ['red', 'green', 'blue'])

    def test_rankings_without_stats_use_list_and_commit_recency(self):
        themes = self.themes()
        build.metrics(themes, dict(fetched='2026-10-12', source='none', repos={}))
        self.assertEqual([t['r']['pop'] for t in themes], [100, 100, 40])
        self.assertTrue(all(t['r']['trend'] is None for t in themes))
        themes[2]['last_commit'] = '2025-10-12'
        build.metrics(themes, dict(fetched='2026-10-12', source='none', repos={}))
        self.assertAlmostEqual(themes[2]['r']['pop'], 40 * .5 ** (365 / 30.44 / 6), delta=.06)

    def test_popularity_weights_and_trend_use_stats_snapshot_date(self):
        themes = self.themes()
        themes[1]['lists'] = ['official', 'manual']
        stats = self.statistics()
        stats['repos']['example/official'].update(stars=15, forks=2, created='2026-01-12', pushed='2026-04-12')
        build.metrics(themes, stats)
        self.assertAlmostEqual(themes[1]['r']['pop'], 51.2252323, delta=.06)
        self.assertAlmostEqual(themes[1]['r']['trend'], 1.3368467, delta=.06)
        stats['repos']['example/official'].update(stars=999, forks=999, pushed='2026-10-12')
        themes[1]['lists'] = ['official', 'manual', 'awesome-a', 'awesome-w', 'hub-l']
        build.metrics(themes, stats)
        self.assertEqual(themes[1]['r']['pop'], 100, 'stars and forks are capped before combining signals')

    def test_pca_and_quilt_are_deterministic_under_shuffle(self):
        original = self.themes()
        duplicate = copy.deepcopy(original[0])
        duplicate.update(slug='a-duplicate', name='A duplicate')
        original.append(duplicate)
        results = []
        for fixtures in [original, copy.deepcopy(original), list(reversed(copy.deepcopy(original)))]:
            viz = build.metrics(fixtures, self.statistics())
            results.append((viz, {t['slug']: t['m'] for t in fixtures}))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        viz, records = results[0]
        self.assertEqual(viz['grid'], math.ceil(math.sqrt(1.5 * len(original))))
        self.assertEqual(len({(m['cx'], m['cy']) for m in records.values()}), len(records))
        self.assertAlmostEqual(sum(viz['pca']), 1, delta=.01)
        for axis in ('x', 'y'):
            self.assertEqual(min(m[axis] for m in records.values()), 0)
            self.assertEqual(max(m[axis] for m in records.values()), 1)
        for m in records.values():
            self.assertTrue(0 <= m['cx'] < viz['grid'])
            self.assertTrue(0 <= m['cy'] < viz['grid'])
        first, collision = records['a-duplicate'], records['builtin']
        wanted = (round(first['x'] * (viz['grid'] - 1)), round((1 - first['y']) * (viz['grid'] - 1)))
        self.assertEqual((first['cx'], first['cy']), wanted)
        ring = sorted((x, y) for y in range(max(0, wanted[1] - 1), min(viz['grid'], wanted[1] + 2))
                      for x in range(max(0, wanted[0] - 1), min(viz['grid'], wanted[0] + 2))
                      if max(abs(x - wanted[0]), abs(y - wanted[1])) == 1)
        ring.sort(key=lambda cell: (cell[1], cell[0]))
        self.assertEqual((collision['cx'], collision['cy']), ring[0])

    def test_pca_small_and_identical_palettes_remain_finite(self):
        for count in (0, 1, 2, 3):
            themes = [dict(self.themes()[0], slug='fixture-%d' % i) for i in range(count)]
            viz = build.metrics(themes, self.statistics())
            for theme in themes:
                self.assertEqual((theme['m']['x'], theme['m']['y']), (.5, .5))
            self.assertTrue(all(math.isfinite(value) for value in viz['pca']))

    def test_palette_dedupe_priority_and_receipts(self):
        def record(slug, tier, stars=0, created='2026-10-12'):
            return dict(slug=slug, name=slug, repo='https://github.com/example/' + slug,
                        builtin=tier == 'builtin', tier=tier, lists=[tier] if tier == 'official' else [],
                        palette=self.palette(), stats=dict(stars=stars, created=created))
        pairs = [
            (record('builtin', 'builtin'), record('official', 'official', 999)),
            (record('official', 'official'), record('curated', 'curated', 999)),
            (record('curated', 'curated'), record('found', 'found', 999)),
            (record('high-stars', 'found', 10), record('older', 'found', 2, '2020-01-01')),
            (record('older', 'found', 10, '2020-01-01'), record('newer', 'found', 10)),
        ]
        for winner, loser in pairs:
            with self.subTest(winner=winner['slug'], loser=loser['slug']):
                kept, duplicates = build.dedupe_themes([loser, winner])
                self.assertEqual([t['slug'] for t in kept], [winner['slug']])
                self.assertEqual([(t['slug'], t['duplicate_of']) for t in duplicates],
                                 [(loser['slug'], winner['slug'])])
        changed = record('changed-accent', 'found')
        changed['palette']['accent'] = '#00ff00'
        kept, _ = build.dedupe_themes([record('builtin', 'builtin'), changed])
        self.assertEqual(len(kept), 1, 'accent is deliberately excluded from the duplicate key')
        changed['palette']['bright'][7] = '#112233'
        self.assertEqual(len(build.dedupe_themes([record('builtin', 'builtin'), changed])[0]), 2)

    def test_stats_no_token_and_no_api_write_none_without_network(self):
        for no_api in (False, True):
            with self.subTest(no_api=no_api), patch.object(build, 'resolve_token', return_value=None), \
                 patch.object(build, 'github_graphql') as api, contextlib.redirect_stdout(io.StringIO()):
                build.stats(self.root, no_api=no_api)
                self.assertEqual(build.read_json(self.root / 'stats.json')['source'], 'none')
                self.assertEqual(build.read_json(self.root / 'stats.json')['repos'], {})
                api.assert_not_called()

    def test_stats_fresh_cache_and_explicit_no_api_override(self):
        cached = self.statistics()
        cached['fetched'] = date.today().isoformat()
        build.write_json(self.root / 'stats.json', cached)
        with patch.object(build, 'resolve_token', return_value='marker-token'), \
             patch.object(build, 'github_graphql') as api, contextlib.redirect_stdout(io.StringIO()):
            build.stats(self.root)
            self.assertEqual(build.read_json(self.root / 'stats.json'), cached)
            api.assert_not_called()
            build.stats(self.root, no_api=True)
            self.assertEqual(build.read_json(self.root / 'stats.json')['source'], 'none')
            api.assert_not_called()

    def test_stats_failure_redacts_token_in_output_and_files(self):
        marker = 'ghp_' + 'secretMarker' * 4
        build.write_json(self.root / 'list.json', dict(builtin=[], community=[
            dict(slug='theme', name='Theme', author='Example', repo='https://github.com/example/theme')]))
        output = io.StringIO()
        with patch.object(build, 'resolve_token', return_value=marker), \
             patch.object(build, 'github_graphql', side_effect=RuntimeError('request failed with ' + marker)), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            build.stats(self.root, refresh=True)
        self.assertEqual(build.read_json(self.root / 'stats.json')['source'], 'none')
        self.assertEqual(build.read_json(self.root / 'stats.json')['repos'], {})
        self.assertNotIn(marker, output.getvalue())
        self.assertIn('RuntimeError', output.getvalue())
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(marker.encode(), path.read_bytes(), str(path))

    def graphql_node(self, name='example/theme'):
        return dict(nameWithOwner=name, isFork=False, isEmpty=False, stargazerCount=12,
                    forkCount=2, createdAt='2026-01-02T01:02:03Z', pushedAt='2026-10-10T10:20:30Z',
                    issues=dict(totalCount=4), licenseInfo=dict(spdxId='MIT'), isArchived=False)

    def test_stats_refresh_expiry_null_alias_and_non_github(self):
        rows = [dict(repo='https://github.com/example/theme'),
                dict(repo='https://github.com/example/private'),
                dict(repo='https://codeberg.org/example/codeberg'),
                dict(repo='https://gitlab.com/example/gitlab')]
        build.write_json(self.root / 'candidates.json', rows)
        expected = dict(stars=12, forks=2, created='2026-01-02', pushed='2026-10-10',
                        issues=4, licence='MIT', archived=False)
        for refresh, age in [(True, 0), (False, 7), (False, 8)]:
            with self.subTest(refresh=refresh, age=age):
                cached = self.statistics()
                cached['fetched'] = (date.today() - timedelta(days=age)).isoformat()
                build.write_json(self.root / 'stats.json', cached)
                output = io.StringIO()
                marker = 'ghp_' + 'redactionMarker' * 3
                with patch.object(build, 'resolve_token', return_value=marker), \
                     patch.object(build, 'github_graphql', return_value={'r0': None, 'r1': self.graphql_node()}) as api, \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    build.stats(self.root, refresh=refresh)
                api.assert_called_once()
                query, token = api.call_args.args
                self.assertEqual(token, marker)
                self.assertNotIn('codeberg', query)
                self.assertNotIn('gitlab', query)
                self.assertNotIn(marker, query)
                saved = build.read_json(self.root / 'stats.json')
                self.assertEqual(saved, dict(fetched=date.today().isoformat(), source='github-graphql',
                                              repos={'example/theme': expected}))
                self.assertNotIn(marker, output.getvalue())
                for path in self.root.rglob('*'):
                    if path.is_file():
                        self.assertNotIn(marker.encode(), path.read_bytes(), str(path))

    def test_stats_batches_100_and_discards_partial_results_on_failure(self):
        rows = [dict(repo='https://github.com/example/theme-%03d' % i) for i in range(101)]
        build.write_json(self.root / 'candidates.json', rows)
        def response(query, token):
            if 'theme-100' in query:
                return {'r0': self.graphql_node('example/theme-100')}
            self.assertIn('r99:repository', query)
            self.assertNotIn('r100:repository', query)
            return {'r%d' % i: self.graphql_node('example/theme-%03d' % i) for i in range(100)}
        with patch.object(build, 'resolve_token', return_value='marker-token'), \
             patch.object(build, 'github_graphql', side_effect=response) as api, \
             contextlib.redirect_stdout(io.StringIO()):
            build.stats(self.root)
            self.assertEqual(api.call_count, 2)
        self.assertEqual(len(build.read_json(self.root / 'stats.json')['repos']), 101)
        with patch.object(build, 'resolve_token', return_value='marker-token'), \
             patch.object(build, 'github_graphql', side_effect=[{'r0': self.graphql_node()}, RuntimeError('offline')]), \
             contextlib.redirect_stdout(io.StringIO()):
            build.stats(self.root, refresh=True)
        self.assertEqual(build.read_json(self.root / 'stats.json')['source'], 'none')
        self.assertEqual(build.read_json(self.root / 'stats.json')['repos'], {})

    def test_token_resolver_precedence_and_gh_timeout(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'first', 'GH_TOKEN': 'second'}, clear=True), \
             patch.object(build.subprocess, 'run') as command:
            self.assertEqual(build.resolve_token(), 'first')
            command.assert_not_called()
        with patch.dict(os.environ, {'GH_TOKEN': 'second'}, clear=True), \
             patch.object(build.subprocess, 'run') as command:
            self.assertEqual(build.resolve_token(), 'second')
            command.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch.object(build.shutil, 'which', return_value='/bin/gh'), \
             patch.object(build.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'third\n', 'ignored')) as command:
            self.assertEqual(build.resolve_token(), 'third')
            self.assertEqual(command.call_args.args[0], ['gh', 'auth', 'token'])
            self.assertEqual(command.call_args.kwargs['timeout'], 10)
        with patch.dict(os.environ, {}, clear=True), patch.object(build.shutil, 'which', return_value='/bin/gh'), \
             patch.object(build.subprocess, 'run', side_effect=subprocess.TimeoutExpired('gh', 10)):
            self.assertIsNone(build.resolve_token())

    def test_graphql_token_only_in_authorization_header(self):
        marker = 'ghp_' + 'headerMarker' * 3
        payload = io.BytesIO(json.dumps({'data': {'result': 1}}).encode())
        with patch.object(build, 'urlopen', return_value=contextlib.closing(payload)) as network:
            self.assertEqual(build.github_graphql('query { viewer { login } }', marker), {'result': 1})
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.github.com/graphql')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + marker)
        self.assertNotIn(marker, request.full_url)
        self.assertNotIn(marker.encode(), request.data)

    def test_canonical_repository_merge_retains_curation_and_position(self):
        rows = [
            dict(repo='https://github.com/OWNER/renamed.git', host='github.com', lists=['search'],
                 official_pos=None, tier='found', accepted=True, reason='modern'),
            dict(repo='https://github.com/owner/Renamed', host='github.com', lists=['manual'],
                 official_pos=None, tier='curated', accepted=True, reason='modern'),
            dict(repo='https://github.com/Owner/renamed/', host='github.com', lists=['official'],
                 official_pos=4, tier='official', name='Official name', accepted=True, reason='modern'),
            dict(repo='https://codeberg.org/owner/renamed', host='codeberg.org', lists=['manual'],
                 official_pos=None, tier='curated', accepted=None, reason='not probed')]
        merged = build.merge_candidates(rows)
        self.assertEqual(len(merged), 2)
        github = next(row for row in merged if row['host'] == 'github.com')
        self.assertEqual(set(github['lists']), {'search', 'manual', 'official'})
        self.assertEqual(github['tier'], 'official')
        self.assertEqual(github['official_pos'], 4)
        self.assertEqual(github['name'], 'Official name')

    def test_cached_discovery_refreshes_curated_memberships_without_api_requests(self):
        build.write_json(self.root / 'list.json', dict(builtin=[], community=[
            dict(repo='https://github.com/example/old-name', name='Theme')]))
        build.write_json(self.root / 'stats.json', dict(
            source='github-graphql', fetched=build.date.today().isoformat(), repos={}))
        build.write_json(self.root / 'candidates.json', [
            dict(repo='https://github.com/example/theme', host='github.com',
                 lists=['official', 'manual', 'search'], accepted=True, reason='colors.toml',
                 queried_as=['https://github.com/example/old-name']),
            dict(repo='https://github.com/example/removed', host='github.com',
                 lists=['manual'], accepted=True, reason='colors.toml')])
        def clone(cache, url, dest, **kwargs):
            dest.mkdir(parents=True)
            if dest.name == 'hub-l':
                build.write_json(dest / 'src/data/themes-data.json', [])
            else:
                (dest / 'README.md').write_text('## Themes\n## Contributing\n')
        with patch.object(build, 'clone_repo', side_effect=clone), \
                patch.object(build, 'urlopen', return_value=contextlib.closing(io.BytesIO(b''))), \
                patch.object(build, 'github_graphql', side_effect=AssertionError('fresh cache needs no API')), \
                patch.object(build, 'resolve_token', side_effect=AssertionError('fresh cache needs no token')), \
                contextlib.redirect_stdout(io.StringIO()):
            build.discover(self.root)
        rows = build.read_json(self.root / 'candidates.json')
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]['lists']), {'official', 'search'})
        self.assertTrue(rows[0]['accepted'])
        self.assertEqual(rows[0]['repo'], 'https://github.com/example/theme')

    def test_manual_request_has_identified_user_agent_and_no_credentials(self):
        build.write_json(self.root / 'list.json', dict(builtin=[], community=[]))
        payload = io.BytesIO(b'[Example](https://github.com/example/omarchy-sample-theme)')
        with patch.object(build, 'clone_repo', side_effect=ValueError('list unavailable')), \
                patch.object(build, 'urlopen', return_value=contextlib.closing(payload)) as network, \
                patch.object(build, 'resolve_token', side_effect=AssertionError('API disabled')), \
                contextlib.redirect_stdout(io.StringIO()):
            build.discover(self.root, no_search=True, no_api=True)
        request = network.call_args.args[0]
        self.assertIsInstance(request, build.Request)
        self.assertEqual(request.full_url, build.MANUAL_URL)
        self.assertIn('OmarchyThemeGallery', request.get_header('User-agent'))
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(network.call_args.kwargs['timeout'], 10)
        rows = build.read_json(self.root / 'candidates.json')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['lists'], ['manual'])
        self.assertEqual(rows[0]['tier'], 'curated')

    def test_discover_merges_api_renames_and_rejects_forks(self):
        build.write_json(self.root / 'list.json', dict(builtin=[], community=[
            dict(repo='https://github.com/example/old-name', name='Official title', official_pos=7),
            dict(repo='https://github.com/example/fork', name='Fork', official_pos=8)]))
        def clone(cache, url, dest, **kwargs):
            dest.mkdir(parents=True)
            if dest.name == 'hub-l':
                build.write_json(dest / 'src/data/themes-data.json', [])
            else:
                text = ('## Themes\nhttps://github.com/example/new-name\n## Contributing\n'
                        'https://github.com/example/unrelated\n') if dest.name == 'awesome-a' else ''
                (dest / 'README.md').write_text(text)
            return True
        canonical = self.graphql_node('Example/canonical-name')
        canonical.update(root=dict(entries=[dict(name='colors.toml', type='blob', mode='100644')]),
                         backgrounds=None, p0=dict(text=NAMED))
        fork = self.graphql_node('example/fork')
        fork['isFork'] = True
        with patch.object(build, 'clone_repo', side_effect=clone), \
             patch.object(build, 'resolve_token', return_value='marker-token'), \
             patch.object(build, 'github_graphql', return_value={'r0': canonical, 'r1': fork, 'r2': canonical}), \
             contextlib.redirect_stdout(io.StringIO()):
            build.discover(self.root, no_search=True)
        rows = build.read_json(self.root / 'candidates.json')
        self.assertEqual(len(rows), 2)
        renamed = next(row for row in rows if row['repo'].lower().endswith('/canonical-name'))
        self.assertEqual(set(renamed['lists']), {'official', 'awesome-a'})
        self.assertEqual(renamed['tier'], 'official')
        self.assertEqual(renamed['official_pos'], 7)
        self.assertTrue(renamed['accepted'])
        self.assertEqual(renamed['name'], 'Official title')
        rejected = next(row for row in rows if row['repo'].endswith('/fork'))
        self.assertFalse(rejected['accepted'])
        self.assertEqual(rejected['reason'], 'fork')

    def test_fetch_higher_tiers_fall_back_without_token(self):
        build.write_json(self.root / 'list.json', dict(builtin=['fixture'], community=[]))
        (self.root / 'src/omarchy/themes/fixture').mkdir(parents=True)
        for tier in ('official', 'curated', 'starred', 'all'):
            output = io.StringIO()
            with self.subTest(tier=tier), patch.object(build, 'resolve_token', return_value=None), \
                 contextlib.redirect_stdout(output):
                build.fetch_themes(self.root, tier=tier)
                self.assertEqual('falling back to curated' in output.getvalue(), tier in ('starred', 'all'))
                self.assertEqual(build.read_json(self.root / 'fetch.json')[0]['status'], 'ok')

    def test_catalog_tier_order_and_star_threshold(self):
        catalog = dict(builtin=['builtin'], community=[])
        def row(slug, tier, lists, stars=0, pos=None):
            return dict(repo='https://github.com/example/' + slug, name=slug, tier=tier, lists=lists,
                        stats=dict(stars=stars), official_pos=pos, accepted=True)
        candidates = [row('found-low', 'found', ['search'], 1), row('found-high', 'found', ['search'], 10),
                      row('curated-one', 'curated', ['manual']), row('curated-two', 'curated', ['manual', 'hub-l']),
                      row('official-late', 'official', ['official'], pos=4),
                      row('official-first', 'official', ['official'], pos=1)]
        expected = ['builtin', 'official-first', 'official-late', 'curated-two', 'curated-one']
        for tier, slugs in [('official', expected[:3]), ('curated', expected),
                            ('starred', expected + ['found-high']), ('all', expected + ['found-high', 'found-low'])]:
            with self.subTest(tier=tier):
                entries = build.catalog_entries(catalog, candidates=candidates, tier=tier, min_stars=2)
                self.assertEqual([entry['slug'] for entry in entries], slugs)
                self.assertEqual([entry['slug'] for entry in build.select_entries(entries, limit=2)], slugs[:2])

    def test_lean_checkout_caps_wallpapers_and_escapes_sparse_patterns(self):
        paths = ['colors.toml', 'preview.png', 'LICENSE', 'backgrounds/z.jpg', 'backgrounds/a*.png',
                 'backgrounds/b[1].png', 'backgrounds/c!.png', 'backgrounds/new\nline.png',
                 'backgrounds/video.mp4', 'secret.txt']
        with patch.object(build, 'run_git') as git:
            build.checkout_theme(self.root, self.root / 'theme', paths, lean=True, wallpapers=2)
        self.assertEqual(git.call_count, 2)
        self.assertEqual(git.call_args_list[0].args[1][2:], ['sparse-checkout', 'set', '--no-cone', '--stdin'])
        patterns = git.call_args_list[0].kwargs['input'].splitlines()
        self.assertEqual(set(patterns), {'/colors.toml', '/preview.png', '/LICENSE',
                                        '/backgrounds/a\\*.png', '/backgrounds/b\\[1\\].png'})
        with patch.object(build, 'run_git') as git:
            build.checkout_theme(self.root, self.root / 'theme', paths, lean=True, wallpapers=0)
        self.assertNotIn('new\nline', git.call_args_list[0].kwargs['input'])
        self.assertIn('/backgrounds/z.jpg', git.call_args_list[0].kwargs['input'])
        self.assertIsNone(build.sparse_pattern('bad\npath'))
        self.assertEqual(build.sparse_pattern('a\\*?[]!#.png'), '/a\\\\\\*\\?\\[\\]\\!\\#.png')
        with patch.object(build, 'run_git', return_value=subprocess.CompletedProcess([], 0, 'a\0b\0', '')) as git:
            self.assertEqual(build.repository_paths(self.root, self.root / 'theme'), ['a', 'b'])
        self.assertIn('--name-only', git.call_args.args[1])
        self.assertNotIn('-l', git.call_args.args[1])

    def test_check_rejects_local_paths_and_credentials(self):
        app = self.root / 'app'
        app.mkdir()
        for name in ('index.html', 'gallery.js', 'gallery.css'):
            (app / name).write_text('')
        theme = dict(slug='fixture', thumb=None, previews=[], walls=[])
        for value in ('/home/example/cache', '/mnt/example/cache', '/Users/example/cache',
                      '/tmp/cache', 'ghp_' + 'x' * 24, 'github_pat_marker'):
            with self.subTest(value=value):
                build.write_data(app / 'data.js', dict(themes=[theme], note=value))
                self.assertTrue(build.check_app(app))
        build.write_data(app / 'data.js', dict(themes=[theme], note='img/theme.png'))
        self.assertEqual(build.check_app(app), [])


if __name__ == '__main__':
    unittest.main()
