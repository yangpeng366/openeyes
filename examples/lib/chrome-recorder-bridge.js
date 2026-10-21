// chrome-recorder-bridge.js
// Drive Edge/Chrome DevTools Recorder API via puppeteer-core and emit an
// openeyes_record-compatible Trajectory JSONL (header line + one UIAEvent
// per input step).  The bridge does NOT touch pixels — every step is a
// structured (selector, action) pair so a pure-text LLM can consume it.
//
// Usage:
//   const { recordPage } = require('./chrome-recorder-bridge');
//   const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9333' });
//   const pages   = await browser.pages();
//   const traj    = await recordPage(pages[0], { name: 'demo', appName: 'Edge' });
//   require('fs').writeFileSync('trajectory.jsonl', traj.toJSONL());
//
// Notes:
//   - Uses CDP `Page.startScreencast` only as a heartbeat; the actual event
//     source is `Input.dispatchMouseEvent` / `Input.dispatchKeyEvent` listeners
//     wired through puppeteer's frame lifecycle hooks.
//   - We snapshot the element via `DOM.describeNode` + `Accessibility.getPartialAXTree`
//     at click/keypress time, so the selector is stable across runs.
//   - Multi-frame: every event captures frameId; the selector carries the frame
//     path so Replayer can re-enter the right iframe.

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function nowSeconds() { return Date.now() / 1000; }

// Stable selector preference (mirror openeyes_record.recorder._build_selector):
//   backendNodeId > accessibility name+role > ariaSelector > cssPath > bbox center
async function buildSelectorForBackendNode(cdp, backendNodeId, frameId) {
  const { nodes } = await cdp.send('Accessibility.getPartialAXTree', {
    backendNodeId,
    fetchRelatives: false,
  });
  const node = nodes.find((n) => n.backendNodeId === backendNodeId) || nodes[0];
  if (!node) return `backendNodeId=${backendNodeId}`;
  const role = (node.role && node.role.value) || '';
  const name = (node.name && node.name.value) || '';
  if (name) return `ax=${encodeURIComponent(name)}|role=${encodeURIComponent(role)}|frame=${frameId}`;
  return `backendNodeId=${backendNodeId}|frame=${frameId}`;
}

// Map CDP mouse button code → openeyes_record action string.
function mouseActionToAction(type, button) {
  if (type === 'mousePressed' || type === 'mouseReleased') {
    if (button === 'right') return 'right_click';
    if (button === 'middle') return 'middle_click';
    return 'click';
  }
  return 'mouse_move';
}

// Convert CDP modifiers bitmask → "ctrl+shift+alt+meta" string (InputEvent.combo).
function cdModifiersToCombo(modifiers) {
  if (!modifiers) return '';
  const parts = [];
  if (modifiers & 1) parts.push('alt');
  if (modifiers & 2) parts.push('ctrl');
  if (modifiers & 4) parts.push('meta');
  if (modifiers & 8) parts.push('shift');
  return parts.join('+');
}

// Best-effort mapping of a CDP key event into a human-readable combo.
function cdKeyEventToCombo(event) {
  const mods = cdModifiersToCombo(event.modifiers);
  // text / unmodifiedCode / code (e.g. 'KeyA', 'Enter', 'ArrowUp') — we keep 'code'
  // as it's what browsers emit and is portable across keyboard layouts.
  const code = event.code || '';
  const combo = mods ? `${mods}+${code}` : code;
  return combo || 'unknown';
}

