/**
 * What a markdown link does, decided once for every renderer. A plain anchor inside /app does not
 * open a page: it NAVIGATES THE APP AWAY — the call drops, the avatar unmounts and the session goes
 * with it. `rel` matters as much as `target`: without `noopener` the opened page gets a handle on
 * this one through `window.opener`. The result is MEMOISED, and that is not a micro-optimisation —
 * React compares an element's `type` by reference, so a fresh `a` closure per call is a different
 * component every render and the anchor is unmounted and remounted under the cursor, which loses a
 * selection and can drop the mouseup half of a click. Keyed on the style OBJECT, so a caller MUST
 * hoist its style to a constant: a literal in the JSX is a new key every render and defeats it.
 */
import type { CSSProperties } from "react";
import type { Components } from "react-markdown";

const UNSTYLED = {};
const made = new WeakMap<object, Components>();

export function mdComponents(linkStyle?: CSSProperties): Components {
  const key = linkStyle ?? UNSTYLED;
  let components = made.get(key);
  if (!components) {
    components = {
      a: ({ node, ...props }) => (
        <a
          {...props}
          target="_blank"
          rel="noopener noreferrer"
          style={{ ...linkStyle, ...props.style }}
        />
      ),
    };
    made.set(key, components);
  }
  return components;
}
