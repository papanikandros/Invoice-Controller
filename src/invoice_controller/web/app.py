"""NiceGUI shell (U1): choose the funding program → pick a procedure tab → upload
documents → run → download results.

German UI for the colleagues, styled to the EnergieKonzept-Krause brand (web/theme.py).
The program choice comes first (user decision 2026-09-24): `/` offers EEW and BEG,
`/<program>` shows only that program's procedures as tabs. Every run is a background
job (web/jobs.py) with a live job page; errors arrive pre-translated (web/errors.py),
review flags render as warm parchment bands — visually distinct from errors.
Optional password gate via IC_WEB_PASSWORD in .env; on the server Caddy's
basic_auth is the outer layer.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from nicegui import app, ui

from invoice_controller.web import theme
from invoice_controller.web.jobs import Job, JobStore
from invoice_controller.web.registry import (
    PROCEDURES,
    PROGRAMS,
    ZAHLUNGSNACHWEIS_SUBFOLDER,
    Procedure,
    Program,
    procedures_for,
    program_by_key,
)

STORE = JobStore(Path("tmp") / "webruns")


def _guard() -> bool:
    """Password gate — active only when IC_WEB_PASSWORD is set."""
    password = os.environ.get("IC_WEB_PASSWORD", "")
    if not password or app.storage.user.get("authed"):
        return True
    with ui.element("div").classes("absolute-center w-full max-w-sm px-6"):
        ui.element("img").props('src="/ic-static/logo.png" alt="EnergieKonzept Krause GmbH"').classes("h-16 w-auto mx-auto mb-6 block")
        ui.label("Invoice-Controller").classes("ic-headline text-center")
        ui.label("Bitte anmelden").classes("ic-label text-center mb-2")
        pw = ui.input("Passwort", password=True, password_toggle_button=True).classes("w-full")

        def check() -> None:
            if secrets.compare_digest(pw.value or "", password):
                app.storage.user["authed"] = True
                ui.navigate.reload()
            else:
                ui.notify("Falsches Passwort", type="negative")

        pw.on("keydown.enter", check)
        ui.button("Anmelden", icon="chevron_right", on_click=check).classes("ic-btn mt-4 w-full")
    return False


def _program_of(job: Job) -> Program | None:
    return next((prog for prog in PROGRAMS if job.procedure.startswith(prog.key + "-")), None)


def _procedure_panel(proc: Procedure) -> None:
    pending: list[tuple[str, bytes, str | None]] = []
    field_inputs: dict[str, ui.input | ui.select] = {}

    ui.label(proc.upload_hint).classes("ic-muted")

    def stash(subfolder: str | None):
        async def handler(e) -> None:
            # NiceGUI 3.x: the event carries a FileUpload (`e.file`) with an ASYNC
            # read — accessing e.name/e.content was the 3.16 upload bug (2026-09-03).
            content = await e.file.read()
            pending.append((e.file.name, content, subfolder))
            ui.notify(f"{e.file.name} hochgeladen")
            counter.set_text(f"{len(pending)} Datei(en) bereit")
        return handler

    ui.upload(on_upload=stash(None), multiple=True, auto_upload=True) \
        .props(f'accept="{proc.accept}" label="Dokumente hier ablegen oder auswählen" flat') \
        .classes("ic-upload w-full")
    if proc.with_zahlungsnachweise:
        ui.label("Zahlungsnachweise (Überweisungs-Screenshots / PDFs) — separat ablegen:") \
            .classes("ic-muted mt-2")
        ui.upload(on_upload=stash(ZAHLUNGSNACHWEIS_SUBFOLDER), multiple=True, auto_upload=True) \
            .props('accept=".pdf,.png,.jpg,.jpeg" label="Zahlungsnachweise hier ablegen" flat') \
            .classes("ic-upload w-full")

    # Wide inputs: Quasar truncates long labels ("Kostendeckel-Förderanteil (%)")
    # in narrow fields — each input fills its grid cell, two generous columns.
    with ui.grid(columns=2).classes("gap-x-8 gap-y-2 mt-4 w-full max-w-5xl"):
        for spec in proc.fields:
            label = spec.label + (" *" if spec.required else "")
            if spec.options:
                field_inputs[spec.key] = ui.select(
                    list(spec.options), value=spec.options[0], label=label
                ).props("stack-label").classes("w-full min-w-[22rem]")
            else:
                # stack-label: with a placeholder present, Quasar's floating label
                # would otherwise sit on top of the placeholder text.
                field_inputs[spec.key] = ui.input(label, placeholder=spec.placeholder) \
                    .props("stack-label").classes("w-full min-w-[22rem]")

    with ui.row().classes("items-center gap-6 mt-6"):
        def start() -> None:
            if not pending:
                ui.notify("Bitte zuerst Dokumente hochladen.", type="warning")
                return
            params = {key: (inp.value or "") for key, inp in field_inputs.items()}
            for spec in proc.fields:
                if spec.required and not params.get(spec.key, "").strip():
                    ui.notify(f"Bitte '{spec.label}' ausfüllen.", type="warning")
                    return
            projekt = params.get("projekt", "").strip()
            title = f"{proc.label} — {projekt}" if projekt else f"{proc.label} — {len(pending)} Datei(en)"
            job = STORE.create(proc.key, title)
            for name, content, subfolder in pending:
                STORE.save_upload(job, name, content, subfolder=subfolder)
            STORE.start(job, lambda j: proc.run(j, params))
            ui.navigate.to(f"/lauf/{job.id}")

        ui.button("Start", icon="chevron_right", on_click=start).classes("ic-btn")
        counter = ui.label("0 Datei(en) bereit").classes("ic-label")


@ui.page("/")
def index() -> None:
    theme.install()
    if not _guard():
        return
    theme.header()
    with ui.element("section").classes("ic-band w-full"):
        with ui.element("div").classes("ic-container py-12 text-center"):
            ui.label("Förderprogramm wählen").classes("ic-display ic-underline")
            ui.label("Danach erscheinen die Verfahren des Programms als Reiter — "
                     "Dokumente hochladen, Start drücken, Ergebnis herunterladen.") \
                .classes("ic-muted mt-6 mx-auto max-w-[60ch]")
    with ui.element("div").classes("ic-container py-10"):
        with ui.grid(columns="repeat(auto-fit, minmax(280px, 1fr))").classes("gap-8 items-stretch"):
            for prog in PROGRAMS:
                with ui.element("div").classes("ic-program"):
                    ui.label(prog.title).classes("ic-display")
                    ui.label(prog.description).classes("ic-muted")
                    with ui.element("ul").classes("mt-2"):
                        for proc in procedures_for(prog):
                            with ui.element("li").classes("py-0.5"):
                                ui.label(proc.label)
                    ui.space()
                    ui.button(f"{prog.label} öffnen", icon="chevron_right",
                              on_click=lambda p=prog: ui.navigate.to(f"/{p.key}")) \
                        .classes("ic-btn self-start mt-4")


@ui.page("/laeufe")
def history() -> None:
    theme.install()
    if not _guard():
        return
    theme.header(back_to=("← Programm wählen", "/"))
    with ui.element("div").classes("ic-container pt-8 pb-12"):
        ui.label(f"Bisherige Läufe (die letzten {len(STORE.jobs)})").classes("ic-display")
        if not STORE.jobs:
            ui.label("Noch keine Läufe — ein Verfahren starten, dann erscheint es hier.").classes("ic-muted mt-2")
        with ui.element("div").classes("mt-4"):
            for job in sorted(STORE.jobs.values(), key=lambda j: j.created, reverse=True):
                with ui.row().classes("ic-row items-center gap-4 py-3 w-full flex-nowrap"):
                    ui.label(f"{job.created:%d.%m.%Y %H:%M}").classes("text-sm ic-muted shrink-0 w-32")
                    theme.badge(job.status)
                    ui.link(job.title, f"/lauf/{job.id}").classes("ic-link truncate")
                    ui.space()
                    if job.errors:
                        ui.label(f"{len(job.errors)} Fehler").classes("ic-badge").style(
                            f"background:{theme.OXBLOOD};color:{theme.SURFACE}")
                    if job.flags:
                        ui.label(f"{len(job.flags)} Hinweise").classes("ic-badge").style(
                            f"background:{theme.PARCHMENT};color:{theme.GRAPHITE}")


# Registered after /laeufe on purpose: Starlette matches routes in order and the
# wildcard would otherwise swallow the history page.
@ui.page("/{program_key}")
def program_page(program_key: str) -> None:
    theme.install()
    if not _guard():
        return
    prog = program_by_key(program_key)
    if prog is None:
        theme.header()
        with ui.element("div").classes("ic-container py-10"):
            ui.label("Unbekanntes Förderprogramm.").classes("ic-headline")
            ui.link("← Programm wählen", "/")
        return
    theme.header(back_to=("← Programm wechseln", "/"), program_label=prog.title)
    procs = procedures_for(prog)
    with ui.element("div").classes("ic-container pt-8 pb-12"):
        ui.label(prog.title).classes("ic-display")
        ui.label(prog.description).classes("ic-muted mb-4 max-w-[70ch]")
        with ui.tabs().classes("ic-tabs w-full") as tabs:
            for proc in procs:
                ui.tab(proc.key, label=proc.label)
        with ui.tab_panels(tabs, value=procs[0].key).classes("w-full"):
            for proc in procs:
                with ui.tab_panel(proc.key).classes("px-0 pt-6"):
                    _procedure_panel(proc)


@ui.page("/lauf/{job_id}")
def job_page(job_id: str) -> None:
    theme.install()
    if not _guard():
        return
    job = STORE.jobs.get(job_id)
    prog = _program_of(job) if job else None
    theme.header(back_to=("← Übersicht", f"/{prog.key}" if prog else "/"),
                 program_label=prog.title if prog else None)
    with ui.element("div").classes("ic-container pt-8 pb-12"):
        if job is None:
            ui.label("Lauf nicht gefunden (evtl. bereits aufgeräumt).").classes("ic-headline")
            return

        @ui.refreshable
        def render(j: Job = job) -> None:
            with ui.row().classes("items-center gap-4"):
                ui.label(j.title).classes("ic-display")
                theme.badge(j.status)
                if j.status == "läuft":
                    ui.spinner(size="sm", color="primary")
                    ui.button("Abbrechen", icon="close",
                              on_click=lambda: (j.cancel(), ui.notify("Abbruch angefordert"))) \
                        .props("flat dense no-caps").classes("ic-btn")
            for err in j.errors:
                with ui.row().classes("ic-error items-center gap-3 w-full mt-2 flex-nowrap"):
                    ui.icon("error").style(f"color:{theme.OXBLOOD}")
                    ui.label(err.message_de)
            for flag in j.flags:
                with ui.row().classes("ic-flag items-center gap-3 w-full mt-2 flex-nowrap"):
                    ui.icon("warning").style(f"color:{theme.GOLD}")
                    ui.label(flag)
            if j.outputs:
                ui.label("Ergebnisse").classes("ic-headline mt-6")
                with ui.row().classes("gap-3 mt-1"):
                    for out in j.outputs:
                        ui.button(out.name, icon="download",
                                  on_click=lambda p=out: ui.download(p)).props("outline").classes("ic-btn")
            if j.files:
                ui.label("Dokumente").classes("ic-headline mt-6")
                with ui.grid(columns="auto auto 1fr").classes("gap-x-6 gap-y-1 items-center mt-1"):
                    for fs in j.files.values():
                        ui.label(fs.name).classes("text-sm")
                        theme.badge(fs.status)
                        ui.label(fs.message).classes("text-sm ic-muted")

        render()

        # The Protokoll expansion lives OUTSIDE the refreshable: a refresh would recreate
        # it, collapsing its open state and discarding any text selection (reported
        # 2026-09-03). Its content is updated in place instead.
        with ui.expansion("Protokoll", icon="terminal").classes("ic-log w-full mt-6"):
            log_label = ui.label("\n".join(job.progress[-60:]) or "—").classes(
                "text-xs whitespace-pre-wrap font-mono"
            )

        # Refresh ONLY when the job actually changed; once it is finished, one final
        # refresh and the timer stops — the page becomes fully static.
        seen = {"version": job.version}

        def poll() -> None:
            if job.version == seen["version"]:
                if job.status != "läuft":
                    timer.cancel()
                return
            seen["version"] = job.version
            render.refresh()
            log_label.set_text("\n".join(job.progress[-60:]) or "—")

        timer = ui.timer(1.0, poll)


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    STORE.load_history()
    ui.run(
        host=host,
        port=port,
        title="Invoice-Controller · EnergieKonzept Krause",
        favicon=str(theme.STATIC_DIR / "favicon.png"),
        show=False,
        reload=False,
        storage_secret=os.environ.get("IC_WEB_STORAGE_SECRET", secrets.token_hex(16)),
    )


__all__ = ["PROCEDURES", "STORE", "run_server"]
