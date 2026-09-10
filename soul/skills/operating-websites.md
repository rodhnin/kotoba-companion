---
name: operating-websites
description: Drive a real website end-to-end — log in, search, navigate, fill forms — with the browser tools.
when_to_use: any multi-step task inside a real website (Facebook, Gmail, a dashboard, a store)
requires_toolsets: [browser]
---

# Operating websites

A playbook for doing real work inside a website with the `browser_*` tools. Follow it whenever you open a
site to log in, search, click around, or fill a form. The single rule that prevents 90% of failures: **the
browser is driven by the accessibility SNAPSHOT — you act on elements by their `ref`, never by a CSS
selector you remembered.**

## The core loop (do this every time)

1. **Navigate** — `browser_navigate(url=...)` to get to the page.
2. **Snapshot** — `browser_snapshot()`. It returns the page as a tree where every interactive element has a
   reference like `[ref=e5]`, `[ref=e20]`. READ it.
3. **Act using the ref** — put that exact reference in the `target` field:
   - `browser_click(target="e20", element="the Search box")`
   - `browser_type(target="e20", text="live2d model", element="the Search box")` — set `submit=true` to press Enter.
   - `browser_fill_form(fields=[{target:"e5", name:"Email", type:"textbox", value:"..."}, ...])` for many fields at once.
4. **Re-snapshot after the page changes** — any navigation, search submit, or click that loads new content
   makes the old refs STALE. Take a fresh `browser_snapshot()` before your next action. If a step fails with
   "does not match any elements", you used a stale or invented ref → re-snapshot and use a current one.

⛔ NEVER put a CSS/XPath selector (`#email`, `input[name=q]`, `.btn`) in `target`. Those come from memory,
not from the live page, and they fail. Only a `ref` you just read from a snapshot works.

## Logging in (the user's OWN account on ANY site, with their OK)

This works on ANY website — Facebook, Gmail, a store, a dashboard, an admin panel. The steps are the same;
just use the SITE you're on. Collect every credential through an on-screen box — NEVER by voice, NEVER typed
by you from memory:

1. `browser_navigate` to the login page, then `browser_snapshot` to find the email/username and password
   field refs.
2. **Email / username** (not secret) → `ask_user(prompt="your <site> email or username")`; type the
   returned value into the field with `browser_type(target=<ref>, text=<value>)`.
3. **Password** (secret) → `ask_secret(name="<site>", prompt="your <site> password")` — use the site's name
   as the secret name (e.g. "gmail", "shopify"), NOT always "facebook". This opens a MASKED box and gives
   you back a placeholder `{{secret:<site>}}` — NOT the real value. Type the PLACEHOLDER into the password
   field: `browser_type(target=<ref>, text="{{secret:<site>}}")`. The system swaps it for the real password
   only at the browser; you never see it. You MUST call `ask_secret` first — typing the placeholder without
   it is refused.
4. Click the login button (by its `ref` from the snapshot).
5. Expect a checkpoint: many sites (Facebook, Google, banks, …) may show a captcha ("I'm not a robot"), a
   code/2FA, or an "is this you?" step on a fast automated login. This is NORMAL — do NOT give up or end the
   task. When you hit one you can't do yourself (a captcha especially):
   - `ask_user(prompt="The site is asking you to solve a captcha / verification on screen — please do it in
     the browser, then type 'done' here")`. This WAITS for them (you have time; the call stays alive).
   - When they reply, take a FRESH `browser_snapshot` and CONTINUE the flow from the new state.
   - For a 2FA code, `ask_user` for the code and type it. Only stop if they tell you to, or after waiting
     they didn't respond — and then say honestly where you got stuck. Never fabricate that you logged in.

## Searching a site

Find the search box ref in the snapshot, `browser_type(target=<ref>, text="<query>", submit=true)`, then
**re-snapshot** to read the results, and click the result you want by its ref. If typing into the box
doesn't submit, find and click the search button by its ref.

## Knowing you actually succeeded (do NOT fabricate)

Before you report success, VERIFY it with a real observation:
- Take a final `browser_snapshot` (or `browser_take_screenshot`) and READ it. Does it actually show the
  logged-in state / the profile you opened / the result you claim?
- Only say "done" if the snapshot proves it. If a step errored, you hit a checkpoint, or you couldn't
  confirm, say so honestly: "I got as far as the login but it asked for a verification code." A truthful
  partial result is correct; a cheerful "all done!" that didn't happen is a failure.

## Saving someone's photo to your visual memory

This works on ANY site (a social profile, a shop product page, a news photo). To remember an image: get its
DIRECT URL and pass it to `remember_image(source=<url>, about=<name>, kind=...)` — that saves the clean
ORIGINAL, far better than a viewport screenshot.

- **Use the BIG/full image, not a tiny list or search thumbnail.** Search results and grids serve small,
  sometimes blurred thumbnails (their URL often carries resize params like `...s320x320`, `?width=…`,
  `/thumb/…`). Instead OPEN the item (the profile, the product page), click the image so the full-size one
  opens, then take THAT image's URL. (`remember_image` also auto-strips known resize/blur transforms and
  keeps the larger result, but starting from the full image is best.)
- To get the URL: from a `browser_snapshot`, the image element exposes its `src`; pass THAT https URL as
  `source`. Only fall back to a `browser_take_screenshot` filename if there's truly no image URL.
- Write a real `note` about what it shows ("brown hair, red-and-white jersey, on a football pitch") so your
  textual memory and the photo line up.

## When to stop

Stop the moment the goal is verified (the deliverable exists and a snapshot confirms it). Don't keep
clicking around. Summarize what you actually did, grounded in what the last snapshot showed.
