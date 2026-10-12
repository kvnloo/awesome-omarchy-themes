import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import struct
import zlib
import unittest
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
            (tdir / 'payload').write_bytes(b'x' * size)
        catalog = dict(builtin=[], community=[dict(slug=s, name=s, author='Example',
                       repo='https://github.com/example/' + s) for s in ['small', 'large']])
        build.write_json(cache / 'list.json', catalog)
        with contextlib.redirect_stdout(io.StringIO()):
            build.fetch_themes(cache, max_mb=5 / 1024**2, jobs=2)
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
        for slug in catalog['builtin']:
            tdir = cache / 'src/omarchy/themes' / slug
            tdir.mkdir(parents=True)
            (tdir / 'colors.toml').write_text(NAMED)
            (tdir / 'preview.png').write_bytes(png)
            (tdir / 'backgrounds').mkdir()
            (tdir / 'backgrounds/wall.png').write_bytes(png)
        backend = build.ImageBackend()
        for attr in ('Image', 'ImageOps', 'magick', 'convert', 'identify', 'ffmpeg', 'ffprobe'):
            setattr(backend, attr, None)
        with contextlib.redirect_stdout(io.StringIO()), patch.object(build, 'ImageBackend', return_value=backend):
            build.fetch_themes(cache)  # Simulate an older, wider selection first.
            build.fetch_themes(cache, only='tokyo-night,catppuccin,gruvbox')
            build.palettes(cache)
            build.images(cache, app)
            build.data(cache, app)
        for name in ('index.html', 'gallery.css', 'gallery.js'):
            (app / name).write_text('')
        gallery = build.read_data(app / 'data.js')
        self.assertEqual({t['slug'] for t in gallery['themes']}, {'tokyo-night', 'catppuccin', 'gruvbox'})
        self.assertEqual(build.check_app(app), [])
        for theme in gallery['themes']:
            self.assertEqual(theme['p']['bg'], '#101020')
            self.assertTrue(theme['thumb'].startswith('img/'))
            for im in theme['previews'] + theme['walls']:
                self.assertTrue(im['src'].startswith('img/'))
                self.assertTrue(im['orig'].startswith('img/'))
        self.assertNotIn(str(cache), (app / 'data.js').read_text())
        self.assertNotIn(str(cache), (cache / 'jobs.json').read_text())

    def test_all_cli_stage_order_and_options(self):
        calls = []
        with patch.object(build, 'list_themes', side_effect=lambda c: calls.append('list')), \
             patch.object(build, 'fetch_themes', side_effect=lambda *a: calls.append(('fetch', a[1:]))), \
             patch.object(build, 'palettes', side_effect=lambda c: calls.append('palettes')), \
             patch.object(build, 'images', side_effect=lambda c: calls.append('images')), \
             patch.object(build, 'data', side_effect=lambda c: calls.append('data')), \
             patch.object(build, 'check', side_effect=lambda: calls.append('check')):
            status = build.main(['all', '--cache', str(self.root / 'custom'), '--only', 'gruvbox',
                                 '--limit', '1', '--max-mb', '2', '--jobs', '3'])
        self.assertEqual(status, 0)
        self.assertEqual(calls, ['list', ('fetch', ('gruvbox', 1, 2.0, 3)), 'palettes', 'images', 'data', 'check'])

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


if __name__ == '__main__':
    unittest.main()
