# Agent notes

## Project conventions

- Modular: one app per domain under `apps/`, one package per concern inside it (for example `apps/kernel/storage/`). Don't pile unrelated modules flat in an app; add or extend a package. Layers are enforced by `lint-imports`.
- Where stored files go is decided only in `apps/kernel/storage/keys.py`.
