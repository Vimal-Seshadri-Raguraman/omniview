"""The SDK door: the ctx object handed to every module (Phase 1).

The only interface modules get - ctx.raw, ctx.ontology, ctx.bus, ctx.log.
No module touches the database, another module, or the filesystem outside
its folder.
"""
