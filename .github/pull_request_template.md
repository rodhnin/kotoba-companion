## What this changes

<!-- One or two sentences. If it fixes an issue, say `Fixes #123`. -->

## How you know it works

<!-- The command you ran and what it said. A test that fails without the change and passes with it is
     the strongest form of this; "it should work" is the weakest. -->

## Checklist

- [ ] `pytest api/tests -q` passes
- [ ] `npm test` and `npx tsc --noEmit --noUnusedLocals --noUnusedParameters` pass, if anything under `app/`, `components/` or `lib/` moved
- [ ] `python scripts/build_web.py` still builds, if anything the packaged UI ships was touched
- [ ] Comments say WHY, not what, and no comment is longer than it has to be
- [ ] No key, token, personal path or real name is in the diff
