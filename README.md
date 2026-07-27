# Nebula Systems Installer

The supported installer and operations CLI for
[Nebula Core](https://github.com/Monolink-systems/nebula-core) and
[Nebula Panel](https://github.com/Monolink-systems/nebula-panel).

It prepares Ubuntu, installs compatible runtimes, downloads both components,
configures Docker and systemd, creates the first super-administrator, and
installs the `nebula` management command. No manual environment files, Python
virtual environments, npm setup, service units, or token synchronization are
required.

## Quick start

### Developer installation

```bash
curl -fsSL https://raw.githubusercontent.com/Monolink-systems/nebula-systems-installer/main/install.sh \
  | bash -s -- --mode dev
```

The installer requests sudo authentication once, then asks for the initial
administrator account. Leave the administrator password empty to generate a
local development password.

The resulting services are available at:

```text
Panel: http://127.0.0.1:5173
Core:  http://127.0.0.1:8000
```

Core runs with automatic reload. Panel runs through Vite with HMR. Both are
managed as systemd services and start with the host.

### Production installation

Create two DNS records pointing to the server before installation:

```text
panel.example.com -> server public IP
core.example.com  -> server public IP
```

Open inbound TCP ports 80 and 443, then run:

```bash
curl -fsSL https://raw.githubusercontent.com/Monolink-systems/nebula-systems-installer/main/install.sh \
  | bash -s -- --mode prod \
      --panel-domain panel.example.com \
      --core-domain core.example.com
```

Caddy obtains and renews TLS certificates, redirects HTTP to HTTPS, and proxies
requests to Core and Panel. The application services remain bound to
`127.0.0.1`.

> Nebula Core and Nebula Panel are currently pre-alpha projects. The production
> profile hardens the deployment host, but it does not make a pre-alpha API
> stable. Review the deployment before placing critical workloads on it.

## Installation profiles

| Area | Developer | Production |
| --- | --- | --- |
| Installation root | `~/.local/share/nebula` | `/opt/nebula` |
| Core runtime | Managed Python 3.11, reload enabled | Managed Python 3.11 |
| Panel runtime | Vite/HMR on port 5173 | adapter-node build on port 3000 |
| Secrets | Fixed local-only values | Random 48-byte URL-safe values |
| Service accounts | Current user | Separate `nebula-core` and `nebula-panel` users |
| Docker access | Current user and Core | Core only |
| Network | Loopback HTTP | Caddy, TLS, strict CORS, secure cookies |
| Plugins | In-process | Process runtime and cgroup v2 |
| Backups | Manual | Daily systemd timer and manual command |

The installer provides CPython 3.11 through
[uv](https://docs.astral.sh/uv/) and installs a checksum-verified official
Node.js LTS archive. Existing unsupported or non-LTS host runtimes are not used
for the application.

## Management

```bash
nebula status
nebula doctor
nebula start all
nebula stop core
nebula restart panel
nebula logs all
nebula logs core --follow
nebula repair
nebula update
nebula backup
nebula backup --databases-only
nebula rotate-secrets
```

Valid service targets are `all`, `core`, `panel`, and `proxy`. The same commands
can be run from a local checkout through `./nebulactl.sh`.

## Failure handling

Interactive validation does not terminate the installer. Invalid usernames,
passwords, confirmations, domains, modes, and yes/no responses are explained and
requested again.

If a host operation fails, the installer displays the relevant command output,
preserves completed work, and offers to retry from the current idempotent state.
The user may explicitly stop and resume later by running the same command.

Non-interactive installations never wait for input. They return a non-zero exit
code on failure and can be rerun safely.

## Automatic checks

The installation verifies:

- Ubuntu or Debian compatibility, systemd, and CPU architecture;
- the managed Python 3.11 runtime and a supported Node.js LTS release;
- Core version 0.6.0 or newer and Panel version 0.2.0 or newer;
- Docker daemon availability;
- matching internal tokens in Core and Panel;
- Core readiness through `/system/status`;
- Core and Panel systemd services;
- production DNS records and the server public IP;
- generated Caddyfile syntax before Caddy is reloaded;
- Panel HTTPS availability when DNS is ready;
- npm runtime dependency advisories.

Existing databases and unknown environment values are preserved. Source
repositories with local modifications are not overwritten during updates.

## Production security

The production profile:

- runs Core and Panel under separate unprivileged users and primary groups;
- grants Docker access only to Core;
- stores secret and database files with mode `600`;
- uses `NoNewPrivileges`, `ProtectSystem`, `ProtectHome`, `PrivateTmp`,
  `PrivateDevices`, an empty capability set, and restricted address families;
- binds Core, Panel, and internal gRPC endpoints to loopback;
- enables secure cookies, exact CORS origins, and login throttling;
- enables process-isolated plugins and cgroup controls;
- configures UFW while preserving the detected SSH port;
- creates daily consistent backups in `/var/backups/nebula`.

Docker-published container ports bypass ordinary UFW rules. Publish only
required plugin ports and define an appropriate `DOCKER-USER` policy when
external container access is required.

Copy production backups to separate encrypted storage. A local backup protects
against update mistakes, but not against loss of the server disk.

## Local checkout

```bash
git clone https://github.com/Monolink-systems/nebula-systems-installer.git
cd nebula-systems-installer
./install.sh --mode dev
```

Use verbose output when diagnosing a host command:

```bash
./nebulactl.sh --verbose install --mode dev
```

## Non-interactive installation

Pass the production administrator password through a temporary mode-600 file:

```bash
chmod 600 /secure/admin-password
./install.sh --mode prod \
  --panel-domain panel.example.com \
  --core-domain core.example.com \
  --admin-user nebula_admin \
  --admin-password-file /secure/admin-password \
  --yes
```

Use `--no-firewall` when the host firewall is managed by another system.

## Diagnostics

Start with:

```bash
nebula doctor
```

Then inspect or repair the deployment:

```bash
nebula logs core
nebula logs panel
nebula logs proxy
nebula repair
```

## License

Copyright (c) 2026 Monolink Systems.

Nebula Open Source Edition (non-corporate) is distributed under the
[GNU Affero General Public License v3.0](LICENSE).
