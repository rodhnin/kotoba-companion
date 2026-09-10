# Third-party notices

The web UI inside the Python package is a compiled bundle, so third-party JavaScript travels with the
wheel even though its sources do not.

Two halves. **The licence texts below are written by hand**, because a licence that asks for its terms
to travel has to have them reproduced, and because minification removes almost every banner comment
these packages ship — a notice that only survives by accident is not a notice. **The inventory after
them is generated** from the frontend's runtime dependency tree by `scripts/licenses.py`, which the
test suite re-runs: a list this long, kept by hand, is a list that is quietly wrong.

Build tooling that appears here never reaches a user: the wheel carries the compiled output with no
`node_modules` beside it. Over-inclusion is the safe direction, so nothing is pruned by hand.

## Apache License 2.0

<https://www.apache.org/licenses/LICENSE-2.0>

None of these ships a `NOTICE` file, so section 4(d) adds nothing here. Their attribution is recorded
anyway, because minification removes every banner these packages ship. No year is given because none of
them states one: their licence files carry the unfilled Apache appendix, and a year written in here
would be an assertion about somebody else's copyright that they never made.

```
livekit-client       Copyright LiveKit, Inc.
@livekit/protocol    Copyright LiveKit, Inc.
@livekit/mutex       Copyright LiveKit, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

They arrive through the ElevenLabs browser SDK, which uses LiveKit for the WebRTC call.

**@bufbuild/protobuf** is Apache-2.0 as a whole, with one file under BSD-3-Clause — see below.

## BSD 3-Clause

Two copyright holders, one set of conditions:

```
Copyright (c) 2014, The WebRTC project authors. All rights reserved.
Copyright (c) 2018, The adapter.js project authors. All rights reserved.
    — webrtc-adapter

Copyright 2008 Google Inc. All rights reserved.
    — the varint codec inside @bufbuild/protobuf

Redistribution and use in source and binary forms, with or without modification, are permitted
provided that the following conditions are met:

  * Redistributions of source code must retain the above copyright notice, this list of conditions
    and the following disclaimer.
  * Redistributions in binary form must reproduce the above copyright notice, this list of
    conditions and the following disclaimer in the documentation and/or other materials provided
    with the distribution.
  * Neither the name of the copyright holder nor the names of its contributors may be used to
    endorse or promote products derived from this software without specific prior written
    permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER
IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

**The FXAA shader inside pixi.js** carries its own BSD notice — "Copyright (c) 2011 by Armin
Ronacher. Some rights reserved." — under the same three conditions. pixi.js itself is MIT; this one
shader is not, and its notice is one of the few that survives minification inside the bundle.

## ISC

```
Copyright (c) 2016, Mapbox   — earcut

Permission to use, copy, modify, and/or distribute this software for any purpose with or without
fee is hereby granted, provided that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE
OF THIS SOFTWARE.
```

## Every package, and what it says about itself

<!-- generated: do not edit below this line -->

The frontend's runtime dependency tree is **245 packages**. Every one of them may be compiled
into the bundle in whole or in part, so every one is listed. Where a package states a copyright
of its own it is reproduced verbatim; where it states none, none is invented.

### Apache-2.0 — 5

- **@livekit/mutex** 1.1.1
- **@livekit/protocol** 1.50.4
- **@swc/helpers** 0.5.23 — Copyright 2024 SWC contributors
- **baseline-browser-mapping** 2.11.21
- **livekit-client** 2.22.3

### (Apache-2.0 AND BSD-3-Clause) — 1

- **@bufbuild/protobuf** 1.10.1

### BSD-3-Clause — 3

- **qs** 6.16.0
- **source-map-js** 1.2.1 — Copyright (c) 2009-2011, Mozilla Foundation and contributors
- **webrtc-adapter** 9.0.6 — Copyright (c) 2014, The WebRTC project authors. All rights reserved

### ISC — 7

