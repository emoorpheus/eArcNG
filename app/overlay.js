/* overlay.js - OSD громкости для eARC/ARC. Показывает ТОЛЬКО значения от cec_volume_bridge.py
 * (params.src === "cec", реальная громкость из CEC 0x7A). Своего счёта нажатий нет.
 * Окно: техника Nikolay1243 (плавающее окно без фокуса и без зоны ввода, закрытие после таймаута).
 * Тайминги как у нативного индикатора: гаснет через ttl после ПОСЛЕДНЕГО НАЖАТИЯ (params.kt), fade 150 мс;
 * при появлении (запуск окна) - ttl от появления окна на экране (visibilitychange или первый кадр после паузы
 * requestAnimationFrame), не короче. */
(function () {
  'use strict';
  var CFG = {
    sources: { cec: true, preview: true },  // счётчик штатного watcher.js (без src) не принимается
    ttl: 1500,                              // мс видимости после последнего нажатия/обновления (натив: 1500)
    fade: 150,                              // мс появления/исчезновения (натив: 150)
    minVisible: 400,                        // запоздавшее обновление на экране видно хотя бы столько
    frameGap: 120,                          // мс без кадров (requestAnimationFrame) = окна на экране ещё нет
    steadyFrames: 15,                       // столько ровных кадров подряд - окно на экране, слежка не нужна
    appearMax: 3000,                        // мс от запуска: дольше появления не ждать (окно не висит)
    max: 100,
    setup: '/home/root/cec-volume/remote-setup.sh',              // установленный бэкенд
    appId: 'com.github.emoorpheus.earcng',                       // бэкенд в пакете приложения: <база>/<id>/backend/
    appBases: ['/media/developer/apps/usr/palm/applications', '/media/cryptofs/apps/usr/palm/applications']
  };
  var STATES = { running: 'ok', stopped: 'error', disabled: 'off', missing: 'error', switching: 'busy' };   // первая строка status

  /* --- язык экрана настройки: как у ТВ. Русский для русского и языков СНГ, иначе английский --- */
  var TEXT = {
    ru: {
      enable: 'Включить', disable: 'Отключить', test: 'Тест',
      checking: 'Проверка…', enabling: 'Включение…', disabling: 'Отключение…',
      running: 'Работает', stopped: 'Остановлен', disabled: 'Отключён', missing: 'Не установлен',
      unknown: 'Неизвестно', error: 'Ошибка',
      uiNative: 'системный индикатор', uiOverlay: 'оверлей', uiNativeOff: 'оверлей · системный индикатор недоступен',
      missingHint: 'Установите с ПК: tools/earcng.sh',
      install: 'Установить', installing: 'Установка…', update: 'Обновить', updating: 'Обновление…',
      installHint: 'Нажмите «Установить»: мост поставится из приложения (root через Homebrew Channel)',
      updateHint: 'В приложении бэкенд {new} (установлен {old}) — нажмите «Обновить»',
      hbDenied: 'Homebrew Channel не дал root-доступ: проверьте Root status в настройках Homebrew Channel',
      hbNoRoot: 'Команда выполнена не от root: нужен ТВ с root (Homebrew Channel → Root status)',
      hbMissing: 'Сервис Homebrew Channel не найден: установите или обновите Homebrew Channel',
      trkActive: 'Работает trk501 lg-soundbar-volume-osd — сначала отключите его (см. README)',
      noPython: 'На ТВ нет python3 — мост не может работать',
      modes: 'Индикатор громкости', modeNative: 'Системный', modeOverlay: 'Оверлей', switching: 'Переключение…',
      switchHint: 'Экран ТВ может погаснуть примерно на 15 секунд, открытые приложения закроются',
      confirmSwitch: 'Нажмите ещё раз: интерфейс ТВ перезапустится, открытые приложения закроются',
      nativeFailed: 'Системный индикатор не включился — показывается оверлей',
      switchedNative: 'Готово: включён системный индикатор', switchedOverlay: 'Готово: включён оверлей',
      noBridge: 'Нет PalmServiceBridge: запустите приложение на ТВ',
      noAnswer: 'Homebrew Channel не ответил — проверьте root',
      badReply: 'Некорректный ответ сервиса', execFailed: 'Нужен root в Homebrew Channel'
    },
    en: {
      enable: 'Enable', disable: 'Disable', test: 'Test',
      checking: 'Checking…', enabling: 'Enabling…', disabling: 'Disabling…',
      running: 'Running', stopped: 'Stopped', disabled: 'Disabled', missing: 'Not installed',
      unknown: 'Unknown', error: 'Error',
      uiNative: 'system indicator', uiOverlay: 'overlay', uiNativeOff: 'overlay · system indicator unavailable',
      missingHint: 'Install from a PC: tools/earcng.sh',
      install: 'Install', installing: 'Installing…', update: 'Update', updating: 'Updating…',
      installHint: 'Press Install: the bridge is set up from the app (root via Homebrew Channel)',
      updateHint: 'The app brings backend {new} (installed {old}) — press Update',
      hbDenied: 'Homebrew Channel refused root access: check Root status in Homebrew Channel settings',
      hbNoRoot: 'The command did not run as root: the TV must be rooted (Homebrew Channel → Root status)',
      hbMissing: 'Homebrew Channel service not found: install or update Homebrew Channel',
      trkActive: 'trk501 lg-soundbar-volume-osd is running — disable it first (see README)',
      noPython: 'No python3 on this TV — the bridge cannot run',
      modes: 'Volume indicator', modeNative: 'System', modeOverlay: 'Overlay', switching: 'Switching…',
      switchHint: 'The TV screen may go black for about 15 seconds, open apps will close',
      confirmSwitch: 'Press again: the TV interface restarts, open apps will close',
      nativeFailed: 'The system indicator did not start — the overlay is shown',
      switchedNative: 'Done: the system indicator is on', switchedOverlay: 'Done: the overlay is on',
      noBridge: 'No PalmServiceBridge: run the app on the TV',
      noAnswer: 'Homebrew Channel did not answer — check root',
      badReply: 'Invalid service reply', execFailed: 'Root in Homebrew Channel is required'
    }
  };
  // русский, украинский, белорусский, казахский, киргизский, узбекский, таджикский, туркменский,
  // армянский, азербайджанский, грузинский, молдавский (mo, ro-MD)
  var RU_LANGS = { ru: true, uk: true, be: true, kk: true, ky: true, uz: true, tg: true, tk: true,
    hy: true, az: true, ka: true, mo: true };
  var LOCALE = /^([a-z]{2,3})(?:[-_][a-z]{4})?(?:[-_]([a-z]{2}|\d{3}))?(?:[-_.@]|$)/i;
  function langOf(locale) {                                     // 'ru-RU' -> 'ru', 'de-DE' -> 'en', мусор -> ''
    var m = LOCALE.exec(typeof locale === 'string' ? locale : '');
    if (!m) return '';
    var l = m[1].toLowerCase(), region = (m[2] || '').toUpperCase();
    return RU_LANGS[l] === true || (l === 'ro' && region === 'MD') ? 'ru' : 'en';
  }
  function systemLocale() {                                     // язык интерфейса ТВ без запросов к сервисам
    var found = [];
    try { if (window.webOSSystem) found.push(window.webOSSystem.locale); } catch (e) { /* ignore */ }
    try { if (window.PalmSystem) found.push(window.PalmSystem.locale); } catch (e) { /* ignore */ }
    try {
      var nav = window.navigator;
      if (nav) found.push(nav.languages && nav.languages[0], nav.language);
    } catch (e) { /* ignore */ }
    for (var i = 0; i < found.length; i++) if (langOf(found[i])) return found[i];
    return '';
  }

  var POS = /^(?:(?:top|bottom)-(?:left|right|center)|center-(?:left|right))$/;
  var VARIANTS = { badge: true, pill: true, ring: true };
  function $(id) { return document.getElementById(id); }
  var body = document.body, osd = $('osd'), valueEl = $('osd-value');
  var heroEl = $('hero'), statusEl = $('status'), uiEl = $('ui'), detailsEl = $('details'), modesEl = $('modes');
  var timers = {}, mode = '', visible = false, last = { sid: null, seq: -1 };
  // появление окна оверлея: appearAt - когда оно на экране (или показано), appearing - ещё запускается
  // (document.hidden), cur - тайминги последнего показа для перезапуска таймера при появлении,
  // frames - слежка за кадрами при запуске ({ last, steady, since })
  var appearAt = 0, appearing = false, cur = null, frames = null;
  var lang = '', localeBridge = null;
  // экран настройки: key - текст статуса, kind - вид кольца (ok|off|error|busy), on - мост включён и работает,
  // mode - действующий индикатор (native|overlay), modes - выбор индикатора доступен (работает и есть QML)
  // install - бэкенда нет, но он есть в пакете приложения; update - пакет несёт другой бэкенд (vers: {new, old})
  var st = { key: 'checking', kind: 'busy', on: false, ui: '', mode: '', modes: false, hint: '', raw: '',
    install: false, update: false, vers: {} };
  var pending = '';                                             // режим, ждущий второго нажатия (подтверждение)
  var switched = '';                                            // экран открыт снова после переключения (params.switched)

  function t(key, vars) {
    var s = (TEXT[lang] || TEXT.en)[key];
    return vars ? s.replace(/\{(\w+)\}/g, function (m, k) { return vars.hasOwnProperty(k) ? vars[k] : m; }) : s;
  }
  // ошибка Homebrew Channel / remote-setup.sh -> понятная подсказка (сам текст остаётся рядом, для issue)
  var ERROR_HINTS = [
    [/denied method call|not permitted|permission denied|access denied/i, 'hbDenied'],
    [/root-required|run as root|not root/i, 'hbNoRoot'],
    [/unknown (?:method|service|category)|service (?:does not exist|not found)|hbchannel[^\n]*not found/i, 'hbMissing'],
    [/trk501/i, 'trkActive'],
    [/python3 not found/i, 'noPython']
  ];
  function errorHint(text) {
    for (var i = 0; i < ERROR_HINTS.length; i++) if (ERROR_HINTS[i][0].test(text)) return ERROR_HINTS[i][1];
    return '';
  }
  function applyLang(locale) {
    var l = langOf(locale) || 'en';
    if (l === lang) return;
    lang = l;
    if (document.documentElement) document.documentElement.lang = l;
    $('test').textContent = t('test');
    $('update').textContent = t('update');
    $('modes-label').textContent = t('modes');
    $('ui-native').textContent = t('modeNative');
    $('ui-overlay').textContent = t('modeOverlay');
    renderSetup();
  }
  function querySettingsLocale() {                              // точный язык UI из настроек ТВ, если доступен
    if (!window.PalmServiceBridge) return;
    try {
      var bridge = new PalmServiceBridge();
      localeBridge = bridge;                                    // ссылка, чтобы ответ не потерялся
      bridge.onservicecallback = function (raw) {
        var ui = '';
        try {
          var info = JSON.parse(raw).settings.localeInfo;
          ui = info.locales.UI;
        } catch (e) { ui = ''; }
        if (localeBridge === bridge) localeBridge = null;
        if (langOf(ui)) applyLang(ui);
      };
      bridge.call('luna://com.webos.settingsservice/getSystemSettings', JSON.stringify({ keys: ['localeInfo'] }));
    } catch (e) { localeBridge = null; }
  }

  function later(name, fn, ms) {
    cancel(name);
    timers[name] = window.setTimeout(function () { timers[name] = null; fn(); }, ms);
  }
  function cancel(name) {
    if (timers[name]) { window.clearTimeout(timers[name]); timers[name] = null; }
  }
  function cancelAll() { for (var k in timers) { if (timers.hasOwnProperty(k)) cancel(k); } }

  function paramsOf(input) {
    var p = input;
    try { if (typeof p === 'string') p = JSON.parse(p); } catch (e) { p = {}; }
    if (!p || typeof p !== 'object') p = {};
    if (p.params && typeof p.params === 'object') p = p.params;
    return p;
  }
  function hasVolume(p) { return p.volume !== undefined || p.mute !== undefined; }
  function num(v, lo, hi, dflt) { var n = Number(v); return v !== undefined && v !== null && n >= lo && n <= hi ? n : dflt; }

  /* --- окно: без фокуса и без зоны ввода, пульт остаётся у приложения под оверлеем --- */
  function configureInput(passive) {
    if (!window.PalmSystem) return;
    if (PalmSystem.setInputRegion) PalmSystem.setInputRegion([{ x: 0, y: 0, width: passive ? 0 : 1920, height: passive ? 0 : 1080 }]);
    if (PalmSystem.setKeyMask) PalmSystem.setKeyMask(['KeyMaskDefault']);
  }
  function setWindowType(type) {
    if (!window.PalmSystem) return;
    var passive = type === 'overlay';
    if (PalmSystem.setWindowProperty) {
      PalmSystem.setWindowProperty('needFocus', passive ? 'false' : 'true');
      PalmSystem.setWindowProperty('_WEBOS_WINDOW_TYPE', passive ? '_WEBOS_WINDOW_TYPE_FLOATING' : '_WEBOS_WINDOW_TYPE_CARD');
    }
    configureInput(passive);
  }
  function setMode(m) { mode = m; body.className = m ? m + '-mode' : ''; }

  /* --- данные --- */
  function accept(p) {
    if (!CFG.sources[p.src]) return false;                     // только реальные значения
    if (!isFinite(Number(p.volume))) return false;
    if (p.sid !== undefined && p.seq !== undefined) {
      var seq = Number(p.seq);
      if (p.sid === last.sid && seq <= last.seq) return false;  // устаревший/повторный relaunch
      last.sid = p.sid;
      last.seq = seq;
    }
    return true;
  }

  function render(p) {
    var max = Number(p.max) > 0 ? Number(p.max) : CFG.max;
    var v = Math.max(0, Math.min(max, Math.round(Number(p.volume))));
    var muted = p.mute === true || p.muted === true;
    valueEl.textContent = String(v);
    osd.style.setProperty('--level', String(v / max));
    osd.classList.toggle('is-muted', muted);
    osd.classList.toggle('is-zero', v === 0);
    if (VARIANTS[p.variant] === true) osd.setAttribute('data-variant', p.variant);
    if (typeof p.pos === 'string' && POS.test(p.pos)) osd.setAttribute('data-pos', p.pos);
    if (p.reason === 'limit' || p.reason === 'repeat') {
      osd.classList.remove('is-bump');
      void osd.offsetWidth;                                     // перезапуск CSS-анимации
      osd.classList.add('is-bump');
      later('bump', function () { osd.classList.remove('is-bump'); }, 400);
    }
  }

  function show(p, preview) {
    var wasVisible = mode === 'overlay' && visible;
    cancel('hide'); cancel('close'); cancel('focus');
    if (!wasVisible) setWindowType('overlay');
    setMode('overlay');
    render(p);
    if (!wasVisible) {
      appearAt = Date.now();
      appearing = !preview && document.hidden === true;
      frames = null;
      if (appearing) watchFrames();
      osd.classList.remove('show');
      later('show', function () { osd.classList.add('show'); }, 20);
      // activate() только при появлении: повторный вызов снова забирает фокус у приложения
      if (window.PalmSystem && PalmSystem.activate) PalmSystem.activate();
    }
    visible = true;
    configureInput(true);
    later('focus', function () {                                // нативное окно уже создано - отдать фокус
      if (mode !== 'overlay') return;
      if (window.PalmSystem && PalmSystem.setWindowProperty) PalmSystem.setWindowProperty('needFocus', 'false');
      configureInput(true);
    }, 60);
    var ttl = num(p.ttl, 500, 10000, CFG.ttl);
    var fade = num(p.fade, 0, 1000, CFG.fade);
    var keep = preview ? 0 : num(p.keep, 0, 600000, 0);        // keepalive: скрытое окно живёт ещё keep мс
    osd.style.setProperty('--osd-fade', fade + 'ms');
    // Появление - полный ttl от появления окна: оно на экране через 0,4-1,5 с после нажатия (OLED65M59LA), а
    // ответ саундбара (relaunch) приходит раньше, чем окно на экране - отсчёт от нажатия оставил бы доли
    // секунды. Пока окно запускается (document.hidden), ttl отсчитывается заново от visibilitychange и от
    // первого кадра после паузы (watchFrames). Уже на экране - гаснет через ttl после последнего нажатия,
    // как нативный; запоздавшее - хотя бы minVisible.
    var now = Date.now(), due = now + ttl, kt = Number(p.kt);
    if (wasVisible && kt > 0) {
      var age = now - kt;
      if (age >= 0 && age < 60000) due = Math.max(appearAt + ttl, kt + ttl, now + CFG.minVisible);
    }
    cur = { ttl: ttl, fade: fade, keep: keep, preview: preview };
    armHide(due - now);
  }
  function armHide(delay) {
    var c = cur;
    later('hide', function () {
      if (stalled()) { armHide(100); return; }                  // окно ещё не на экране - гасить рано
      osd.classList.remove('show');
      visible = false;
      appearing = false;
      frames = null;
      later('close', function () { if (c.preview) showSetup(); else window.close(); }, c.fade + c.keep);
    }, delay);
  }
  function appeared() {                                         // visibilitychange: окно оверлея видимо
    if (!appearing || mode !== 'overlay' || !visible || !cur) return;
    if (!frames) appearing = false;                             // кадров не видно - появление = visibilitychange
    appearAt = Date.now();
    armHide(cur.ttl);
  }
  // Окно на экране, когда композитор отдаёт ему кадры. На OLED65M59LA при холодном запуске после 2-3 кадров
  // бывает пауза ~1,1 с: visibilitychange уже пришёл, а окна на экране ещё нет (LSM visible - после паузы).
  // Первый кадр после паузы = появление: ttl заново от него; ровные кадры подряд - слежка не нужна.
  function watchFrames() {
    if (typeof window.requestAnimationFrame !== 'function') return;
    var f = frames = { last: Date.now(), steady: 0, since: Date.now() };
    function frame() {
      if (frames !== f || !appearing || mode !== 'overlay' || !visible || !cur) return;
      var t = Date.now();
      if (t - f.last > CFG.frameGap) {
        f.steady = 0;
        appearAt = t;
        armHide(cur.ttl);
      } else if (!document.hidden && ++f.steady >= CFG.steadyFrames) {
        appearing = false;
        frames = null;
        return;
      }
      f.last = t;
      window.requestAnimationFrame(frame);
    }
    window.requestAnimationFrame(frame);
  }
  function stalled() {                                          // кадров давно нет, но с запуска < appearMax
    var t = Date.now();
    return !!frames && appearing && t - frames.last > CFG.frameGap && t - frames.since < CFG.appearMax;
  }

  /* --- экран настройки (открытие из списка приложений) --- */
  // ожидание ответа hbchannel exec: status - доли секунды; установка/обновление на ТВ под Homebrew Channel
  // заметно медленнее, чем по SSH (на OLED65M59LA activate ~13 с) - для действий запас больше
  var WAIT_STATUS = 15000, WAIT_ACTION = 60000;
  function rootExec(command, cb, wait) {
    if (window.earcRootExec) { window.earcRootExec(command, cb); return; }
    if (!window.PalmServiceBridge) { cb(new Error(t('noBridge'))); return; }
    var bridge = new PalmServiceBridge(), done = false;
    var timer = window.setTimeout(function () { finish(new Error(t('noAnswer'))); }, wait || WAIT_STATUS);
    function finish(err, out) {
      if (done) return;
      done = true;
      window.clearTimeout(timer);
      try { if (bridge.cancel) bridge.cancel(); } catch (e) { /* ignore */ }
      cb(err, out || '');
    }
    bridge.onservicecallback = function (raw) {
      var r;
      try { r = JSON.parse(raw); } catch (e) { finish(new Error(t('badReply'))); return; }
      if (r.returnValue === false || r.error) finish(new Error(r.stderrString || r.errorText || t('execFailed')));
      else finish(null, r.stdoutString);
    };
    try { bridge.call('luna://org.webosbrew.hbchannel.service/exec', JSON.stringify({ command: command })); }
    catch (e) { finish(e); }
  }
  function firstScript(list, action) {                          // первый существующий remote-setup.sh из списка
    return 'S=; for f in ' + list.join(' ') + '; do if [ -z "$S" ] && [ -f "$f" ]; then S=$f; fi; done; ' +
      'if [ -n "$S" ]; then sh "$S" ' + action + '; else echo missing; fi';
  }
  function setupCommand(action) {
    // status и activate - скриптом из пакета приложения (тот же выпуск, что этот экран; бэкенд мог быть
    // поставлен раньше), остальное - установленным бэкендом, а без него - только status
    var bundled = [];
    for (var i = 0; i < CFG.appBases.length; i++) bundled.push(CFG.appBases[i] + '/' + CFG.appId + '/backend/remote-setup.sh');
    if (action === 'activate') return firstScript(bundled, action);
    var status = firstScript(bundled.concat([CFG.setup]), 'status');
    if (action === 'status') return status;
    return 'if [ -f ' + CFG.setup + ' ]; then sh ' + CFG.setup + ' ' + action + '; else ' + status + '; fi';
  }
  var MODES = { 'ui-native': 'native', 'ui-overlay': 'overlay' };
  function renderSetup() {
    var busy = String(st.kind === 'busy');                     // пока идёт команда - кнопки не нажимаются
    statusEl.textContent = t(st.key);
    heroEl.setAttribute('data-state', st.kind);
    // при выборе индикатора строка режима нужна только для пояснения «системный индикатор недоступен»
    uiEl.textContent = st.kind === 'ok' && st.ui && (!st.modes || st.ui === 'uiNativeOff') ? t(st.ui) : '';
    var hint = st.hint ? t(st.hint, st.vers) : '';
    detailsEl.textContent = pending ? t('confirmSwitch') : hint && st.raw ? hint + ' — ' + st.raw : hint || st.raw;
    var toggle = $('toggle'), update = $('update');
    // фокус до скрытия: на ТВ (OLED65M59LA) скрытая кнопка теряет фокус сразу (activeElement = body)
    var focused = document.activeElement;
    toggle.textContent = t(st.install ? 'install' : st.on ? 'disable' : 'enable');
    toggle.setAttribute('aria-disabled', busy);
    update.hidden = !st.update;
    update.setAttribute('aria-disabled', busy);
    if (update.hidden && focused === update) toggle.focus();
    modesEl.hidden = !st.modes;
    for (var id in MODES) {
      if (!MODES.hasOwnProperty(id)) continue;
      var b = $(id);
      b.setAttribute('aria-pressed', String(st.mode === MODES[id]));
      b.setAttribute('aria-disabled', busy);
      b.className = pending === MODES[id] ? 'mode is-pending' : 'mode';
      if (!st.modes && focused === b) toggle.focus();           // кнопки скрыты - фокус на переключатель
    }
  }
  function setState(next) {
    for (var k in next) if (next.hasOwnProperty(k)) st[k] = next[k];
    renderSetup();
  }
  function uiOf(lines) {                                        // строка "ui: ..." из remote-setup.sh status
    for (var i = 1; i < lines.length; i++) {
      if (/^ui: native \(QML patch active[),]/.test(lines[i])) return 'uiNative';
      if (/^ui: native/.test(lines[i])) return 'uiNativeOff';
      if (/^ui: overlay/.test(lines[i])) return 'uiOverlay';
    }
    return '';
  }
  function errorOf(lines) {                                     // хвост /tmp/cec-volume-bridge.err, если остановлен
    for (var i = 1; i < lines.length; i++) {
      if (/^last error/.test(lines[i])) return lines.slice(i + 1).join(' ').replace(/\s+/g, ' ').trim();
    }
    return '';
  }
  function switchErrorOf(lines) {                              // "switch error: ..." - переключение не начато
    for (var i = 1; i < lines.length; i++) {
      if (/^switch error: /.test(lines[i])) return lines[i].slice(14).trim();
    }
    return '';
  }
  function updateOf(lines) {                                    // "update: backend X in the app, installed Y ..."
    for (var i = 1; i < lines.length; i++) {
      var m = /^update: backend (\S+) in the app, installed (\S*)/.exec(lines[i]);
      if (m) return { 'new': m[1], old: m[2] || '?' };
    }
    return null;
  }
  function hasLine(lines, re) {
    for (var i = 1; i < lines.length; i++) if (re.test(lines[i])) return true;
    return false;
  }
  var runId = 0;
  function runSetup(action, busyKey, busyHint) {
    var id = ++runId;
    pending = '';
    if (action !== 'status') switched = '';                     // новое действие - итог прежнего не показывать
    setState({ key: busyKey, kind: 'busy', hint: busyHint || '', raw: '' });
    rootExec(setupCommand(action), function (err, out) {
      if (id !== runId) return;                                 // ответ устаревшей команды (после демо и т.п.)
      if (err) {
        var msg = String(err.message || err);
        setState({ key: 'error', kind: 'error', on: false, ui: '', hint: errorHint(msg), raw: msg, install: false, update: false });
        return;
      }
      var lines = String(out).replace(/\r/g, '').split('\n'), state = (lines[0] || '').replace(/\s+/g, '');
      if (!STATES.hasOwnProperty(state)) {
        setState({ key: 'unknown', kind: 'error', on: false, ui: '', hint: '', raw: state, install: false, update: false });
        return;
      }
      if (state === 'switching') {                              // фоновое переключение индикатора: ждать
        // (переключение запускается только из работающего моста; экран может погаснуть и закрыть приложение)
        setState({ key: 'switching', kind: 'busy', on: true, ui: '', modes: false, hint: 'switchHint', raw: '',
          install: false, update: false });
        later('poll', function () { runSetup('status', 'switching', 'switchHint'); }, 3000);
        return;
      }
      var ui = uiOf(lines), running = state === 'running', failed = switchErrorOf(lines), hint = '';
      var install = state === 'missing' && hasLine(lines, /^bundled: backend /), vers = updateOf(lines);
      var update = state !== 'missing' && !!vers;
      if (state === 'missing') hint = install ? 'installHint' : 'missingHint';
      else if (running && !failed && ui !== 'uiNative' && hasLine(lines, /^ui: .*failed attempt/)) hint = 'nativeFailed';
      else if (running && !failed && switched === 'native' && ui === 'uiNative') hint = 'switchedNative';
      else if (running && !failed && switched === 'overlay' && ui === 'uiOverlay') hint = 'switchedOverlay';
      else if (update && !failed) hint = 'updateHint';
      setState({ key: state, kind: install ? 'off' : STATES[state], on: running, ui: ui,
        mode: ui === 'uiNative' ? 'native' : 'overlay', modes: running && hasLine(lines, /^qml: yes/), hint: hint,
        raw: state === 'stopped' ? errorOf(lines) : failed, install: install, update: update, vers: vers || {} });
    }, action === 'status' ? WAIT_STATUS : WAIT_ACTION);
  }
  function pickMode(m) {                                        // выбор индикатора: второе нажатие подтверждает
    if (st.kind === 'busy' || !st.modes) return;
    if (m === st.mode) { if (pending) { pending = ''; renderSetup(); } return; }
    if (pending !== m) { pending = m; renderSetup(); return; }
    runSetup('switch ' + m, 'switching', 'switchHint');
  }

  function showSetup(p) {
    cancelAll();
    // переключение индикатора перезапускает интерфейс ТВ; remote-setup.sh открывает экран снова с итогом
    switched = p && (p.switched === 'native' || p.switched === 'overlay') ? p.switched : '';
    visible = false;
    appearing = false;
    setWindowType('card');
    setMode('setup');
    osd.classList.remove('show');
    applyLang(systemLocale());
    querySettingsLocale();
    runSetup('status', 'checking');
    window.setTimeout(function () { var b = $('toggle'); if (b) b.focus(); }, 30);
    if (window.PalmSystem && PalmSystem.activate) PalmSystem.activate();
  }
  var DEMO = [18, 19, 20, 21, 22, 23, 24, 25, 'limit', 'mute', 25];
  function demo(i) {
    if (i >= DEMO.length) return;                               // последний hide вернёт экран настройки
    var d = DEMO[i];
    show({ src: 'preview', volume: typeof d === 'number' ? d : 25, mute: d === 'mute', reason: d === 'limit' ? 'limit' : 'report' }, true);
    later('demo', function () { demo(i + 1); }, i < 7 ? 260 : 900);
  }

  $('toggle').onclick = function () {
    if (st.kind === 'busy') return;                             // не запускать вторую команду параллельно
    if (st.install) runSetup('activate', 'installing');         // бэкенд из пакета приложения (Homebrew Channel)
    else if (st.on) runSetup('disable', 'disabling');
    else runSetup('enable', 'enabling');
  };
  $('update').onclick = function () {
    if (st.kind === 'busy' || !st.update) return;
    runSetup('activate', 'updating');
  };
  $('test').onclick = function () { demo(0); };
  $('ui-native').onclick = function () { pickMode('native'); };
  $('ui-overlay').onclick = function () { pickMode('overlay'); };
  var ROWS = [['toggle', 'test', 'update'], ['ui-native', 'ui-overlay']];
  function rowIds(i) {                                          // «Обновить» - только когда видна
    var out = [];
    for (var j = 0; j < ROWS[i].length; j++) if (ROWS[i][j] !== 'update' || st.update) out.push(ROWS[i][j]);
    return out;
  }
  function moveFocus(dx, dy) {                                  // пульт: кнопки - верхний ряд, индикатор - нижний
    var rows = st.modes ? 2 : 1, r = -1, c = -1, ids;
    for (var i = 0; i < rows; i++) {
      ids = rowIds(i);
      for (var j = 0; j < ids.length; j++) if ($(ids[j]) === document.activeElement) { r = i; c = j; }
    }
    if (r < 0) { $('toggle').focus(); return true; }            // фокус потерян - вернуть на переключатель
    if (dy > 0 && r + 1 < rows) { $(st.mode === 'native' ? 'ui-native' : 'ui-overlay').focus(); return true; }
    if (dy < 0 && r > 0) { ids = rowIds(0); $(ids[Math.min(c, ids.length - 1)]).focus(); return true; }
    ids = rowIds(r);
    var nc = c + dx;
    if (!dx || nc < 0 || nc >= ids.length) return false;
    $(ids[nc]).focus();
    return true;
  }
  var ARROWS = { 37: [-1, 0], 38: [0, -1], 39: [1, 0], 40: [0, 1] };
  document.addEventListener('keydown', function (e) {
    if (mode !== 'setup') return;
    if (e.keyCode === 461 || e.keyCode === 27) { e.preventDefault(); cancelAll(); window.close(); return; }
    if (!ARROWS.hasOwnProperty(e.keyCode)) return;
    if (pending) { pending = ''; renderSetup(); }               // любая стрелка отменяет подтверждение
    if (moveFocus(ARROWS[e.keyCode][0], ARROWS[e.keyCode][1])) e.preventDefault();
  });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { appeared(); return; }
    if (mode === 'setup') { cancelAll(); window.close(); }
  });

  /* --- запуск / relaunch от бэкенда --- */
  function handle(input, initial) {
    var p = paramsOf(input);
    if (!hasVolume(p)) { showSetup(p); return; }                // открыто из списка приложений (или снова после переключения)
    if (mode === 'setup' && !document.hidden) return;           // фоновые значения не закрывают настройки
    if (!accept(p)) {                                           // чужое/устаревшее значение
      if (initial) window.close();                              // не оставлять пустое окно-карточку
      return;
    }
    show(p, false);
  }
  window.webOSRelaunch = function () {
    var p = {};
    try { p = window.PalmSystem ? PalmSystem.launchParams : {}; } catch (e) { /* ignore */ }
    handle(p, false);
    return true;
  };
  document.addEventListener('webOSRelaunch', function (event) {
    if (event && event.detail !== undefined) handle(event.detail, false);
  }, true);
  var initial = {};
  try { initial = window.PalmSystem ? PalmSystem.launchParams : {}; } catch (e) { /* ignore */ }
  handle(initial, true);
}());
