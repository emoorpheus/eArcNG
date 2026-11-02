// Тест жизненного цикла overlay.js в node:vm с фейковыми DOM/PalmSystem/таймерами.  node tests/test-overlay.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function classList() {
  const set = new Set();
  return { add: (c) => set.add(c), remove: (c) => set.delete(c), contains: (c) => set.has(c),
    toggle: (c, on) => { if (on === undefined ? !set.has(c) : on) set.add(c); else set.delete(c); }, _set: set };
}
function element(id) {
  const props = {};
  return { id, textContent: '', className: '', hidden: true, onclick: null, attrs: {}, classList: classList(), offsetWidth: 100,
    style: { setProperty: (k, v) => { props[k] = v; }, props },
    setAttribute(k, v) { this.attrs[k] = String(v); }, focus() { doc.activeElement = this; } };
}
const SETUP_IDS = ['hero', 'status', 'ui', 'details', 'toggle', 'test', 'modes', 'modes-label', 'ui-native', 'ui-overlay'];
const el = Object.fromEntries(['osd', 'osd-value'].concat(SETUP_IDS).map((id) => [id, element(id)]));
const listeners = {};
const doc = { body: { className: '' }, documentElement: { lang: 'en' }, hidden: false, activeElement: null,
  getElementById: (id) => el[id],
  addEventListener: (name, fn) => { listeners[name] = fn; } };

let now = 0, nextId = 0;
const timers = new Map();
function advance(ms) {
  const end = now + ms;
  for (;;) {
    let best = null;
    for (const [id, t] of timers) if (t.due <= end && (!best || t.due < best[1].due)) best = [id, t];
    if (!best) break;
    timers.delete(best[0]); now = best[1].due; best[1].fn();
  }
  now = end;
}
const RUNNING = 'running\npython3: 3.10.4; cec: /dev/cec0 ; args: none\nui: native (QML patch active)\n';
let execReply = RUNNING;
const log = { activate: 0, close: 0, types: [], focus: [], regions: [], masks: [], exec: [] };
const wall0 = 1.7e12;                                           // Date.now() идёт вместе с фейковыми таймерами
const win = {
  document: doc,
  Date: { now: () => wall0 + now },
  PalmSystem: { locale: 'ru-RU',
    launchParams: { src: 'cec', sid: 'a', seq: 1, volume: 21, mute: false, max: 100, reason: 'report', ttl: 2000 },
    activate() { log.activate++; },
    setWindowProperty(k, v) { if (k === '_WEBOS_WINDOW_TYPE') log.types.push(v); if (k === 'needFocus') log.focus.push(v); },
    setInputRegion(r) { log.regions.push(r[0]); }, setKeyMask(m) { log.masks.push(Array.from(m)); } },
  setTimeout(fn, ms) { timers.set(++nextId, { fn, due: now + ms }); return nextId; },
  clearTimeout(id) { timers.delete(id); },
  close() { log.close++; },
  earcRootExec(cmd, cb) { log.exec.push(cmd); cb(null, execReply); },
};
win.window = win;
const relaunch = (p) => { win.PalmSystem.launchParams = p; return win.webOSRelaunch(); };
const cls = (c) => el.osd.classList.contains(c);
const code = fs.readFileSync(path.join(__dirname, '..', 'app', 'overlay.js'), 'utf8');
vm.runInNewContext(code, win);

// 1. первый запуск с реальным значением
assert.equal(doc.body.className, 'overlay-mode');
assert.equal(el['osd-value'].textContent, '21');
assert.equal(el.osd.style.props['--level'], '0.21');
assert.equal(log.types.at(-1), '_WEBOS_WINDOW_TYPE_FLOATING');
assert.equal(log.regions.at(-1).width, 0, 'зона ввода нулевая - пульт у приложения под оверлеем');
assert.deepEqual(log.masks.at(-1), ['KeyMaskDefault']);
advance(20); assert.ok(cls('show'));
advance(40); assert.equal(log.focus.at(-1), 'false', 'фокус отдан после создания окна');
assert.equal(log.activate, 1);

// 2. обновления: seq растёт, повтор/устаревшие/чужие - отбрасываются
relaunch({ src: 'cec', sid: 'a', seq: 2, volume: 22, mute: false, ttl: 2000 });
assert.equal(el['osd-value'].textContent, '22');
listeners.webOSRelaunch({ detail: { src: 'cec', sid: 'a', seq: 2, volume: 22 } });   // тот же relaunch вторым путём
relaunch({ src: 'cec', sid: 'a', seq: 1, volume: 5 });
assert.equal(el['osd-value'].textContent, '22', 'устаревший seq не показывается');
relaunch({ volume: 7, mute: false, output: 'external_arc' });
assert.equal(el['osd-value'].textContent, '22', 'счётчик штатного watcher.js (без src) игнорируется');
listeners.webOSRelaunch({});
assert.equal(doc.body.className, 'overlay-mode', 'событие без detail не открывает настройки');
assert.equal(log.activate, 1, 'обновление видимого окна не забирает фокус');
relaunch({ src: 'cec', sid: 'b', seq: 1, volume: 23 });
assert.equal(el['osd-value'].textContent, '23', 'новый sid (перезапуск бэкенда) принимается');

