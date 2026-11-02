// Рендер OSD в headless Chromium 1920x1080 поверх тестового фона -> tests/out/*.png
//   node tests/render-preview.cjs        (CHROMIUM=/path/to/chromium при необходимости)
// native: true - рядом рисуется МАКЕТ нативного индикатора ARC «+ / динамик / −» (по фото с ТВ и метрикам
// StarfishVolume.qml: подсветка Ø60, отступ справа 57, центр по вертикали 540) - только для превью.
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const cases = [
  { name: 'badge-22', params: { src: 'cec', volume: 22, mute: false }, native: true },
  { name: 'badge-100-light', params: { src: 'cec', volume: 100, mute: false }, bg: 'bg-light.jpg', native: true },
  { name: 'badge-muted', params: { src: 'cec', volume: 22, mute: true }, native: true },
  { name: 'badge-7-light', params: { src: 'cec', volume: 7 }, bg: 'bg-light.jpg', native: true },
  { name: 'pill-center-right-22', params: { src: 'cec', volume: 22, variant: 'pill' }, native: true },
  { name: 'ring-center-right-42', params: { src: 'cec', volume: 42, variant: 'ring' }, native: true },
  { name: 'ring-42', params: { src: 'cec', volume: 42, variant: 'ring', pos: 'top-right' } },
  // экран настройки: язык ТВ, ответ remote-setup.sh status (null - не отвечает), клавиши пульта
  { name: 'setup-ru', params: {}, locale: 'ru-RU', out: 'running\npython3: 3.10.13; cec: /dev/cec0 ; args: --position center-right\nui: native (QML patch active)\nqml: yes\n2026-11-01 23:11:12.904 sound output: external_arc -> indicator on' },
  { name: 'setup-ru-no-qml', params: {}, locale: 'ru-RU', out: 'running\nui: overlay\nqml: no (no QML compositor - webOS 11 Flutter UI?)' },
  // выбор индикатора: вниз - кнопка текущего режима; первое нажатие другого - подтверждение
  { name: 'setup-en-modes-focus', params: {}, locale: 'en-US', out: 'running\nui: overlay\nqml: yes', keys: ['ArrowDown'] },
  { name: 'setup-ru-confirm', params: {}, locale: 'ru-RU', out: 'running\nui: native (QML patch active)\nqml: yes', keys: ['ArrowDown', 'ArrowRight', 'Enter'] },
  { name: 'setup-ru-switching', params: {}, locale: 'ru-RU', out: 'switching\nui: native (QML patch active)\nqml: yes' },
  // экран открыт снова после переключения (params.switched) - итог переключения
  { name: 'setup-ru-switched', params: { switched: 'native' }, locale: 'ru-RU', out: 'running\nui: native (QML patch active)\nqml: yes' },
  { name: 'setup-en-native-failed', params: {}, locale: 'en-US', out: 'running\nui: overlay (system indicator disabled after a failed attempt)\nqml: yes' },
  { name: 'setup-en-switch-error', params: {}, locale: 'en-US',
    out: 'running\nui: overlay\nqml: yes\nswitch error: not applicable - StarfishVolumeView.qml: no unique \'    function updateVisibility() {\' line' },
  { name: 'setup-en-test-focus', params: {}, locale: 'en-US', out: 'running\nui: overlay', keys: ['ArrowRight'] },
  { name: 'setup-disabled', params: {}, locale: 'ru-RU', out: 'disabled\nui: native (QML patch inactive)' },
  { name: 'setup-stopped', params: {}, locale: 'ru-RU',
    out: 'stopped\nui: overlay\nlast error (/tmp/cec-volume-bridge.err):\nerror: unrecognized arguments: --off 2' },
  { name: 'setup-missing-en', params: {}, locale: 'en-US', out: 'missing' },
  { name: 'setup-busy', params: {}, locale: 'ru-RU', out: null },
];
const NATIVE_MOCK = `
  <div style="position:absolute;right:57px;top:540px;width:60px;height:0;z-index:5;color:#f2f2f2;
              filter:drop-shadow(0 2px 6px rgba(0,0,0,.55))">
    <div style="position:absolute;left:0;top:-106px;width:60px;height:60px;border-radius:50%;background:#e6e6e6;
                box-shadow:0 10px 12px rgba(0,0,0,.3)">
      <div style="position:absolute;left:17px;top:29px;width:26px;height:2.5px;background:#373a41"></div>
      <div style="position:absolute;left:28.75px;top:17px;width:2.5px;height:26px;background:#373a41"></div>
    </div>
    <svg viewBox="0 0 48 48" style="position:absolute;left:8px;top:-22px;width:44px;height:44px">
      <path d="M8 18.5h7.5L26 10v28l-10.5-8.5H8z" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>
      <path d="M31.5 18a8.5 8.5 0 0 1 0 12M35.8 13.6a14.5 14.5 0 0 1 0 20.8" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
    </svg>
    <div style="position:absolute;left:12px;top:75px;width:36px;height:5px;border-radius:3px;background:currentColor"></div>
  </div>`;
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || '/usr/local/bin/chromium', args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  for (const c of cases) {
    await page.addInitScript(([p, locale, out]) => {
      window.PalmSystem = { launchParams: p, locale, activate() {}, setWindowProperty() {}, setInputRegion() {}, setKeyMask() {} };
      window.earcRootExec = (cmd, cb) => { if (out !== null) setTimeout(() => cb(null, out), 10); };
      window.close = () => {};
    }, [c.params, c.locale || 'en-US', c.out === undefined ? 'running\nui: overlay' : c.out]);
    await page.goto('file://' + path.join(root, 'app', 'index.html'));
    await page.addStyleTag({ content: `html{background:#000 url("file://${path.join(root, 'tests', 'fixtures', c.bg || 'bg-dark.jpg')}") center/cover}` });
    if (c.native) await page.evaluate((html) => document.body.insertAdjacentHTML('beforeend', html), NATIVE_MOCK);
    await page.waitForTimeout(450);
    for (const k of c.keys || []) { await page.keyboard.press(k); await page.waitForTimeout(200); }
    const out = path.join(root, 'tests', 'out', c.name + '.png');
    await page.screenshot({ path: out });
    console.log('saved', out);
  }
  await page.close();
  await browser.close();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
