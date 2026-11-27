"""The AI gateway (AD-8): the only door to an AI provider.

Callers build requests from `redact(text, known_values_for(actor))` and send them through
`gateway`. Only this package may import a provider SDK or open outbound network
(`egress.ALLOWED_HOSTS`); an import contract and an AST test hold that line. Domain apps use the
submodules `redaction`, `ports`, `gateway` and `known_values`, never an adapter directly.
"""