- **@ungap/structured-clone** 1.4.0 — Copyright (c) 2021, Andrea Giammarchi, @WebReflection
- **earcut** 2.2.4 — Copyright (c) 2016, Mapbox
- **fastq** 1.20.3 — Copyright (c) 2015-2020, Matteo Collina <matteo.collina@gmail.com>
- **glob-parent** 5.1.2 — Copyright (c) 2015, 2019 Elan Shanker
- **graceful-fs** 4.2.11 — Copyright (c) 2011-2022 Isaac Z. Schlueter, Ben Noordhuis, and Contributors
- **picocolors** 1.1.1 — Copyright (c) 2021-2024 Oleksii Raspopov, Kostiantyn Denysov, Anton Verinov
- **semver** 6.3.1

### 0BSD — 1

- **tslib** 2.8.1

### MIT — 227

- **@elevenlabs/client** 1.25.0 — Copyright (c) 2025 ElevenLabs
- **@elevenlabs/react** 1.15.2 — Copyright (c) 2025 ElevenLabs
- **@elevenlabs/types** 0.23.0 — Copyright (c) 2025 ElevenLabs
- **@next/env** 16.3.4
- **@nodelib/fs.scandir** 2.1.5
- **@nodelib/fs.stat** 2.0.5
- **@nodelib/fs.walk** 1.2.8
- **@pixi/accessibility** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/app** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/compressed-textures** 6.5.10 — Copyright (c) 2013-2017 Mathew Groves, Chad Engler
- **@pixi/constants** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/core** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/display** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/extensions** 6.5.10 — Copyright (c) 2022 Matt Karl
- **@pixi/extract** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/filter-alpha** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/filter-blur** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/filter-color-matrix** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/filter-displacement** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/filter-fxaa** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/filter-noise** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/graphics** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/interaction** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/loaders** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/math** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/mesh** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/mesh-extras** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/mixin-cache-as-bitmap** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/mixin-get-child-by-name** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/mixin-get-global-position** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/particle-container** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/polyfill** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/prepare** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/runner** 6.5.10 — Copyright (c) 2013-2017 Mathew Groves, Chad Engler
- **@pixi/settings** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/sprite** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/sprite-animated** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/sprite-tiling** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/spritesheet** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/text** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/text-bitmap** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/ticker** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@pixi/utils** 6.5.10 — Copyright (c) 2013-2018 Mathew Groves, Chad Engler
- **@types/debug** 4.1.13
- **@types/dom-mediacapture-record** 1.0.22
- **@types/earcut** 2.1.4
- **@types/estree** 1.0.9
- **@types/estree-jsx** 1.0.5
- **@types/hast** 3.0.5
- **@types/mdast** 4.0.4
- **@types/ms** 2.1.0
- **@types/offscreencanvas** 2019.7.3
- **@types/react** 19.3.0
- **@types/unist** 3.0.3
- **array-union** 2.1.0
- **async** 3.2.6 — Copyright (c) 2010-2018 Caolan McMahon
- **bail** 2.0.2 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **braces** 3.0.3 — Copyright (c) 2014-present, Jon Schlinkert
- **call-bind-apply-helpers** 1.0.2 — Copyright (c) 2024 Jordan Harband
- **call-bound** 1.0.4 — Copyright (c) 2024 Jordan Harband
- **ccount** 2.0.1 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **character-entities** 2.0.2 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **character-entities-html4** 2.1.0 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **character-entities-legacy** 3.0.0 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **character-reference-invalid** 2.0.1 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **client-only** 0.0.1
- **comma-separated-tokens** 2.0.3 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **commander** 13.1.0 — Copyright (c) 2011 TJ Holowaychuk <tj@vision-media.ca>
- **commondir** 1.0.1 — Copyright (c) 2013 James Halliday (mail@substack.net)
- **csstype** 3.2.3 — Copyright (c) 2017-2018 Fredrik Nicol
- **debug** 4.4.3 — Copyright (c) 2014-2017 TJ Holowaychuk <tj@vision-media.ca>
- **decode-named-character-reference** 1.3.0
- **dequal** 2.0.3
- **devlop** 1.1.0 — Copyright (c) 2023 Titus Wormer <tituswormer@gmail.com>
- **dir-glob** 3.0.1
- **dunder-proto** 1.0.1 — Copyright (c) 2024 ECMAScript Shims
- **email-addresses** 5.0.0 — Copyright (c) 2013 Fog Creek Software
- **es-define-property** 1.0.1 — Copyright (c) 2024 Jordan Harband
- **es-errors** 1.3.0 — Copyright (c) 2024 Jordan Harband
- **es-object-atoms** 1.1.2 — Copyright (c) 2024 Jordan Harband
- **escape-string-regexp** 5.0.0
- **estree-util-is-identifier-name** 3.0.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **eventemitter3** 3.1.2 — Copyright (c) 2014 Arnout Kazemier
- **events** 3.3.0
- **extend** 3.0.2 — Copyright (c) 2014 Stefan Thomas
- **fast-glob** 3.3.3
- **filename-reserved-regex** 2.0.0
- **filenamify** 4.3.0
- **fill-range** 7.1.1 — Copyright (c) 2014-present, Jon Schlinkert
- **find-cache-dir** 3.3.2
- **find-up** 4.1.0
- **fs-extra** 11.4.0 — Copyright (c) 2011-2024 JP Richardson
- **function-bind** 1.1.2 — Copyright (c) 2013 Raynos
- **get-intrinsic** 1.3.0 — Copyright (c) 2020 Jordan Harband
- **get-proto** 1.0.1 — Copyright (c) 2025 Jordan Harband
- **gh-pages** 6.3.0 — Copyright (c) 2014 Tim Schaub
- **globby** 11.1.0
- **gopd** 1.2.0 — Copyright (c) 2022 Jordan Harband
- **has-symbols** 1.1.0 — Copyright (c) 2016 Jordan Harband
- **hasown** 2.0.4
- **hast-util-to-jsx-runtime** 2.3.6
- **hast-util-whitespace** 3.0.0 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **html-url-attributes** 3.0.1
- **ignore** 5.3.2
- **inline-style-parser** 0.2.7 — Copyright (c) 2012 TJ Holowaychuk <tj@vision-media.ca>
- **is-alphabetical** 2.0.1 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **is-alphanumerical** 2.0.1 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **is-decimal** 2.0.1 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **is-extglob** 2.1.1 — Copyright (c) 2014-2016, Jon Schlinkert
- **is-glob** 4.0.3 — Copyright (c) 2014-2017, Jon Schlinkert
- **is-hexadecimal** 2.0.1 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **is-number** 7.0.0 — Copyright (c) 2014-present, Jon Schlinkert
- **is-plain-obj** 4.1.0
- **jose** 6.2.12 — Copyright (c) 2018 Filip Skokan
- **jsonfile** 6.2.1 — Copyright (c) 2012-2015, JP Richardson <jprichardson@gmail.com>
- **locate-path** 5.0.0
- **loglevel** 1.9.2
- **longest-streak** 3.1.0 — Copyright (c) 2015 Titus Wormer <mailto:tituswormer@gmail.com>
- **machina** 7.0.1 — Copyright (c) 2011-2023 Jim Cowart (MIT License)
- **make-dir** 3.1.0
- **markdown-table** 3.0.4
- **math-intrinsics** 1.1.0 — Copyright (c) 2024 ECMAScript Shims
- **mdast-util-find-and-replace** 3.0.2
- **mdast-util-from-markdown** 2.0.3
- **mdast-util-gfm** 3.1.0
- **mdast-util-gfm-autolink-literal** 2.0.1 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **mdast-util-gfm-footnote** 2.1.0
- **mdast-util-gfm-strikethrough** 2.0.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **mdast-util-gfm-table** 2.0.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **mdast-util-gfm-task-list-item** 2.0.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **mdast-util-mdx-expression** 2.0.1 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **mdast-util-mdx-jsx** 3.2.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **mdast-util-mdxjs-esm** 2.0.1 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **mdast-util-phrasing** 4.1.0 — Copyright (c) 2017 Titus Wormer <tituswormer@gmail.com>
- **mdast-util-to-hast** 13.2.1 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **mdast-util-to-markdown** 2.1.2
- **mdast-util-to-string** 4.0.0 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **merge2** 1.4.1 — Copyright (c) 2014-2020 Teambition
- **micromark** 4.0.2
- **micromark-core-commonmark** 2.0.3
- **micromark-extension-gfm** 3.0.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **micromark-extension-gfm-autolink-literal** 2.1.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **micromark-extension-gfm-footnote** 2.1.0 — Copyright (c) 2021 Titus Wormer <tituswormer@gmail.com>
- **micromark-extension-gfm-strikethrough** 2.1.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **micromark-extension-gfm-table** 2.1.1
- **micromark-extension-gfm-tagfilter** 2.0.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **micromark-extension-gfm-task-list-item** 2.1.0 — Copyright (c) 2020 Titus Wormer <tituswormer@gmail.com>
- **micromark-factory-destination** 2.0.1
- **micromark-factory-label** 2.0.1
- **micromark-factory-space** 2.0.1
- **micromark-factory-title** 2.0.1
- **micromark-factory-whitespace** 2.0.1
- **micromark-util-character** 2.1.1
- **micromark-util-chunked** 2.0.1
- **micromark-util-classify-character** 2.0.1
- **micromark-util-combine-extensions** 2.0.1
- **micromark-util-decode-numeric-character-reference** 2.0.2
- **micromark-util-decode-string** 2.0.1
- **micromark-util-encode** 2.0.1
- **micromark-util-html-tag-name** 2.0.1
- **micromark-util-normalize-identifier** 2.0.1
- **micromark-util-resolve-all** 2.0.1
- **micromark-util-sanitize-uri** 2.0.1
- **micromark-util-subtokenize** 2.1.0
- **micromark-util-symbol** 2.0.1
- **micromark-util-types** 2.0.2
- **micromatch** 4.0.8 — Copyright (c) 2014-present, Jon Schlinkert
- **ms** 2.1.3
- **nanoid** 3.3.18 — Copyright 2017 Andrey Sitnik <andrey@sitnik.ru>
- **next** 16.3.4
- **object-assign** 4.1.1
- **object-inspect** 1.13.4 — Copyright (c) 2013 James Halliday
- **p-limit** 2.3.0
- **p-locate** 4.1.0
- **p-try** 2.2.0
- **parse-entities** 4.0.2
- **path-exists** 4.0.0
- **path-type** 4.0.0
- **picomatch** 2.3.2 — Copyright (c) 2017-present, Jon Schlinkert
- **pixi-live2d-display** 0.4.0 — Copyright (c) 2020 Guan
- **pixi.js** 6.5.10 — Copyright (c) 2013-2017 Mathew Groves, Chad Engler
- **pkg-dir** 4.2.0
- **postcss** 8.5.23 — Copyright 2013 Andrey Sitnik <andrey@sitnik.es>
- **promise-polyfill** 8.3.0 — Copyright (c) 2014 Taylor Hakes
- **property-information** 7.2.0
- **punycode** 1.4.1
- **queue-microtask** 1.2.3
- **react** 19.3.0
- **react-dom** 19.3.0
- **react-markdown** 10.1.0
- **remark-gfm** 4.0.1
- **remark-parse** 11.0.0 — Copyright (c) 2014 Titus Wormer <tituswormer@gmail.com>
- **remark-rehype** 11.1.2
- **remark-stringify** 11.0.0 — Copyright (c) 2014 Titus Wormer <tituswormer@gmail.com>
- **reusify** 1.1.0 — Copyright (c) 2015-2024 Matteo Collina
- **run-parallel** 1.2.0
- **scheduler** 0.28.0
- **sdp** 3.2.2 — Copyright (c) 2017 Philipp Hancke
- **sdp-transform** 2.15.0 — Copyright (c) 2013 Eirik Albrigtsen
- **side-channel** 1.1.1 — Copyright (c) 2019 Jordan Harband
- **side-channel-list** 1.0.1 — Copyright (c) 2024 Jordan Harband
- **side-channel-map** 1.0.1 — Copyright (c) 2024 Jordan Harband
- **side-channel-weakmap** 1.0.2 — Copyright (c) 2019 Jordan Harband
- **slash** 3.0.0
- **space-separated-tokens** 2.0.2 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **stringify-entities** 4.0.4 — Copyright (c) 2015 Titus Wormer <mailto:tituswormer@gmail.com>
- **strip-outer** 1.0.1
- **style-to-js** 1.1.21 — Copyright (c) 2020 Menglin "Mark" Xu <mark@remarkablemark.org>
- **style-to-object** 1.0.14 — Copyright (c) 2017 Menglin "Mark" Xu <mark@remarkablemark.org>
- **styled-jsx** 5.1.6
- **to-regex-range** 5.0.1 — Copyright (c) 2015-present, Jon Schlinkert
- **trim-lines** 3.0.1 — Copyright (c) 2015 Titus Wormer <mailto:tituswormer@gmail.com>
- **trim-repeated** 1.0.0
- **trough** 2.2.0 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **typed-emitter** 2.1.0 — Copyright (c) 2018 Andy Wermke
- **unified** 11.0.5 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **unist-util-is** 6.0.1 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **unist-util-position** 5.0.0 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **unist-util-stringify-position** 4.0.0 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **unist-util-visit** 5.1.0 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **unist-util-visit-parents** 6.0.2 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>
- **universalify** 2.0.1 — Copyright (c) 2017, Ryan Zimmerman <opensrc@ryanzim.com>
- **url** 0.11.4 — Copyright 2014 Joyent, Inc. and other Node contributors
- **vfile** 6.0.3 — Copyright (c) 2015 Titus Wormer <tituswormer@gmail.com>
- **vfile-message** 4.0.3
- **zustand** 5.0.15 — Copyright (c) 2019 Paul Henschel
- **zwitch** 2.0.4 — Copyright (c) 2016 Titus Wormer <tituswormer@gmail.com>

### CC-BY-4.0 — 1

- **caniuse-lite** 1.0.30001810

<!-- end generated -->

## Live2D

`live2dcubismcore.min.js` is Live2D Cubism Core, redistributed under the Live2D Proprietary Software
Licence Agreement as "Redistributable Code". Its own header states the terms.

The built web UI also contains **Live2D Cubism Web Framework** — Copyright © Live2D Inc., used under
the [Live2D Open Software License Agreement](https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html).
It arrives compiled inside `pixi-live2d-display`'s `cubism4` bundle rather than as a file of its own,
and the bundler's minification removes the source headers, so the notice is recorded here instead.

Two things follow for anyone redistributing this project. The Framework may be distributed only as
part of a work that also ships Live2D's own runtime, which is the Cubism Core above. And Live2D asks
any business whose annual gross revenue exceeds 10,000,000 JPY to hold a separate Cubism SDK Release
License — a threshold about the redistributor, not about this repository.

**No Live2D model is included.** Models belong to their authors and this project never ships one —
[the self-hosting guide](docs/self-hosting.md#2-give-her-a-face)
explains how to bring your own.

## Python

Nothing is vendored: every Python dependency is installed from its own published package with its
own licence intact, so no notice is owed here.
