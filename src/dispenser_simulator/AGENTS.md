# Canonical simulator runtime

This is the single runtime source, shipped inside the independently cloneable MCP
checkout. The sibling simulator project keeps developer tests, docs and a package
bootstrap only. Do not create a second physics implementation or parent-tree
runtime dependency. The simulator specialist retains ownership of model, clock,
contract and scenario behavior; coordinate through root and keep the decision
specialist isolated from hidden simulator configuration and internal state.
Backend selection/HTTP integration belong to the MCP engineer. Relocation alone
does not authorize changing dynamics, bounds, interlocks or scientific semantics.
No-load stop state is process-local. Do not add startup inspection/reset gates or
durable authorization files; the nearby human handles between-session checks.
Keep current-band checks and automatic OFF, and allow explicit OFF after a trip.
