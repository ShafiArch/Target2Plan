// about.js: the About panel in the header: author, license and credits.
import { h } from "./common.js";
import { overlay } from "./detail.js";

const REPO = "https://github.com/ShafiArch/Target2Plan";
const link = (href, text) => h("a", { href, target: "_blank", rel: "noopener" }, text);

export function openAbout() {
  let close = () => {};
  const panel = h("div.detail.about", { role: "dialog", "aria-label": "About Target2Plan" },
    h("div.detail-head", h("span.h", "ABOUT"), h("span.idx", "Target2Plan"), h("span.grow"),
      h("button.icon-btn", { title: "Close (Esc)", on: { click: () => close() } }, "✕")),
    h("div.about-body",
      h("p", "From a room program and QVCI / AQI targets to zone assignments, connectivity graphs and schematic plans. The trained models run in your browser; nothing leaves your machine."),
      h("div.about-sec", h("div.h", "THESIS"),
        h("p", "Developed by ", h("b", "Shafiul Islam"), " as a thesis in the Master of Science in Architecture / Design Technology program, Department of Architecture, University of Washington."),
        h("p.people", "Thesis supervisor: ", h("b", "Tomás Méndez Echenagucia"), ", Associate Professor, Department of Architecture"),
        h("p.people", "Thesis reviewer: ", h("b", "Mohammad Tabatabaei Manesh"), ", PhD student, Design Technology")),
      h("div.about-sec", h("div.h", "AUTHOR AND LICENSE"),
        h("p", "© 2026 Shafiul Islam. The code and this website are licensed under ",
          link("https://creativecommons.org/licenses/by-nc/4.0/", "CC BY-NC 4.0"),
          ": anyone may use, share and adapt them with full credit to the author; commercial use is not permitted."),
        h("p.credit", "Credit as: Target2Plan by Shafiul Islam (MS in Architecture / Design Technology thesis, University of Washington, supervised by Tomás Méndez Echenagucia), ", link(REPO, "github.com/ShafiArch/Target2Plan"), ", licensed under CC BY-NC 4.0."),
        h("p", link(`${REPO}/blob/main/LICENSE`, "License"), "  ·  ", link(`${REPO}/blob/main/THIRD_PARTY_NOTICES.md`, "Third-party notices"), "  ·  ", link(REPO, "Source code"))),
      h("div.about-sec", h("div.h", "CREDITS"),
        h("p", h("b", "Furniture placement"), " is a port of the furniture placement logic in RGeoLib, the research geometry library of the Hypergraph project by ",
          h("b", "Ramon Elias Weber"), " (", link("https://github.com/ramonweber/hypergraph", "github.com/ramonweber/hypergraph"), ", MIT License)."),
        h("p.cite", "Weber, R. E., Mueller, C. & Reinhart, C. A hypergraph model shows the carbon reduction potential of effective space use in housing. ",
          h("i", "Nature Communications"), " 15, 8327 (2024). ", link("https://doi.org/10.1038/s41467-024-52506-z", "doi.org/10.1038/s41467-024-52506-z")),
        h("p", h("b", "GIF export"), ": gifenc by Matt DesLauriers (MIT).  ", h("b", "MP4 export"), ": mp4-muxer by Vanilagy (MIT).  ",
          h("b", "Fonts"), ": Inter and JetBrains Mono (SIL Open Font License), served by Google Fonts."))));
  close = overlay(panel);
}