// 3. mute / 0 / граница / max / вариант и позиция
relaunch({ src: 'cec', sid: 'b', seq: 2, volume: 23, mute: true });
assert.ok(cls('is-muted'));
relaunch({ src: 'cec', sid: 'b', seq: 3, volume: 0, mute: false });
assert.ok(!cls('is-muted') && cls('is-zero'));
relaunch({ src: 'cec', sid: 'b', seq: 4, volume: 100, reason: 'limit' });
assert.ok(cls('is-bump')); advance(400); assert.ok(!cls('is-bump'));
relaunch({ src: 'cec', sid: 'b', seq: 5, volume: 25, max: 50, variant: 'ring', pos: 'bottom-center' });
assert.equal(el.osd.style.props['--level'], '0.5');
assert.equal(el.osd.attrs['data-variant'], 'ring');
assert.equal(el.osd.attrs['data-pos'], 'bottom-center');
relaunch({ src: 'cec', sid: 'b', seq: 6, volume: 30, pos: 'center;evil' });
assert.equal(el.osd.attrs['data-pos'], 'bottom-center', 'неизвестная позиция игнорируется');
relaunch({ src: 'cec', sid: 'p', seq: 1, volume: 31, pos: 'center-right' });
assert.equal(el.osd.attrs['data-pos'], 'center-right', 'center-right (у правой кромки, по центру)');
relaunch({ src: 'cec', sid: 'p', seq: 2, volume: 32, pos: 'center-center' });
assert.equal(el.osd.attrs['data-pos'], 'center-right', 'center-center не поддерживается');

// 4. скрытие как у нативного: 1500 мс + fade 150 по умолчанию; обновление во время fade отменяет закрытие
assert.equal(el.osd.style.props['--osd-fade'], '150ms');
advance(1499); assert.ok(cls('show'));
advance(1); assert.ok(!cls('show'));
relaunch({ src: 'cec', sid: 'b', seq: 7, volume: 31, ttl: 1000 });
advance(150); assert.equal(log.close, 0, 'обновление во время fade отменяет закрытие');
assert.equal(log.activate, 2, 'после fade окно снова активируется один раз');
advance(999); assert.equal(log.close, 0);
advance(1); assert.equal(log.close, 1, 'закрытие через ttl + fade');

// 5. запуск из списка приложений -> настройки: кольцо-статус, режим индикатора, одна кнопка-переключатель
relaunch({});
assert.equal(doc.body.className, 'setup-mode');
assert.equal(log.types.at(-1), '_WEBOS_WINDOW_TYPE_CARD');
assert.equal(log.regions.at(-1).width, 1920);
assert.match(log.exec.at(-1), /remote-setup\.sh status/);
assert.equal(el.status.textContent, 'Работает');
assert.equal(el.hero.attrs['data-state'], 'ok');
assert.equal(el.ui.textContent, 'системный индикатор', 'ui: native (QML patch active)');
assert.equal(el.details.textContent, '', 'работает - без технических подробностей');
assert.equal(el.toggle.textContent, 'Отключить', 'PalmSystem.locale ru-RU -> русский экран; работает -> «Отключить»');
assert.equal(el.toggle.attrs['aria-disabled'], 'false');
assert.equal(doc.documentElement.lang, 'ru');
advance(30); assert.equal(doc.activeElement, el.toggle);
relaunch({ src: 'cec', sid: 'b', seq: 8, volume: 40 });
assert.equal(doc.body.className, 'setup-mode', 'фоновые значения не закрывают настройки');
execReply = 'disabled\npython3: 3.10.4\nui: native (QML patch inactive)\n';
el.toggle.onclick(); assert.match(log.exec.at(-1), /remote-setup\.sh disable/);
assert.equal(el.status.textContent, 'Отключён');
assert.equal(el.hero.attrs['data-state'], 'off');
assert.equal(el.ui.textContent, '', 'режим индикатора - только когда работает');
assert.equal(el.toggle.textContent, 'Включить');
execReply = RUNNING;
el.toggle.onclick(); assert.match(log.exec.at(-1), /remote-setup\.sh enable/);
assert.equal(el.status.textContent, 'Работает');
assert.equal(el.toggle.textContent, 'Отключить');
// 6. тест-демо возвращается в настройки
el.test.onclick();
assert.equal(doc.body.className, 'overlay-mode');
assert.equal(el['osd-value'].textContent, '18');
advance(260 * 7); assert.equal(el['osd-value'].textContent, '25');
advance(900); assert.ok(cls('is-bump'), 'демо: граница');
advance(900); assert.ok(cls('is-muted'), 'демо: mute');
advance(900 + 1499); assert.equal(doc.body.className, 'overlay-mode');
advance(1 + 150);
assert.equal(doc.body.className, 'setup-mode', 'после демо - снова настройки');
assert.equal(log.close, 1);

// 7. пульт: влево/вправо между двумя кнопками; Back и Esc закрывают настройки; скрытие системой тоже
function key(code) { let p = false; listeners.keydown({ keyCode: code, preventDefault() { p = true; } }); return p; }
advance(30); assert.equal(doc.activeElement, el.toggle, 'после демо фокус снова на переключателе');
assert.ok(key(39)); assert.equal(doc.activeElement, el.test, 'вправо - «Тест»');
assert.ok(!key(39)); assert.equal(doc.activeElement, el.test, 'правее ничего нет');
assert.ok(!key(38) && !key(40)); assert.equal(doc.activeElement, el.test, 'вверх/вниз ничего нет');
assert.ok(key(37)); assert.equal(doc.activeElement, el.toggle, 'влево - переключатель');
assert.ok(!key(37)); assert.equal(doc.activeElement, el.toggle, 'левее ничего нет');
for (const code of [37, 38, 39, 40]) {
  doc.activeElement = null;
  assert.ok(key(code)); assert.equal(doc.activeElement, el.toggle, 'фокус потерян - стрелка ' + code + ' возвращает на переключатель');
}
assert.ok(!key(13), 'OK/Enter обрабатывает сама кнопка');
assert.ok(key(461)); assert.equal(log.close, 2);
assert.ok(key(27)); assert.equal(log.close, 3);
doc.hidden = true; listeners.visibilitychange(); assert.equal(log.close, 4);
relaunch({ src: 'cec', sid: 'b', seq: 9, volume: 41 });
assert.equal(doc.body.className, 'overlay-mode', 'значение восстанавливает скрытый экземпляр');
const focused = doc.activeElement;
assert.ok(!key(40) && !key(461)); assert.equal(log.close, 4, 'в режиме OSD пульт не обрабатывается');
assert.equal(doc.activeElement, focused);

