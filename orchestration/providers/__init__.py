"""HTTP-backed LLM providers for the orchestration layer. This subpackage is the ONLY part of
`orchestration/` allowed to import an HTTP client (enforced by tests/orchestration/test_invariants.py);
agents, the broker and the runtime reach models only through `PolicyRouter`."""
