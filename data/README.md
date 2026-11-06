# Runtime data

This directory follows the VOGUE repository convention. Source data, derived
reports, and local state are runtime files and must not be committed.

- `raw/`: source files, only after a source and its usage rights are approved.
- `interim/`: temporary processing artifacts.
- `processed/`: reproducible, source-backed outputs.
- `local/`: machine-specific caches or development state.

No provider or market dataset is configured yet, so these directories contain
no data. Keep credentials in local environment configuration and never in Git.
