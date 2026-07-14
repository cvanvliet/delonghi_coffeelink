# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Home Assistant custom integration (HACS-style) that controls De'Longhi "Coffee Link" connected coffee machines through De'Longhi's cloud. Auth chain: it logs into a **Gigya** (SAP CDC) identity, exchanges the JWT for an **Ayla Networks** IoT access token, then lists / polls / controls the machine via Ayla's device REST API. Cloud-polling only; there is no local API.

Minimum HA version `2024.1.0` (`hacs.json`). No third-party Python requirements (`manifest.json` `requirements` is empty).

## Commands

- Run all tests: `pytest tests/` (from repo root)
- Single file: `pytest tests/test_command_builder.py`
- Single test: `pytest tests/test_command_builder.py::<test_name>`

Tests deliberately import **only the dependency-free leaf modules** (`const`, `command_builder`, `monitor`, `counters`) directly via `importlib` — they never import the package `__init__` (which pulls in `homeassistant`). So `pytest` alone runs them, no HA install needed. Keep any logic you want unit-tested inside those leaf modules and free of `homeassistant` imports.

CI (`.github/workflows/validate.yml`) runs **HACS validation** and **hassfest** (manifest check) on push/PR — it does **not** run pytest, and there is no configured linter/formatter. Run `pytest` locally yourself.

## Local dev against a live HA box

This repo is typically developed against a running Home Assistant: cloned **outside** the HA config dir and **symlinked** into `custom_components/delonghi_coffeelink`. Edit the checkout, then **restart HA** to load changes — custom-component code is imported once at startup, so a config-entry *reload* does not pick up code edits, only a full restart does. Bump `manifest.json` `version` for releases.

## Architecture

Cloud auth + transport live entirely in `ayla_client.py`; everything else is HA glue plus binary-protocol logic.

### Auth chain — `ayla_client.py` (`DelonghiAylaClient`)
Gigya `accounts.login` → `accounts.getJWT` (HMAC-SHA1 signed request) → Ayla `token_sign_in` (JWT → `access_token`) → Ayla device/property calls. `async_ensure_auth()` re-runs the chain when the token is near expiry, before each request.

### Regions — `const.py` (`REGIONS`)
The **Ayla** side is regional: EU and US use different `app_id`/`app_secret` **and** endpoints (`ads-eu`/`user-field-eu` vs `ads-field`/`user-field`). The **Gigya** identity is *global* (eu1) and shared by both. The region is picked in the config flow, stored on the entry, and passed to `DelonghiAylaClient`, which resolves `REGIONS[region]` into its endpoints/credentials. **Adding a region = one entry in `REGIONS`** — no other code changes. (US: app_id `DeLonghiCoffeeIdKit-yA-id`, hosts `ads-field`/`user-field`.)

### Setup — `config_flow.py`, `__init__.py`
`config_flow.py` validates the account by running the full auth chain plus a device-list call (fails with `invalid_auth` / `no_devices` / `cannot_connect`). `async_setup_entry` builds the client, lists devices, creates **one `DelonghiCoordinator` per device**, forwards the `sensor` / `binary_sensor` / `button` platforms, and registers the integration services (`start_beverage`, `stop_beverage`, `send_raw_command`).

### Coordinator — `coordinator.py`
A `DataUpdateCoordinator` polling all device properties every 30s. Three non-obvious jobs:
- **Property auto-detection** — property names differ by model (`data_request` on Soul vs `app_data_request` on Eletta); the live name is chosen from candidate lists in `const.py`, not hard-coded.
- **Cloud session** (ECAM/Eletta) — these machines require a registered `app_device_connected` session (an `app_id`) before commands relay. The logic mirrors the `DlghIoT` client: adopt the official app's session if it holds one, POST + confirm on a cold connect, and revert to our own id once released. It never keeps a foreign session alive.
- **Command sniffer / learn-and-replay** — each poll already fetches the command/response channels, so the coordinator watches them; when the official app sends a frame it is captured, and for learn-from-app models the exact per-beverage bytes are **learned and persisted** (survive restarts) for verbatim replay.

### Per-model behaviour — `model_profiles.py`
Instead of scattered `if oem_model == ...`, each family is a `ModelProfile` with two possible strategies:
- **Synthesize** (PrimaDonna Soul, `DL-millcore`): build fixed command frames from scratch.
- **Learn-and-replay** (Eletta Explore, `DL-striker*`, and any unknown model by default): the machine ignores synthesized frames, so replay the app's own captured bytes. To support a new model, add a `ModelProfile` subclass with a `matches()` rule and (if needed) `learns_from_app = True` — nothing else changes.

### Binary protocol — `command_builder.py`, `monitor.py`, `const.py`
`command_builder.py` builds/decodes the frames (CRC16 AUG-CCITT; brew family `0x83 0xf0`, power/wake family `0x84 0x0f`). `monitor.py` decodes the `d302_monitor_machine` status blob into a machine state. `const.py` is the single source for the beverage table, the counter/info sensor definitions (candidate property name → entity), machine status codes, and every protocol constant. Protocol reference: DlghIoT (`framagit.org/mattgk/dlghiot`).

### Entities
`sensor.py` (beverage counters, info, diagnostics), `binary_sensor.py`, `button.py` (per-beverage brew buttons, wake/standby, recipe-dump diagnostic). Counter/info sensors are created only when their backing property exists on the device, to avoid permanently-`unknown` entities.
