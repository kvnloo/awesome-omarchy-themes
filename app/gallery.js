/* Theme preview gallery. Plain JS, no network, opens from file://. */
(function () {
  'use strict';
  var D = window.GALLERY || { themes: [] };
  var T = D.themes, N = T.length;
  var $ = function (id) { return document.getElementById(id); };
  var KINDS = ['preview', 'wall', 'mock', 'lock', 'palette'];
  var KIND_LABEL = { preview: 'Screenshot', wall: 'Wallpaper', mock: 'Desktop', lock: 'Lock', palette: 'Palette' };
  var FAV_KEY = 'omarchy-gallery:favs', LIST_KEY = 'omarchy-gallery:starred-list', UI_KEY = 'omarchy-gallery:ui';

  var store = {
    get: function (k, d) { try { var v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
    raw: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };

  var bySlug = {};
  T.forEach(function (t, i) { t.i = i; bySlug[t.slug] = t; });
  var favs = {};
  (store.get(FAV_KEY, []) || []).forEach(function (s) { if (bySlug[s]) favs[s] = 1; });
  var ui = store.get(UI_KEY, {}) || {};

  var S = {
    i: 0,            // current theme index into T
    prev: -1,        // previously viewed theme (for compare)
    cmpB: -1,        // pinned compare theme
    kind: 'preview',
    mem: {},         // slug -> {preview:n, wall:n}
    dual: false,
    compare: 0,      // 0 off, 1 split, 2 wipe
    wipe: 0.5,
    overlays: true,
    hud: ui.hud !== false,
    order: T.map(function (t) { return t.i; }),
    grid: false, gsel: 0,
    filter: { q: '', mode: 'all', fav: false, sort: 'name' }
  };

  /* ---------- colour helpers ---------- */
  function rgb(h) { return [parseInt(h.substr(1, 2), 16), parseInt(h.substr(3, 2), 16), parseInt(h.substr(5, 2), 16)]; }
  function rgba(h, a) { var c = rgb(h); return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a + ')'; }
  function lum(h) {
    var c = rgb(h).map(function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function contrast(a, b) { var x = lum(a), y = lum(b); if (x < y) { var t = x; x = y; y = t; } return (x + 0.05) / (y + 0.05); }
  function rating(r) { return r >= 7 ? ['AAA', 'ok'] : r >= 4.5 ? ['AA', 'ok'] : r >= 3 ? ['AA large', 'mid'] : ['fail', 'no']; }
  function onColour(h) { return lum(h) > 0.4 ? '#000000' : '#ffffff'; }

  /* ---------- tiny DOM helpers ---------- */
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function img(src, cls) {
    var e = new Image(); e.decoding = cls === 'backdrop' ? 'async' : 'sync'; e.draggable = false; e.alt = ''; if (cls) e.className = cls;
    e.onerror = function () { e.style.visibility = 'hidden'; };
    e.src = src; return e;
  }
  // mini markup: ‹4:text› colour 4, ‹b4:text› bold, ‹d:text› dim, ‹a:text› accent, ‹s:text› selection, ‹k:text› cursor, ‹u9:text› squiggle
  function mk(s) {
    return esc(s).replace(/‹([a-z]*)(\d*):([^›]*)›/g, function (_, f, n, txt) {
      var c = [];
      if (n !== '') c.push('c' + n);
      for (var i = 0; i < f.length; i++) c.push({ b: 'b', d: 'dim', a: 'ac', s: 'selx', k: 'curs', i: 'i', u: 'u' }[f[i]] || '');
      return '<span class="' + c.join(' ') + '">' + txt + '</span>';
    });
  }
  function vars(node, t) {
    var p = t.p, st = node.style;
    var set = function (k, v) { st.setProperty(k, v); };
    set('--bg', p.bg); set('--fg', p.fg); set('--ac', p.ac); set('--cur', p.cur); set('--sel', p.sel); set('--selfg', p.selfg);
    set('--mu', p.mu); set('--ab', p.ab); set('--ib', p.ib); set('--bg2', p.bg2); set('--bg0', p.bg0); set('--fgb', p.fgb); set('--fgd', p.fgd);
    set('--bgA', rgba(p.bg, 0.86)); set('--bgT', rgba(p.bg, 0.94)); set('--bgP', rgba(p.bg, 0.97)); set('--line', rgba(p.fg, 0.14));
    for (var i = 0; i < 8; i++) { set('--c' + i, p.n[i]); set('--c' + (i + 8), p.b[i]); }
  }

  /* ---------- slides ---------- */
  function mem(t) { return S.mem[t.slug] || (S.mem[t.slug] = { preview: 0, wall: 0 }); }
  function slides(t) {
    var out = [], i;
    for (i = 0; i < t.previews.length; i++) out.push({ kind: 'preview', n: i });
    for (i = 0; i < t.walls.length; i++) out.push({ kind: 'wall', n: i });
    out.push({ kind: 'mock', n: 0 }, { kind: 'lock', n: 0 }, { kind: 'palette', n: 0 });
    return out;
  }
  function has(t, kind) { return kind === 'preview' ? t.previews.length > 0 : kind === 'wall' ? t.walls.length > 0 : true; }
  function effKind(t) { return has(t, S.kind) ? S.kind : (S.kind === 'preview' && has(t, 'wall') ? 'wall' : 'mock'); }
  function curSlide(t) {
    var k = effKind(t), m = mem(t), n = 0;
    if (k === 'preview') n = Math.min(m.preview, t.previews.length - 1);
    if (k === 'wall') n = Math.min(m.wall, t.walls.length - 1);
    return { kind: k, n: n };
  }
  function wallOf(t) { return t.walls.length ? t.walls[Math.min(mem(t).wall, t.walls.length - 1)] : null; }

  /* ---------- mock desktop pieces ---------- */
  var ICON = {
    wifi: '<svg viewBox="0 0 16 16"><path d="M1.5 6a9.5 9.5 0 0 1 13 0M4 8.6a6 6 0 0 1 8 0M6.4 11.1a2.4 2.4 0 0 1 3.2 0"/></svg>',
    vol: '<svg viewBox="0 0 16 16"><path d="M2 6h2.5L8 3v10L4.500 10H2zM10.500 5.500a3.500 3.500 0 0 1 0 5M12.500 3.500a6.300 6.300 0 0 1 0 9"/></svg>',
    cpu: '<svg viewBox="0 0 16 16"><rect x="4" y="4" width="8" height="8" rx="1.500"/><path d="M6 1.500v2M10 1.500v2M6 12.500v2M10 12.500v2M1.500 6h2M1.500 10h2M12.500 6h2M12.500 10h2"/></svg>',
    mem: '<svg viewBox="0 0 16 16"><rect x="1.500" y="4.500" width="13" height="7" rx="1.500"/><path d="M4.500 7v2M7 7v2M9.500 7v2M12 7v2"/></svg>',
    bell: '<svg viewBox="0 0 16 16"><path d="M4 11V7a4 4 0 0 1 8 0v4l1.200 1.500H2.800zM6.700 14.200a1.500 1.500 0 0 0 2.600 0"/></svg>',
    pwr: '<svg viewBox="0 0 16 16"><path d="M8 1.800v6M4.300 4.200a5.300 5.300 0 1 0 7.400 0"/></svg>',
    find: '<svg viewBox="0 0 16 16"><circle cx="7" cy="7" r="4.500"/><path d="M10.500 10.500L14 14"/></svg>'
  };
  function clock() {
    var d = new Date(), p = function (n) { return (n < 10 ? '0' : '') + n; };
    var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'], mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var full = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    return { hm: p(d.getHours()) + ':' + p(d.getMinutes()), date: days[d.getDay()] + ' ' + d.getDate() + ' ' + mon[d.getMonth()],
      long: full[d.getDay()] + ', ' + d.getDate() + ' ' + mon[d.getMonth()] + ' ' + d.getFullYear() };
  }
  function barHTML(t, portrait, wsActive) {
    var c = clock(), ws = '';
    for (var i = 1; i <= 6; i++) ws += i === wsActive ? '<i class="act">' + i + '</i>' : '<i class="' + (i < 5 ? 'occ' : '') + '"></i>';
    return '<div class="bar">' +
      '<div class="grp"><span class="cap logo">' + esc(t.name.charAt(0).toUpperCase()) + '</span><span class="cap"><span class="ws">' + ws + '</span></span>' +
      (portrait ? '' : '<span class="cap ttl">kitty &mdash; ~/.files</span>') + '</div>' +
      '<div class="grp mid"><span class="cap clock">' + c.hm + ' <span>' + c.date + '</span></span></div>' +
      '<div class="grp end">' +
      (portrait ? '' : '<span class="cap"><span class="c2">' + ICON.cpu + '</span>12%<span class="c4">' + ICON.mem + '</span>48%</span>') +
      '<span class="cap"><span class="c6">' + ICON.wifi + '</span><span class="c5">' + ICON.vol + '</span>64%</span>' +
      '<span class="cap"><span class="c3">' + ICON.bell + '</span>2</span><span class="cap"><span class="c1">' + ICON.pwr + '</span></span></div></div>';
  }
  function termHTML(t) {
    var P = '‹b4:~/.files› ‹5:on› ‹b5:dev› ‹3:[!+?]› ‹8:took 2s›\n‹b2:❯› ';
    return mk(
      P + 'git status\n' +
      'On branch ‹b:dev›\n' +
      'Your branch is ahead of \'‹1:origin/dev›\' by ‹b:2› commits.\n\n' +
      'Changes to be committed:\n' +
      '        ‹2:modified:   config/hypr/hyprland.conf›\n' +
      '        ‹2:new file:   config/noctalia/colors.json›\n\n' +
      'Changes not staged for commit:\n' +
      '        ‹1:modified:   config/kitty/kitty.conf›\n' +
      '        ‹1:deleted:    config/waybar/style.css›\n\n' +
      'Untracked files:\n' +
      '        ‹1:scripts/theme-switch.sh›\n\n' +
      P + 'ls --color\n' +
      '‹b4:config/›    ‹b4:scripts/›   ‹b4:themes/›    ‹b6:current›‹d:@›   ‹b2:install.sh›‹d:*›  ‹b2:iac›‹d:*›\n' +
      'README.md  ‹3:Makefile›   ‹b1:themes.tar.gz›  ‹b5:preview.png›  ‹b5:wall.jpg›  ‹b3:pipe›‹d:|›  ‹8:.gitignore›\n\n' +
      P + 'git diff config/kitty/kitty.conf\n' +
      '‹b:diff --git a/config/kitty/kitty.conf b/config/kitty/kitty.conf›\n' +
      '‹b:--- a/config/kitty/kitty.conf›\n' +
      '‹b:+++ b/config/kitty/kitty.conf›\n' +
      '‹6:@@ -12,7 +12,7 @@› font_size 11.0\n' +
      ' window_padding_width 12\n' +
      '‹1:-background_opacity 1.0›\n' +
      '‹2:+background_opacity 0.92›\n' +
      ' confirm_os_window_close 0\n' +
      '‹1:-include themes/nord.conf›\n' +
      '‹2:+include themes/' + t.slug + '.conf›\n\n' +
      P + 'theme set ‹s:' + t.slug + '›‹k: ›\n');
  }
  function codeHTML(t) {
    var L = [
      '‹i8:# switch every app to one palette›',
      '‹5:from› ‹6:dataclasses› ‹5:import› dataclass',
      '‹5:import› ‹6:tomllib›',
      '',
      '‹11:@dataclass›(‹3:frozen›‹7:=›‹b9:True›)',
      '‹5:class› ‹b14:Palette›:',
      '    background: ‹14:str›',
      '    accent: ‹14:str› ‹7:=› ‹2:"' + t.p.ac + '"›',
      '    ansi: ‹14:tuple›[‹14:str›, ...] ‹7:=› ()',
      '',
      '‹5:def› ‹b4:load›(‹3:path›: ‹14:str›, ‹3:strict›‹7:=›‹b9:False›) ‹7:->› ‹14:Palette›:',
      '    ‹2:"""Read colors.toml as data. Never execute it."""›',
      '    ‹5:with› ‹12:open›(path, ‹2:"rb"›) ‹5:as› fh:',
      '        raw ‹7:=› tomllib.‹4:load›(fh)',
      '    ansi ‹7:=› ‹12:tuple›(raw[‹2:f"color›‹13:{i}›‹2:"›] ‹5:for› i ‹5:in› ‹12:range›(‹3:16›))',
      '    ‹5:if› strict ‹5:and› ‹12:len›(ansi) ‹7:!=› ‹3:16›:',
      '        ‹5:raise› ‹b1:ValueError›(‹2:"expected 16 colours, got ›‹13:%d›‹2:"› ‹7:%› ‹12:len›(ansi))',
      '    ‹5:return› ‹14:Palette›(raw[‹2:"background"›], raw.‹4:get›(‹2:"accent"›), ansi)',
      '',
      '‹5:def› ‹b4:apply›(‹3:theme›, ‹10:*targets›):',
      '    ‹5:for› app ‹5:in› targets ‹5:or› ‹15:DEFAULT_APPS›:  ‹i8:# hypr, kitty, gtk, qt›',
      '        ‹u9:render›(app, theme)  ‹1:▎› ‹i1:E: "render" is not defined›',
      '    ‹5:return› ‹b9:None›'
    ];
    var out = '';
    for (var i = 0; i < L.length; i++) out += '<span class="cline' + (i === 10 ? ' hl' : '') + '"><span class="ln">' + (i + 1) + '</span>' + mk(L[i]) + '</span>';
    return '<div class="tabs"><span class="on">theme.py</span><span>colors.toml</span><span>hyprland.conf</span></div><pre>' + out + '</pre>' +
      '<div class="status"><span class="m">NORMAL</span><span class="g">dev</span><span>theme.py</span><span class="sp"></span><span>python</span><span>utf-8</span><span class="p">11:5</span></div>';
  }
  function fetchHTML(t) {
    var blk = function (a, b) { var s = ''; for (var i = a; i < b; i++) s += '<span class="blk" style="background:var(--c' + i + ')"></span>'; return s; };
    var bar = function (n, cls) { return '‹' + cls + ':' + new Array(n + 1).join('■') + '›‹8:' + new Array(25 - n).join('■') + '›'; };
    return mk(
      '‹b2:❯› fetch\n' +
      '‹b6:      /\\      ›  ‹ba:Demo session›\n' +
      '‹b6:     /  \\     ›  ‹b4:OS›     CachyOS x86_64\n' +
      '‹b6:    /\\   \\    ›  ‹b4:WM›     Hyprland 0.56.2\n' +
      '‹b6:   /  __  \\   ›  ‹b4:Shell›  Noctalia on Quickshell\n' +
      '‹b6:  /  (  )  \\  ›  ‹b4:Theme›  ' + t.name + ' ‹8:[' + t.mode + ']›\n' +
      '‹b6: /_.-\'  \'-._\\ ›  ‹b4:Term›   kitty, zsh\n\n') +
      '  ' + blk(0, 8) + '\n  ' + blk(8, 16) + '\n\n' +
      mk('‹b10:cpu› ' + bar(9, '2') + ' ‹10:38%›   ‹b12:net› ‹12:▁▂▃▅▂▁▃▆█▅▃▂▁▂▃▂› ‹4:1.2M›\n' +
        '‹b11:mem› ' + bar(15, '3') + ' ‹11:62%›   ‹b13:dsk› ‹13:▂▂▃▃▂▁▁▂▅▇▅▂▁▁▂▁› ‹5:86M›\n' +
        '‹b9:tmp› ' + bar(20, '1') + ' ‹9:81C›   ‹b14:gpu› ‹14:▁▁▂▃▅▆▇▇█▇▆▅▃▂▁▁› ‹6:44%›\n' +
        '‹15:bright white›  ‹7:white›  ‹8:bright black›  ‹0:black›\n');
  }
  function launcherHTML(t) {
    var apps = [['Kitty', 'Terminal emulator', 4], ['Zen Browser', 'Web browser', 5], ['Neovim', 'Text editor', 2], ['Thunar', 'File manager', 3], ['btop', 'System monitor', 1], ['Theme switcher', 'Unified theme manager', 6]];
    var li = apps.map(function (a, i) {
      return '<li class="' + (i === 0 ? 'on' : '') + '"><b style="background:var(--c' + a[2] + ')">' + a[0].charAt(0) + '</b><div>' + a[0] + '<small>' + a[1] + '</small></div>' + (i === 0 ? '<kbd>Enter</kbd>' : '') + '</li>';
    }).join('');
    return '<div class="in">' + ICON.find + '<span>' + esc(t.name.toLowerCase().slice(0, 3)) + '</span><u></u><em>6 results</em></div><ul>' + li +
      '</ul><div class="foot"><span class="on">Apps</span><span>Windows</span><span>Themes</span><span>Clipboard</span><span>Calc</span></div>';
  }
  function notesHTML(t) {
    return '<div class="note"><time>now</time><b>Theme applied</b><span>' + esc(t.name) + ' set for Hyprland, Noctalia, GTK, Qt and kitty.</span><div class="pb"><i></i></div></div>' +
      '<div class="note crit"><time>2m</time><b>Battery low</b><span>Keyboard at 9%. Plug it in soon.</span></div>';
  }
  function wallpaperNode(t) {
    var w = wallOf(t);
    return w ? img(w.src, 'wp') : el('div', 'wpfill');
  }
  // one logical screen of W x H css px; kind: mock | lock | wall
  function screenEl(t, kind, W, H, wsActive) {
    var s = el('div', 'screen ' + kind), portrait = H > W;
    s.style.width = W + 'px'; s.style.height = H + 'px';
    s.appendChild(wallpaperNode(t));
    if (kind === 'wall') return s;
    if (kind === 'lock') {
      var c = clock();
      s.appendChild(el('div', 'dimmer'));
      s.appendChild(el('div', 'lockbox', '<div class="t">' + c.hm + '</div><div class="d">' + c.long + '</div><div class="av">U</div><div class="u">User</div>' +
        '<div class="pw"><i></i><i></i><i></i><i></i><i></i><i></i></div><div class="hint">Enter password &middot; ' + esc(t.name) + '</div>' +
        '<div class="pwr"><span>Suspend</span><span>Reboot</span><span>Shut down</span></div>'));
      return s;
    }
    s.insertAdjacentHTML('beforeend', barHTML(t, portrait, wsActive || 1));
    var tiles = el('div', 'tiles' + (portrait ? ' col' : ''));
    var term = el('div', 'win active'); term.appendChild(el('pre', '', termHTML(t)));
    var code = el('div', 'win code', codeHTML(t));
    var fetch = el('div', 'win'); fetch.appendChild(el('pre', '', fetchHTML(t)));
    if (portrait) {
      term.style.flex = '1.62 1 0'; code.style.flex = '1.2 1 0'; fetch.style.flex = '0.78 1 0';
      tiles.appendChild(code); tiles.appendChild(term); tiles.appendChild(fetch);
    } else {
      term.style.flex = '1.08 1 0';
      var st = el('div', 'stack'); st.style.flex = '1 1 0';
      code.style.flex = '1.62 1 0'; fetch.style.flex = '1 1 0';
      st.appendChild(code); st.appendChild(fetch);
      tiles.appendChild(term); tiles.appendChild(st);
    }
    s.appendChild(tiles);
    if (S.overlays) {
      var l = el('div', 'launcher', launcherHTML(t));
      if (portrait) l.style.top = '33%';
      s.appendChild(l);
      s.appendChild(el('div', 'notes', notesHTML(t)));
    }
    return s;
  }
  function screensView(v, t, kind, pw, ph) {
    var sc = el('div', 'scaler');
    if (S.dual) {
      v.classList.add('dual');
      var gap = 50, pad = 40, top = 64, LW = 1920, LH = 1080, PW = 1080, PH = 1920;
      var totalW = LW + gap + PW + pad * 2, totalH = PH + pad + top;
      var k = Math.min(pw / totalW, ph / totalH);
      var a = screenEl(t, kind, LW, LH, 1), b = screenEl(t, kind, PW, PH, 2);
      a.style.left = pad + 'px'; a.style.top = (top + (PH - LH) / 2) + 'px';
      b.style.left = (pad + LW + gap) + 'px'; b.style.top = top + 'px';
      var la = el('div', 'mlabel', 'DP-1 &middot; 1920&times;1080'), lb = el('div', 'mlabel', 'DP-2 &middot; 1080&times;1920 portrait');
      la.style.left = pad + 'px'; la.style.top = (top + (PH - LH) / 2 - 52) + 'px';
      lb.style.left = (pad + LW + gap) + 'px'; lb.style.top = (top - 50) + 'px';
      sc.appendChild(la); sc.appendChild(lb); sc.appendChild(a); sc.appendChild(b);
      sc.style.width = totalW + 'px'; sc.style.height = totalH + 'px';
      sc.style.transform = 'translate(' + ((pw - totalW * k) / 2) + 'px,' + ((ph - totalH * k) / 2) + 'px) scale(' + k + ')';
    } else {
      var W = pw >= ph ? 1920 : 1080;
      if (pw >= ph && pw / ph < 1.25) W = 1500;      // squarish pane (compare split): keep text readable
      var kk = pw / W, H = Math.round(ph / kk);
      var one = screenEl(t, kind, W, H, 1);
      sc.appendChild(one);
      sc.style.transform = 'scale(' + kk + ')';
    }
    v.appendChild(sc);
  }

  /* ---------- palette sheet ---------- */
  var ANSI = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'];
  function swatch(label, hex, bg, note) {
    var r = contrast(hex, bg), ra = rating(r);
    var b = el('button', '', '<span class="swc" style="background:' + hex + '"></span><span class="lbl"><b>' + esc(label) + '</b>' + hex +
      '<i><span class="r ' + ra[1] + '">' + r.toFixed(2) + ':1</span> ' + (note || ra[0]) + '</i></span>');
    b.title = 'Copy ' + hex; b.dataset.copy = hex;
    return b;
  }
  function paletteView(v, t) {
    var p = t.p, s = el('div', 'sheet');
    var fgbg = contrast(p.fg, p.bg);
    var meta = ['<span>fg on bg <b>' + fgbg.toFixed(2) + ':1 ' + rating(fgbg)[0] + '</b></span>', '<span>mode <b>' + t.mode + '</b></span>',
      '<span>format <b>' + esc(t.format) + '</b></span>', '<span>palette from <b>' + esc(t.srcs.join(' + ') || 'nothing') + '</b></span>',
      '<span>licence <b>' + esc(t.lic || 'none found') + '</b></span>', '<span>commit <b>' + esc(t.commit || '?') + '</b></span>',
      '<span>' + t.walls.length + ' wallpapers' + (t.videos ? ', ' + t.videos + ' videos (not shown)' : '') + '</span>'];
    if (t.guessed.length) meta.push('<span class="bad">guessed: ' + esc(t.guessed.join(', ')) + '</span>');
    if (t.derived.length) meta.push('<span>derived: ' + esc(t.derived.join(', ')) + '</span>');
    s.appendChild(el('h1', '', esc(t.name) + '<small>' + esc(t.author) + ' &middot; ' + esc(t.repo.replace(/^https?:[/][/]/, '')) + '</small>'));
    s.appendChild(el('div', 'meta', meta.join('')));
    s.appendChild(el('h3', '', 'Core (contrast against background)'));
    var core = el('div', 'sw');
    [['background', p.bg, 'base'], ['foreground', p.fg], ['accent', p.ac], ['cursor', p.cur], ['selection', p.sel], ['selection text', p.selfg],
      ['muted', p.mu], ['active border', p.ab], ['inactive border', p.ib], ['lighter background', p.bg2], ['dim foreground', p.fgd], ['bright foreground', p.fgb]
    ].forEach(function (c) { core.appendChild(swatch(c[0], c[1], p.bg, c[2])); });
    s.appendChild(core);
    s.appendChild(el('h3', '', 'ANSI normal 0-7'));
    var n = el('div', 'sw eight'); p.n.forEach(function (h, i) { n.appendChild(swatch(i + ' ' + ANSI[i], h, p.bg)); }); s.appendChild(n);
    s.appendChild(el('h3', '', 'ANSI bright 8-15'));
    var b = el('div', 'sw eight'); p.b.forEach(function (h, i) { b.appendChild(swatch((i + 8) + ' ' + ANSI[i], h, p.bg)); }); s.appendChild(b);
    s.appendChild(el('h3', '', 'Pairs in use'));
    var pairs = el('div', 'pairs');
    [['Body text', p.fg, p.bg], ['Selected text', p.selfg, p.sel], ['On accent (active item)', p.bg, p.ac], ['Dim text', p.fgd, p.bg],
      ['Muted / comments', p.b[0], p.bg], ['Error text', p.n[1], p.bg], ['Text on lighter background', p.fg, p.bg2], ['Cursor block', p.bg, p.cur]
    ].forEach(function (c) {
      var r = contrast(c[1], c[2]), d = el('div', 'pair', 'The quick brown fox 0123<small>' + c[0] + ' &middot; ' + c[1] + ' on ' + c[2] + ' &middot; ' + r.toFixed(2) + ':1 ' + rating(r)[0] + '</small>');
      d.style.color = c[1]; d.style.background = c[2]; pairs.appendChild(d);
    });
    s.appendChild(pairs);
    v.appendChild(s);
  }

  /* ---------- view building, cache, crossfade ---------- */
  function buildView(ti, pw, ph) {
    var t = T[ti], sl = curSlide(t), v = el('div', 'view');
    vars(v, t);
    if (sl.kind === 'preview') {
      var p = t.previews[sl.n];
      v.appendChild(img(t.wthumb || t.thumb || p.src, 'backdrop'));
      v.appendChild(img(p.src, 'full contain'));
    } else if (sl.kind === 'wall' && !S.dual) {
      v.appendChild(img(t.walls[sl.n].src, 'full cover'));
    } else if (sl.kind === 'palette') {
      paletteView(v, t);
    } else {
      screensView(v, t, sl.kind, pw, ph);
    }
    return v;
  }
  var cache = {};
  function viewKey(ti, pw, ph) {
    var t = T[ti], sl = curSlide(t), m = mem(t);
    return [ti, sl.kind, sl.n, m.wall, S.dual ? 1 : 0, S.overlays ? 1 : 0, pw, ph].join('|');
  }
  function getView(ti, pw, ph) {
    var k = viewKey(ti, pw, ph);
    if (!cache[k]) { cache[k] = buildView(ti, pw, ph); cache[k].dataset.k = k; }
    return cache[k];
  }
  function paint(pane, ti, label) {
    var pw = pane.clientWidth, ph = pane.clientHeight;
    if (!pw || !ph) return;
    var v = getView(ti, pw, ph);
    if (pane._cur === v && v.parentNode === pane) { setLabel(pane, label); return; }
    var kids = pane.querySelectorAll('.view');
    for (var i = 0; i < kids.length; i++) if (kids[i] !== pane._cur && kids[i] !== v) pane.removeChild(kids[i]);
    var old = pane._cur;
    v.classList.remove('on');
    pane.appendChild(v);
    void v.offsetWidth;
    v.classList.add('on');
    pane._cur = v;
    if (old && old !== v) setTimeout(function () { if (old.parentNode === pane && pane._cur !== old) pane.removeChild(old); }, 170);
    setLabel(pane, label);
  }
  function setLabel(pane, label) {
    var l = pane.querySelector('.cmpLabel');
    if (!label) { if (l) pane.removeChild(l); return; }
    if (!l) { l = el('div', 'cmpLabel'); pane.appendChild(l); }
    l.textContent = label;
  }
  var preloadTimer = 0;
  function preload() {
    clearTimeout(preloadTimer);
    preloadTimer = setTimeout(function () {
      var pane = $('paneA'), pw = pane.clientWidth, ph = pane.clientHeight, keep = {};
      var pos = S.order.indexOf(S.i), near = [S.i];
      if (S.order.length > 1) {
        var a = pos < 0 ? 0 : pos;
        near.push(S.order[(a + 1) % S.order.length], S.order[(a - 1 + S.order.length) % S.order.length]);
      }
      near.forEach(function (ti) {
        var v = getView(ti, pw, ph); keep[v.dataset.k] = 1;
        if (!v.parentNode) Array.prototype.forEach.call(v.querySelectorAll('img'), function (im) { if (im.decode) im.decode().catch(function () {}); });
      });
      if (S.compare && S.cmpB >= 0) { var pb = $('paneB'); keep[viewKey(S.cmpB, pb.clientWidth, pb.clientHeight)] = 1; }
      Object.keys(cache).forEach(function (k) { if (!keep[k] && !cache[k].parentNode) delete cache[k]; else if (!keep[k] && !cache[k].classList.contains('on')) delete cache[k]; });
    }, 90);
  }

  function render() {
    var t = T[S.i], b = document.body;
    b.classList.toggle('cmp-split', S.compare === 1);
    b.classList.toggle('cmp-wipe', S.compare === 2);
    b.classList.toggle('wide', innerWidth >= innerHeight);
    b.classList.toggle('tall', innerWidth < innerHeight);
    b.classList.toggle('nohud', !S.hud);
    var A = $('paneA'), B = $('paneB'), dv = $('divider');
    var cmp = S.compare && S.cmpB >= 0 && S.cmpB !== S.i;
    B.hidden = !cmp; dv.hidden = !(cmp && S.compare === 2);
    if (cmp && S.compare === 2) {
      var x = Math.round(S.wipe * innerWidth);
      B.style.clipPath = 'inset(0 0 0 ' + x + 'px)'; dv.style.left = x + 'px';
    } else { B.style.clipPath = ''; }
    paint(A, S.i, cmp ? t.name : '');
    if (cmp) paint(B, S.cmpB, T[S.cmpB].name);
    hud(); preload(); hash();
  }
  function hash() {
    var t = T[S.i], sl = curSlide(t);
    try { history.replaceState(null, '', '#' + t.slug + '/' + sl.kind + (sl.n ? '/' + (sl.n + 1) : '')); } catch (e) { /* file:// quirks */ }
  }

  /* ---------- HUD ---------- */
  function hud() {
    var t = T[S.i], sl = curSlide(t), pos = S.order.indexOf(S.i);
    $('hudName').textContent = t.name;
    $('hudAuthor').textContent = 'by ' + t.author;
    $('hudIndex').textContent = (pos >= 0 ? (pos + 1) + '/' + S.order.length : 'filtered out') + (S.order.length !== N ? ' of ' + N : '');
    $('hudFormat').textContent = t.format + (t.src && t.src !== 'colors.toml' ? ' via ' + t.src : '');
    $('hudMode').textContent = t.mode;
    var lab = KIND_LABEL[sl.kind];
    if (sl.kind === 'preview' && t.previews.length > 1) lab += ' ' + (sl.n + 1) + '/' + t.previews.length;
    if (sl.kind === 'wall') lab += ' ' + (sl.n + 1) + '/' + t.walls.length;
    if (S.dual && (sl.kind === 'mock' || sl.kind === 'lock' || sl.kind === 'wall')) lab += ' · dual';
    $('hudSlide').textContent = lab;
    var w = $('hudWarn'), warn = [];
    if (t.guessed.length) warn.push('colours guessed');
    if (sl.kind === 'preview' && t.psrc && t.psrc !== 'preview.png') warn.push('no preview.png: ' + t.psrc);
    w.hidden = !warn.length; w.textContent = warn.join(' · ');
    var c = $('hudCmp');
    c.hidden = !S.compare;
    if (S.compare) c.textContent = (S.compare === 1 ? 'split' : 'wipe') + ' vs ' + (S.cmpB >= 0 && S.cmpB !== S.i ? T[S.cmpB].name : 'nothing yet, move to another theme');
    var st = $('hudStar'); st.classList.toggle('on', !!favs[t.slug]); st.textContent = favs[t.slug] ? '★' : '☆';
    var rail = $('rail'), html = '';
    KINDS.forEach(function (k, i) {
      var n = k === 'preview' ? t.previews.length : k === 'wall' ? t.walls.length : 1;
      html += '<button data-k="' + k + '"' + (n ? '' : ' disabled') + ' class="' + (sl.kind === k ? 'on' : '') + '"><kbd>' + (i + 1) + '</kbd>' + KIND_LABEL[k] + (n > 1 ? ' ' + n : '') + '</button>';
    });
    rail.innerHTML = html;
    document.title = t.name + ' · Theme Preview';
  }

  /* ---------- navigation ---------- */
  function go(ti) {
    if (ti === S.i || ti < 0 || ti >= N) return;
    S.prev = S.i; S.i = ti;
    if (S.compare && (S.cmpB < 0 || S.cmpB === S.i)) S.cmpB = S.prev;
    render();
  }
  function themeStep(d) {
    var o = S.order; if (!o.length) return;
    var pos = o.indexOf(S.i);
    if (pos < 0) { go(o[0]); return; }
    go(o[(pos + d + o.length) % o.length]);
  }
  function slideStep(d) {
    var t = T[S.i], list = slides(t), cur = curSlide(t), at = 0;
    for (var i = 0; i < list.length; i++) if (list[i].kind === cur.kind && list[i].n === cur.n) at = i;
    setSlide(list[(at + d + list.length) % list.length]);
  }
  function setSlide(sl) {
    var t = T[S.i];
    S.kind = sl.kind;
    if (sl.kind === 'preview') mem(t).preview = sl.n;
    if (sl.kind === 'wall') mem(t).wall = sl.n;
    render();
  }
  function kindKey(k) {
    var t = T[S.i], cur = curSlide(t);
    if (!has(t, k)) { toast('No ' + KIND_LABEL[k].toLowerCase() + ' for this theme'); return; }
    if (cur.kind === k && k === 'wall') { setSlide({ kind: k, n: (cur.n + 1) % t.walls.length }); return; }
    if (cur.kind === k && k === 'preview') { setSlide({ kind: k, n: (cur.n + 1) % t.previews.length }); return; }
    S.kind = k; render();
  }
  function wallStep(d) {
    var t = T[S.i]; if (!t.walls.length) return;
    var m = mem(t); m.wall = (Math.min(m.wall, t.walls.length - 1) + d + t.walls.length) % t.walls.length;
    render(); toast('Wallpaper ' + (m.wall + 1) + '/' + t.walls.length + ' · ' + t.walls[m.wall].file);
  }
  function toggleFav(ti) {
    var s = T[ti].slug;
    if (favs[s]) delete favs[s]; else favs[s] = 1;
    saveFavs(); hud();
    var c = document.querySelector('.card[data-i="' + ti + '"] .st');
    if (c) { c.classList.toggle('on', !!favs[s]); c.textContent = favs[s] ? '★' : '☆'; }
    if (S.filter.fav) applyFilter();
  }
  function favList() { return T.filter(function (t) { return favs[t.slug]; }).map(function (t) { return t.slug; }); }
  function saveFavs() { var l = favList(); store.set(FAV_KEY, l); store.raw(LIST_KEY, l.join('\n')); }
  function cycleCompare() {
    S.compare = (S.compare + 1) % 3;
    if (S.compare === 1) S.cmpB = S.prev >= 0 ? S.prev : -1;
    if (S.compare && S.cmpB < 0) toast('Compare is on. Move to another theme to fill the second half.');
    else toast(['Compare off', 'Compare: side by side', 'Compare: wipe, move the mouse'][S.compare]);
    render();
  }
  function fullscreen() {
    var d = document, e = d.documentElement;
    try {
      if (d.fullscreenElement) d.exitFullscreen();
      else if (e.requestFullscreen) { var p = e.requestFullscreen(); if (p && p.catch) p.catch(function () { toast('Fullscreen was blocked. Press F11.'); }); }
      else toast('Press F11 for fullscreen');
    } catch (err) { toast('Press F11 for fullscreen'); }
  }
  var toastTimer = 0;
  function toast(msg) { var t = $('toast'); t.textContent = msg; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, 1500); }
  function copy(text, okMsg) {
    var done = function () { toast(okMsg || 'Copied'); };
    var fallback = function () {
      var ta = el('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { toast('Copy failed. Select the text and press Ctrl+C.'); }
      document.body.removeChild(ta);
    };
    try { if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback); else fallback(); } catch (e) { fallback(); }
  }

  /* ---------- grid ---------- */
  var cardsBuilt = false;
  function buildCards() {
    if (cardsBuilt) return; cardsBuilt = true;
    var wrap = $('cards'), frag = document.createDocumentFragment();
    T.forEach(function (t) {
      var c = el('div', 'card'); c.dataset.i = t.i; c.style.setProperty('--bg', t.p.bg);
      var strip = [t.p.bg, t.p.fg, t.p.ac, t.p.n[1], t.p.n[2], t.p.n[3], t.p.n[4], t.p.n[5], t.p.n[6]].map(function (h) { return '<i style="background:' + h + '"></i>'; }).join('');
      c.innerHTML = (t.thumb ? '<img class="th" loading="lazy" decoding="async" alt="" src="' + t.thumb + '">' : '<div class="th"></div>') +
        '<div class="strip">' + strip + '</div><div class="nm"><b>' + esc(t.name) + '</b><span>' + esc(t.author) + '</span></div>' +
        '<div class="bd">' + (t.mode === 'light' ? '<span>light</span>' : '') + (t.builtin ? '<span>built-in</span>' : '') + (t.format.indexOf('4.x') === 0 ? '' : '<span>' + (t.format.indexOf('legacy') === 0 ? 'legacy' : '3.x') + '</span>') + '</div>' +
        '<button class="st' + (favs[t.slug] ? ' on' : '') + '" title="Star">' + (favs[t.slug] ? '★' : '☆') + '</button>';
      frag.appendChild(c);
    });
    wrap.appendChild(frag);
    wrap.appendChild(el('div', 'empty', 'No theme matches.'));
  }
  function sorted(list) {
    var s = S.filter.sort, name = function (a, b) { return T[a].name.toLowerCase() < T[b].name.toLowerCase() ? -1 : 1; };
    if (s === 'hue') return list.sort(function (a, b) {
      var x = T[a], y = T[b], gx = x.sat < 0.15, gy = y.sat < 0.15;
      if (gx !== gy) return gx ? 1 : -1;
      if (gx) return lum(x.p.bg) - lum(y.p.bg) || name(a, b);
      return x.hue - y.hue || name(a, b);
    });
    if (s === 'contrast') return list.sort(function (a, b) { return T[b].contrast - T[a].contrast || name(a, b); });
    if (s === 'format') return list.sort(function (a, b) { return T[a].format < T[b].format ? -1 : T[a].format > T[b].format ? 1 : name(a, b); });
    return list.sort(name);
  }
  function applyFilter() {
    var f = S.filter, q = f.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    var list = [];
    T.forEach(function (t) {
      if (f.mode !== 'all' && t.mode !== f.mode) return;
      if (f.fav && !favs[t.slug]) return;
      var hay = (t.name + ' ' + t.slug + ' ' + t.author + ' ' + t.format + ' ' + t.mode + (t.builtin ? ' built-in builtin' : '')).toLowerCase();
      for (var i = 0; i < q.length; i++) if (hay.indexOf(q[i]) < 0) return;
      list.push(t.i);
    });
    S.order = sorted(list);
    if (cardsBuilt) {
      var wrap = $('cards'), map = {}, cards = wrap.querySelectorAll('.card');
      for (var i = 0; i < cards.length; i++) { map[cards[i].dataset.i] = cards[i]; cards[i].hidden = true; }
      var empty = wrap.querySelector('.empty');
      S.order.forEach(function (ti) { var c = map[ti]; c.hidden = false; wrap.insertBefore(c, empty); });
      empty.hidden = S.order.length > 0;
      var at = S.order.indexOf(S.i); S.gsel = at >= 0 ? at : 0; markSel(false);
    }
    $('gCount').textContent = S.order.length + ' of ' + N + ' · ' + favList().length + ' starred';
    if (!S.grid) hud();
  }
  function markSel(scroll) {
    var cards = $('cards').querySelectorAll('.card');
    for (var i = 0; i < cards.length; i++) { cards[i].classList.remove('pick'); cards[i].classList.toggle('cur', +cards[i].dataset.i === S.i); }
    var c = S.order.length ? $('cards').querySelector('.card[data-i="' + S.order[S.gsel] + '"]') : null;
    if (c) { c.classList.add('pick'); if (scroll) c.scrollIntoView({ block: 'nearest' }); }
  }
  function openGrid() {
    buildCards(); S.grid = true; $('grid').hidden = false; applyFilter();
    var at = S.order.indexOf(S.i); S.gsel = at >= 0 ? at : 0; markSel(true);
    var c = $('cards').querySelector('.card.pick'); if (c) c.scrollIntoView({ block: 'center' });
    $('q').focus(); $('q').select();
  }
  function closeGrid(openIdx) {
    S.grid = false; $('grid').hidden = true; $('q').blur();
    if (openIdx != null && openIdx !== S.i) go(openIdx); else render();
  }
  function gridCols() {
    var cards = Array.prototype.filter.call($('cards').querySelectorAll('.card'), function (c) { return !c.hidden; });
    if (!cards.length) return 1;
    var top = cards[0].offsetTop, n = 0;
    while (n < cards.length && cards[n].offsetTop === top) n++;
    return n || 1;
  }
  function gridMove(d) {
    if (!S.order.length) return;
    S.gsel = Math.max(0, Math.min(S.order.length - 1, S.gsel + d)); markSel(true);
  }

  /* ---------- modals ---------- */
  function openFavs() {
    var l = favList(); saveFavs();
    $('favText').value = l.join('\n');
    $('favInfo').textContent = l.length ? l.length + ' starred. One theme slug per line. Also stored in localStorage as "' + LIST_KEY + '".' : 'Nothing starred yet. Press F on a theme.';
    $('favModal').hidden = false; $('favText').focus(); $('favText').select();
  }
  function modalOpen() { return !$('favModal').hidden || !$('help').hidden; }
  function closeModals() { $('favModal').hidden = true; $('help').hidden = true; }

  /* ---------- events ---------- */
  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = e.key;
    if (modalOpen()) { if (k === 'Escape' || (k === '?' && !$('help').hidden) || ((k === 's' || k === 'S') && !$('favModal').hidden)) { closeModals(); e.preventDefault(); } return; }
    if (S.grid) {
      var inSearch = document.activeElement === $('q');
      if (k === 'Escape') { if (inSearch && $('q').value) { $('q').value = ''; S.filter.q = ''; applyFilter(); } else closeGrid(); }
      else if (k === 'Enter') { if (S.order.length) closeGrid(S.order[S.gsel]); }
      else if (k === 'ArrowDown') gridMove(gridCols());
      else if (k === 'ArrowUp') gridMove(-gridCols());
      else if (k === 'ArrowRight' && (!inSearch || !$('q').value)) gridMove(1);
      else if (k === 'ArrowLeft' && (!inSearch || !$('q').value)) gridMove(-1);
      else if (!inSearch && (k === 'g' || k === 'G')) closeGrid();
      else if (!inSearch && k.length === 1 && k !== ' ') { $('q').focus(); return; }
      else return;
      e.preventDefault(); return;
    }
    var handled = true;
    switch (k) {
      case 'ArrowRight': case 'j': case 'J': case 'PageDown': themeStep(1); break;
      case 'ArrowLeft': case 'k': case 'K': case 'PageUp': themeStep(-1); break;
      case 'ArrowDown': slideStep(1); break;
      case 'ArrowUp': slideStep(-1); break;
      case ' ': slideStep(e.shiftKey ? -1 : 1); break;
      case 'Home': if (S.order.length) go(S.order[0]); break;
      case 'End': if (S.order.length) go(S.order[S.order.length - 1]); break;
      case '1': case '2': case '3': case '4': case '5': kindKey(KINDS[+k - 1]); break;
      case 'w': wallStep(1); break;
      case 'W': wallStep(-1); break;
      case 'l': case 'L': S.overlays = !S.overlays; if (S.kind !== 'mock') S.kind = 'mock'; render(); break;
      case 'm': case 'M': S.dual = !S.dual; if (S.dual && (S.kind === 'preview' || S.kind === 'palette')) S.kind = 'mock'; render(); toast(S.dual ? 'Dual monitor: landscape + portrait' : 'Single screen'); break;
      case 'c': case 'C': cycleCompare(); break;
      case 'x': case 'X': if (S.compare && S.cmpB >= 0 && S.cmpB !== S.i) { var b = S.cmpB; S.cmpB = S.i; S.prev = S.i; S.i = b; render(); } break;
      case 'g': case 'G': case 'Escape': openGrid(); break;
      case 'f': case 'F': toggleFav(S.i); toast(favs[T[S.i].slug] ? 'Starred ' + T[S.i].name : 'Unstarred ' + T[S.i].name); break;
      case 's': case 'S': openFavs(); break;
      case 'h': case 'H': S.hud = !S.hud; ui.hud = S.hud; store.set(UI_KEY, ui); render(); if (!S.hud) toast('HUD hidden. H shows it again.'); break;
      case 'Enter': fullscreen(); break;
      case 'r': case 'R': if (S.order.length > 1) { var r; do { r = S.order[Math.floor(Math.random() * S.order.length)]; } while (r === S.i); go(r); } break;
      case 'o': case 'O': var sl = curSlide(T[S.i]), src = sl.kind === 'preview' ? T[S.i].previews[sl.n] : wallOf(T[S.i]); if (src) window.open(src.orig, '_blank', 'noopener'); break;
      case '?': $('help').hidden = false; break;
      default: handled = false;
    }
    if (handled) e.preventDefault();
  });

  var wAcc = 0, wLast = 0, wSeen = 0;
  window.addEventListener('wheel', function (e) {
    if (S.grid || modalOpen()) return;
    if (e.ctrlKey) return;
    var sheet = e.target.closest && e.target.closest('.sheet');
    if (sheet && sheet.scrollHeight > sheet.clientHeight + 2 && Math.abs(e.deltaY) > Math.abs(e.deltaX)) return;   // let a tall palette scroll
    e.preventDefault();
    var d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    if (e.deltaMode === 1) d *= 32; else if (e.deltaMode === 2) d *= 400;
    var now = performance.now(), fine = e.deltaMode === 0 && Math.abs(d) < 50;
    if (now - wSeen > 220) wAcc = 0;
    wSeen = now;
    if (now - wLast < (fine ? 330 : 110)) return;
    wAcc += d;
    if (Math.abs(wAcc) >= (fine ? 60 : 30)) { (e.shiftKey ? slideStep : themeStep)(wAcc > 0 ? 1 : -1); wAcc = 0; wLast = now; }
  }, { passive: false });

  var tStart = null;
  window.addEventListener('touchstart', function (e) { if (e.touches.length === 1) tStart = { x: e.touches[0].clientX, y: e.touches[0].clientY }; }, { passive: true });
  window.addEventListener('touchend', function (e) {
    if (!tStart || S.grid || modalOpen()) return;
    var c = e.changedTouches[0], dx = c.clientX - tStart.x, dy = c.clientY - tStart.y; tStart = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.3) themeStep(dx < 0 ? 1 : -1);
    else if (Math.abs(dy) > 50 && Math.abs(dy) > Math.abs(dx) * 1.3) slideStep(dy < 0 ? 1 : -1);
  }, { passive: true });

  var idleTimer = 0, wipeRaf = 0;
  window.addEventListener('mousemove', function (e) {
    document.body.classList.remove('idle'); clearTimeout(idleTimer);
    idleTimer = setTimeout(function () { if (!S.grid && !modalOpen()) document.body.classList.add('idle'); }, 2500);
    if (S.compare === 2 && !S.grid) {
      S.wipe = Math.max(0, Math.min(1, e.clientX / innerWidth));
      if (!wipeRaf) wipeRaf = requestAnimationFrame(function () {
        wipeRaf = 0; var x = Math.round(S.wipe * innerWidth);
        $('paneB').style.clipPath = 'inset(0 0 0 ' + x + 'px)'; $('divider').style.left = x + 'px';
      });
    }
  });
  var rsz = 0;
  window.addEventListener('resize', function () { clearTimeout(rsz); rsz = setTimeout(function () { cache = {}; render(); }, 120); });
  document.addEventListener('fullscreenchange', function () { cache = {}; render(); });

  $('edgeL').addEventListener('click', function () { themeStep(-1); this.blur(); });
  $('edgeR').addEventListener('click', function () { themeStep(1); this.blur(); });
  $('rail').addEventListener('click', function (e) { var b = e.target.closest('button'); if (b && !b.disabled) kindKey(b.dataset.k); });
  $('hudStar').addEventListener('click', function () { toggleFav(S.i); this.blur(); });
  $('stage').addEventListener('click', function (e) { var b = e.target.closest('[data-copy]'); if (b) copy(b.dataset.copy, 'Copied ' + b.dataset.copy); });
  $('cards').addEventListener('click', function (e) {
    var c = e.target.closest('.card'); if (!c) return;
    if (e.target.closest('.st')) { toggleFav(+c.dataset.i); $('gCount').textContent = S.order.length + ' of ' + N + ' · ' + favList().length + ' starred'; return; }
    closeGrid(+c.dataset.i);
  });
  $('q').addEventListener('input', function () { S.filter.q = this.value; applyFilter(); S.gsel = 0; markSel(true); });
  $('fMode').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    Array.prototype.forEach.call(this.children, function (x) { x.classList.toggle('on', x === b); });
    S.filter.mode = b.dataset.v; applyFilter();
  });
  $('fFav').addEventListener('change', function () { S.filter.fav = this.checked; applyFilter(); });
  $('fSort').addEventListener('change', function () { S.filter.sort = this.value; applyFilter(); this.blur(); });
  $('gClose').addEventListener('click', function () { closeGrid(); });
  $('gFavs').addEventListener('click', openFavs);
  $('favClose').addEventListener('click', closeModals);
  $('helpClose').addEventListener('click', closeModals);
  $('favCopy').addEventListener('click', function () { copy($('favText').value, 'Copied ' + favList().length + ' slugs'); });
  $('favSave').addEventListener('click', function () {
    try {
      var a = el('a'); a.href = URL.createObjectURL(new Blob([$('favText').value + '\n'], { type: 'text/plain' })); a.download = 'starred-themes.txt';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    } catch (e) { toast('Download failed. Use Copy.'); }
  });
  $('favClear').addEventListener('click', function () { favs = {}; saveFavs(); openFavs(); applyFilter(); if (cardsBuilt) { var st = document.querySelectorAll('.card .st'); for (var i = 0; i < st.length; i++) { st[i].classList.remove('on'); st[i].textContent = '☆'; } } });
  Array.prototype.forEach.call(document.querySelectorAll('.modal'), function (m) { m.addEventListener('click', function (e) { if (e.target === m) closeModals(); }); });

  /* ---------- boot ---------- */
  if (!N) { $('stage').appendChild(el('div', 'nothing', 'data.js did not load or holds no themes.')); return; }
  (function () {
    var h = decodeURIComponent((location.hash || '').slice(1)).split('/');
    if (h[0] && bySlug[h[0]]) S.i = bySlug[h[0]].i;
    else { var tn = bySlug['tokyo-night']; if (tn) S.i = tn.i; }
    if (h[1] && KINDS.indexOf(h[1]) >= 0) S.kind = h[1];
    var n = parseInt(h[2], 10);
    if (n > 0) { var m = mem(T[S.i]); if (S.kind === 'wall') m.wall = n - 1; if (S.kind === 'preview') m.preview = n - 1; }
    var q = location.search || '';
    if (/[?&]dual=1/.test(q)) S.dual = true;
    if (/[?&]grid=1/.test(q)) setTimeout(openGrid, 0);
    if (/[?&]hud=0/.test(q)) S.hud = false;
    if (/[?&]overlays=0/.test(q)) S.overlays = false;
    var cm = /[?&]compare=([a-z0-9._-]+)/.exec(q);
    if (cm && bySlug[cm[1]]) { S.compare = /[?&]wipe=1/.test(q) ? 2 : 1; S.cmpB = bySlug[cm[1]].i; }
  })();
  $('helpMeta').textContent = N + ' themes, data generated ' + (D.generated || '?') + '. ' + (D.skipped && D.skipped.length ? 'Not collected: ' + D.skipped.map(function (s) { return s.name + ' (' + s.reason + ')'; }).join('; ') + '.' : '');
  applyFilter();
  render();
})();
