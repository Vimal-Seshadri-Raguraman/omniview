# Modules

One folder per module, forever: `manifest.yaml` + `module.py`.
The manifest is the module's contract with the core (see manifest-spec.md,
local doc); an invalid manifest does not load — no warnings, only refusals.
The modularity test applies to every module including agents: delete any
module — no other module may notice.
