// kiosk-main.js — replaces flipoff's main.js for kiosk mode.
// - Loads quotes from /api/state on boot
// - Subscribes to /api/events (SSE) for live updates
// - Honors blackout / mute / next / prev events
// - Auto-reconnects SSE on backend restart

import { Board } from './Board.js';
import { SoundEngine } from './SoundEngine.js';
import { MessageRotator } from './MessageRotator.js';
import { KeyboardController } from './KeyboardController.js';

const boardContainer = document.getElementById('board-container');
const gifImage = document.getElementById('gif-image');
const soundEngine = new SoundEngine();
const board = new Board(boardContainer, soundEngine);
const rotator = new MessageRotator(board);
const keyboard = new KeyboardController(rotator, soundEngine);

// Unlock audio on any interaction. Since the kiosk has no user input,
// chromium is launched with --autoplay-policy=no-user-gesture-required.
let audioInitialized = false;
async function initAudio() {
  if (audioInitialized) return;
  audioInitialized = true;
  try {
    await soundEngine.init();
    soundEngine.resume();
  } catch (err) {
    console.warn('audio init failed', err);
  }
}
initAudio();

// Re-layout a quote's lines so the non-empty content is centered vertically
// within the current row count. Horizontal centering is already handled by
// Board._formatToGrid, which Math.floor-pads each line inside the col width.
function centerLinesVertically(lines, rows) {
  const content = (lines || []).slice();
  // Strip leading/trailing blank rows (treat whitespace-only as blank).
  while (content.length && !(content[0] || '').trim()) content.shift();
  while (content.length && !(content[content.length - 1] || '').trim()) content.pop();
  // Clamp to grid — if the user wrote more lines than rows, keep the top ones.
  if (content.length >= rows) return content.slice(0, rows);
  // Symmetric padding, top-biased on odd remainder so multi-line content
  // visually nests like real airport boards.
  const pad = rows - content.length;
  const topPad = Math.floor(pad / 2);
  const botPad = pad - topPad;
  const out = [];
  for (let i = 0; i < topPad; i++) out.push('');
  for (const line of content) out.push(line);
  for (let i = 0; i < botPad; i++) out.push('');
  return out;
}

function linesToMessages(quotes, rows) {
  return (quotes || []).map(q => centerLinesVertically(q.lines, rows));
}

// Rows/cols at page load time. If the SSE pushes different values we reload
// the page so constants.js re-imports with the new dimensions.
const BOOT_CFG = window.__KIOSK_CFG__ || {};
const BOOT_ROWS = Number.isInteger(BOOT_CFG.rows) ? BOOT_CFG.rows : 4;
const BOOT_COLS = Number.isInteger(BOOT_CFG.cols) ? BOOT_CFG.cols : 22;

function setRotationTimer(intervalMs, enabled) {
  if (!enabled) {
    rotator.stop();
    return;
  }

  if (rotator._timer && rotator._kioskIntervalMs === intervalMs) return;
  rotator._kioskIntervalMs = intervalMs;
  rotator.stop();
  if (rotator.currentIndex < 0 && rotator.messages && rotator.messages.length > 0) {
    rotator.currentIndex = 0;
    rotator.board.displayMessage(rotator.messages[0]);
  }
  rotator._timer = setInterval(() => {
    if (!rotator._paused && !rotator.board.isTransitioning) {
      rotator.next();
    }
  }, intervalMs);
}

function applyState(newState) {
  if (!newState) return;

  // Grid dimensions changed → need a hard reload because Board caches
  // cols/rows at construction and the engine imports are immutable.
  if ((newState.rows && newState.rows !== BOOT_ROWS) ||
      (newState.cols && newState.cols !== BOOT_COLS)) {
    window.location.reload();
    return;
  }

  // Runtime-adjustable CSS variables: margins and letter scale take effect
  // instantly without a reload.
  const root = document.documentElement.style;
  if (newState.sideMarginPx != null) root.setProperty('--side-margin', newState.sideMarginPx + 'px');
  if (newState.topMarginPx != null)  root.setProperty('--top-margin', newState.topMarginPx + 'px');
  if (newState.letterScale != null)  root.setProperty('--letter-scale', String(newState.letterScale));
  if (newState.rows != null)         root.setProperty('--grid-rows', String(newState.rows));
  if (newState.cols != null)         root.setProperty('--grid-cols', String(newState.cols));

  const gifBrightness = Number(newState.gifBrightness);
  root.setProperty(
    '--gif-brightness',
    String(Number.isFinite(gifBrightness) ? gifBrightness : 1),
  );

  const gifUrl = (newState.gifUrl || '').trim();
  const gifActive = newState.displayMode === 'gif' && gifUrl.length > 0;
  if (gifImage) {
    if (gifActive) {
      if (gifImage.getAttribute('src') !== gifUrl) {
        gifImage.setAttribute('src', gifUrl);
      }
    } else {
      gifImage.removeAttribute('src');
    }
  }
  document.body.classList.toggle('gif-mode', gifActive);

  // Update rotator messages in place — MessageRotator reads this.messages
  // on every next()/prev() call, so live mutation works. Quotes are
  // vertically centered against the current row count so users can write
  // 1-4 line quotes and they'll sit in the middle of the grid automatically.
  const messages = linesToMessages(newState.quotes, newState.rows || BOOT_ROWS);
  if (messages.length > 0) {
    const wasEmpty = !rotator.messages || rotator.messages.length === 0;
    rotator.messages = messages;
    rotator.currentIndex = Math.min(
      Math.max(rotator.currentIndex, -1),
      messages.length - 1
    );
    if (wasEmpty) rotator.next();
  }

  // Interval changes require restart of the timer loop.
  const newIntervalMs = (newState.intervalSec || 8) * 1000;
  setRotationTimer(newIntervalMs, !gifActive);

  // Blackout toggle
  document.body.classList.toggle('blackout', !!newState.blackout);

  // Mute
  if (soundEngine && typeof soundEngine.setMuted === 'function') {
    soundEngine.setMuted(!!newState.muted);
  } else if (soundEngine && newState.muted !== undefined) {
    // Fallback: toggle until matches
    if (soundEngine.muted !== newState.muted) {
      soundEngine.toggleMute();
    }
  }
}

function connectSSE() {
  const es = new EventSource('/api/events');

  es.addEventListener('state', ev => {
    try {
      applyState(JSON.parse(ev.data));
    } catch (err) {
      console.warn('bad state payload', err);
    }
  });

  es.addEventListener('next', () => rotator.next());
  es.addEventListener('prev', () => rotator.prev());
  es.addEventListener('reload', () => window.location.reload());
  es.addEventListener('ping', () => {});

  es.onerror = () => {
    es.close();
    // Reconnect after short delay; also reload page if backend
    // came back with a different version of static assets.
    setTimeout(connectSSE, 1500);
  };
}

// Boot: fetch initial state, start rotation, subscribe to events.
fetch('/api/state')
  .then(r => r.json())
  .then(initial => {
    applyState(initial);
  })
  .catch(err => {
    console.error('failed to load initial state', err);
    // Fall back to starting with whatever constants.js had
    rotator.start();
  })
  .finally(connectSSE);
