export interface CssRect { left: number; top: number; width: number; height: number; }

export function mapLogicalToCss(
  x: number, y: number, w: number, h: number,
  viewportW: number, viewportH: number,
): CssRect {
  return {
    left: (x / 1000) * viewportW,
    top: (y / 1000) * viewportH,
    width: (w / 1000) * viewportW,
    height: (h / 1000) * viewportH,
  };
}

export function ensureMinHit(rect: CssRect, minPx = 44): CssRect {
  const growX = Math.max(0, minPx - rect.width) / 2;
  const growY = Math.max(0, minPx - rect.height) / 2;
  return {
    left: rect.left - growX,
    top: rect.top - growY,
    width: Math.max(rect.width, minPx),
    height: Math.max(rect.height, minPx),
  };
}
