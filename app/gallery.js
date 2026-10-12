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
  if(ui.builtinsOpen==null)ui.builtinsOpen=false;

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
    grid: false, stats: false, gsel: 0,
    filter: { q: '', sort: 'pop' }
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
    var b = el('button', '', '<span class="swc" style="background:' + esc(hex) + '"></span><span class="lbl"><b>' + esc(label) + '</b>' + esc(hex) +
      '<i><span class="r ' + ra[1] + '">' + r.toFixed(2) + ':1</span> ' + (note || ra[0]) + '</i></span>');
    b.title = 'Copy ' + hex; b.dataset.copy = hex;
    return b;
  }
  function paletteView(v, t) {
    var p = t.p, s = el('div', 'sheet');
    var fgbg = contrast(p.fg, p.bg);
    var meta = ['<span>fg on bg <b>' + fgbg.toFixed(2) + ':1 ' + rating(fgbg)[0] + '</b></span>', '<span>mode <b>' + esc(t.mode) + '</b></span>',
      '<span>format <b>' + esc(t.format) + '</b></span>', '<span>palette from <b>' + esc(t.srcs.join(' + ') || 'nothing') + '</b></span>',
      '<span>licence <b>' + esc(t.lic || 'none found') + '</b></span>', '<span>commit <b>' + esc(t.commit || '?') + '</b></span>',
      '<span>' + t.walls.length + ' wallpapers' + (t.videos ? ', ' + esc(t.videos) + ' videos (not shown)' : '') + '</span>'];
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
      var r = contrast(c[1], c[2]), d = el('div', 'pair', 'The quick brown fox 0123<small>' + c[0] + ' &middot; ' + esc(c[1]) + ' on ' + esc(c[2]) + ' &middot; ' + r.toFixed(2) + ':1 ' + rating(r)[0] + '</small>');
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
    var params = new URLSearchParams();
    HASH_KEYS.forEach(function (k) { var v = k === 'view' ? (S.stats ? 'stats' : S.grid ? 'grid' : '') : S.filter[k]; if (k === 'q' && v) v = v.trim(); if (v && !(k === 'sort' && (v === 'similar' || (v === 'pop' && !S.filter.like && !S.filter.col)))) params.set(k, Array.isArray(v) ? v.join(',') : String(v)); });
    try { history.replaceState(null, '', '#' + t.slug + '/' + sl.kind + (sl.n ? '/' + (sl.n + 1) : '') + (params.toString() ? '?' + params.toString() : '')); } catch (e) { /* file:// quirks */ }
  }

  /* ---------- HUD ---------- */
  function hud() {
    var t = T[S.i], sl = curSlide(t), pos = S.order.indexOf(S.i);
    $('hudName').textContent = t.name;
    $('hudAuthor').textContent = 'by ' + t.author;
    $('hudRank').textContent = rankDescription(t);
    if (!$('themeInfo').hidden) fillInfo();
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

  /* ---------- rankings, filters and visible grid ---------- */
  var HAS_STATS = !!(D.stats && D.stats.source !== 'none');
  var HASH_KEYS = ['view','sort','mode','hue','bg','con','leg','fmt','lic','src','video','walls','stars','since','by','fav','like','col','hdeg','L','cr','box','born','tc','an','twins','q'];
  var OPTIONS = {mode:['dark','light'],hue:['red','orange','yellow','green','teal','blue','purple','pink','neutral'],bg:['black','dark','dim','light'],con:['aa','aaa'],fmt:['4x','3x','legacy'],lic:['permissive','copyleft','other','none'],src:['builtin','official','curated','found']};
  var FILTER_NAMES = {mode:'mode',hue:'accent',bg:'background',con:'text contrast',leg:'every colour readable',fmt:'format',lic:'licence',src:'source',video:'video wallpapers',walls:'wallpapers ≥',stars:'stars ≥',since:'updated within',by:'author',fav:'starred',like:'similar to',col:'colour',hdeg:'accent hue',L:'background lightness',cr:'text contrast',box:'palette region',born:'published',tc:'terminal hue',an:'accent name',q:'search',twins:'twins hidden'};
  var RANKS = [
    ['pop',HAS_STATS?'Popularity':'Popularity (lists and recency)',HAS_STATS?'Stars, forks, how many curated lists carry it, and how recently it was updated.':'How many curated lists carry it and how recently it was updated.'],
    ['stars','Stars','GitHub stars.'],['trend','Trending','Stars per month since it appeared, damped for very new themes.'],['updated','Recently updated','Last push to the repository.'],['new','Newest','When the repository was created.'],['complete','Completeness','How much of the desktop the theme defines itself.'],['walls','Wallpapers','Number of wallpapers; ties go to the higher resolution.'],['legible','Legibility','Worst-case contrast of text and the twelve coloured terminal colours on the background.'],['colour','Colourfulness','How saturated the palette is on average.'],['unique','Uniqueness','How far its palette is from the closest other theme.'],['name','Name','Alphabetical.'],['hue','Accent hue','Rainbow order by accent colour, with neutrals last.']
  ].filter(function (r) { return HAS_STATS || ['stars','trend','new'].indexOf(r[0]) < 0; });
  var nowDate = new Date((D.stats && D.stats.fetched || D.generated || new Date().toISOString().slice(0,10)).slice(0,10) + 'T00:00:00Z');
  var vectorCache = {}, colourCache = {}, matchCache = {}, cardPositions = [], cardsBuilt = false;
  function oklab(hex) {
    if (colourCache[hex]) return colourCache[hex];
    var c = rgb(hex).map(function (v) { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055,2.4); });
    var l = Math.cbrt(.4122214708*c[0]+.5363325363*c[1]+.0514459929*c[2]), m = Math.cbrt(.2119034982*c[0]+.6806995451*c[1]+.1073969566*c[2]), b = Math.cbrt(.0883024619*c[0]+.2817188376*c[1]+.6299787005*c[2]);
    return colourCache[hex] = [.2104542553*l+.793617785*m-.0040720468*b,1.9779984951*l-2.428592205*m+.4505937099*b,.0259040371*l+.7827717662*m-.808675766*b];
  }
  function polar(hex) { var p = oklab(hex); return {L:p[0],C:Math.hypot(p[1],p[2]),h:(Math.atan2(p[2],p[1])*180/Math.PI+360)%360}; }
  function delta(a,b) { return 100*Math.hypot(a[0]-b[0],a[1]-b[1],a[2]-b[2]); }
  function vector(t) { return vectorCache[t.slug] || (vectorCache[t.slug] = [t.p.bg,t.p.fg,t.p.ac].concat(t.p.n,t.p.b).reduce(function(a,h,i){return a.concat(oklab(h).map(function(v){return v*100*Math.sqrt(i===0?4:i<3?2:1);}));},[])); }
  function distance(a,b) { var x=vector(a),y=vector(b),n=0; for(var i=0;i<x.length;i++)n+=Math.pow(x[i]-y[i],2); return Math.sqrt(n); }
  function metric(t,k) { if(t.m && t.m[k]!=null)return t.m[k]; var a=polar(t.p.ac); if(k==='L')return oklab(t.p.bg)[0]; if(k==='ah')return a.h; if(k==='ac')return a.C; if(k==='fam'){if(a.C<.04)return 'neutral'; var h=a.h;return h<15||h>=330?'pink':h<45?'red':h<80?'orange':h<118?'yellow':h<165?'green':h<225?'teal':h<285?'blue':'purple';} return null; }
  function tier(t) { return t.tier || (t.builtin?'builtin':'official'); }
  function rankKey() { return (S.filter.sort || 'pop').replace(/^-/,''); }
  function rankLabel() { var k=rankKey(),r=RANKS.filter(function(x){return x[0]===k;})[0];return r?r[1]:'Similarity'; }
  function isPinned(t,k) { return !!t.builtin && ['pop','stars','trend','new','updated'].indexOf(k||rankKey())>=0; }
  function rankNumber(t) { return S.order.filter(function(i){return !isPinned(T[i]);}).indexOf(t.i)+1; }
  function rankDescription(t) { return isPinned(t)?'Built into Omarchy':S.order.indexOf(t.i)<0?'Filtered out':rankValue(t,rankKey())==null?'no '+rankLabel().toLowerCase()+' data':'#'+rankNumber(t)+' by '+rankLabel().toLowerCase(); }
  function popularityDetails(t) {
    if(t.builtin)return 'Built into Omarchy · no repository statistics';
    return (t.st?t.st.s+' stars · '+t.st.f+' forks':'No repository statistics')+' · '+(t.ls||[]).filter(function(x){return x!=='search';}).length+' lists · last push '+(t.st&&t.st.u||t.lc||'unknown');
  }
  function rankValue(t,k) {
    if(isPinned(t,k))return null;
    if(k==='pop')return t.r && t.r.pop!=null?t.r.pop:null;
    if(k==='stars')return t.st?t.st.s:null;
    if(k==='trend')return t.r?t.r.trend:null;
    if(k==='updated')return t.st && t.st.u || t.lc || null;
    if(k==='new')return t.st?t.st.c:null;
    if(k==='walls')return (t.wn==null?t.walls.length:t.wn)+Math.min(.99,(t.wmp||0)/100);
    if(k==='complete')return metric(t,'comp'); if(k==='legible')return metric(t,'leg');
    if(k==='colour')return metric(t,'col'); if(k==='unique')return metric(t,'uniq');
    if(k==='hue')return t.hue==null?metric(t,'ah'):t.hue; if(k==='name')return t.name.toLowerCase();
    if(k==='similar')return similarityScores[t.i]; return null;
  }
  function rankChip(t) {
    var k=rankKey(),v=rankValue(t,k);if(v==null)return 'No data';
    if(k==='stars')return v+' stars';if(k==='trend')return Number(v).toFixed(1)+' / month';
    if(k==='legible')return rating(v)[0]+' '+Number(v).toFixed(1)+':1';
    if(k==='complete')return Math.round(v)+'%';if(k==='updated'||k==='new')return v;
    if(k==='walls')return (t.wn==null?t.walls.length:t.wn)+' wallpapers';if(k==='name')return 'A–Z';if(k==='hue')return Math.round(v)+'°';
    if(k==='similar')return Number(v).toFixed(1)+' distance';return Number(v).toFixed(1)+(k==='pop'?' score':'');
  }
  function validFilter(k,v) {
    if(typeof v!=='string')return '';v=v.trim();
    if(OPTIONS[k]) { var xs=v.toLowerCase().split(',');return xs.length && xs.every(function(x){return OPTIONS[k].indexOf(x)>=0;})?Array.from(new Set(xs)).join(','):''; }
    if(k==='sort'){var x=v.replace(/^-/,'');x=x==='contrast'?'legible':x==='format'?'name':x;return RANKS.some(function(r){return r[0]===x;})?(v[0]==='-'?'-':'')+x:'';}
    if(k==='twins')return v==='hide'?v:'';if(k==='q')return v;if(k==='by')return T.some(function(t){return t.author.toLowerCase()===v.toLowerCase();})?v:'';
    if(k==='like')return bySlug[v]?v:'';if(k==='col')return /^[a-f0-9]{6}$/i.test(v)?v.toLowerCase():'';
    if(k==='video'||k==='fav')return v==='1'?v:'';if(k==='leg')return v==='3'?v:'';
    if(k==='walls'||k==='stars')return (k!=='stars'||HAS_STATS)&&/^\d+$/.test(v)&&Number(v)>0&&Number(v)<=100000?String(Number(v)):'';
    if(k==='since')return ['30d','90d','1y'].indexOf(v)>=0?v:'';
    if(k==='an')return /^[a-z]+(?:,[a-z]+)*$/.test(v)&&v.split(',').every(function(x){return ACCENT_NAMES.indexOf(x)>=0;})?v:'';
    if(k==='born'){var b=v.split('..');return b.length===2&&b.every(function(x){return /^\d{4}-(0[1-9]|1[0-2])$/.test(x);})&&b[0]<=b[1]?v:'';}
    if(k==='tc'){var tc=/^([1-6]):(.+)$/.exec(v);return tc&&validFilter('hdeg',tc[2])?v:'';}
    if(['hdeg','L','cr'].indexOf(k)>=0){var r=/^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$/.exec(v),max=k==='hdeg'?360:k==='L'?1:21,min=k==='cr'?1:0;return r&&+r[1]>=min&&+r[2]>=min&&+r[1]<=max&&+r[2]<=max&&(k==='hdeg'||+r[1]<=+r[2])?v:'';}
    if(k==='box'){var p=v.split(',').map(Number);return p.length===4&&p.every(function(n){return Number.isFinite(n)&&n>=0&&n<=1;})&&p[0]<=p[2]&&p[1]<=p[3]?v:'';}
    return '';
  }
  function inRange(v,r,wrap) { var p=r.split('-').map(Number);return wrap&&p[0]>p[1]?v>=p[0]||v<=p[1]:v>=p[0]&&v<=p[1]; }
  function formatKind(t) {
    var format=t.format||'';
    if (/^4\.x\b|^colors\.toml \(4\.x\b/.test(format)) return '4x';
    if (/^3\.x\b|^colors\.toml\b/.test(format)) return '3x';
    return 'legacy';
  }
  function any(v,test) { return v.split(',').some(test); }
  function qualifies(t,f,skip) {
    for(var k in f){var v=f[k];if(!v||k===skip||['q','sort','like','col'].indexOf(k)>=0)continue;
      if(k==='twins'&&v==='hide'&&t.tw)return false;
      if(k==='mode'&&!any(v,function(x){return t.mode===x;}))return false;
      if(k==='hue'&&!any(v,function(x){return metric(t,'fam')===x;}))return false;
      if(k==='bg'){var L=metric(t,'L'),bg=L<.15?'black':L<.30?'dark':L<.55?'dim':'light';if(!any(v,function(x){return bg===x;}))return false;}
      if(k==='con'&&!any(v,function(x){return t.contrast>=(x==='aaa'?7:4.5);}))return false;
      if(k==='leg'&&metric(t,'leg')<3)return false;
      if(k==='fmt'&&!any(v,function(x){return formatKind(t)===x;}))return false;
      if(k==='lic'&&!any(v,function(x){return (t.licc||'none')===x;}))return false;
      if(k==='src'&&!any(v,function(x){return tier(t)===x;}))return false;
      if(k==='video'&&!(t.videos>0))return false;if(k==='fav'&&!favs[t.slug])return false;
      if(k==='walls'&&(t.wn==null?t.walls.length:t.wn)<+v)return false;
      if(k==='stars'&&(!t.st||t.st.s<+v))return false;
      if(k==='since'){var date=t.st&&t.st.u||t.lc,age=(nowDate-new Date(date+'T00:00:00Z'))/86400000;if(!date||!Number.isFinite(age)||age>(v==='1y'?365:parseInt(v,10)))return false;}
      if(k==='by'&&t.author.toLowerCase()!==v.toLowerCase())return false;
      if(k==='hdeg'&&(metric(t,'ac')<.04||!inRange(metric(t,'ah'),v,true)))return false;
      if(k==='L'&&!inRange(metric(t,'L'),v))return false;if(k==='cr'&&!inRange(t.contrast,v))return false;
      if(k==='box'){var b=v.split(',').map(Number);if(!t.m||t.m.x<b[0]||t.m.x>b[2]||t.m.y<b[1]||t.m.y>b[3])return false;}
      if(k==='born'){var r=v.split('..'),d=t.st&&t.st.c&&t.st.c.slice(0,7);if(!d||d<r[0]||d>r[1])return false;}
      if(k==='tc'){var tc=v.split(':'),slot=+tc[0];if(![t.p.n[slot],t.p.b[slot]].some(function(c){var p=polar(c);return p.C>=.04&&inRange(p.h,tc[1],true);}))return false;}
      if(k==='an'&&!any(v,function(x){return t.m&&t.m.an===x;}))return false;
    }
    if(skip!=='q'&&f.q){var hay=[t.name,t.slug,t.author,t.format,t.mode,tier(t),t.licence||t.license||t.lic||'',t.licc||'',t.m&&t.m.an||'',(t.ls||[]).map(function(x){return D.lists&&D.lists[x]||x;}).join(' ')].join(' ').toLowerCase();if(!f.q.toLowerCase().split(/\s+/).every(function(w){return hay.indexOf(w)>=0;}))return false;}
    return true;
  }
  var similarityScores={};
  function restriction(f) {
    var key=(f.like||'')+'|'+(f.col||'');if(matchCache[key])return matchCache[key];
    var scores={},allow=null;
    if(f.like){var target=bySlug[f.like];T.forEach(function(t){scores[t.i]=distance(t,target);});allow=T.map(function(t){return t.i;}).sort(function(a,b){return scores[a]-scores[b]||T[a].slug.localeCompare(T[b].slug);}).slice(0,25);}
    if(f.col){var p=oklab('#'+f.col),cs={};T.forEach(function(t){cs[t.i]=Math.min(delta(p,oklab(t.p.ac)),3+Math.min.apply(null,[1,2,3,4,5,6].reduce(function(a,i){return a.concat([t.p.n[i],t.p.b[i]]);},[]).map(function(c){return delta(p,oklab(c));})));});var order=T.map(function(t){return t.i;}).sort(function(a,b){return cs[a]-cs[b]||T[a].name.localeCompare(T[b].name);}),matches=order.filter(function(i){return cs[i]<=8;});matches=matches.length<12?order.slice(0,12):matches.slice(0,48);allow=allow?allow.filter(function(i){return matches.indexOf(i)>=0;}):matches;scores=cs;}
    return matchCache[key]={allow:allow,scores:scores};
  }
  function sorted(list,f,scores) {
    var s=(f.sort||'pop'),reverse=s[0]==='-',k=s.replace(/^-/,'');
    return list.sort(function(a,b){var x=T[a],y=T[b];if(isPinned(x,k)!==isPinned(y,k))return isPinned(x,k)?-1:1;var u=k==='similar'?scores[a]:rankValue(x,k),v=k==='similar'?scores[b]:rankValue(y,k),name=x.name.toLowerCase().localeCompare(y.name.toLowerCase())||x.slug.localeCompare(y.slug);if(u==null||v==null)return u==null&&v==null?name:u==null?1:-1;var cmp=0;
      if(k==='hue'){var gx=x.sat<.15,gy=y.sat<.15;if(gx!==gy)return gx?1:-1;cmp=gx?lum(x.p.bg)-lum(y.p.bg):u-v;}
      else if(k==='name')cmp=u.localeCompare(v);else if(k==='updated'||k==='new')cmp=v.localeCompare(u);else cmp=k==='similar'?u-v:v-u;return (reverse?-cmp:cmp)||name;
    });
  }
  function pipeline(f,skip) {
    var local=Object.assign({},f);if(skip)delete local[skip];
    var r=restriction(local),list=T.filter(function(t){return qualifies(t,local,skip);}).map(function(t){return t.i;});
    if(local.like){var target=bySlug[local.like];list.sort(function(a,b){return distance(T[a],target)-distance(T[b],target)||T[a].name.localeCompare(T[b].name);});list=list.slice(0,25);}
    if(local.col){list.sort(function(a,b){return r.scores[a]-r.scores[b]||T[a].name.localeCompare(T[b].name);});var matching=list.filter(function(i){return r.scores[i]<=8;});list=matching.length>=12?matching.slice(0,48):list.slice(0,12);}
    return sorted(list,local,r.scores);
  }
  function liftWords(q,finalize) {
    var remaining=q.replace(/\S+/g,function(w,offset){
      if(finalize===false&&offset+w.length===q.length)return w;
      var m=/^([^:]+):(.+)$/.exec(w);if(!m)return w;
      var v=validFilter(m[1],m[2]);if(!v)return w;
      S.filter[m[1]]=v;if((m[1]==='like'||m[1]==='col')&&!/\bsort:/.test(q))S.filter.sort='similar';return '';
    }).replace(/^\s+/,'').replace(/[ \t]{2,}/g,' ');
    return finalize===false?remaining:remaining.trim();
  }
  function readSearch(finalize) {
    var before=$('q').value,remaining=liftWords(before,finalize===true||!!(finalize&&finalize.inputType==='insertFromPaste')),lifted=before.trim()!==remaining.trim();
    if(finalize===true&&!lifted&&S.filter.q===remaining)return false;
    S.filter.q=remaining;$('q').value=remaining;applyFilter(finalize!==true && S.stats);S.gsel=cardPositions.findIndex(Boolean);markSel(true);return lifted;
  }
  function setFilter(k,v) { var x=validFilter(k,String(v));if(x)S.filter[k]=x;else delete S.filter[k];applyFilter(); }
  function clearFilters() { var sort=S.filter.sort;S.filter={q:'',sort:sort==='similar'?'pop':sort};applyFilter(); }
  function similar(slug) { S.filter.like=slug;delete S.filter.col;S.filter.sort='similar';openGrid(); }
  function cycleRank(d) { var keys=RANKS.map(function(x){return x[0];}),at=keys.indexOf(rankKey());S.filter.sort=keys[(at+d+keys.length)%keys.length];applyFilter();toast(rankLabel()); }
  function colourPrompt() { $('colourModal').hidden=false;$('colourHex').value=S.filter.col||T[S.i].p.ac.slice(1);$('colourPicker').value='#'+$('colourHex').value;$('colourHex').focus(); }
  function fillInfo() {
    var t=T[S.i],p=S.order.indexOf(S.i),info=$('themeInfo');info.replaceChildren();
    var h=el('h2');h.textContent=t.name;info.appendChild(h);if(t.tw){var badge=el('span','twin-badge');badge.textContent='same palette as '+(bySlug[t.tw]?bySlug[t.tw].name:t.tw);info.appendChild(badge);}
    var details=[['Rank',rankDescription(t)],['Value',isPinned(t)?'Unranked':rankChip(t)],['Stars / forks',t.st?t.st.s+' / '+t.st.f:'No repository stats'],['Created',t.st&&t.st.c||'No data'],['Updated',t.st&&t.st.u||t.lc||'No data'],['Source',tier(t)+(t.pos?' · official #'+t.pos:'')],['Lists',(t.ls||[]).map(function(x){return D.lists&&D.lists[x]||x;}).join(', ')||'None'],['Licence',t.licence||t.license||t.lic||t.licc||'None']];
    details.push(['Open issues',t.st&&t.st.i!=null?t.st.i:'No data']);
    if(t.st&&t.st.a)details.push(['Repository','Archived']);
    if(rankKey()==='pop'){
      var curated=(t.ls||[]).filter(function(x){return ['manual','awesome-a','awesome-w','hub-l'].indexOf(x)>=0;}).length,M=Math.min(1,(2*(tier(t)==='official'||(t.ls||[]).indexOf('official')>=0)+curated)/6),date=t.st?t.st.u:t.lc,months=date?Math.max(0,(nowDate-new Date(date+'T00:00:00Z'))/86400000/30.44):null,R=months==null?0:Math.pow(.5,months/6),parts=[];
      if(HAS_STATS)parts.push('Stars '+(t.st?70*Math.min(1,Math.log1p(t.st.s)/Math.log(151)):0).toFixed(1),'forks '+(t.st?5*Math.min(1,Math.log1p(t.st.f)/Math.log(21)):0).toFixed(1));
      parts.push('lists '+(M*(HAS_STATS?10:60)).toFixed(1),'recency '+(R*(HAS_STATS?15:40)).toFixed(1));
      details.push(['Popularity breakdown',t.builtin?'No repository score':parts.join(' + ')+' points.']);
    }
    var dl=el('dl');details.forEach(function(d){var dt=el('dt'),dd=el('dd');dt.textContent=d[0];dd.textContent=d[1];dl.append(dt,dd);});info.appendChild(dl);
  }
  function initFilters() {
    var wrap=$('filterControls');
    Object.keys(OPTIONS).filter(function(k){return k!=='mode';}).forEach(function(k){var row=el('div','filter-kind'),label=el('span','filter-label');label.textContent=FILTER_NAMES[k];row.appendChild(label);OPTIONS[k].forEach(function(v){var b=el('button','filter-option');b.dataset.filter=k;b.dataset.value=v;b.textContent=v;if(k==='hue'){var dot=el('i');dot.style.background=HUE_SWATCH[v];b.prepend(dot);}b.addEventListener('click',function(){var vals=(S.filter[k]||'').split(',').filter(Boolean),at=vals.indexOf(v);if(at>=0)vals.splice(at,1);else vals.push(v);setFilter(k,vals.join(','));});row.appendChild(b);});wrap.appendChild(row);});
    var twins=el('button','filter-option on');twins.id='fTwins';twins.textContent='Twins shown';twins.setAttribute('aria-pressed','true');twins.addEventListener('click',function(){setFilter('twins',S.filter.twins?'':'hide');});wrap.appendChild(twins);
    [['leg','Every colour readable','3'],['video','Video wallpapers','1']].forEach(function(x){var label=el('label','tog'),input=el('input');input.type='checkbox';input.dataset.filter=x[0];input.addEventListener('change',function(){setFilter(x[0],this.checked?x[2]:'');});label.append(input,document.createTextNode(x[1]));wrap.appendChild(label);});
    [['walls','Wallpapers',[1,3,5,10]],['stars','Stars',[1,5,10,25,50]],['since','Updated within',['30d','90d','1y']]].forEach(function(x){if(x[0]==='stars'&&!HAS_STATS)return;var label=el('label','sel'),select=el('select');select.dataset.filter=x[0];var all=el('option');all.value='';all.textContent='Any';select.appendChild(all);x[2].forEach(function(v){var o=el('option');o.value=v;o.textContent=x[0]==='since'?v:'At least '+v;select.appendChild(o);});select.addEventListener('change',function(){setFilter(x[0],this.value);});label.append(document.createTextNode(x[1]+' '),select);wrap.appendChild(label);});
    var by=el('input');by.placeholder='Author';by.setAttribute('list','authorNames');by.setAttribute('aria-label','Author');by.dataset.filter='by';by.addEventListener('change',function(){setFilter('by',this.value);});wrap.appendChild(by);var dl=el('datalist');dl.id='authorNames';var authors={};T.forEach(function(t){authors[t.author]=(authors[t.author]||0)+1;});Object.keys(authors).sort().filter(function(a){return authors[a]>=2;}).forEach(function(a){var o=el('option');o.value=a;dl.appendChild(o);});wrap.appendChild(dl);
    var col=el('button');col.textContent='Match a colour';col.addEventListener('click',colourPrompt);wrap.appendChild(col);
  }
  var HUE_SWATCH={red:'#e5484d',orange:'#f5842a',yellow:'#f2e04a',green:'#4caf50',teal:'#1f9e9e',blue:'#3b7de9',purple:'#9b3fc4',pink:'#f58fc0',neutral:'#808088'};
  function syncControls() {
    $('q').value=S.filter.q||'';$('fFav').setAttribute('aria-pressed',!!S.filter.fav);$('fFav').classList.toggle('on',!!S.filter.fav);var twins=$('fTwins');twins.classList.toggle('on',!S.filter.twins);twins.setAttribute('aria-pressed',!S.filter.twins);twins.textContent=S.filter.twins?'Twins hidden':'Twins shown';
    Array.from($('fMode').children).forEach(function(b){b.classList.toggle('on',b.dataset.v===(S.filter.mode||'all'));b.setAttribute('aria-pressed',b.dataset.v===(S.filter.mode||'all'));});
    var select=$('fSort'),menu=$('rankMenu');select.value=S.filter.sort;select.textContent='Sort: '+rankLabel()+((S.filter.sort||'')[0]==='-'?' ↑':' ↓');
    if(!menu.children.length){var ranks=RANKS.slice();ranks.unshift(['similar','Similarity']);ranks.forEach(function(r){var b=el('button');b.type='button';b.dataset.rank=r[0];b.setAttribute('role','menuitemradio');b.tabIndex=-1;b.textContent=r[1];b.addEventListener('click',function(){S.filter.sort=r[0];$('cards').scrollTop=0;closeRankMenu();applyFilter();select.focus();});menu.appendChild(b);});}
    Array.from(menu.children).forEach(function(b){b.hidden=b.dataset.rank==='similar'&&!S.filter.like&&!S.filter.col;b.setAttribute('aria-checked',b.dataset.rank===rankKey());});
    var ranking=RANKS.filter(function(r){return r[0]===rankKey();})[0];$('sortExplanation').textContent=ranking?ranking[2]:'Nearest palettes first. Choose a ranking to reorder these matches.';
    Array.from(document.querySelectorAll('[data-filter]')).forEach(function(c){var k=c.dataset.filter,values=(S.filter[k]||'').split(',');if(c.tagName==='BUTTON'){var v=c.dataset.value,f=Object.assign({},S.filter);f[k]=v;var n=f.like||f.col?pipeline(f).length:T.filter(function(t){return qualifies(t,f);}).length,active=values.indexOf(v)>=0;c.classList.toggle('on',active);c.classList.toggle('zero',n===0);c.setAttribute('aria-pressed',active);c.title=n+' matching themes';var count=c.querySelector('.filter-count');if(!count){count=el('span','filter-count');c.appendChild(count);}count.textContent=n;}else if(c.type==='checkbox')c.checked=!!S.filter[k];else c.value=S.filter[k]||'';});
    var active=$('activeFilters');active.replaceChildren();HASH_KEYS.forEach(function(k){if(k==='view'||k==='sort'||!S.filter[k]||(k==='q'&&!S.filter.q.trim()))return;var b=el('button','active-chip');b.textContent=(FILTER_NAMES[k]||k)+(['fav','video','leg','twins'].indexOf(k)>=0?'':' '+(k==='q'?S.filter[k].trim():S.filter[k]))+' ×';b.setAttribute('aria-label','Remove '+(FILTER_NAMES[k]||k)+' filter');b.addEventListener('click',function(){delete S.filter[k];if((k==='like'||k==='col')&&rankKey()==='similar'&&!S.filter.like&&!S.filter.col)S.filter.sort='pop';applyFilter();});active.appendChild(b);});if(active.children.length){var clear=el('button','clear-filters');clear.textContent='Clear all';clear.addEventListener('click',clearFilters);active.appendChild(clear);}active.hidden=!active.children.length;
  }
  var statsTimer=0;
  function applyFilter(debounceStats) {
    similarityScores=restriction(S.filter).scores;S.order=pipeline(S.filter);scoreMax=Math.max.apply(null,S.order.map(function(i){var v=rankValue(T[i],rankKey());return typeof v==='number'?v:0;}).concat([1]));var at=S.order.indexOf(S.i);S.gsel=at>=0&&rankValue(T[S.i],rankKey())!=null?at:0;
    $('gCount').textContent=S.order.length+' of '+N+' - '+favList().length+' starred';$('statsCount').textContent=S.order.length+' of '+N+' themes';syncControls();
    if(S.grid){layoutCards();if(!cardPositions[S.gsel])S.gsel=cardPositions.findIndex(Boolean);renderCards();}clearTimeout(statsTimer);if(S.stats){if(debounceStats)statsTimer=setTimeout(function(){if(S.stats)renderStats();},120);else renderStats();}if(N)hud();if(N)hash();
  }
  function scoreMarkup(t) {
    if(isPinned(t))return '<span class="builtin-note">Included with Omarchy</span>';
    var value=rankValue(t,rankKey()),maximum=scoreMax||1,pct=typeof value==='number'?clamp(value/maximum*100,0,100):null;
    return '<span class="rank-value" title="'+esc(rankKey()==='pop'?popularityDetails(t):rankLabel())+'"><span class="rank-number">'+(value==null?'':'#'+rankNumber(t)+' ')+'</span>'+esc(rankChip(t))+(pct==null?'':'<i class="score-meter" aria-hidden="true"><i style="width:'+pct.toFixed(1)+'%"></i></i>')+'</span>';
  }
  var scoreMax=1;
  function cardNode(t) {
    var c=el('div','card');c.dataset.i=t.i;c.tabIndex=0;c.setAttribute('role','button');c.setAttribute('aria-label',t.name+', '+rankChip(t));c.style.setProperty('--bg',t.p.bg);
    var strip=[t.p.bg,t.p.fg,t.p.ac].concat(t.p.n.slice(1,7)).map(function(h){return '<i style="background:'+esc(h)+'"></i>';}).join('');
    c.innerHTML=(t.thumb?'<img class="th" loading="lazy" decoding="async" alt="" src="'+esc(t.thumb)+'">':'<div class="th"></div>')+'<div class="strip">'+strip+'</div><div class="nm"><b>'+esc(t.name)+'</b><button class="author-link">'+esc(t.author)+'</button></div><div class="card-meta">'+scoreMarkup(t)+'<button class="similar" title="Find similar palettes">Similar</button></div><div class="bd">'+(t.mode==='light'?'<span>light</span>':'')+(t.builtin?'<span>built-in</span>':'')+(t.st&&t.st.a?'<span>archived</span>':'')+(t.tw?'<span class="twin-badge">same palette as '+esc(bySlug[t.tw]?bySlug[t.tw].name:t.tw)+'</span>':'')+'</div><button class="st'+(favs[t.slug]?' on':'')+'" title="Star '+esc(t.name)+'">'+(favs[t.slug]?'★':'☆')+'</button>';
    return c;
  }
  function layoutCards() {
    var wrap=$('cards'),w=wrap.clientWidth-32,cols=Math.max(1,Math.floor((w+14)/244)),cw=(w-14*(cols-1))/cols,h=cw*9/16+106,y=16;
    var pinned=S.order.filter(function(i){return isPinned(T[i]);}),ranked=S.order.filter(function(i){return !isPinned(T[i]);});cardPositions=[];
    wrap._layout={cols:cols,pinned:pinned.length,divider:null};
    function group(list){list.forEach(function(i,n){cardPositions[S.order.indexOf(i)]={i:i,x:16+(n%cols)*(cw+14),y:y+Math.floor(n/cols)*h,w:cw,h:h-14};});y+=Math.ceil(list.length/cols)*h;}
    if(pinned.length){y+=44;if(ui.builtinsOpen!==false)group(pinned);y+=16;}
    var missing=ranked.findIndex(function(i){return rankValue(T[i],rankKey())==null;});
    if(missing>=0){group(ranked.slice(0,missing));wrap._layout.divider=y;y+=40;group(ranked.slice(missing));}else group(ranked);
    wrap._layout.height=Math.max(150,y+16);
  }
  function renderCards() {
    if(!S.grid)return;var wrap=$('cards');if(!wrap._layout)layoutCards();var top=wrap.scrollTop,bottom=top+wrap.clientHeight,frag=document.createDocumentFragment(),spacer=el('div','grid-spacer');spacer.style.height=wrap._layout.height+'px';frag.appendChild(spacer);
    cardPositions.forEach(function(p,n){if(p.y+p.h<top-300||p.y>bottom+300)return;var c=cardNode(T[p.i]);c.style.left=p.x+'px';c.style.top=p.y+'px';c.style.width=p.w+'px';c.style.height=p.h+'px';c.classList.toggle('pick',n===S.gsel);c.classList.toggle('cur',p.i===S.i);frag.appendChild(c);});
    if(wrap._layout.pinned){var b=el('button','builtin-disclosure');b.id='builtinsToggle';b.setAttribute('aria-expanded',ui.builtinsOpen!==false);b.textContent=(ui.builtinsOpen===false?'▸ ':'▾ ')+'Built into Omarchy';var count=el('span');count.textContent=wrap._layout.pinned+' themes · no repository rankings';b.appendChild(count);b.addEventListener('click',function(){ui.builtinsOpen=ui.builtinsOpen===false;store.set(UI_KEY,ui);layoutCards();if(!cardPositions[S.gsel])S.gsel=cardPositions.findIndex(Boolean);renderCards();$('builtinsToggle').focus({preventScroll:true});});frag.appendChild(b);}
    if(wrap._layout.divider!=null){var d=el('div','no-data-divider');d.textContent='No data';d.style.top=wrap._layout.divider+'px';frag.appendChild(d);}if(!S.order.length)frag.appendChild(el('div','empty','No theme matches. Try removing a filter.'));
    var active=document.activeElement,focused=active&&active.closest('.card'),index=focused&&focused.dataset.i,child=active&&active.closest('.author-link,.similar,.st'),selector=child&&(child.classList.contains('author-link')?'.author-link':child.classList.contains('similar')?'.similar':'.st');wrap.replaceChildren(frag);if(index){var restored=wrap.querySelector('.card[data-i="'+index+'"]');if(restored)(selector&&restored.querySelector(selector)||restored).focus({preventScroll:true});}cardsBuilt=true;
  }
  function markSel(scroll) { if(!S.grid)return;if(scroll&&cardPositions[S.gsel]){var p=cardPositions[S.gsel],wrap=$('cards');if(p.y<wrap.scrollTop)wrap.scrollTop=p.y-16;else if(p.y+p.h>wrap.scrollTop+wrap.clientHeight)wrap.scrollTop=p.y+p.h-wrap.clientHeight+16;}renderCards(); }
  function openGrid() { S.stats=false;S.grid=true;$('stats').hidden=true;$('grid').hidden=false;$('grid').prepend($('galleryToolbar'));document.body.classList.remove('idle');$('chartTooltip').hidden=true;applyFilter();if(rankValue(T[S.i],rankKey())==null||S.order.indexOf(S.i)<0){S.gsel=cardPositions.findIndex(Boolean);$('cards').scrollTop=0;renderCards();}else markSel(true);$('q').focus(); }
  function closeGrid(openIdx) { S.grid=false;$('grid').hidden=true;$('q').blur();if(openIdx!=null&&openIdx!==S.i)go(openIdx);else render(); }
  function gridCols() { return $('cards')._layout?$('cards')._layout.cols:1; }
  function gridMove(d) { var visible=cardPositions.map(function(p,n){return p?n:null;}).filter(function(n){return n!=null;});if(!visible.length)return;var at=visible.indexOf(S.gsel);S.gsel=visible[clamp(at+d,0,visible.length-1)];markSel(true); }
  function openStats() { S.grid=false;S.stats=true;$('grid').hidden=true;$('stats').hidden=false;$('statsToolbar').appendChild($('galleryToolbar'));document.body.classList.remove('idle');applyFilter();$('statsCards').querySelector('.viz-card').focus(); }
  function closeStats() { S.stats=false;$('stats').hidden=true;$('chartTooltip').hidden=true;render(); }

  /* ---------- shared chart anatomy and interactions ---------- */
  var ACCENT_NAMES = ['red','crimson','coral','orange','amber','gold','yellow','lime','green','emerald','mint','teal','cyan','sky','blue','cobalt','indigo','lavender','violet','purple','magenta','pink','rose','peach','sand','brown','olive','white','silver','grey','black'];
  var SVG_NS = el('div','','<svg></svg>').firstChild.namespaceURI;
  var chartTables={}, quiltMode=true, tooltipTheme=null;
  function svgNode(tag,attrs,text) { var n=document.createElementNS(SVG_NS,tag);Object.keys(attrs||{}).forEach(function(k){n.setAttribute(k,attrs[k]);});if(text!=null)n.textContent=text;return n; }
  function draw(parent,tag,attrs,text) { var n=svgNode(tag,attrs,text);parent.appendChild(n);return n; }
  function svgChart(body,w,h,label) { var svg=draw(body,'svg',{viewBox:'0 0 '+w+' '+h,role:'img','aria-label':label,'class':'chart-svg'});svg._marks=[];svg.addEventListener('keydown',function(e){if(!/^Arrow/.test(e.key))return;var current=e.target.closest('[data-mark]'),marks=svg._marks;if(!marks.length)return;var at=marks.indexOf(current),next;
      if(svg._lattice && current){var pos=current._point,dx=e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0,dy=e.key==='ArrowUp'?-1:e.key==='ArrowDown'?1:0;next=marks.filter(function(m){return dx?(m._point.x-pos.x)*dx>0:(m._point.y-pos.y)*dy>0;}).sort(function(a,b){function score(m){return Math.hypot(m._point.x-pos.x,m._point.y-pos.y)+3*Math.abs(dx?m._point.y-pos.y:m._point.x-pos.x);}return score(a)-score(b);})[0];}
      if(!next)next=marks[(at+(e.key==='ArrowLeft'||e.key==='ArrowUp'?-1:1)+marks.length)%marks.length];marks.forEach(function(m){m.setAttribute('tabindex',m===next?'0':'-1');});next.focus();e.preventDefault();
    });return svg; }
  function chartCard(id,title,caption) { var card=el('article','viz-card');card.id='viz-'+id;card.tabIndex=0;card.dataset.chart=id;var head=el('div','viz-head'),h=el('h2'),btn=el('button','table-toggle');h.textContent=title;btn.textContent=chartTables[id]?'Chart':'Table';btn.setAttribute('aria-pressed',!!chartTables[id]);head.append(h,btn);var cap=el('p','viz-caption');cap.textContent=caption;var body=el('div','viz-body');card.append(head,cap,body);$('statsCards').appendChild(card);card._body=body;card._rows=[];
    btn.addEventListener('click',function(){chartTables[id]=!chartTables[id];renderStats(id);$('viz-'+id).querySelector('.table-toggle').focus();});return card; }
  function tableTwin(card,heads,rows) { card._rows=rows;if(!chartTables[card.dataset.chart])return false;var table=el('table','viz-table'),thead=el('thead'),tr=el('tr');heads.forEach(function(h){var th=el('th');th.scope='col';th.textContent=h;tr.appendChild(th);});thead.appendChild(tr);table.appendChild(thead);var tbody=el('tbody');rows.slice(0,200).forEach(function(row){var tr=el('tr');row.cells.forEach(function(v){var td=el('td');td.textContent=v;tr.appendChild(td);});if(row.action){tr.tabIndex=0;tr.setAttribute('role','button');tr.addEventListener('click',row.action);tr.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){row.action();e.preventDefault();}});}tbody.appendChild(tr);});table.appendChild(tbody);card._body.appendChild(table);if(rows.length>200){var b=el('button','show-all-grid');b.textContent='Show all '+rows.length+' in grid';b.addEventListener('click',openGrid);card._body.appendChild(b);}return true; }
  function showTooltip(value,name,t,event) { var tip=$('chartTooltip');tip.replaceChildren();var strong=el('strong'),label=el('span');strong.textContent=value;label.textContent=name;tip.append(strong,label);if(t){var strip=el('div','tooltip-palette');[t.p.bg,t.p.fg,t.p.ac].concat(t.p.n.slice(1,7)).forEach(function(h){var i=el('i');i.style.background=h;strip.appendChild(i);});tip.appendChild(strip);}tip.hidden=false;tooltipTheme=t;
    var rect=event&&event.currentTarget&&event.currentTarget.getBoundingClientRect(),x=event&&event.clientX!=null?event.clientX:rect?rect.left+rect.width/2:innerWidth/2,y=event&&event.clientY!=null?event.clientY:rect?rect.top:80;tip.style.left=Math.max(8,Math.min(innerWidth-tip.offsetWidth-8,x+14))+'px';tip.style.top=Math.max(8,Math.min(innerHeight-tip.offsetHeight-8,y+14))+'px'; }
  function themeAction(t) { return function(){S.stats=false;$('stats').hidden=true;$('chartTooltip').hidden=true;if(t.i!==S.i)go(t.i);else render();}; }
  function mark(svg,node,x,y,value,name,t,action) { node.dataset.mark='1';node.setAttribute('tabindex',svg._marks.length?'-1':'0');node.setAttribute('role','button');node.setAttribute('aria-label',value+', '+name);node._point={x:x,y:y};node._value=value;node._name=name;node._theme=t;node._action=action;svg._marks.push(node);node.addEventListener('pointermove',function(e){showTooltip(value,name,t,e);});node.addEventListener('focus',function(e){showTooltip(value,name,t,e);});node.addEventListener('pointerleave',function(){$('chartTooltip').hidden=true;});node.addEventListener('blur',function(){$('chartTooltip').hidden=true;});node.addEventListener('click',function(){if(!svg._brushed&&action)action();});node.addEventListener('keydown',function(e){if((e.key==='Enter'||e.key===' ')&&action){action();e.preventDefault();}});return node; }
  function datum(svg,tag,attrs,colour) { var g=draw(svg,'g',{});if(contrast(colour,'#15151a')<1.6)draw(g,tag,Object.assign({},attrs,{fill:colour,stroke:'var(--viz-axis)','stroke-width':4}));draw(g,tag,Object.assign({},attrs,{fill:colour,stroke:'var(--viz-surface)','stroke-width':2}));return g; }
  function nearest(svg) { svg.addEventListener('pointermove',function(e){if(svg._drag)return;var p=svgPoint(svg,e),best=null,d=24;svg._marks.forEach(function(m){var v=Math.hypot(m._point.x-p.x,m._point.y-p.y);if(v<d){d=v;best=m;}});svg._nearest=best;if(best)showTooltip(best._value,best._name,best._theme,e);else $('chartTooltip').hidden=true;});svg.addEventListener('pointerleave',function(){$('chartTooltip').hidden=true;});svg.addEventListener('click',function(e){if(!svg._brushed&&!e.target.closest('[data-mark]')&&svg._nearest&&svg._nearest._action)svg._nearest._action();}); }
  function svgPoint(svg,e) { var p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;var c=svg.getScreenCTM();return c?p.matrixTransform(c.inverse()):{x:0,y:0}; }
  function brush(svg,bounds,done,arc) {
    var start=null,overlay=null;svg.style.touchAction='none';
    svg.addEventListener('pointerdown',function(e){
      if(e.button!==0)return;var p=svgPoint(svg,e);
      if(p.x<bounds.x||p.x>bounds.x+bounds.w||p.y<bounds.y||p.y>bounds.y+bounds.h)return;
      if(arc&&Math.hypot(p.x-arc.x,p.y-arc.y)<=arc.minRadius)return;
      start=p;svg._brushed=false;svg._drag=false;
    });
    svg.addEventListener('pointermove',function(e){
      if(!start)return;var p=svgPoint(svg,e);if(Math.hypot(p.x-start.x,p.y-start.y)<5&&!overlay)return;
      svg._brushed=true;svg._drag=true;$('chartTooltip').hidden=true;
      if(!overlay){
        try{svg.setPointerCapture(e.pointerId);}catch(err){}
        overlay=draw(svg,arc?'path':'rect',{'class':'brush-selection',fill:'var(--viz-s1)','fill-opacity':.15,stroke:'var(--viz-s1)','stroke-width':1,'pointer-events':'none'});
      }
      if(arc){var a=Math.atan2(start.y-arc.y,start.x-arc.x),b=Math.atan2(p.y-arc.y,p.x-arc.x),r=arc.r;overlay.setAttribute('d','M '+(arc.x+Math.cos(a)*r)+' '+(arc.y+Math.sin(a)*r)+' A '+r+' '+r+' 0 '+(((b-a+Math.PI*2)%(Math.PI*2))>Math.PI?1:0)+' 1 '+(arc.x+Math.cos(b)*r)+' '+(arc.y+Math.sin(b)*r));overlay.setAttribute('stroke-width',12);overlay.setAttribute('fill','none');}
      else{overlay.setAttribute('x',Math.min(start.x,p.x));overlay.setAttribute('y',Math.min(start.y,p.y));overlay.setAttribute('width',Math.abs(p.x-start.x));overlay.setAttribute('height',Math.abs(p.y-start.y));}
    });
    svg.addEventListener('pointerup',function(e){
      if(!start)return;var a=start,b=svgPoint(svg,e),dragged=svg._brushed;start=null;svg._drag=false;
      if(overlay){overlay.remove();overlay=null;}
      if(dragged){e.preventDefault();done(a,b);setTimeout(function(){svg._brushed=false;},80);}
    });
    svg.addEventListener('pointerleave',function(){if(!overlay)start=null;});
    svg.addEventListener('pointercancel',function(){start=null;svg._drag=false;svg._brushed=false;if(overlay)overlay.remove();overlay=null;});
  }
  function label(svg,x,y,text,cls,anchor) { return draw(svg,'text',{x:x,y:y,'class':cls||'axis-label','text-anchor':anchor||'start'},text); }
  function gridLine(svg,x1,y1,x2,y2,axis) { return draw(svg,'line',{x1:x1,y1:y1,x2:x2,y2:y2,stroke:axis?'var(--viz-axis)':'var(--viz-grid)','stroke-width':1}); }
  function bar(svg,x,y,w,h,colour,vertical) {
    if(w<=0||h<=0)return;var r=Math.min(4,w/2,h/2),d=vertical?
      'M '+x+' '+(y+h)+' V '+(y+r)+' Q '+x+' '+y+' '+(x+r)+' '+y+' H '+(x+w-r)+' Q '+(x+w)+' '+y+' '+(x+w)+' '+(y+r)+' V '+(y+h)+' Z':
      'M '+x+' '+y+' H '+(x+w-r)+' Q '+(x+w)+' '+y+' '+(x+w)+' '+(y+r)+' V '+(y+h-r)+' Q '+(x+w)+' '+(y+h)+' '+(x+w-r)+' '+(y+h)+' H '+x+' Z';
    return draw(svg,'path',{d:d,fill:colour});
  }
  function emptyChart(card) { var p=el('p','chart-empty');p.textContent='Needs star data. Rebuild with a GitHub token.';card._body.appendChild(p); }
  function percentage(n,total) { return total?Math.round(100*n/total)+'%':'0%'; }
  function clamp(x,a,b) { return Math.max(a,Math.min(b,x)); }
  function setRanges(values) { Object.keys(values).forEach(function(k){S.filter[k]=values[k];});applyFilter(); }
  function numbersCard(ts) {
    var card=chartCard('numbers','The collection in view','Every figure follows the filters above.'),n=ts.length,dark=ts.filter(function(t){return t.mode==='dark';}).length,four=ts.filter(function(t){return formatKind(t)==='4x';}).length,licensed=ts.filter(function(t){return ['permissive','copyleft'].indexOf(t.licc)>=0;}).length,aa=ts.filter(function(t){return t.contrast>=4.5;}).length,starred=ts.filter(function(t){return t.st&&t.st.s>=1;}).length,video=ts.filter(function(t){return t.videos>0;}).length,wn=ts.map(function(t){return t.wn==null?t.walls.length:t.wn;}).sort(function(a,b){return a-b;}),median=n?(wn[Math.floor((n-1)/2)]+wn[Math.floor(n/2)])/2:0;
    var rows=[{cells:['Themes',n+' of '+N]},{cells:['Dark',dark],action:function(){setFilter('mode','dark');}},{cells:['Light',n-dark],action:function(){setFilter('mode','light');}},{cells:['colors.toml 4.x',four+' · '+percentage(four,n)],action:function(){setFilter('fmt','4x');}},{cells:['Licensed',licensed+' · '+percentage(licensed,n)],action:function(){setFilter('lic','permissive,copyleft');}},{cells:['Readable (AA)',percentage(aa,n)],action:function(){setFilter('con','aa');}},{cells:['Starred at least once',HAS_STATS?percentage(starred,n):'No star data'],action:HAS_STATS?function(){setFilter('stars','1');}:null},{cells:['Median wallpapers',median]},{cells:['Video wallpapers',video],action:function(){setFilter('video','1');}}];
    finding(card,percentage(dark,n)+' of this selection uses a dark background.');
    if(tableTwin(card,['Measure','Value'],rows))return;
    var tiles=el('div','number-tiles');rows.forEach(function(row,i){if(i===2)return;var tile=el(row.action?'button':'div','number-tile'+(i===0?' hero-number':'')),title=el('span'),value=el('strong'),sub=el('small');title.textContent=i===1?'Dark / Light':row.cells[0];value.textContent=i===0?n:i===1?dark+' / '+(n-dark):row.cells[1];sub.textContent=i===0?'of '+N+' themes':i===1?'Click for dark themes':i===6&&!HAS_STATS?'Rebuild with a GitHub token.':'';tile.append(title,value,sub);if(row.action)tile.addEventListener('click',row.action);if(i===1){var meter=el('div','mode-meter'),fill=el('i');fill.style.width=percentage(dark,n);meter.appendChild(fill);tile.appendChild(meter);var light=el('button','light-tile-link');light.textContent='Show light';light.addEventListener('click',function(e){e.stopPropagation();setFilter('mode','light');});var group=el('div','mode-tile');group.append(tile,light);tiles.appendChild(group);}else tiles.appendChild(tile);});card._body.appendChild(tiles);
  }
  function finding(card,text) { var p=el('p','viz-finding');p.textContent=text;card.insertBefore(p,card.querySelector('.viz-caption')); }
  function quiltLayout(ts) {
    var columns=Math.max(1,Math.ceil(Math.sqrt(ts.length*1.5))),rows=Math.max(1,Math.ceil(ts.length/columns)),cell=Math.min(560/columns,350/rows),left=(650-columns*cell)/2,top=(450-rows*cell)/2,positions={};
    var sorted=ts.slice().sort(function(a,b){return (b.m&&b.m.y||0)-(a.m&&a.m.y||0)||a.slug.localeCompare(b.slug);});
    for(var row=0;row<rows;row++)sorted.slice(row*columns,(row+1)*columns).sort(function(a,b){return (a.m&&a.m.x||0)-(b.m&&b.m.x||0)||a.slug.localeCompare(b.slug);}).forEach(function(t,col){positions[t.slug]={x:left+col*cell,y:top+row*cell,size:Math.max(3,cell-2)};});
    return positions;
  }
  function wheelCard(ts) {
    var card=chartCard('wheel','Accent hue wheel','Angle is hue. Radius is chroma, from neutral to saturated. Drag the rim to select an arc.'),rows=ts.map(function(t){return {cells:[t.name,Math.round(metric(t,'ah'))+'°',Number(metric(t,'ac')).toFixed(3),metric(t,'fam')],action:themeAction(t)};});
    finding(card,percentage(ts.filter(function(t){return ['blue','purple'].indexOf(metric(t,'fam'))>=0;}).length,ts.length)+' of themes have blue or purple accents.');
    if(tableTwin(card,['Theme','Hue','Chroma','Family'],rows))return;
    var svg=svgChart(card._body,650,450,'Accent hue and chroma'),cx=325,cy=222,r=150,bins=Array.from({length:72},function(){return 0;});ts.forEach(function(t){if(metric(t,'ac')>=.04)bins[Math.floor(metric(t,'ah')/5)%72]++;});var max=Math.max.apply(null,bins.concat([1]));
    [.1,.2,.3].forEach(function(c){draw(svg,'circle',{cx:cx,cy:cy,r:r*c/.32,fill:'none',stroke:'var(--viz-grid)','stroke-width':1});});
    bins.forEach(function(n,i){var a=(i*5+2.5)*Math.PI/180,len=n/max*30;draw(svg,'line',{x1:cx+Math.cos(a)*(r+7),y1:cy+Math.sin(a)*(r+7),x2:cx+Math.cos(a)*(r+7+len),y2:cy+Math.sin(a)*(r+7+len),stroke:'var(--viz-deemph)','stroke-width':4});});
    if(S.filter.hdeg){var arc=S.filter.hdeg.split('-').map(Number),a=arc[0]*Math.PI/180,b=arc[1]*Math.PI/180;draw(svg,'path',{d:'M '+(cx+Math.cos(a)*(r+32))+' '+(cy+Math.sin(a)*(r+32))+' A '+(r+32)+' '+(r+32)+' 0 '+(((arc[1]-arc[0]+360)%360)>180?1:0)+' 1 '+(cx+Math.cos(b)*(r+32))+' '+(cy+Math.sin(b)*(r+32)),fill:'none',stroke:'var(--viz-s1)','stroke-width':4});}
    var neutrals=ts.filter(function(t){return metric(t,'ac')<.04;}).sort(function(a,b){return metric(a,'L')-metric(b,'L');});
    ts.forEach(function(t){var h=metric(t,'ah')*Math.PI/180,rad=clamp(metric(t,'ac')/.32,0,1)*r,x=cx+Math.cos(h)*rad,y=cy+Math.sin(h)*rad;if(metric(t,'ac')<.04){var j=neutrals.indexOf(t),cols=12;x=cx-44+(j%cols)*8;y=cy+Math.floor(j/cols)*9;}var g=datum(svg,'circle',{cx:x,cy:y,r:5},t.p.ac);mark(svg,g,x,y,Math.round(metric(t,'ah'))+'° · chroma '+Number(metric(t,'ac')).toFixed(3),t.name,t,themeAction(t));});
    [.1,.2,.3].forEach(function(c){var text=label(svg,cx+5,cy-r*c/.32+12,c.toFixed(1),'axis-label');text.style.cssText='paint-order:stroke;stroke:var(--viz-surface);stroke-width:3px';});
    [['pink',0],['red',30],['orange',62],['yellow',99],['green',142],['teal',195],['blue',255],['purple',306]].forEach(function(f){var a=f[1]*Math.PI/180,x=cx+Math.cos(a)*(r+58),y=cy+Math.sin(a)*(r+58),text=label(svg,x,y,f[0],'family-label','middle');mark(svg,text,x,y,f[0]+' accents','Filter by family',null,function(){setFilter('hue',f[0]);});});nearest(svg);brush(svg,{x:cx-r-35,y:cy-r-35,w:2*(r+35),h:2*(r+35)},function(a,b){function angle(p){return Math.round((Math.atan2(p.y-cy,p.x-cx)*180/Math.PI+360)%360);}setFilter('hdeg',angle(a)+'-'+angle(b));},{x:cx,y:cy,r:r+28,minRadius:r-10});
  }
  function mapCard(ts) {
    var pca=D.viz&&D.viz.pca||[0,0],card=chartCard('map','Palette neighbourhoods','Position shows palette similarity. The axes have no units; left to right explains '+Math.round(pca[0]*100)+'% of the variation, bottom to top '+Math.round(pca[1]*100)+'%. Quilt packs similar palettes into a compact lattice. Outlined tiles are built-ins. Use True positions to drag a palette region.'),toggle=el('button','quilt-toggle');toggle.textContent=quiltMode?'True positions':'Quilt';toggle.addEventListener('click',function(){quiltMode=!quiltMode;renderStats('map');});card.querySelector('.viz-head').insertBefore(toggle,card.querySelector('.table-toggle'));
    finding(card,'The first two palette dimensions explain '+Math.round((pca[0]+pca[1])*100)+'% of colour variation in the full catalogue.');
    var rows=ts.map(function(t){return {cells:[t.name,t.m?t.m.x:'No data',t.m?t.m.y:'No data',t.p.bg,t.p.ac],action:themeAction(t)};});if(tableTwin(card,['Theme','Position X','Position Y','Background','Accent'],rows))return;
    var packed=quiltLayout(ts);
    var svg=svgChart(card._body,650,450,'Map of palette similarity');svg._lattice=true;
    ts.forEach(function(t,j){var m=t.m||{},x=quiltMode?packed[t.slug].x:45+(m.x==null?.5:m.x)*560,y=quiltMode?packed[t.slug].y:25+(1-(m.y==null?.5:m.y))*390,size=quiltMode?packed[t.slug].size:14,g=datum(svg,'rect',{x:x,y:y,width:size,height:size,rx:Math.min(3,size/8)},t.p.bg);draw(g,'rect',{x:x+1,y:y+1,width:Math.max(1,size-2),height:Math.max(1,size/3),rx:1,fill:t.p.ac});if(size>=30)t.p.n.slice(1,7).forEach(function(c,k){draw(g,'circle',{cx:x+(k+.5)*size/6,cy:y+size*.75,r:size/18,fill:c});});if(t.builtin)draw(g,'rect',{x:x,y:y,width:size,height:size,rx:Math.min(3,size/8),fill:'none',stroke:'var(--viz-ink2)','stroke-width':1.5});mark(svg,g,x+size/2,y+size/2,'Palette position '+(m.x==null?'unknown':m.x.toFixed(2)+', '+m.y.toFixed(2)),t.name,t,themeAction(t));});
    nearest(svg);
    brush(svg,{x:0,y:0,w:650,h:450},function(a,b){
      if(quiltMode){toast('Switch to True positions to select a palette region.');return;}
      var x0=Math.min(a.x,b.x),x1=Math.max(a.x,b.x),y0=Math.min(a.y,b.y),y1=Math.max(a.y,b.y);
      setFilter('box',[clamp((x0-45)/560,0,1),clamp(1-(y1-25)/390,0,1),clamp((x1-45)/560,0,1),clamp(1-(y0-25)/390,0,1)].map(function(v){return v.toFixed(4);}).join(','));
    });
  }
  var scatterEnvelope=[];
  for(var n=0;n<=100;n++){var lightness=n/100,bestContrast=0;for(var sample=0;sample<=255;sample++){var hex='#'+sample.toString(16).padStart(2,'0').repeat(3);if(Math.abs(oklab(hex)[0]-lightness)<.007)bestContrast=Math.max(bestContrast,contrast(hex,'#000000'),contrast(hex,'#ffffff'));}if(bestContrast)scatterEnvelope.push([lightness,bestContrast]);}
  function scatterCard(ts) {
    var card=chartCard('scatter','Lightness and text contrast','Each square is the background; its dot is the text. Drag a rectangle to select both ranges.'),rows=ts.map(function(t){return {cells:[t.name,Number(metric(t,'L')).toFixed(3),t.contrast.toFixed(2)+':1',rating(t.contrast)[0]],action:themeAction(t)};});
    finding(card,percentage(ts.filter(function(t){return t.contrast>=4.5;}).length,ts.length)+' of themes meet AA contrast for foreground text.');
    if(tableTwin(card,['Theme','Lightness','Contrast','Rating'],rows))return;
    var svg=svgChart(card._body,650,450,'Background lightness against text contrast'),x=function(v){return 55+v*520;},y=function(v){return 390-(v-1)/20*350;};[1,5,10,15,21].forEach(function(v){gridLine(svg,55,y(v),575,y(v));label(svg,44,y(v)+4,v+':1',null,'end');});[0,.25,.5,.75,1].forEach(function(v){gridLine(svg,x(v),390,x(v),395,true);label(svg,x(v),414,String(v),null,'middle');});label(svg,315,440,'Background OKLab lightness',null,'middle');label(svg,55,20,'Text contrast');
    [4.5,7].forEach(function(v){gridLine(svg,55,y(v),575,y(v),true);label(svg,582,y(v)+4,(v===4.5?'AA ':'AAA ')+v+':1');});
    draw(svg,'path',{d:scatterEnvelope.map(function(p,i){return (i?'L ':'M ')+x(p[0])+' '+y(p[1]);}).join(' '),fill:'none',stroke:'var(--viz-axis)','stroke-width':1});
    ts.forEach(function(t){var px=x(metric(t,'L')),py=y(t.contrast),g=datum(svg,'rect',{x:px-6,y:py-6,width:12,height:12,rx:3},t.p.bg);draw(g,'circle',{cx:px,cy:py,r:2.5,fill:t.p.fg});mark(svg,g,px,py,t.contrast.toFixed(2)+':1 · '+rating(t.contrast)[0],t.name,t,themeAction(t));});nearest(svg);
    brush(svg,{x:55,y:40,w:520,h:350},function(a,b){setRanges({L:[clamp((Math.min(a.x,b.x)-55)/520,0,1),clamp((Math.max(a.x,b.x)-55)/520,0,1)].map(function(v){return v.toFixed(3);}).join('-'),cr:[clamp(1+(390-Math.max(a.y,b.y))/350*20,1,21),clamp(1+(390-Math.min(a.y,b.y))/350*20,1,21)].map(function(v){return v.toFixed(2);}).join('-')});});
  }
  function leaderboardCard(ts) {
    var key=rankKey(),card=chartCard('leaderboard','Top 20 by '+rankLabel().toLowerCase(),'The current ranking, with each theme’s own palette. Press T here to cycle rankings, or Shift+T to show the table.'+(key==='pop'?' Built-in themes ship with Omarchy and are left out here.':'')),top=ts.filter(function(t){return !isPinned(t,key)&&rankValue(t,key)!=null;}).slice(0,20),rows=top.map(function(t,i){return {cells:[i+1,t.name,rankChip(t)],action:themeAction(t)};});
    finding(card,top.length?top[0].name+' leads this selection with '+rankChip(top[0])+'.':'No ranked values in this selection.');
    if(tableTwin(card,['Rank','Theme','Value'],rows))return;
    if(!top.length){card._body.appendChild(el('p','chart-empty','No themes in this selection.'));return;}
    var svg=svgChart(card._body,650,Math.max(160,top.length*30+30),'Theme leaderboard'),max=Math.max.apply(null,top.map(function(t){var v=rankValue(t,key);return typeof v==='number'?v:0;}).concat([1])),listOnly=['new','updated','name','hue','similar'].indexOf(key)>=0;
    top.forEach(function(t,i){var y=22+i*30,g=draw(svg,'g',{'class':'leaderboard-row'});draw(g,'rect',{x:0,y:y-18,width:650,height:28,fill:'transparent'});label(g,10,y,i+1,'axis-label');[t.p.bg,t.p.fg,t.p.ac].concat(t.p.n.slice(1,7)).forEach(function(c,j){datum(g,'rect',{x:38+j*6,y:y-11,width:6,height:12},c);});label(g,103,y,t.name.length>24?t.name.slice(0,23)+'…':t.name,'theme-label');var v=rankValue(t,key);if(!listOnly&&v!=null){var w=Number(v)/max*205;bar(g,310,y-13,w,18,'var(--viz-s1)');label(g,320+w,y,rankChip(t),'value-label');}else label(g,620,y,rankChip(t),'value-label','end');mark(svg,g,325,y,rankChip(t),t.name,t,themeAction(t));});var b=el('button','show-all-grid');b.textContent='Show in grid';b.addEventListener('click',openGrid);card._body.appendChild(b);
  }
  function monthCard(ts) {
    var card=chartCard('months','Themes published per month','Creation dates show the format handover. Drag across months to filter; hover for both formats and the running total.');if(!HAS_STATS){finding(card,'No repository creation dates are available in this build.');emptyChart(card);return;}
    var dated=ts.filter(function(t){return t.st&&t.st.c;}),counts={};dated.forEach(function(t){var m=t.st.c.slice(0,7);if(!counts[m])counts[m]={toml:0,legacy:0};counts[m][formatKind(t)==='legacy'?'legacy':'toml']++;});var dates=Object.keys(counts).sort(),months=[];if(dates.length){var cursor=new Date(dates[0]+'-01T00:00:00Z'),end=new Date((D.stats.fetched||dates[dates.length-1]+'-01').slice(0,7)+'-01T00:00:00Z');while(cursor<=end&&months.length<2400){months.push(cursor.toISOString().slice(0,7));cursor.setUTCMonth(cursor.getUTCMonth()+1);}}
    var running=0,rows=months.map(function(m){var c=counts[m]||{toml:0,legacy:0};running+=c.toml+c.legacy;return {cells:[m,c.toml,c.legacy,running],action:function(){setFilter('born',m+'..'+m);}};});
    var peak=months.reduce(function(best,m){var c=counts[m]||{toml:0,legacy:0},v=c.toml+c.legacy;return v>best.n?{month:m,n:v}:best;},{month:'',n:0});finding(card,dated.length?peak.month+' accounts for '+peak.n+' themes, '+percentage(peak.n,dated.length)+' of dated themes in this selection.':'No dated themes in this selection.');
    if(tableTwin(card,['Month','colors.toml','Legacy per-app files','Running total'],rows))return;
    if(!months.length){card._body.appendChild(el('p','chart-empty','No creation dates in this selection.'));return;}var legend=el('div','viz-legend');[['4x,3x','colors.toml','s1'],['legacy','Legacy per-app files','s2']].forEach(function(x){var b=el('button'),swatch=el('i');swatch.style.background='var(--viz-'+x[2]+')';b.append(swatch,document.createTextNode(x[1]));b.addEventListener('click',function(){setFilter('fmt',x[0]);});legend.appendChild(b);});card._body.appendChild(legend);
    var svg=svgChart(card._body,650,400,'Themes created each month'),max=Math.max.apply(null,rows.map(function(r){return r.cells[1]+r.cells[2];}).concat([1])),step=Math.pow(10,Math.floor(Math.log10(max))),tick=Math.max(1,Math.ceil(max/5/step)*step),ceiling=Math.ceil(max/tick)*tick,xStep=560/months.length,w=Math.min(24,Math.max(1,xStep-2)),base=345,height=285,scale=function(v){return v/ceiling*height;};for(var v=0;v<=ceiling;v+=tick){gridLine(svg,55,base-scale(v),615,base-scale(v));label(svg,43,base-scale(v)+4,v>=1000?(v/1000)+'K':v,null,'end');}
    rows.forEach(function(row,i){var c=row.cells,x=55+i*xStep+(xStep-w)/2,toml=scale(c[1]),legacy=scale(c[2]),g=draw(svg,'g',{});bar(g,x,base-legacy,w,legacy,'var(--viz-s2)',true);bar(g,x,base-legacy-toml,w,Math.max(0,toml-(legacy>0?2:0)),'var(--viz-s1)',true);draw(g,'rect',{x:x-(24-w)/2,y:55,width:Math.max(w,24),height:295,fill:'transparent'});var total=c[1]+c[2];if(total===max)label(svg,x+w/2,base-scale(total)-8,total,'value-label','middle');mark(svg,g,x+w/2,base-scale(total)/2,c[1]+' colors.toml · '+c[2]+' legacy · '+c[3]+' total',c[0],null,row.action);if(i===0||i===months.length-1||i%Math.max(1,Math.ceil(months.length/7))===0)label(svg,x+w/2,372,c[0],null,'middle');});
    brush(svg,{x:55,y:55,w:560,h:290},function(a,b){var first=clamp(Math.floor((Math.min(a.x,b.x)-55)/xStep),0,months.length-1),last=clamp(Math.floor((Math.max(a.x,b.x)-55)/xStep),0,months.length-1);setFilter('born',months[first]+'..'+months[last]);});
  }
  function censusCard(ts) {
    var card=chartCard('census','How red is red?','Normal and bright terminal colours, grouped by actual hue. Each row has its own count scale; columns sample their members from dark to light.'),slots=['red','green','yellow','blue','magenta','cyan'],nominal=[30,142,99,255,306,195],series=slots.map(function(_,j){var bins=Array.from({length:72},function(){return [];});ts.forEach(function(t){[t.p.n[j+1],t.p.b[j+1]].forEach(function(c){var p=polar(c);if(p.C>=.04)bins[Math.floor(p.h/5)%72].push({t:t,c:c,L:p.L});});});return bins;}),rows=[];series.forEach(function(bins,j){bins.forEach(function(members,i){if(members.length)rows.push({cells:[slots[j],i*5+'–'+(i+1)*5+'°',members.length,Array.from(new Set(members.map(function(m){return m.t.name;}))).slice(0,3).join(', ')],action:function(){setFilter('tc',(j+1)+':'+i*5+'-'+((i+1)*5));}});});});
    var reds=series[0].reduce(function(n,b){return n+b.length;},0),warm=series[0].slice(3,9).reduce(function(n,b){return n+b.length;},0);finding(card,percentage(reds-warm,reds)+' of chromatic red terminal colours fall outside the red accent range of 15–45°.');
    if(tableTwin(card,['ANSI slot','Actual hue','Count','Examples'],rows))return;
    var svg=svgChart(card._body,650,510,'Terminal colour hue census'),left=90,w=520,bw=w/72-2;
    series.forEach(function(bins,j){var y=60+j*69,max=Math.max.apply(null,bins.map(function(a){return a.length;}).concat([1]));label(svg,12,y+19,slots[j],'theme-label');label(svg,12,y+35,'max '+max);gridLine(svg,left,y+46,left+w,y+46);var marker=left+nominal[j]/360*w;gridLine(svg,marker,y+48,marker,y+54,true);
      bins.forEach(function(members,i){if(!members.length)return;members.sort(function(a,b){return a.L-b.L;});var h=members.length/max*45,x=left+i*w/72,g=draw(svg,'g',{}),samples=Math.min(12,members.length);var darkColumn=true;for(var k=0;k<samples;k++){var member=members[Math.round(k*(members.length-1)/Math.max(1,samples-1))];if(contrast(member.c,'#15151a')>=1.6)darkColumn=false;draw(g,'rect',{x:x,y:y+46-h+k*h/samples,width:bw,height:h/samples+.1,fill:member.c});}if(darkColumn)draw(g,'rect',{x:x,y:y+46-h,width:bw,height:h,fill:'none',stroke:'var(--viz-axis)','stroke-width':1});draw(g,'rect',{x:x-1,y:y,width:w/72,height:48,fill:'transparent'});var action=function(){setFilter('tc',(j+1)+':'+i*5+'-'+((i+1)*5));};mark(svg,g,x+bw/2,y+23,members.length+(members.length===1?' colour · ':' colours · ')+i*5+'–'+(i+1)*5+'°',slots[j]+': '+Array.from(new Set(members.map(function(m){return m.t.name;}))).slice(0,3).join(', '),null,action);});});
    for(var degree=0;degree<=360;degree+=45){var x=left+degree/360*w;gridLine(svg,x,472,x,478,true);label(svg,x,496,degree+'°',null,'middle');}
    brush(svg,{x:left,y:60,w:w,h:393},function(a,b){var slot=clamp(Math.floor((a.y-60)/69),0,5)+1,start=clamp(Math.floor((Math.min(a.x,b.x)-left)/w*72)*5,0,355),end=clamp(Math.ceil((Math.max(a.x,b.x)-left)/w*72)*5,5,360);setFilter('tc',slot+':'+start+'-'+end);});
  }
  function accentCard(ts) {
    var grouped={};ts.forEach(function(t){var name=t.m&&t.m.an||'grey';if(!grouped[name])grouped[name]=[];grouped[name].push(t);});var groups=Object.keys(grouped).sort(function(a,b){return grouped[b].length-grouped[a].length||a.localeCompare(b);}),top=groups;
    var card=chartCard('accents','Accents, in plain language','All names in this selection and their actual accents. Click a name to filter, or an accent tick to open a theme.'),rows=top.map(function(k){return {cells:[k,grouped[k].length,grouped[k].map(function(t){return t.name;}).join(', ')],action:function(){setFilter('an',k);}};});
    finding(card,groups.length?groups[0]+' is the most common accent name, '+percentage(grouped[groups[0]].length,ts.length)+' of themes.':'No accents in this selection.');
    if(tableTwin(card,['Accent name','Count','Themes'],rows))return;
    var svg=svgChart(card._body,650,Math.max(180,top.length*20+24),'Accent names and actual colours'),max=Math.max.apply(null,top.map(function(k){return grouped[k].length;}).concat([1]));top.forEach(function(k,i){var y=24+i*20,members=grouped[k].slice().sort(function(a,b){return metric(a,'ah')-metric(b,'ah');}),w=members.length/max*245,g=draw(svg,'g',{});draw(g,'rect',{x:0,y:y-18,width:390,height:20,fill:'transparent'});label(g,12,y,k,'theme-label');bar(g,100,y-10,w,12,'var(--viz-deemph)');label(g,110+w,y,members.length,'value-label');mark(svg,g,190,y,members.length+' themes',k,null,function(){setFilter('an',k);});var ticks=Math.min(32,members.length);for(var j=0;j<ticks;j++){var t=members[Math.round(j*(members.length-1)/Math.max(1,ticks-1))],x=410+j*6.5,tick=datum(svg,'rect',{x:x,y:y-10,width:6,height:12,rx:1},t.p.ac);mark(svg,tick,x+3,y-5,t.p.ac,t.name,t,themeAction(t));}});
  }
  function renderStats(focusId) {
    var scroll=$('stats').scrollTop,active=document.activeElement,previous=focusId||active&&active.closest('.viz-card')&&active.closest('.viz-card').dataset.chart,ts=S.order.map(function(i){return T[i];});$('chartTooltip').hidden=true;$('statsCards').replaceChildren();numbersCard(ts);wheelCard(ts);mapCard(ts);scatterCard(ts);leaderboardCard(ts);monthCard(ts);censusCard(ts);accentCard(ts);$('stats').scrollTop=scroll;if(previous){var card=$('viz-'+previous);if(card)card.focus({preventScroll:true});}
  }

  /* ---------- modals ---------- */
  function openFavs() {
    var l = favList(); saveFavs();
    $('favText').value = l.join('\n');
    $('favInfo').textContent = l.length ? l.length + ' starred. One theme slug per line. Also stored in localStorage as "' + LIST_KEY + '".' : 'Nothing starred yet. Press F on a theme.';
    $('favModal').hidden = false; $('favText').focus(); $('favText').select();
  }
  function modalOpen() { return !$('favModal').hidden || !$('help').hidden || !$('colourModal').hidden; }
  function closeModals() { $('favModal').hidden = true; $('help').hidden = true; $('colourModal').hidden = true; }

  function closeRankMenu() { $('rankMenu').hidden=true;$('fSort').setAttribute('aria-expanded','false'); }
  $('fSort').addEventListener('click',function(){var menu=$('rankMenu');menu.hidden=!menu.hidden;this.setAttribute('aria-expanded',!menu.hidden);if(!menu.hidden)(menu.querySelector('[aria-checked="true"]')||menu.querySelector('button:not([hidden])')).focus();});
  $('rankMenu').addEventListener('keydown',function(e){var buttons=Array.from(this.children).filter(function(b){return !b.hidden;}),at=buttons.indexOf(document.activeElement);if(['ArrowDown','ArrowUp','Home','End','Escape','Tab'].indexOf(e.key)<0)return;if(e.key==='Escape'||e.key==='Tab'){closeRankMenu();if(e.key==='Escape')$('fSort').focus();}else buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(at+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();if(e.key!=='Tab')e.preventDefault();e.stopPropagation();});
  document.addEventListener('click',function(e){if(!e.target.closest('.rank-control'))closeRankMenu();});
  $('moreFilters').addEventListener('click',function(){$('filterDetails').hidden=!$('filterDetails').hidden;this.setAttribute('aria-expanded',!$('filterDetails').hidden);});

  /* ---------- events ---------- */
  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = e.key;
    if (modalOpen()) { if (k === 'Escape' || (k === '?' && !$('help').hidden) || ((k === 's' || k === 'S') && !$('favModal').hidden)) { closeModals(); e.preventDefault(); } return; }
    if (S.stats) {
      if (k === 'Escape' || ((k === 'v' || k === 'V') && !e.target.closest('input,select,textarea'))) { closeStats(); e.preventDefault(); }
      else if ((k === 't' || k === 'T') && e.target.closest('.viz-card')) { var card=e.target.closest('.viz-card');if(card.dataset.chart==='leaderboard'&&!e.shiftKey)cycleRank(1);else card.querySelector('.table-toggle').click();e.preventDefault(); }
      else if(k==='Enter'&&e.target===$('q')){readSearch(true);e.preventDefault();}
      return;
    }
    if (S.grid) {
      var nativeControl = e.target.closest('button,select,input,summary,a');
      if (nativeControl && e.target !== $('q') && k !== 'Escape') return;
      var inSearch = document.activeElement === $('q');
      if (k === 'Escape') { if (inSearch && $('q').value) { $('q').value = ''; S.filter.q = ''; applyFilter(); } else closeGrid(); }
      else if (k === 'Enter') { if ((!inSearch || !readSearch(true)) && cardPositions[S.gsel]) closeGrid(S.order[S.gsel]); }
      else if (k === 'ArrowDown') gridMove(gridCols());
      else if (k === 'ArrowUp') gridMove(-gridCols());
      else if (k === 'ArrowRight' && (!inSearch || !$('q').value)) gridMove(1);
      else if (k === 'ArrowLeft' && (!inSearch || !$('q').value)) gridMove(-1);
      else if (!inSearch && k.length === 1) { $('q').focus(); $('q').value += k; readSearch(); e.preventDefault(); return; }
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
      case 'v': case 'V': openStats(); break;
      case 't': case 'T': cycleRank(e.shiftKey ? -1 : 1); break;
      case 'a': case 'A': similar(T[S.i].slug); break;
      case 'e': case 'E': colourPrompt(); break;
      case 'i': case 'I': $('themeInfo').hidden = !$('themeInfo').hidden; fillInfo(); break;
      case '0': clearFilters(); toast('Filters cleared'); break;
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
    if (S.grid || S.stats || modalOpen()) return;
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
    if (!tStart || S.grid || S.stats || modalOpen()) return;
    var c = e.changedTouches[0], dx = c.clientX - tStart.x, dy = c.clientY - tStart.y; tStart = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.3) themeStep(dx < 0 ? 1 : -1);
    else if (Math.abs(dy) > 50 && Math.abs(dy) > Math.abs(dx) * 1.3) slideStep(dy < 0 ? 1 : -1);
  }, { passive: true });

  var idleTimer = 0, wipeRaf = 0;
  window.addEventListener('mousemove', function (e) {
    document.body.classList.remove('idle'); clearTimeout(idleTimer);
    idleTimer = setTimeout(function () { if (!S.grid && !S.stats && !modalOpen()) document.body.classList.add('idle'); }, 2500);
    if (S.compare === 2 && !S.grid && !S.stats) {
      S.wipe = Math.max(0, Math.min(1, e.clientX / innerWidth));
      if (!wipeRaf) wipeRaf = requestAnimationFrame(function () {
        wipeRaf = 0; var x = Math.round(S.wipe * innerWidth);
        $('paneB').style.clipPath = 'inset(0 0 0 ' + x + 'px)'; $('divider').style.left = x + 'px';
      });
    }
  });
  var rsz = 0;
  window.addEventListener('resize', function () { clearTimeout(rsz); rsz = setTimeout(function () { cache = {}; render(); if(S.grid){layoutCards();if(!cardPositions[S.gsel])S.gsel=cardPositions.findIndex(Boolean);renderCards();} }, 120); });
  document.addEventListener('fullscreenchange', function () { cache = {}; render(); });

  $('edgeL').addEventListener('click', function () { themeStep(-1); this.blur(); });
  $('edgeR').addEventListener('click', function () { themeStep(1); this.blur(); });
  $('rail').addEventListener('click', function (e) { var b = e.target.closest('button'); if (b && !b.disabled) kindKey(b.dataset.k); });
  $('hudStar').addEventListener('click', function () { toggleFav(S.i); this.blur(); });
  $('stage').addEventListener('click', function (e) { var b = e.target.closest('[data-copy]'); if (b) copy(b.dataset.copy, 'Copied ' + b.dataset.copy); });
  $('cards').addEventListener('click', function (e) {
    var c = e.target.closest('.card'); if (!c) return;
    if (e.target.closest('.similar')) { similar(T[+c.dataset.i].slug); return; }
    if (e.target.closest('.author-link')) { setFilter('by', T[+c.dataset.i].author); return; }
    if (e.target.closest('.st')) { toggleFav(+c.dataset.i); $('gCount').textContent = S.order.length + ' of ' + N + ' - ' + favList().length + ' starred'; return; }
    closeGrid(+c.dataset.i);
  });
  $('q').addEventListener('input', readSearch);
  $('q').addEventListener('blur', function(){setTimeout(function(){readSearch(true);},0);});
  $('fMode').addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) setFilter('mode', b.dataset.v === 'all' ? '' : b.dataset.v); });
  $('fFav').addEventListener('click', function () { setFilter('fav', S.filter.fav ? '' : '1'); });
  $('fSort').addEventListener('change', function () { S.filter.sort = this.value; S.gsel = 0; $('cards').scrollTop = 0; applyFilter(); });
  $('gStats').addEventListener('click', openStats);
  $('statsGrid').addEventListener('click', openGrid);
  $('statsClose').addEventListener('click', closeStats);
  $('colourPicker').addEventListener('input', function () { $('colourHex').value = this.value.slice(1); });
  $('colourApply').addEventListener('click', function () { var v = validFilter('col', $('colourHex').value.replace(/^#/, '')); if (v) { closeModals(); S.filter.sort = 'similar'; setFilter('col', v); openGrid(); } });
  $('colourClose').addEventListener('click', closeModals);
  $('colourHex').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('colourApply').click(); });
  $('hudAuthor').addEventListener('click', function () { setFilter('by', T[S.i].author); openGrid(); });
  $('cards').addEventListener('scroll', renderCards, { passive: true });
  $('cards').addEventListener('keydown', function (e) { var c = e.target.closest('.card'); if (c && e.key === 'Enter' && e.target === c) { closeGrid(+c.dataset.i); e.preventDefault(); } });
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
    var rawHash = (location.hash || '').slice(1).split('?'), h;
    try { h = decodeURIComponent(rawHash[0]).split('/'); } catch (e) { h = []; }
    if (h[0] && bySlug[h[0]]) S.i = bySlug[h[0]].i;
    else { var tn = bySlug['tokyo-night']; if (tn) S.i = tn.i; }
    if (h[1] && KINDS.indexOf(h[1]) >= 0) S.kind = h[1];
    var n = parseInt(h[2], 10);
    if (n > 0) { var m = mem(T[S.i]); if (S.kind === 'wall') m.wall = n - 1; if (S.kind === 'preview') m.preview = n - 1; }
    var params = new URLSearchParams(rawHash[1] || '');
    params.forEach(function (v, k) { if (k !== 'view') { var ok = validFilter(k, v); if (ok) S.filter[k] = ok; } });
    if (S.filter.q) S.filter.q = liftWords(S.filter.q);
    if ((S.filter.like || S.filter.col) && !params.has('sort')) S.filter.sort = 'similar';
    var view = params.get('view');
    if (view === 'grid') setTimeout(openGrid, 0);
    if (view === 'stats') setTimeout(openStats, 0);
    var q = location.search || '';
    if (/[?&]dual=1/.test(q)) S.dual = true;
    if (/[?&]grid=1/.test(q)) setTimeout(openGrid, 0);
    if (/[?&]hud=0/.test(q)) S.hud = false;
    if (/[?&]overlays=0/.test(q)) S.overlays = false;
    var cm = /[?&]compare=([a-z0-9._-]+)/.exec(q);
    if (cm && bySlug[cm[1]]) { S.compare = /[?&]wipe=1/.test(q) ? 2 : 1; S.cmpB = bySlug[cm[1]].i; }
  })();
  $('helpMeta').textContent = N + ' themes, data generated ' + (D.generated || '?') + '. ' + (D.skipped && D.skipped.length ? 'Not collected: ' + D.skipped.map(function (s) { return s.name + ' (' + s.reason + ')'; }).join('; ') + '.' : '');
  initFilters();
  applyFilter();
  render();
})();
