/**
 * Geometry for the helper-chibi row, shared with the component so the arithmetic cannot drift from
 * the layout. The row is one fixed, non-wrapping line pinned to the bottom-left corner, so past the
 * viewport's width the last heads — and their dismiss controls with them — would sit off-screen,
 * leaving the row emptiable only by a work bracket, which a `delegate` called outside one never gets.
 * Wrapping would bound the width at the cost of the baseline every head sits on, so the row caps
 * itself at what fits and spends its last slot on a counter for the rest. Order is insertion order,
 * because a head is a click target and must never slide under the cursor. The process bubble is
 * centred on its chibi, then clamped to the margins, with the tail told where the chibi ended up.
 */

export const CHIBI_SIZE = 60;
export const CHIBI_GAP = 14;
export const BUBBLE_MAX_WIDTH = 330;
export const BUBBLE_VIEWPORT_FRACTION = 0.8;

const TAIL_INSET = 24;
const INSET = { min: 12, fraction: 0.02, max: 28 };

/** The row's own corner offset. The component renders this; rowInset() resolves the same value. */
export const ROW_INSET_CSS = `clamp(${INSET.min}px, ${INSET.fraction * 100}vw, ${INSET.max}px)`;

export function rowInset(viewportWidth: number): number {
  return Math.min(INSET.max, Math.max(INSET.min, viewportWidth * INSET.fraction));
}

/** How many heads fit on one line at this width, counting the gaps between them and both margins. */
export function rowCapacity(viewportWidth: number): number {
  const available = viewportWidth - 2 * rowInset(viewportWidth);
  const slots = Math.floor((available + CHIBI_GAP) / (CHIBI_SIZE + CHIBI_GAP));
  return Math.max(1, slots);
}

/**
 * Split the helpers into the ones the row draws and the ones the counter stands for. The counter
 * occupies a slot of its own, so it is never itself the thing that pushes the line off-screen.
 * A width of 0 means "not measured yet" and caps nothing.
 */
export function splitRow(total: number, viewportWidth: number): { visible: number; overflow: number } {
  if (total <= 0) return { visible: 0, overflow: 0 };
  if (!(viewportWidth > 0)) return { visible: total, overflow: 0 };
  const capacity = rowCapacity(viewportWidth);
  if (total <= capacity) return { visible: total, overflow: 0 };
  const visible = Math.max(1, capacity - 1);
  return { visible, overflow: total - visible };
}

/** Counter face. Three characters is the widest it may get, so it stays legible at head size. */
export function overflowLabel(count: number): string {
  return count > 99 ? "99+" : `+${count}`;
}

export type BubblePlacement = {
  /** px offset from the chibi's own left edge — the bubble is positioned inside the chibi wrapper. */
  left: number;
  width: number;
  /** px from the bubble's left edge to the centre of the chibi it belongs to. */
  tailX: number;
};

export function bubblePlacement(index: number, viewportWidth: number): BubblePlacement {
  if (!(viewportWidth > 0)) return { left: 0, width: BUBBLE_MAX_WIDTH, tailX: TAIL_INSET };
  const width = Math.min(viewportWidth * BUBBLE_VIEWPORT_FRACTION, BUBBLE_MAX_WIDTH);
  const inset = rowInset(viewportWidth);
  const chibiLeft = inset + index * (CHIBI_SIZE + CHIBI_GAP);
  const centre = chibiLeft + CHIBI_SIZE / 2;
  const rightmost = Math.max(inset, viewportWidth - inset - width);
  const left = Math.min(Math.max(centre - width / 2, inset), rightmost);
  const tailLimit = Math.max(TAIL_INSET, width - TAIL_INSET);
  const tailX = Math.min(Math.max(centre - left, TAIL_INSET), tailLimit);
  return { left: left - chibiLeft, width, tailX };
}
