# Panels dock here

Each child folder is one panel: `<name>/panel.yaml` + its entrypoint file.
The shell (`python -m intermediate.shell`) discovers, validates, and docks
panels at boot — an invalid panel is refused with the violated rule named.

Contract (`panel.yaml`):

    name: feed-health            # kebab-case, must match the folder name
    mirrors: layer:L1            # layer:L1..L5 | module:<backend-module>
    order: 10                    # position within the band, ascending
    description: >
      Feed health per raw source.
    data:
      source: null               # URL of a data binding, or null
      poll: 5                    # refresh seconds (>=1 when source is set)
    entrypoint: panel.py:render  # callable(PanelCtx) -> HTML fragment str

The band a panel docks in is derived from `mirrors:` — that mapping is the
1-to-1 backend↔intermediate mirror the console renders pipelines from.
Retired panels move to `.retired/` (dot-folders are never discovered).
