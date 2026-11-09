# Runtime data

This directory follows the VOGUE repository convention. Source data, derived
reports, and local state are runtime files and must not be committed.

- `raw/`: source files, only after a source and its usage rights are approved.
- `interim/`: temporary processing artifacts.
- `processed/`: reproducible, source-backed outputs.
- `local/`: machine-specific caches or development state.

The workspace may contain local H&M Kaggle source and processed files. They are
Git-ignored; their presence does not confirm usage permission, nor does it
mean an approved internal sales ledger or market provider is connected. Do not
delete, copy, inspect, or upload these files as part of repository cleanup.
Keep credentials in local environment configuration and never in Git.
