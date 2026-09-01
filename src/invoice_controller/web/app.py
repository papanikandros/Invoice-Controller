"""NiceGUI shell (U1): select procedure → upload documents → run → download results.

German UI for the colleagues. Every run is a background job (web/jobs.py) with a
live job page; errors arrive pre-translated (web/errors.py), review flags render
yellow/red — visually distinct from errors. Optional password gate via
IC_WEB_PASSWORD in .env (recommended when the port is exposed through ngrok;
additionally use `ngrok http --basic-auth ...`).
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from nicegui import app, ui

from invoice_controller.web.jobs import Job, JobStore
from invoice_controller.web.registry import PROCEDURES, ZAHLUNGSNACHWEIS_SUBFOLDER, Procedure

STORE = JobStore(Path("tmp") / "webruns")

_STATUS_COLORS = {
    "läuft": "blue", "fertig": "green", "fehler": "red", "abgebrochen": "orange",
    "wartet": "grey", "ok": "green", "hinweis": "orange",
}


def _guard() -> bool:
    """Password gate — active only when IC_WEB_PASSWORD is set."""
    password = os.environ.get("IC_WEB_PASSWORD", "")
    if not password or app.storage.user.get("authed"):
        return True
    with ui.card().classes("absolute-center items-center"):
        ui.label("Invoice-Controller").classes("text-xl font-bold")
        pw = ui.input("Passwort", password=True, password_toggle_button=True)

        def check() -> None:
            if secrets.compare_digest(pw.value or "", password):
                app.storage.user["authed"] = True
                ui.navigate.reload()
            else:
                ui.notify("Falsches Passwort", type="negative")

        pw.on("keydown.enter", check)
        ui.button("Anmelden", on_click=check)
    return False


def _badge(status: str) -> None:
    ui.badge(status, color=_STATUS_COLORS.get(status, "grey"))


def _procedure_panel(proc: Procedure) -> None:
    pending: list[tuple[str, bytes, str | None]] = []
    field_inputs: dict[str, ui.input | ui.select] = {}

    ui.label(proc.upload_hint).classes("text-sm text-gray-600")

    def stash(subfolder: str | None):
        def handler(e) -> None:
            pending.append((e.name, e.content.read(), subfolder))
            ui.notify(f"{e.name} hochgeladen")
            counter.set_text(f"{len(pending)} Datei(en) bereit")
        return handler

    ui.upload(on_upload=stash(None), multiple=True, auto_upload=True) \
        .props(f'accept="{proc.accept}" label="Dokumente hier ablegen"').classes("w-full")
    if proc.with_zahlungsnachweise:
        ui.label("Zahlungsnachweise (Überweisungs-Screenshots / PDFs) — separat ablegen:") \
            .classes("text-sm text-gray-600 mt-2")
        ui.upload(on_upload=stash(ZAHLUNGSNACHWEIS_SUBFOLDER), multiple=True, auto_upload=True) \
            .props('accept=".pdf,.png,.jpg,.jpeg" label="Zahlungsnachweise hier ablegen"').classes("w-full")

    for spec in proc.fields:
        if spec.options:
            field_inputs[spec.key] = ui.select(list(spec.options), value=spec.options[0], label=spec.label)
        else:
            field_inputs[spec.key] = ui.input(spec.label, placeholder=spec.placeholder)

    counter = ui.label("0 Datei(en) bereit").classes("text-sm")

    def start() -> None:
        if not pending:
            ui.notify("Bitte zuerst Dokumente hochladen.", type="warning")
            return
        params = {key: (inp.value or "") for key, inp in field_inputs.items()}
        job = STORE.create(proc.key, f"{proc.label} — {len(pending)} Datei(en)")
        for name, content, subfolder in pending:
            STORE.save_upload(job, name, content, subfolder=subfolder)
        STORE.start(job, lambda j: proc.run(j, params))
        ui.navigate.to(f"/lauf/{job.id}")

    ui.button("Start", icon="play_arrow", on_click=start).classes("mt-2")


@ui.page("/")
def index() -> None:
    if not _guard():
        return
    with ui.header().classes("items-center"):
        ui.label("Invoice-Controller").classes("text-lg font-bold")
        ui.space()
        ui.link("Bisherige Läufe", "/laeufe").classes("text-white")
    ui.label("Verfahren wählen, Dokumente hochladen, Start drücken — das Ergebnis "
             "gibt es als Download; alle Prüf-Hinweise werden angezeigt.").classes("text-gray-600")
    with ui.tabs() as tabs:
        for proc in PROCEDURES:
            ui.tab(proc.key, label=proc.label)
    with ui.tab_panels(tabs, value=PROCEDURES[0].key).classes("w-full"):
        for proc in PROCEDURES:
            with ui.tab_panel(proc.key):
                _procedure_panel(proc)


@ui.page("/lauf/{job_id}")
def job_page(job_id: str) -> None:
    if not _guard():
        return
    job = STORE.jobs.get(job_id)
    with ui.header().classes("items-center"):
        ui.link("← Übersicht", "/").classes("text-white")
        ui.space()
        ui.link("Bisherige Läufe", "/laeufe").classes("text-white")
    if job is None:
        ui.label("Lauf nicht gefunden (evtl. bereits aufgeräumt).").classes("text-red-600")
        return

    @ui.refreshable
    def render(j: Job = job) -> None:
        with ui.row().classes("items-center"):
            ui.label(j.title).classes("text-lg font-bold")
            _badge(j.status)
            if j.status == "läuft":
                ui.spinner(size="sm")
                ui.button("Abbrechen", icon="cancel", color="red",
                          on_click=lambda: (j.cancel(), ui.notify("Abbruch angefordert"))).props("flat dense")
        for err in j.errors:
            with ui.row().classes("bg-red-100 rounded p-2 w-full"):
                ui.icon("error", color="red")
                ui.label(err.message_de)
        for flag in j.flags:
            with ui.row().classes("bg-orange-100 rounded p-2 w-full"):
                ui.icon("warning", color="orange")
                ui.label(flag)
        if j.outputs:
            ui.label("Ergebnisse").classes("font-bold mt-2")
            for out in j.outputs:
                ui.button(out.name, icon="download",
                          on_click=lambda p=out: ui.download(p)).props("outline")
        if j.files:
            ui.label("Dokumente").classes("font-bold mt-2")
            with ui.grid(columns="auto auto 1fr").classes("gap-x-4 gap-y-1 items-center"):
                for fs in j.files.values():
                    ui.label(fs.name).classes("text-sm")
                    _badge(fs.status)
                    ui.label(fs.message).classes("text-sm text-gray-600")
        with ui.expansion("Protokoll", icon="terminal").classes("w-full mt-2"):
            ui.label("\n".join(job.progress[-60:]) or "—").classes("text-xs whitespace-pre-wrap font-mono")

    render()
    ui.timer(1.0, render.refresh)


@ui.page("/laeufe")
def history() -> None:
    if not _guard():
        return
    with ui.header().classes("items-center"):
        ui.link("← Übersicht", "/").classes("text-white")
    ui.label(f"Bisherige Läufe (die letzten {len(STORE.jobs)})").classes("text-lg font-bold")
    for job in sorted(STORE.jobs.values(), key=lambda j: j.created, reverse=True):
        with ui.row().classes("items-center gap-4 border-b p-2 w-full"):
            ui.label(f"{job.created:%d.%m.%Y %H:%M}").classes("text-sm text-gray-500")
            _badge(job.status)
            ui.link(job.title, f"/lauf/{job.id}")
            if job.errors:
                ui.badge(f"{len(job.errors)} Fehler", color="red")
            if job.flags:
                ui.badge(f"{len(job.flags)} Hinweise", color="orange")


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    STORE.load_history()
    ui.run(
        host=host,
        port=port,
        title="Invoice-Controller",
        favicon="🧾",
        show=False,
        reload=False,
        storage_secret=os.environ.get("IC_WEB_STORAGE_SECRET", secrets.token_hex(16)),
    )
