# P07 runtime data directory

P07's HTTP review normally reads market research from its configured R2 source
or combined research snapshot; it does not require source files in this local
directory. This directory is reserved for explicitly configured local
processing, temporary artifacts, and machine-specific state. Runtime files
must not be committed.

- `raw/`: source files, only after a source and its usage rights are approved.
- `interim/`: temporary processing artifacts.
- `processed/`: reproducible, source-backed outputs.
- `local/`: machine-specific caches or development state.

Local H&M Kaggle files, if present, are not proof of usage permission and do
not mean an internal sales ledger or current market provider is connected.
Customer-level source rows must not be copied into a runtime aggregate snapshot;
only the approved aggregate fields belong there. Keep credentials in local
environment configuration and never in Git. Check source rights and the
configured data flow before using local files.