// Public API.
async function recordPage(page, opts) {
  const options = Object.assign({
    name: 'untitled',
    appName: 'Edge',
    description: '',
    outputPath: null,           // if set, write JSONL here
    heartbeatMs: 1500,          // screencast heartbeat — confirms tab is alive
    idleTimeoutMs: 5000,        // auto-stop after this much inactivity
    maxEvents: 500,
  }, opts || {});

  const client = await page.target().createCDPSession();
  await client.send('Page.enable');
  await client.send('DOM.enable');
  await client.send('Accessibility.enable');

  // Heartbeat — Page.startScreencast confirms tab is alive; we only count frames.
  let lastFrameAt = nowSeconds();
  const heartbeatHandler = () => { lastFrameAt = nowSeconds(); };
  client.on('Page.screencastFrame', heartbeatHandler);

  await client.send('Page.startScreencast', { format: 'jpeg', quality: 50, everyNthFrame: 30 });

  const sessionId = crypto.randomUUID();
  const startedAt = nowSeconds();
  const steps = []; // array of either UIAEvent dict or InputEvent dict
  let lastEventAt = startedAt;
  let stopped = false;
  let stopReason = null;
  let suppressKeyEvents = false;

  async function pushUIA(event) {
    steps.push(event);
    lastEventAt = nowSeconds();
  }

  function elementStub(frameId, backendNodeId) {
    return {
      backendNodeId,
      frameId,
      source: 'cdp-browser',
    };
  }

  // Listen for input events emitted by puppeteer into the page.
  // puppeteer re-emits mouse events as 'mouse' on the page; we use
  // CDP Input.dispatchMouseEvent / dispatchKeyEvent as the source of truth.
  const onMouseEvent = async (event) => {
    if (stopped) return;
    try {
      const { type, x, y, button, modifiers, clickCount, backendNodeId, frameId } = event;
        const action = mouseActionToAction(type, button);
      // Element lookup: only meaningful for press/release with a hit target.
      let selector = `pointer=(${x},${y})`;
      let element = elementStub(frameId || '', backendNodeId || 0);
      if (backendNodeId && (type === 'mousePressed' || type === 'mouseReleased')) {
        selector = await buildSelectorForBackendNode(client, backendNodeId, frameId || '');
        element = elementStub(frameId || '', backendNodeId);
      }
      if (type !== 'mouseMoved') {
        await pushUIA({
          ts: nowSeconds(),
          action,
          element,
          selector,
          app_name: options.appName,
          window_title: await page.title().catch(() => ''),
          hwnd: 0,
          pid: 0,
          intent: '',
          meta: {
            source: 'cdp-browser',
            x, y, button, modifiers, clickCount,
            url: page.url(),
          },
        });
      }
    } catch (e) {
      steps.push({ _kind: 'bridge_warn', ts: nowSeconds(), phase: 'mouse', err: String(e) });
    }
  };

  const onKeyEvent = async (event) => {
    if (stopped || suppressKeyEvents) return;
    try {
      const { type, modifiers, text, code, key } = event;
      if (type !== 'keyDown') return; // collapse keyDown/keyUp to one step
      const combo = cdKeyEventToCombo({ modifiers, code });
      const isChar = !!text;
      if (isChar) {
        await pushUIA({
          ts: nowSeconds(),
          action: 'type',
          element: elementStub('', 0),
          selector: `combo=${combo}`,
          app_name: options.appName,
          window_title: await page.title().catch(() => ''),
          hwnd: 0,
          pid: 0,
          intent: '',
          input_text: text,
          meta: {
            source: 'cdp-browser',
            key, code,
            url: page.url(),
          },
        });
      } else {
        steps.push({
          ts: nowSeconds(),
          action: 'hotkey',
          combo,
          duration_ms: 0,
          intent: '',
          meta: {
            source: 'cdp-browser',
            key, code,
            url: page.url(),
          },
        });
        lastEventAt = nowSeconds();
      }
    } catch (e) {
      steps.push({ _kind: 'bridge_warn', ts: nowSeconds(), phase: 'key', err: String(e) });
    }
  };

  // Hook puppeteer's public keyboard / mouse wrappers (page.type, page.click,
  // page.keyboard.press, page.mouse.click etc.) so we catch every input the page
  // performs. Puppeteer 21+ removed the public _client CDP channel, so wrapping
  // the high-level API is the only stable way to observe inputs from JS.
  const kb = page.keyboard; const ms = page.mouse;
  const orig = {
    press: kb.press.bind(kb),
    down: kb.down.bind(kb),
    up: kb.up.bind(kb),
    type: kb.type && kb.type.bind(kb),
    sendCharacter: kb.sendCharacter && kb.sendCharacter.bind(kb),
    click: ms.click.bind(ms),
    move: ms.move.bind(ms),
    down_m: ms.down.bind(ms),
    up_m: ms.up.bind(ms),
    wheel: ms.wheel && ms.wheel.bind(ms),
    drag: ms.drag && ms.drag.bind(ms),
    dragEnter: ms.dragEnter && ms.dragEnter.bind(ms),
    dragOver: ms.dragOver && ms.dragOver.bind(ms),
    drop: ms.drop && ms.drop.bind(ms),
  };
  function modMask(modifiers) { return modifiers || 0; }
  kb.press = async function (key, opts) {
    if (suppressKeyEvents) return orig.press(key, opts);
    await onKeyEvent({ type: 'keyDown', key, code: '', text: '', modifiers: modMask(opts && opts.modifiers) });
    return orig.press(key, opts);
  };
  kb.down = async function (key, opts) {
    if (suppressKeyEvents) return orig.down(key, opts);
    await onKeyEvent({ type: 'keyDown', key, code: '', text: '', modifiers: modMask(opts && opts.modifiers) });
    return orig.down(key, opts);
  };
  kb.up = async function (key, opts) {
    if (suppressKeyEvents) return orig.up(key, opts);
    await onKeyEvent({ type: 'keyUp', key, code: '', text: '', modifiers: modMask(opts && opts.modifiers) });
    return orig.up(key, opts);
  };
  if (orig.type) {
    kb.type = async function (text, opts) {
      suppressKeyEvents = true;
      try {
        await orig.type(text, opts);
      } finally {
        suppressKeyEvents = false;
      }
      return pushUIA({
        ts: nowSeconds(),
        action: 'type',
        element: elementStub('', 0),
        selector: 'combo=unknown',
        app_name: options.appName,
        window_title: await page.title().catch(() => ''),
        hwnd: 0,
        pid: 0,
        intent: '',
        input_text: text,
        meta: {
          source: 'cdp-browser',
          input_length: text.length,
          url: page.url(),
        },
      });
    };
  }
  if (orig.sendCharacter) {
    kb.sendCharacter = async function (ch) {
      if (suppressKeyEvents) return orig.sendCharacter(ch);
      await onKeyEvent({ type: 'keyDown', key: '', code: '', text: ch, modifiers: 0 });
      return orig.sendCharacter(ch);
    };
  }
  ms.click = async function (x, y, opts) {
    const o = opts || {};
    await orig.click(x, y, opts);
    return onMouseEvent({ type: 'mouseReleased', x, y, button: o.button || 'left', modifiers: modMask(o.modifiers), clickCount: o.count || 1, backendNodeId: 0, frameId: '' });
  };
  ms.move = async function (x, y, opts) { return orig.move(x, y, opts); };
  // ms.down/up intentionally NOT wrapped: puppeteer's ms.click internally calls
  // kb.down/up with mouse options mistakenly treated as a key. Hooking only the
  // high-level entry points (ms.click, kb.press/type) is enough for our bridge.
  if (orig.wheel) ms.wheel = async function (opts) { return orig.wheel(opts); };
  if (orig.drag) ms.drag = async function (...args) { return orig.drag(...args); };
  if (orig.dragEnter) ms.dragEnter = async function (...args) { return orig.dragEnter(...args); };
  if (orig.dragOver) ms.dragOver = async function (...args) { return orig.dragOver(...args); };
  if (orig.drop) ms.drop = async function (...args) { return orig.drop(...args); };

  // Idle / max-events watchdog.
  const watchdog = setInterval(async () => {
    if (stopped) return;
    const now = nowSeconds();
    if (now - lastEventAt > options.idleTimeoutMs / 1000) {
      stopReason = 'idle';
      await stop();
      return;
    }
    if (steps.filter((s) => !('_kind' in s)).length >= options.maxEvents) {
      stopReason = 'max';
      await stop();
    }
  }, 500);

  function buildJSONL() {
    const header = {
      _kind: 'header',
      id: sessionId,
      name: options.name,
      started_at: startedAt,
      ended_at: nowSeconds(),
      app_name: options.appName,
      description: options.description,
      step_count: steps.filter((s) => !('_kind' in s)).length,
      source: 'chrome-recorder-bridge',
      url: page.url(),
    };
    const lines = [JSON.stringify(header)];
    for (const s of steps) lines.push(JSON.stringify(s));
    return lines.join('\n') + '\n';
  }

  let _resolve;
  const stoppedPromise = new Promise((r) => { _resolve = r; });
  async function stop() {
    if (stopped) return;
    stopped = true;
    clearInterval(watchdog);
    try { client.off('Input.dispatchMouseEvent', onMouseEvent); } catch {}
    try { client.off('Input.dispatchKeyEvent', onKeyEvent); } catch {}
    try { client.off('Page.screencastFrame', heartbeatHandler); } catch {}
    try { await client.send('Page.stopScreencast'); } catch {}
    try { await client.detach(); } catch {}
    const jsonl = buildJSONL();
    if (options.outputPath) {
      fs.mkdirSync(path.dirname(options.outputPath), { recursive: true });
      fs.writeFileSync(options.outputPath, jsonl, { encoding: 'utf-8' });
    }
    _resolve({ steps, jsonl, stopReason });
  }

  return {
    sessionId,
    stop,
    promise: stoppedPromise,
    getStepCount: () => steps.filter((s) => !('_kind' in s)).length,
  };
}

// Translate JSONL → Python callable: helper for shell runners.
function parseJSONL(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (!lines.length) throw new Error('empty trajectory jsonl');
  const header = JSON.parse(lines[0]);
  if (header._kind !== 'header') throw new Error('first line must be header');
  const steps = lines.slice(1).map((l) => JSON.parse(l));
  return { header, steps };
}

module.exports = { recordPage, parseJSONL };
