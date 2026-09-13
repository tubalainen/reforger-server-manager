// Where a "?" help popover opens (#189). It is drawn in the viewport's own frame
// (position: fixed), so a card that clips its content cannot cut it off; this keeps
// it on screen. Kept free of the DOM so it can be tested on its own.

const MARGIN = 8
const GAP = 6

/**
 * @param anchor   the "?" button's rect: { left, right, top, bottom }
 * @param size     the popover's { width, height }
 * @param viewport { width, height }
 * @returns { left, top } for the popover
 */
export function placePopover(anchor, size, viewport) {
  // Start under the button, lined up with its left edge…
  let left = anchor.left
  // …but never past the right edge, and never past the left one.
  left = Math.min(left, viewport.width - size.width - MARGIN)
  left = Math.max(left, MARGIN)

  // Below the button if it fits; above it if only that fits; otherwise below and
  // let the page scroll.
  let top = anchor.bottom + GAP
  if (top + size.height > viewport.height - MARGIN && anchor.top - GAP - size.height >= MARGIN) {
    top = anchor.top - GAP - size.height
  }
  return { left: Math.round(left), top: Math.round(top) }
}
