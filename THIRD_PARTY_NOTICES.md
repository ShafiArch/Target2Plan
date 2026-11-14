# Third-party notices

Target2Plan is licensed under CC BY-NC 4.0 (see [LICENSE](LICENSE)). The components below are the work of other authors and remain under their own licenses.

## RGeoLib: furniture placement

The furniture step of the schematic synthesis is a port of the furniture placement logic in **RGeoLib**, the research geometry library of the Hypergraph project by **Ramon Elias Weber** ([github.com/ramonweber/hypergraph](https://github.com/ramonweber/hypergraph), `ResearchGeometryLibrary/RGeoLib/BuildingSolver`). It appears twice in this repository:

* original prototype: `models/schematic_layout.py` (Python port)
* web and desktop apps: `docs/js/engine/schematic.js` (`furnishRoom` and its helpers, ported from the Python)

Please cite the accompanying paper as well:

> Weber, R. E., Mueller, C. & Reinhart, C. A hypergraph model shows the carbon reduction potential of effective space use in housing. *Nature Communications* 15, 8327 (2024). https://doi.org/10.1038/s41467-024-52506-z

RGeoLib license (also in `docs/js/engine/LICENSE-RGeoLib.txt`, next to the web code):

```
MIT License

Copyright (c) 2024 ramonweber

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## gifenc 1.0.3: GIF export (web app)

[github.com/mattdesl/gifenc](https://github.com/mattdesl/gifenc). MIT License, Copyright (c) 2017 Matt DesLauriers. Full text: `docs/js/vendor/LICENSE-gifenc.md`.

## mp4-muxer 5.2.2: MP4 export (web app)

[github.com/Vanilagy/mp4-muxer](https://github.com/Vanilagy/mp4-muxer). MIT License, Copyright (c) 2023 Vanilagy. Full text: `docs/js/vendor/LICENSE-mp4-muxer.txt`.

## Fonts: Inter and JetBrains Mono

The web and desktop apps include the Latin and Latin Extended subsets of **Inter** (Copyright 2016 The Inter Project Authors, [github.com/rsms/inter](https://github.com/rsms/inter)) and **JetBrains Mono** (Copyright 2020 The JetBrains Mono Project Authors, [github.com/JetBrains/JetBrainsMono](https://github.com/JetBrains/JetBrainsMono)) as WOFF2 files in `docs/fonts/`, taken from the Fontsource packages. Both are licensed under the SIL Open Font License 1.1; full texts: `docs/fonts/OFL-Inter.txt` and `docs/fonts/OFL-JetBrainsMono.txt`.

## Tauri: desktop app

The desktop app in `desktop/` is built with **Tauri** and its official dialog, file system and opener plugins ([tauri.app](https://tauri.app), [github.com/tauri-apps/tauri](https://github.com/tauri-apps/tauri)), licensed under the MIT License or the Apache License 2.0, at your option. The compiled app also contains the Rust crates Tauri depends on, each under its own open-source license (mostly MIT or Apache 2.0).
