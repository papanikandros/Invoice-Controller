"""EnergieKonzept-Krause brand for the web UI (user request 2026-09-24).

Source of truth: the company website's DESIGN.md (~/Workspace/EnergieKonzept/
ekk-website) — "warm authority": near-white page, Consultant's Taupe as the one
accent, gold only as a small jewel, Oxblood for errors, Source Sans 3 in weights
instead of a second face, flat surfaces separated by warm tints, pill CTAs,
underline-only fields. Two brand rules matter in code: taupe is never body text
(2:1 on white), and gold covers ≤ 5 % of any screen — so status colours stay
small badges and the page ink is Graphite.

The font and logo are vendored under web/static/ (Source Sans 3: SIL OFL 1.1) so
the UI needs no third-party host — colleagues sit behind corporate proxies.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

STATIC_DIR = Path(__file__).parent / "static"

# DESIGN.md tokens
BROWN = "#B39573"       # Consultant's Taupe — accent, never ink
GOLD = "#D4AF37"
OXBLOOD = "#8B1418"
PARCHMENT = "#FFF7DF"
SAND = "#F5F3E8"
LINEN = "#F6ECE1"
GRAPHITE = "#474747"
SLATE = "#6B6B6B"
STEEL = "#687074"
MIST = "#C4C4C4"
SURFACE = "#FFFFFF"
PAGE = "#FAFAFA"

# Status → (badge background, badge text). Taupe/Gold/Oxblood each carry ONE
# meaning; the resting states stay neutral so the accents keep their signal.
STATUS_STYLE: dict[str, tuple[str, str]] = {
    "läuft": (STEEL, SURFACE),
    "wartet": (MIST, GRAPHITE),
    "ok": (BROWN, SURFACE),
    "fertig": (BROWN, SURFACE),
    "hinweis": (GOLD, GRAPHITE),
    "fehler": (OXBLOOD, SURFACE),
    "abgebrochen": (SLATE, SURFACE),
}

_CSS = f"""
@font-face {{
  font-family: 'Source Sans 3';
  font-style: normal;
  font-weight: 200 900;
  font-display: swap;
  src: url('/ic-static/source-sans-3-latin-wght-normal.woff2') format('woff2');
}}
:root {{
  --ic-brown: {BROWN}; --ic-gold: {GOLD}; --ic-oxblood: {OXBLOOD};
  --ic-parchment: {PARCHMENT}; --ic-sand: {SAND}; --ic-linen: {LINEN};
  --ic-graphite: {GRAPHITE}; --ic-slate: {SLATE}; --ic-steel: {STEEL};
  --ic-mist: {MIST}; --ic-surface: {SURFACE}; --ic-page: {PAGE};
}}
html, body, .q-field, .q-btn, .q-tab, .q-item {{
  font-family: 'Source Sans 3', 'Segoe UI', system-ui, sans-serif;
}}
body {{ background: var(--ic-page); color: var(--ic-graphite); font-size: 1rem; line-height: 1.6; }}
.nicegui-content {{ padding: 0; }}

/* header: white, flat, one Mist hairline — the website's logo bar */
.ic-header {{
  background: var(--ic-surface); color: var(--ic-graphite);
  border-bottom: 1px solid var(--ic-mist); box-shadow: none; padding: 0;
}}
.ic-header .ic-nav a {{
  color: var(--ic-graphite); font-weight: 300; text-decoration: none;
  transition: color 180ms cubic-bezier(0.22, 1, 0.36, 1);
}}
.ic-header .ic-nav a:hover, .ic-header .ic-nav a:focus-visible {{ color: var(--ic-brown); }}
.ic-header .ic-nav a:focus-visible {{ outline: 2px solid var(--ic-brown); outline-offset: 4px; border-radius: 2px; }}

/* padding-inline only: Tailwind 4 utilities live in a cascade layer, so an
   unlayered `padding` shorthand here would silently beat py-12 / pt-8 on the same element. */
.ic-container {{ width: 100%; max-width: 72rem; margin: 0 auto; padding-inline: 1.5rem; }}
.ic-band {{ background: var(--ic-sand); }}
.ic-band-parchment {{ background: var(--ic-parchment); }}

/* the website's section marker: heading + centred taupe rule */
.ic-underline::after {{
  content: ''; display: block; width: 8rem; height: 2px; background: var(--ic-brown);
  margin: 0.75rem auto 0;
}}
.ic-display {{ font-size: 2rem; font-weight: 600; line-height: 1.1; letter-spacing: -0.01em; text-wrap: balance; }}
.ic-headline {{ font-size: 1.125rem; font-weight: 600; line-height: 1.3; }}
.ic-muted {{ color: var(--ic-slate); }}
.ic-label {{ color: var(--ic-slate); font-weight: 300; font-size: 0.95rem; }}

/* program choice: two flat panels, not a card grid — tint, hairline, one pill */
.ic-program {{
  background: var(--ic-surface); border: 1px solid var(--ic-mist); border-radius: 8px;
  padding: 2rem; display: flex; flex-direction: column; gap: 0.75rem; min-height: 100%;
  transition: border-color 180ms cubic-bezier(0.22, 1, 0.36, 1);
}}
.ic-program:hover, .ic-program:focus-within {{ border-color: var(--ic-brown); }}
.ic-program ul {{ margin: 0; padding-left: 1.1rem; color: var(--ic-slate); list-style: disc; }}
.ic-program ul li::marker {{ color: var(--ic-brown); }}

