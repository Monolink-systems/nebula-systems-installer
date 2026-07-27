# Changelog

All notable changes to the **Nebula Systems Installer** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- All installer prompts, status messages, and documentation now use formal
  English and restrained terminal status labels.
- Interactive validation requests corrected input instead of terminating the
  installer.
- Failed interactive installation operations preserve completed work and offer
  an immediate idempotent retry.
- Database provisioning now migrates the container access, role policy, and
  storage tables required by a pristine Core checkout.
- `nebula doctor` now detects incomplete Core database schemas.

## [2.0.0-alpha.1] - 2026-07-27

### Added

- One-command Ubuntu bootstrap with automatic host dependencies.
- Explicit developer and production installation profiles.
- Managed CPython 3.11 and verified current Node.js LTS runtime installation.
- Production DNS validation, Caddy automatic HTTPS, UFW setup and hardened
  systemd services.
- Separate Core and Panel service accounts, production plugin isolation and
  consistent scheduled backups.
- Installed `nebula` CLI with status, doctor, repair, update, backup, logs and
  service-management commands.
- Automated tests for profiles, secrets, generated units, version compatibility
  and runtime selection.

### Changed

- Component URLs now point to the `Monolink-systems` repositories.
- Installation is a single idempotent pipeline instead of separate fetch/setup
  commands.
- Core's plugin profile is generated as a git-excluded runtime file instead of
  changing the tracked upstream configuration.
- Documentation is consolidated into one task-focused README.

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

[Unreleased]: https://github.com/Monolink-systems/nebula-systems-installer/compare/v2.0.0-alpha.1...HEAD
[2.0.0-alpha.1]: https://github.com/Monolink-systems/nebula-systems-installer/compare/v1.0.0-alpha.1...v2.0.0-alpha.1
[1.0.0-alpha.1]: https://github.com/Monolink-systems/nebula-systems-installer/releases/tag/v1.0.0-alpha.1
