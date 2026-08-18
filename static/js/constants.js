// constants.js — patched for kiosk mode.
// Reads grid dimensions from window.__KIOSK_CFG__ if present so they
// can be configured at runtime by the backend (see kiosk.html's inline
// <script> that sets this before any module loads).

const CFG = (typeof window !== 'undefined' && window.__KIOSK_CFG__) || {};

export const GRID_COLS = Number.isInteger(CFG.cols) ? CFG.cols : 22;
export const GRID_ROWS = Number.isInteger(CFG.rows) ? CFG.rows : 4;

export const SCRAMBLE_DURATION = 800;
export const FLIP_DURATION = 300;
export const STAGGER_DELAY = 25;
export const TOTAL_TRANSITION = 3800;
export const MESSAGE_INTERVAL = 4000;

export const CHARSET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,-!?\'/: ';

export const SCRAMBLE_COLORS = [
  '#00AAFF', '#00FFCC', '#AA00FF',
  '#FF2D00', '#FFCC00', '#FFFFFF'
];

export const ACCENT_COLORS = [
  '#00FF7F', '#FF4D00', '#AA00FF',
  '#00AAFF', '#00FFCC'
];

// MESSAGES is intentionally empty here: quotes come from /api/state at
// runtime and are injected into MessageRotator via kiosk-main.js.
export const MESSAGES = [];
