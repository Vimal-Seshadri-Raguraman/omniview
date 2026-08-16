# OmniView

A modular, time-native intelligence platform: many small modules
(connectors, transforms, analytics, views) around one small core
(append-only bitemporal ontology, event bus, module registry, SDK).

Status: Phase 0 — skeleton. Setup:

    python3 -m venv .ov
    .ov/bin/pip install -r requirements.txt
    .ov/bin/pytest -v

## The intermediate console

`python -m intermediate.shell` serves the stacked-band console on
`OV_SHELL_PORT` (default 8080): five bands, L5 down to L1, each lighting up
as panels dock under `intermediate/panels/` (contract: that folder's README).

## Module lifecycle

`python -m core.dock new <name> --kind <kind> --layer <n>` scaffolds a
module and proves it loads through the registry. `python -m core.dock
retire <name>` undocks it to `modules/.retired/` — the append-only ledger
keeps everything it ever produced.
