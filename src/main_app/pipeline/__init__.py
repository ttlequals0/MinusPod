"""Episode processing pipeline stages, split out of main_app.processing."""

# Import order is a dependency order; each module imports only earlier ones.
STAGE_MODULES = (
    'common', 'transcribe', 'signals', 'detect', 'review', 'cuts', 'keep_rules',
    'verify', 'publish', 'finalize', 'admission', 'failure', 'recut',
)