/* tabs: graphite labels, taupe indicator; active weight instead of accent ink */
.ic-tabs .q-tab {{ color: var(--ic-slate); font-weight: 300; text-transform: none; font-size: 1rem; letter-spacing: 0; }}
.ic-tabs .q-tab--active {{ color: var(--ic-graphite); font-weight: 600; }}
.ic-tabs .q-tab__indicator {{ background: var(--ic-brown); height: 2px; }}
.ic-tabs .q-tabs__content {{ border-bottom: 1px solid var(--ic-mist); }}
.q-tab-panels, .q-tab-panel {{ background: transparent; }}

/* pill CTAs */
.ic-btn {{ border-radius: 9999px; padding: 0.35rem 1.75rem; text-transform: none; font-weight: 600; font-size: 1rem; letter-spacing: 0; }}
.ic-btn.q-btn--outline {{ font-weight: 400; }}
.q-btn:focus-visible {{ outline: 2px solid var(--ic-brown); outline-offset: 3px; }}

/* underline-only fields (Quasar "standard") in brand colours */
.q-field--standard .q-field__control:before {{ border-bottom-color: var(--ic-mist); }}
.q-field--standard .q-field__control:hover:before {{ border-bottom-color: var(--ic-steel); }}
.q-field--standard .q-field__control:after {{ background: var(--ic-brown); height: 2px; }}
.q-field__label {{ color: var(--ic-slate); font-weight: 300; }}
.q-field--focused .q-field__label {{ color: var(--ic-brown); }}
.q-field__native, .q-field__input {{ color: var(--ic-graphite); }}
.q-field__native::placeholder {{ color: var(--ic-steel); opacity: 1; }}

/* dropzone: dashed hairline on sand, taupe on hover */
.ic-upload {{ background: var(--ic-sand); border: 1px dashed var(--ic-mist); border-radius: 8px; box-shadow: none; transition: border-color 180ms cubic-bezier(0.22, 1, 0.36, 1); }}
.ic-upload:hover {{ border-color: var(--ic-brown); }}
.ic-upload .q-uploader__header {{ background: transparent; color: var(--ic-graphite); }}
.ic-upload .q-uploader__title {{ font-weight: 400; }}
.ic-upload .q-uploader__list {{ background: transparent; }}
.ic-upload .q-uploader__subtitle {{ display: none; }}

/* messages: warm bands, no side stripes */
.ic-error {{ background: var(--ic-linen); color: var(--ic-graphite); border-radius: 8px; padding: 0.6rem 0.9rem; }}
.ic-flag {{ background: var(--ic-parchment); color: var(--ic-graphite); border-radius: 8px; padding: 0.6rem 0.9rem; }}
.ic-badge {{ border-radius: 9999px; font-weight: 600; font-size: 0.75rem; padding: 2px 10px; }}

.ic-link {{ color: var(--ic-graphite); text-decoration: underline; text-decoration-color: var(--ic-mist); text-underline-offset: 3px; }}
.ic-link:hover {{ color: var(--ic-brown); text-decoration-color: var(--ic-brown); }}
.ic-row {{ border-bottom: 1px solid var(--ic-mist); }}
.ic-row:last-child {{ border-bottom: none; }}
.ic-log {{ background: var(--ic-surface); border: 1px solid var(--ic-mist); border-radius: 8px; }}
.q-expansion-item .q-item {{ color: var(--ic-graphite); }}

/* floating surfaces only carry shadow (Flat-Ground Rule) */
.q-menu, .q-notification {{ box-shadow: 0 10px 15px -3px rgba(71, 71, 71, 0.15), 0 4px 6px -4px rgba(71, 71, 71, 0.1); }}

@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }}
}}
"""

_registered = False


def install() -> None:
    """One-time: static route for font/logo, brand colours for Quasar's palette
    (primary drives buttons, tab indicators, field focus), the stylesheet."""
    global _registered
    if not _registered:
        app.add_static_files("/ic-static", str(STATIC_DIR))
        _registered = True
    ui.colors(primary=BROWN, secondary=STEEL, accent=GOLD, positive=BROWN,
              negative=OXBLOOD, warning=GOLD, info=STEEL, dark=GRAPHITE)
    ui.add_head_html(f"<style>{_CSS}</style>")


def header(*, back_to: str | None = None, program_label: str | None = None) -> None:
    """The website's logo bar: logo left, extralight nav right. `back_to` adds a
    "← …" link on the left; `program_label` names the chosen family."""
    with ui.header().classes("ic-header"):
        with ui.element("div").classes("ic-container flex items-center gap-6 py-2"):
            with ui.link(target="/").classes("flex items-center no-underline shrink-0"):
                # a plain <img>: ui.image renders a q-img box whose width collapses with w-auto
                ui.element("img").props('src="/ic-static/logo.png" alt="EnergieKonzept Krause GmbH"').classes("h-12 w-auto")
            if program_label:
                ui.label(program_label).classes("ic-headline hidden sm:block")
            ui.space()
            with ui.element("nav").classes("ic-nav flex items-center gap-8"):
                if back_to:
                    ui.link(back_to[0], back_to[1])
                ui.link("Bisherige Läufe", "/laeufe")


def badge(status: str) -> None:
    bg, fg = STATUS_STYLE.get(status, (MIST, GRAPHITE))
    ui.label(status).classes("ic-badge").style(f"background:{bg};color:{fg}")
