# Changelog

All notable changes to the **Nebula Systems Installer** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0-alpha.1]

### Added

- Standalone installer repository, extracted from the Nebula monorepo.
- Component discovery: Core and Panel are located from `--core-dir` /
  `--panel-dir`, `NEBULA_CORE_DIR` / `NEBULA_PANEL_DIR`, sibling directories, or
  the deployment root.
- `--fetch` clones missing component repositories from Git.
- Panel provisioning: `npm ci`, `npm run build`, and a `nebula-panel` systemd
  unit running the `adapter-node` server.
- Per-component `.env` generation with automatic synchronisation of the shared
  internal token.
- `--status`, reporting components, host tooling, services, and Core reachability.
- `nebulactl.sh`, a wrapper covering every routine operation.

### Changed

- Split the single `main.py` into focused modules: `paths`, `env`, `components`,
  `docker_setup`, `core_api`, `core_service`, and `ui`.
- Services are now `nebula-core` and `nebula-panel`; unit generation no longer
  assumes both components share a checkout or a virtual environment.

### Removed

- Flask panel support (`nebula-gui` service, `nebula_gui_flask` paths).
- Legacy `install/.env` discovery and the monorepo-relative path assumptions.

[Unreleased]: https://github.com/elmWilh/NebulaSystemsInstaller/compare/v1.0.0-alpha.1...HEAD
[1.0.0-alpha.1]: https://github.com/elmWilh/NebulaSystemsInstaller/releases/tag/v1.0.0-alpha.1