// 8. первый запуск с чужими параметрами закрывает окно сразу
const win2 = Object.assign({}, win, { PalmSystem: Object.assign({}, win.PalmSystem, { launchParams: { volume: 9, mute: false } }) });
let closes2 = 0; win2.close = () => { closes2++; }; win2.window = win2;
Object.assign(doc, { hidden: false }); doc.body.className = '';
vm.runInNewContext(code, win2);
assert.equal(closes2, 1, 'старый watcher.js не может открыть пустое окно-карточку');

// 9. тайминги (отдельные экземпляры): появление - ttl, на экране - kt (отсчёт от нажатия), fade, keepalive, вид
const html = fs.readFileSync(path.join(__dirname, '..', 'app', 'index.html'), 'utf8');
assert.match(html, /id="osd" class="osd" data-variant="badge" data-pos="center-right"/, 'по умолчанию - кружок у правой кромки');
function instance(params) {
  const lg = { close: 0, activate: 0 };
  const w = Object.assign({}, win, { close() { lg.close++; },
    PalmSystem: Object.assign({}, win.PalmSystem, { launchParams: params, activate() { lg.activate++; } }) });
  w.window = w;
  vm.runInNewContext(code, w);
  return { lg, relaunch: (p) => { w.PalmSystem.launchParams = p; return w.webOSRelaunch(); } };
}
doc.hidden = false;
advance(10000);                                                 // таймеры прежних экземпляров отработали
// появление = запуск окна (~1,5-1,8 с на OLED65M59LA): полный ttl от появления, а не от давнего нажатия
const k1 = instance({ src: 'cec', sid: 'k1', seq: 1, volume: 33, kt: wall0 + now - 1800 });
advance(20); assert.ok(cls('show'));
advance(1479); assert.ok(cls('show'), 'окно запустилось через 1,8 с после нажатия - видно полный ttl от появления');
advance(1); assert.ok(!cls('show'));
advance(149); assert.equal(k1.lg.close, 0);
advance(1); assert.equal(k1.lg.close, 1, 'и закрывается сразу после fade');
// уже на экране: гаснет через ttl после НАЖАТИЯ (как нативный); запоздавшее значение - минимум 400 мс
const k2 = instance({ src: 'cec', sid: 'k2', seq: 1, volume: 34 });
advance(1000);
k2.relaunch({ src: 'cec', sid: 'k2', seq: 2, volume: 35, kt: wall0 + now - 300 });
advance(1199); assert.ok(cls('show'), 'ответ пришёл через 300 мс после нажатия');
advance(1); assert.ok(!cls('show'), 'на экране: гаснет через 1500 мс после НАЖАТИЯ, а не после ответа саундбара');
advance(150); assert.equal(k2.lg.close, 1);
const k6 = instance({ src: 'cec', sid: 'k6', seq: 1, volume: 36 });
advance(1000);
k6.relaunch({ src: 'cec', sid: 'k6', seq: 2, volume: 37, kt: wall0 + now - 1400 });
advance(399); assert.ok(cls('show'));
advance(1); assert.ok(!cls('show'), 'запоздавшее значение на экране видно минимум 400 мс');
advance(150); assert.equal(k6.lg.close, 1);
for (const kt of ['x', wall0 + now + 5000, wall0 + now - 120000]) {   // мусор / из будущего / слишком старое
  const k3 = instance({ src: 'cec', sid: 'k3' + kt, seq: 1, volume: 35 });
  advance(1000);
  k3.relaunch({ src: 'cec', sid: 'k3' + kt, seq: 2, volume: 36, kt, ttl: 50, fade: 0 });
  advance(1499); assert.ok(cls('show'), 'kt=' + kt + ': обычный ttl (50 вне диапазона -> 1500)');
  advance(1); assert.ok(!cls('show'));
  assert.equal(el.osd.style.props['--osd-fade'], '0ms');
  advance(1); assert.equal(k3.lg.close, 1);
}
const k4 = instance({ src: 'cec', sid: 'k4', seq: 1, volume: 36, keep: 30000 });
advance(1500 + 150); assert.ok(!cls('show')); assert.equal(k4.lg.close, 0, 'keepalive: скрытое окно остаётся');
k4.relaunch({ src: 'cec', sid: 'k4', seq: 2, volume: 37, keep: 30000 });
assert.equal(el['osd-value'].textContent, '37');
advance(20); assert.ok(cls('show'), 'повторный показ - в том же окне, без нового запуска');
advance(1480 + 150 + 29999); assert.equal(k4.lg.close, 0);
advance(1); assert.equal(k4.lg.close, 1, 'через keep мс после скрытия окно закрывается');
const k5 = instance({ src: 'cec', sid: 'k5', seq: 1, volume: 38, keep: 900000 });
advance(1500 + 150); assert.equal(k5.lg.close, 1, 'keep вне диапазона (макс. 600 с) игнорируется');
assert.equal(k2.lg.close, 1);

