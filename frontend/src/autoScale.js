// Fits the app interface to the window.
//
// The layout is designed for a large screen. On a laptop, or with Windows
// display scaling, the browser window is much smaller and everything looks
// oversized. This measures the window and sets --ui-scale (1 = full size,
// down to MIN_SCALE); the .app shell in App.css applies it with CSS zoom, so
// the layout keeps its designed proportions at any size. Phones keep the
// normal responsive layout (scale 1).

const DESIGN_WIDTH = 1680
const DESIGN_HEIGHT = 860
const MIN_SCALE = 0.7
const PHONE_MAX_WIDTH = 900

export function computeScale(width, height) {
  if (width <= PHONE_MAX_WIDTH) return 1

  const fit = Math.min(width / DESIGN_WIDTH, height / DESIGN_HEIGHT)

  return Math.round(Math.min(1, Math.max(MIN_SCALE, fit)) * 100) / 100
}

function applyScale() {
  document.documentElement.style.setProperty(
    '--ui-scale',
    String(computeScale(window.innerWidth, window.innerHeight))
  )
}

let frame = 0

function scheduleScale() {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(applyScale)
}

applyScale()
window.addEventListener('resize', scheduleScale)
window.addEventListener('orientationchange', scheduleScale)