// 10. разметка экрана настройки и язык = язык ТВ: русский для русского и языков СНГ, иначе английский
const html2 = html.replace(/\s+/g, ' ');
assert.match(html2, /<html lang="en">/);
assert.match(html2, /<main id="setup" class="setup">/);
assert.match(html2, /<button id="toggle" class="btn">Enable<\/button> <button id="test" class="btn">Test<\/button>/, 'без JS - английский');
assert.doesNotMatch(html2, /id="(?:desc|enable|disable|theme-oled|theme-regular)"|data-theme|class="chip/, 'длинное описание, отдельные кнопки и выбор темы убраны');
assert.match(html2, /<div id="modes" class="setup-modes" hidden> <div id="modes-label" class="setup-modes-label">Volume indicator<\/div> <button id="ui-native" class="mode" aria-pressed="false">System<\/button><button id="ui-overlay" class="mode" aria-pressed="false">Overlay<\/button> <\/div> <div class="setup-credit">/,
  'выбор индикатора: скрыт до ответа status, без JS - английский');
assert.doesNotMatch(code, /localStorage|theme/i, 'одна тема (OLED), ничего не хранится');
const bridgeVersion = /^VERSION = "([\d.]+)"$/m.exec(fs.readFileSync(path.join(__dirname, '..', 'backend', 'cec_volume_bridge.py'), 'utf8'))[1];
assert.match(html2, new RegExp('<div class="setup-credit">github\\.com/<b>emoorpheus</b>/earcng · ' + bridgeVersion.replace(/\./g, '\\.') + '</div>'),
  'автор и версия внизу экрана = версия моста ' + bridgeVersion);
const usedIds = new Set([...code.matchAll(/\$\('([\w-]+)'\)/g)].map((m) => m[1]));
for (const id of usedIds) assert.match(html, new RegExp('id="' + id + '"'), 'в разметке есть #' + id);
for (const id of SETUP_IDS) assert.ok(usedIds.has(id), 'фейковый DOM соответствует overlay.js: #' + id);

const RU = { toggle: 'Отключить', test: 'Тест', status: 'Работает', ui: 'системный индикатор' };
const EN = { toggle: 'Disable', test: 'Test', status: 'Running', ui: 'system indicator' };
function setup(opts) {
  const lg = { exec: [], bridges: [], close: 0 };
  const ps = Object.assign({}, win.PalmSystem, { launchParams: opts.params || {} });
  delete ps.locale;
  if (opts.palm !== undefined) ps.locale = opts.palm;
  const w = Object.assign({}, win, { PalmSystem: ps, close() { lg.close++; } });
  delete w.earcRootExec;                                        // exec: null - нет ни заглушки, ни PalmServiceBridge
  if (opts.exec !== null) {
    w.earcRootExec = opts.exec || ((cmd, cb) => { lg.exec.push(cmd); cb(null, opts.out !== undefined ? opts.out : 'running\npython3: 3.10.4\nui: native (QML patch active)\n'); });
  }
  if (opts.webos !== undefined) w.webOSSystem = opts.webos;
  if (opts.nav !== undefined) w.navigator = opts.nav;
  if (opts.bridge) {
    w.PalmServiceBridge = function () { this.calls = []; lg.bridges.push(this); };
    w.PalmServiceBridge.prototype.call = function (uri, payload) { this.calls.push([uri, JSON.parse(payload)]); };
  }
  w.window = w;
  doc.documentElement.lang = 'en';
  for (const id of SETUP_IDS) { el[id].textContent = ''; el[id].attrs = {}; el[id].className = ''; el[id].hidden = true; }
  vm.runInNewContext(code, w);
  lg.relaunch = (p) => { w.PalmSystem.launchParams = p; return w.webOSRelaunch(); };
  return lg;
}
function expectLang(L, msg) {
  for (const k of Object.keys(L)) assert.equal(el[k].textContent, L[k], msg + ': ' + k);
  assert.equal(doc.documentElement.lang, L === RU ? 'ru' : 'en', msg + ': html lang');
}
const ruLocales = ['ru-RU', 'uk-UA', 'be-BY', 'kk-KZ', 'ky-KG', 'uz-UZ', 'uz-Cyrl-UZ', 'tg-TJ', 'tk-TM', 'hy-AM', 'az-AZ',
  'ka-GE', 'ro-MD', 'mo', 'ru_RU.UTF-8', 'RU'];
const enLocales = ['en-US', 'en-GB', 'de-DE', 'fr', 'ro-RO', 'pl-PL', 'lt-LT', 'lv-LV', 'et-EE', 'tr-TR', 'zh-Hans-CN', 'es-419', 'ko-KR'];
for (const loc of ruLocales) { setup({ palm: loc }); expectLang(RU, loc); }
for (const loc of enLocales) { setup({ palm: loc }); expectLang(EN, loc); }
assert.equal(doc.body.className, 'setup-mode');

// источники: webOSSystem.locale -> PalmSystem.locale -> navigator.languages[0] -> navigator.language -> английский
setup({ webos: { locale: 'en-US' }, palm: 'ru-RU' }); expectLang(EN, 'webOSSystem важнее PalmSystem');
setup({ webos: {}, palm: 'kk-KZ' }); expectLang(RU, 'webOSSystem без locale -> PalmSystem');
setup({ nav: { languages: ['uk-UA'], language: 'en-US' } }); expectLang(RU, 'navigator.languages[0]');
setup({ nav: { language: 'de-DE' } }); expectLang(EN, 'navigator.language');
setup({ palm: 'english', nav: { language: 'ru' } }); expectLang(RU, 'мусор в locale пропускается');
setup({ palm: '', webos: { locale: 42 } }); expectLang(EN, 'язык неизвестен -> английский');

// ответы remote-setup.sh status: короткий статус, вид кольца, режим индикатора, подробности - только при проблеме
const ERR = 'last error (/tmp/cec-volume-bridge.err):\nTraceback (most recent call last):\n  OSError: [Errno 19]  No such device\n';
const statuses = [   // [ответ, статус en, кольцо, режим индикатора, подробности, кнопка, статус ru, подробности ru]
  ['running\npython3: 3.10.4\nui: overlay\n2026-11-01 23:11:12 sound output: external_arc -> indicator on\n', 'Running', 'ok', 'overlay', '', 'Disable', 'Работает', ''],
  ['running\nui: native (QML patch inactive)\n', 'Running', 'ok', 'overlay · system indicator unavailable', '', 'Disable', 'Работает', ''],
  ['running\nui: native (QML patch foreign)\n', 'Running', 'ok', 'overlay · system indicator unavailable', '', 'Disable', 'Работает', ''],
  ['running\nui: native (QML patch inactive, disabled after a failed attempt)\n', 'Running', 'ok', 'overlay · system indicator unavailable',
    'The system indicator did not start — the overlay is shown', 'Disable', 'Работает', 'Системный индикатор не включился — показывается оверлей'],
  ['running\nui: native (QML patch active, disabled after a failed attempt)\n', 'Running', 'ok', 'system indicator', '', 'Disable', 'Работает', ''],
  ['running\npython3: 3.10.4\n', 'Running', 'ok', '', '', 'Disable', 'Работает', ''],
  ['running\r\nui: overlay\r\n', 'Running', 'ok', 'overlay', '', 'Disable', 'Работает', ''],
  ['stopped\nui: overlay\n' + ERR, 'Stopped', 'error', '', 'Traceback (most recent call last): OSError: [Errno 19] No such device', 'Enable', 'Остановлен', 'Traceback (most recent call last): OSError: [Errno 19] No such device'],
  ['stopped\nui: overlay\n', 'Stopped', 'error', '', '', 'Enable', 'Остановлен', ''],
  ['disabled\nui: native (QML patch active)\n', 'Disabled', 'off', '', '', 'Enable', 'Отключён', ''],
  ['missing\n', 'Not installed', 'error', '', 'Install from a PC: tools/earcng.sh', 'Enable', 'Не установлен', 'Установите с ПК: tools/earcng.sh'],
  ['weird\nui: overlay\n', 'Unknown', 'error', '', 'weird', 'Enable', 'Неизвестно', 'weird'],
  ['constructor\n', 'Unknown', 'error', '', 'constructor', 'Enable', 'Неизвестно', 'constructor'],
  ['', 'Unknown', 'error', '', '', 'Enable', 'Неизвестно', ''],
];
for (const [out, text, kind, ui, details, toggle, textRu, detailsRu] of statuses) {
  setup({ palm: 'en-US', out });
  const m = JSON.stringify(out);
  assert.equal(el.status.textContent, text, m);
  assert.equal(el.hero.attrs['data-state'], kind, m + ': кольцо');
  assert.equal(el.ui.textContent, ui, m + ': режим индикатора');
  assert.equal(el.details.textContent, details, m + ': подробности');
  assert.equal(el.toggle.textContent, toggle, m + ': кнопка');
  assert.equal(el.toggle.attrs['aria-disabled'], 'false', m);
  setup({ palm: 'ru-RU', out });
  assert.equal(el.status.textContent, textRu, m + ' (ru)');
  assert.equal(el.details.textContent, detailsRu, m + ': подробности (ru)');
}

// занятость: кнопка неактивна, вторая команда не запускается; ответ устаревшей команды игнорируется
let pending = null, execs = 0;
setup({ palm: 'en-US', exec: (cmd, cb) => { execs++; pending = cb; } });
assert.equal(el.status.textContent, 'Checking…', 'занятость тоже по-английски');
assert.equal(el.hero.attrs['data-state'], 'busy');
assert.equal(el.toggle.attrs['aria-disabled'], 'true');
el.toggle.onclick(); assert.equal(execs, 1, 'пока идёт проверка, переключатель не запускает вторую команду');
pending(null, 'running\nui: overlay\n'); assert.equal(el.status.textContent, 'Running');
assert.equal(el.toggle.attrs['aria-disabled'], 'false');
el.toggle.onclick(); assert.equal(execs, 2); assert.equal(el.status.textContent, 'Disabling…');
assert.equal(el.ui.textContent, '', 'во время команды режим индикатора скрыт');
pending(new Error('hbchannel: permission denied'));
assert.equal(el.status.textContent, 'Error');
assert.equal(el.hero.attrs['data-state'], 'error');
assert.equal(el.details.textContent, 'hbchannel: permission denied', 'текст ошибки сервиса - в подробностях как есть');
assert.equal(el.toggle.textContent, 'Enable');
el.toggle.onclick(); assert.equal(el.status.textContent, 'Enabling…');
const calls = [];
setup({ palm: 'en-US', exec: (cmd, cb) => { calls.push([cmd, cb]); } });
el.test.onclick();
advance(260 * 7 + 900 * 3 + 1500 + 150);
assert.equal(doc.body.className, 'setup-mode');
assert.equal(calls.length, 2, 'после демо статус запрошен заново');
calls[1][1](null, 'disabled\n'); assert.equal(el.status.textContent, 'Disabled');
calls[0][1](null, 'running\n'); assert.equal(el.status.textContent, 'Disabled', 'поздний ответ прежней проверки не перезаписывает статус');
setup({ palm: 'en-US', exec: null });
assert.equal(el.status.textContent, 'Error');
assert.equal(el.details.textContent, 'No PalmServiceBridge: run the app on the TV');
setup({ palm: 'be-BY', exec: null });
assert.equal(el.status.textContent, 'Ошибка');
assert.equal(el.details.textContent, 'Нет PalmServiceBridge: запустите приложение на ТВ');

// уточнение через settingsservice (localeInfo.locales.UI) - асинхронно, только на экране настройки
let lb = setup({ palm: 'en-US', bridge: true });
assert.equal(lb.bridges.length, 1);
assert.deepEqual(lb.bridges[0].calls, [['luna://com.webos.settingsservice/getSystemSettings', { keys: ['localeInfo'] }]]);
expectLang(EN, 'до ответа settingsservice');
lb.bridges[0].onservicecallback(JSON.stringify({ returnValue: true, settings: { localeInfo: { locales: { UI: 'ru-RU', TV: 'ru-RU' } } } }));
expectLang(RU, 'ответ settingsservice переключает язык и перерисовывает статус');
for (const reply of [JSON.stringify({ returnValue: false, errorText: 'Denied' }), 'not json',
  JSON.stringify({ settings: { localeInfo: { locales: { UI: 42 } } } }), JSON.stringify({ settings: { localeInfo: { locales: {} } } }),
  JSON.stringify({ settings: { localeInfo: { locales: { UI: '!!' } } } })]) {
  lb = setup({ palm: 'ru-RU', bridge: true });
  lb.bridges[0].onservicecallback(reply);
  expectLang(RU, 'отказ/мусор settingsservice не меняет язык: ' + reply);
}
lb = setup({ palm: 'ru-RU', bridge: true, params: { src: 'cec', sid: 'L', seq: 1, volume: 44 } });
assert.equal(doc.body.className, 'overlay-mode');
assert.equal(lb.bridges.length, 0, 'показ громкости не делает запросов к сервисам');

// 11. выбор индикатора: кнопки только когда мост работает и есть QML; переключение - вторым нажатием
const QN = 'running\npython3: 3.10.4\nui: native (QML patch active)\nqml: yes\n';
const QO = 'running\nui: overlay\nqml: yes\n';
const mode = (id) => [el[id].attrs['aria-pressed'], el[id].attrs['aria-disabled'], el[id].className].join(' ');
setup({ palm: 'en-US', out: QN });
assert.equal(el.modes.hidden, false, 'работает + qml: yes -> выбор индикатора виден');
assert.equal(el['modes-label'].textContent, 'Volume indicator');
assert.equal(el['ui-native'].textContent, 'System');
assert.equal(el['ui-overlay'].textContent, 'Overlay');
assert.equal(mode('ui-native'), 'true false mode');
assert.equal(mode('ui-overlay'), 'false false mode');
assert.equal(el.ui.textContent, '', 'режим показан кнопками - строка режима не дублирует');
setup({ palm: 'en-US', out: QO });
assert.equal(mode('ui-native'), 'false false mode');
assert.equal(mode('ui-overlay'), 'true false mode');
const FAILED_EN = 'The system indicator did not start — the overlay is shown';
const choice = [   // [ответ, виден выбор, нажата, строка режима, подробности]
  ['running\nui: overlay\nqml: no (no QML compositor - webOS 11 Flutter UI?)\n', true, 'overlay', 'overlay', ''],
  ['running\nui: overlay\n', true, 'overlay', 'overlay', ''],
  ['disabled\nui: overlay\nqml: yes\n', true, 'overlay', '', ''],
  ['stopped\nui: overlay\nqml: yes\n', true, 'overlay', '', ''],
  ['missing\n', true, 'overlay', '', 'Install from a PC: tools/earcng.sh'],
  ['running\nui: native (QML patch inactive)\nqml: yes\n', false, 'overlay', 'overlay · system indicator unavailable', ''],
  ['running\nui: native (QML patch foreign)\nqml: yes\n', false, 'overlay', 'overlay · system indicator unavailable', ''],
  ['running\nui: overlay (system indicator disabled after a failed attempt)\nqml: yes\n', false, 'overlay', '', FAILED_EN],
  ['running\nui: native (QML patch inactive, disabled after a failed attempt)\nqml: yes\n', false, 'overlay', 'overlay · system indicator unavailable', FAILED_EN],
  ['running\nui: native (QML patch active, disabled after a failed attempt)\nqml: yes\n', false, 'native', '', ''],
  ['running\nui: overlay (system indicator disabled after a failed attempt)\nqml: no (no QML compositor - webOS 11 Flutter UI?)\n', true, 'overlay', 'overlay', FAILED_EN],
  ['stopped\nui: overlay (system indicator disabled after a failed attempt)\nqml: yes\n' + ERR, true, 'overlay', '', 'Traceback (most recent call last): OSError: [Errno 19] No such device'],
  ['running\r\nui: overlay\r\nqml: yes\r\n', false, 'overlay', '', ''],
];
for (const [out, hidden, pressed, ui, details] of choice) {
  setup({ palm: 'en-US', out });
  const m = JSON.stringify(out);
  assert.equal(el.modes.hidden, hidden, m + ': выбор индикатора');
  if (!hidden) assert.equal(el['ui-' + pressed].attrs['aria-pressed'], 'true', m + ': нажата ' + pressed);
  assert.equal(el.ui.textContent, ui, m + ': строка режима');
  assert.equal(el.details.textContent, details, m + ': подробности');
}

// подтверждение: первое нажатие - предупреждение, второе - "switch <режим>"; стрелка отменяет; текущий режим - ничего
let ex = [], reply = QN;
setup({ palm: 'en-US', exec: (cmd, cb) => { ex.push(cmd); cb(null, reply); } });
advance(30); assert.equal(doc.activeElement, el.toggle);
el['ui-native'].onclick();
assert.equal(ex.length, 1, 'уже системный индикатор - команда не нужна'); assert.equal(el.details.textContent, '');
el['ui-overlay'].focus(); el['ui-overlay'].onclick();
assert.equal(ex.length, 1, 'первое нажатие ничего не запускает');
assert.equal(el.details.textContent, 'Press again: the TV interface restarts, open apps will close');
assert.equal(mode('ui-overlay'), 'false false mode is-pending');
el['ui-native'].onclick();
assert.equal(el.details.textContent, '', 'нажатие текущего режима отменяет подтверждение');
assert.equal(mode('ui-overlay'), 'false false mode');
el['ui-overlay'].onclick(); assert.ok(el.details.textContent);
assert.ok(key(37)); assert.equal(el.details.textContent, '', 'стрелка отменяет подтверждение');
assert.equal(doc.activeElement, el['ui-native']);
assert.ok(!key(37)); assert.ok(key(39));
el['ui-overlay'].onclick(); assert.equal(ex.length, 1, 'после отмены снова нужно два нажатия');
reply = 'switching\npython3: 3.10.4\nui: native (QML patch active)\nqml: yes\n';
el['ui-overlay'].onclick();
assert.equal(ex.length, 2);
assert.equal(ex[1], 'if [ -f /home/root/cec-volume/remote-setup.sh ]; then sh /home/root/cec-volume/remote-setup.sh switch overlay; else echo missing; fi');
assert.equal(el.status.textContent, 'Switching…');
assert.equal(el.hero.attrs['data-state'], 'busy');
assert.equal(el.details.textContent, 'The TV screen may go black for about 15 seconds, open apps will close');
assert.equal(el.modes.hidden, true, 'во время переключения выбор скрыт');
assert.equal(doc.activeElement, el.toggle, 'фокус со скрытой кнопки - на переключатель');
assert.equal(el.toggle.textContent, 'Disable'); assert.equal(el.toggle.attrs['aria-disabled'], 'true');
el.toggle.onclick(); assert.equal(ex.length, 2, 'во время переключения переключатель не нажимается');
advance(2999); assert.equal(ex.length, 2);
advance(1); assert.equal(ex.length, 3, 'опрос status каждые 3 с'); assert.match(ex[2], /remote-setup\.sh status;/);
assert.equal(el.status.textContent, 'Switching…');
reply = QO;
advance(3000); assert.equal(ex.length, 4);
assert.equal(el.status.textContent, 'Running'); assert.equal(el.details.textContent, '');
assert.equal(el.modes.hidden, false); assert.equal(mode('ui-overlay'), 'true false mode');
advance(10000); assert.equal(ex.length, 4, 'после переключения опрос прекращается');

// ошибка, видимая заранее: "switch error: ..." в подробностях, режим прежний
reply = QO + 'switch error: not applicable - StarfishVolumeView.qml: no unique line\n';
el['ui-native'].onclick(); el['ui-native'].onclick();
assert.match(ex.at(-1), /remote-setup\.sh switch native;/);
assert.equal(el.status.textContent, 'Running');
assert.equal(el.details.textContent, 'not applicable - StarfishVolumeView.qml: no unique line');
assert.equal(el.modes.hidden, false); assert.equal(mode('ui-overlay'), 'true false mode');
reply = 'running\nui: overlay (system indicator disabled after a failed attempt)\nqml: yes\nswitch error: trk501 lg-soundbar-volume-osd is active - disable it first\n';
el['ui-native'].onclick(); el['ui-native'].onclick();
assert.equal(el.details.textContent, 'trk501 lg-soundbar-volume-osd is active - disable it first', 'ошибка переключения важнее подсказки');

// навигация: вниз - кнопка текущего режима, вверх - та же колонка; без выбора вверх/вниз ничего не делают
reply = QN; setup({ palm: 'en-US', exec: (cmd, cb) => { ex.push(cmd); cb(null, reply); } });
advance(30);
assert.ok(!key(38)); assert.equal(doc.activeElement, el.toggle);
assert.ok(key(40)); assert.equal(doc.activeElement, el['ui-native'], 'вниз - текущий режим');
assert.ok(!key(40)); assert.ok(!key(37));
assert.ok(key(39)); assert.equal(doc.activeElement, el['ui-overlay']);
assert.ok(!key(39));
assert.ok(key(38)); assert.equal(doc.activeElement, el.test, 'вверх - та же колонка');
assert.ok(key(40)); assert.equal(doc.activeElement, el['ui-native']);
assert.ok(key(38)); assert.equal(doc.activeElement, el.toggle);
reply = QO; setup({ palm: 'en-US', exec: (cmd, cb) => { ex.push(cmd); cb(null, reply); } });
advance(30); assert.ok(key(39)); assert.ok(key(40)); assert.equal(doc.activeElement, el['ui-overlay'], 'вниз с «Тест» - текущий режим');
for (const code of [37, 38, 39, 40]) {
  doc.activeElement = null;
  assert.ok(key(code)); assert.equal(doc.activeElement, el.toggle, 'фокус потерян - на переключатель');
}

// занятость: выбор не нажимается; открытие во время переключения - ждать; Back останавливает опрос
let cb1 = null; ex = [];
setup({ palm: 'en-US', exec: (cmd, cb) => { ex.push(cmd); cb1 = cb; } });
cb1(null, QO); el.toggle.onclick(); assert.equal(el.status.textContent, 'Disabling…');
assert.equal(el.modes.hidden, false); assert.equal(mode('ui-native'), 'false true mode');
el['ui-native'].onclick(); el['ui-native'].onclick();
assert.equal(ex.length, 2); assert.equal(el.details.textContent, '', 'пока идёт команда, выбор не работает');
cb1(null, 'disabled\nui: overlay\nqml: yes\n'); assert.equal(el.modes.hidden, true);
el['ui-native'].onclick(); assert.equal(el.details.textContent, '', 'скрытый выбор не работает');
ex = []; reply = 'switching\nui: overlay\nqml: yes\n';
const lsw = setup({ palm: 'ru-RU', exec: (cmd, cb) => { ex.push(cmd); cb(null, reply); } });
assert.equal(el.status.textContent, 'Переключение…');
assert.equal(el.details.textContent, 'Экран ТВ может погаснуть примерно на 15 секунд, открытые приложения закроются');
assert.equal(el.toggle.textContent, 'Отключить'); assert.equal(el.modes.hidden, true);
assert.ok(key(461)); assert.equal(lsw.close, 1);
advance(10000); assert.equal(ex.length, 1, 'после Back опрос не продолжается');
reply = QN; setup({ palm: 'ru-RU', exec: (cmd, cb) => { ex.push(cmd); cb(null, reply); } });
assert.equal(el['modes-label'].textContent, 'Индикатор громкости');
assert.equal(el['ui-native'].textContent, 'Системный'); assert.equal(el['ui-overlay'].textContent, 'Оверлей');
el['ui-overlay'].onclick(); assert.equal(el.details.textContent, 'Нажмите ещё раз: интерфейс ТВ перезапустится, открытые приложения закроются');
reply = 'running\nui: overlay (system indicator disabled after a failed attempt)\nqml: yes\n';
setup({ palm: 'ru-RU', exec: (cmd, cb) => { ex.push(cmd); cb(null, reply); } });
assert.equal(el.details.textContent, 'Системный индикатор не включился — показывается оверлей');

// 12. после переключения remote-setup.sh открывает экран снова: params.switched -> итог в подробностях
const FAILED_RU = 'Системный индикатор не включился — показывается оверлей';
const switchedCases = [   // [switched, ответ status, язык, подробности]
  ['native', QN, 'ru-RU', 'Готово: включён системный индикатор'],
  ['native', QN, 'en-US', 'Done: the system indicator is on'],
  ['overlay', QO, 'ru-RU', 'Готово: включён оверлей'],
  ['overlay', QO, 'en-US', 'Done: the overlay is on'],
  ['native', 'running\nui: overlay (system indicator disabled after a failed attempt)\nqml: yes\n', 'ru-RU', FAILED_RU],
  ['native', QO, 'en-US', ''],                                  // режим не тот - без «Готово»
  ['overlay', QN, 'en-US', ''],
  ['native', 'disabled\nui: native (QML patch active)\nqml: yes\n', 'en-US', ''],
  ['native', QN + 'switch error: trk501 lg-soundbar-volume-osd is active - disable it first\n', 'en-US',
    'trk501 lg-soundbar-volume-osd is active - disable it first'],
  ['bogus', QN, 'en-US', ''],
];
for (const [sw, out, palm, details] of switchedCases) {
  setup({ palm, out, params: { switched: sw } });
  assert.equal(doc.body.className, 'setup-mode', 'switched=' + sw + ': экран настроек');
  assert.equal(el.details.textContent, details, 'switched=' + sw + ' ' + JSON.stringify(out));
}
setup({ palm: 'en-US', out: QN, params: JSON.stringify({ switched: 'native' }) });
assert.equal(el.details.textContent, 'Done: the system indicator is on', 'launchParams строкой JSON');
ex = []; reply = QN;
setup({ palm: 'en-US', exec: (cmd, cb) => { ex.push(cmd); cb(null, reply); }, params: { switched: 'native' } });
assert.equal(el.details.textContent, 'Done: the system indicator is on');
el.toggle.onclick(); assert.match(ex.at(-1), /remote-setup\.sh disable/);
assert.equal(el.details.textContent, '', 'новое действие - итог прежнего переключения не показывается');
reply = 'switching\nui: native (QML patch active)\nqml: yes\n';
const lre = setup({ palm: 'en-US', exec: (cmd, cb) => { ex.push(cmd); cb(null, reply); }, params: { switched: 'overlay' } });
assert.equal(el.status.textContent, 'Switching…', 'открыт до конца переключения - ждёт');
reply = QO; advance(3000);
assert.equal(el.details.textContent, 'Done: the overlay is on', 'итог после опроса');
lre.relaunch({});
assert.equal(el.details.textContent, '', 'снова из списка приложений - без итога');
lre.relaunch({ switched: 'overlay' });
assert.equal(el.details.textContent, 'Done: the overlay is on', 'relaunch открытого экрана с итогом');

// 13. своё приложение eArcNG: appinfo.json, значки, id везде один
const ROOT = path.join(__dirname, '..');
const read = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8');
const info = JSON.parse(read('app/appinfo.json'));
const APP_ID = 'com.github.emoorpheus.earcng';
assert.equal(info.id, APP_ID);
assert.equal(info.title, 'eArcNG');
assert.equal(info.version, bridgeVersion, 'версия приложения = версия моста');
assert.equal(info.type, 'web'); assert.equal(info.main, 'index.html');
for (const [k, v] of Object.entries({ visible: true, transparent: true, defaultWindowType: 'card', handlesRelaunch: true,
  noSplashOnLaunch: true, spinnerOnLaunch: false, disableBackHistoryAPI: true, resolution: '1920x1080' })) {
  assert.deepEqual(info[k], v, 'appinfo.' + k + ' (окно card + плавающий оверлей без фокуса)');
}
function pngSize(f) {
  const b = fs.readFileSync(path.join(ROOT, 'app', f));
  assert.equal(b.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', f + ' - PNG');
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
}
assert.deepEqual(pngSize(info.icon), [130, 130], 'icon.png 130x130');
assert.deepEqual(pngSize(info.largeIcon), [256, 256], 'largeIcon.png 256x256');
assert.match(html, /<title>eArcNG<\/title>/);
assert.match(read('backend/cec_volume_bridge.py'), new RegExp('^DEFAULT_APP_ID = "' + APP_ID.replace(/\./g, '\\.') + '"$', 'm'), 'мост запускает eArcNG');
assert.match(read('backend/remote-setup.sh'), new RegExp('^APP_ID=\\$\\{APP_ID:-' + APP_ID.replace(/\./g, '\\.') + '\\}$', 'm'));
assert.ok(read('tools/earcng.sh').includes('/' + APP_ID + '/appinfo.json'), 'preflight ищет eArcNG');
console.log('overlay lifecycle tests: ALL PASSED');
