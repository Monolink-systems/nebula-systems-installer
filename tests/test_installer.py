from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main as installer_main
from modules import components, database, prerequisites, proxy, ui
from modules import env as env_module
from modules.config import DeploymentConfig, normalize_domain
from modules.core_service import build_core_unit, build_panel_unit
from modules.runner import CommandError, Runner

SERVICE_CONFIG = """services:
  server:
    host: "127.0.0.1"
plugins:
  environment: "development"
  in_process_enabled: true
  process_runtime_enabled: false
  runtime_socket_dir: "/tmp/nebula/plugins"
  runtime_log_dir: "/tmp/nebula/logs"
  cgroup_enabled: false
  cgroup_required: false
  allow_remote_grpc: false
"""


class ConfigTests(unittest.TestCase):
    def test_dev_profile_uses_home_style_layout_and_vite(self) -> None:
        config = DeploymentConfig.create("dev", root="/tmp/nebula-test")
        self.assertEqual(config.panel_port, 5173)
        self.assertEqual(config.panel_url, "http://127.0.0.1:5173")
        self.assertEqual(config.core_user, config.panel_user)

    def test_prod_profile_requires_normalized_public_names(self) -> None:
        config = DeploymentConfig.create(
            "prod",
            root="/opt/nebula",
            panel_domain="Panel.Example.COM.",
            core_domain="core.example.com",
        )
        self.assertEqual(config.panel_domain, "panel.example.com")
        self.assertEqual(config.panel_url, "https://panel.example.com")
        self.assertEqual(config.panel_core_url, "http://127.0.0.1:8000")
        self.assertNotEqual(config.core_user, config.panel_user)

    def test_invalid_domain_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_domain("https://panel.example.com/path")


class EnvironmentTests(unittest.TestCase):
    def _source_tree(self, root: Path) -> DeploymentConfig:
        config = DeploymentConfig.create("dev", root=str(root))
        (config.core_path / "nebula_core").mkdir(parents=True)
        (config.panel_path).mkdir(parents=True)
        (config.core_path / "nebula_core/serviceconfig.yaml").write_text(
            SERVICE_CONFIG, encoding="utf-8"
        )
        return config

    def test_dev_environment_is_local_and_tokens_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._source_tree(Path(directory))
            runner = Runner()
            core = env_module.configure_environment(config, runner)
            panel = env_module.read_env_file(config.panel_path / ".env")
            self.assertEqual(core["NEBULA_COOKIE_SECURE"], "false")
            self.assertEqual(core["NEBULA_CORE_RELOAD"], "true")
            self.assertEqual(
                core["NEBULA_INSTALLER_TOKEN"],
                panel["NEBULA_INSTALLER_TOKEN"],
            )
            self.assertEqual(panel["NODE_ENV"], "development")
            self.assertEqual(config.core_env_path.stat().st_mode & 0o777, 0o600)

    def test_prod_secrets_are_strong_and_policy_is_forced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = DeploymentConfig.create(
                "prod",
                root=directory,
                panel_domain="panel.example.com",
                core_domain="core.example.com",
            )
            (config.core_path / "nebula_core").mkdir(parents=True)
            config.panel_path.mkdir(parents=True)
            core = env_module.configure_environment(config, Runner(dry_run=True))
            self.assertEqual(core["NEBULA_COOKIE_SECURE"], "true")
            self.assertEqual(core["NEBULA_CORS_ORIGINS"], "https://panel.example.com")
            for key in env_module.SECRET_KEYS:
                self.assertGreaterEqual(len(core[key]), 43)

    def test_profile_is_external_and_source_remains_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._source_tree(Path(directory))
            source = config.core_path / "nebula_core/serviceconfig.yaml"
            before = source.read_text(encoding="utf-8")
            env_module.configure_core_profile(config, Runner())
            generated = config.core_path / "serviceconfig.yaml"
            self.assertTrue(generated.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), before)
            self.assertIn(
                'environment: "development"', generated.read_text(encoding="utf-8")
            )


class UnitTests(unittest.TestCase):
    def test_production_units_bind_to_managed_config_and_are_hardened(self) -> None:
        config = DeploymentConfig.create(
            "prod",
            panel_domain="panel.example.com",
            core_domain="core.example.com",
        )
        core = build_core_unit(config)
        panel = build_panel_unit(config, node="/usr/local/bin/node")
        self.assertIn("User=nebula-core", core)
        self.assertIn("Group=nebula-core", core)
        self.assertIn("SupplementaryGroups=docker", core)
        self.assertIn("ProtectSystem=full", core)
        self.assertIn("NEBULA_CONFIG_PATH=", core)
        self.assertIn("User=nebula-panel", panel)
        self.assertIn("Group=nebula-panel", panel)
        self.assertNotIn("SupplementaryGroups=docker", panel)
        self.assertIn("ProtectSystem=strict", panel)
        self.assertIn("127.0.0.1", proxy.render_caddy(config))

    def test_development_panel_uses_vite(self) -> None:
        config = DeploymentConfig.create("dev", root="/tmp/nebula-dev")
        unit = build_panel_unit(config, npm="/usr/local/bin/npm")
        self.assertIn("/usr/local/bin/npm run dev", unit)
        self.assertIn("--port 5173", unit)
        self.assertNotIn("ProtectSystem=strict", unit)


class RuntimeTests(unittest.TestCase):
    def test_selects_highest_supported_lts(self) -> None:
        releases: list[dict[str, object]] = [
            {"version": "v26.1.0", "lts": False, "files": ["linux-x64"]},
            {"version": "v24.2.0", "lts": "Krypton", "files": ["linux-x64"]},
            {"version": "v22.9.0", "lts": "Jod", "files": ["linux-x64"]},
            {"version": "v20.1.0", "lts": "Iron", "files": ["linux-x64"]},
        ]
        self.assertEqual(
            prerequisites.select_node_release(releases)["version"], "v24.2.0"
        )

    def test_component_version_floor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = root / "core"
            panel = root / "panel"
            core.mkdir()
            panel.mkdir()
            (core / "VERSION").write_text("0.6.0-alpha.1\n", encoding="utf-8")
            (panel / "package.json").write_text(
                json.dumps({"version": "0.2.0-alpha.1"}), encoding="utf-8"
            )
            ok, message = components.validate_versions(core, panel)
            self.assertTrue(ok, message)

    def test_pristine_system_database_is_bootstrapped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = DeploymentConfig.create("dev", root=directory)
            path = database.ensure_system_database(config, Runner())
            import sqlite3

            connection = sqlite3.connect(path)
            try:
                user_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(users)").fetchall()
                }
                runtime_tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                connection.execute(
                    """
                    INSERT INTO container_permissions
                        (container_id, username, db_name, role_tag)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(container_id, username) DO UPDATE SET
                        db_name = excluded.db_name,
                        role_tag = excluded.role_tag
                    """,
                    ("container-1", "operator", "system.db", "developer"),
                )
            finally:
                connection.close()
            self.assertTrue(
                {"username", "password_hash", "is_staff"} <= user_columns
            )
            self.assertTrue(
                {
                    "container_permissions",
                    "container_role_permissions",
                    "container_storage",
                }
                <= runtime_tables
            )
            self.assertEqual(
                database.schema_status(path),
                (True, "required Core tables are present"),
            )

    def test_incomplete_system_database_is_migrated_in_place(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            config = DeploymentConfig.create("dev", root=directory)
            path = config.core_path / "storage/databases/system.db"
            path.parent.mkdir(parents=True)
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password_hash BLOB NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    ("existing_admin", b"existing-hash"),
                )
                connection.commit()
            finally:
                connection.close()

            database.ensure_system_database(config, Runner())

            connection = sqlite3.connect(path)
            try:
                existing = connection.execute(
                    "SELECT username, password_hash FROM users"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(existing, ("existing_admin", b"existing-hash"))
            self.assertTrue(database.schema_status(path)[0])


class InteractionTests(unittest.TestCase):
    def test_invalid_admin_input_is_requested_again(self) -> None:
        args = argparse.Namespace(admin_user="", admin_password_file="")
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        valid_password = "secure-local-password-2026"

        with (
            mock.patch.object(installer_main.sys, "stdin", fake_stdin),
            mock.patch.object(
                installer_main.ui,
                "ask",
                side_effect=["bad", "valid_admin"],
            ),
            mock.patch.object(
                installer_main.getpass,
                "getpass",
                side_effect=[
                    "first-password",
                    "different-password",
                    "short",
                    "short",
                    valid_password,
                    valid_password,
                ],
            ),
            mock.patch.object(installer_main.ui, "warn") as warning,
        ):
            username, password, generated = installer_main._admin_credentials(
                args, "dev"
            )

        self.assertEqual(username, "valid_admin")
        self.assertEqual(password, valid_password)
        self.assertFalse(generated)
        self.assertGreaterEqual(warning.call_count, 3)

    def test_yes_no_prompt_repeats_invalid_answers(self) -> None:
        with (
            mock.patch("builtins.input", side_effect=["invalid", "yes"]),
            mock.patch.object(ui, "warn") as warning,
        ):
            self.assertTrue(ui.ask_yes_no("Continue?"))
        warning.assert_called_once()

    def test_interactive_installation_retries_operation_failure(self) -> None:
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        with (
            mock.patch.object(
                installer_main.sys,
                "argv",
                ["main.py", "install", "--mode", "dev", "--yes"],
            ),
            mock.patch.object(installer_main.sys, "stdin", fake_stdin),
            mock.patch.object(
                installer_main,
                "install_command",
                side_effect=[CommandError("temporary failure"), 0],
            ) as install,
            mock.patch.object(installer_main.ui, "ask_yes_no", return_value=True),
            mock.patch.object(installer_main.ui, "error"),
            mock.patch.object(installer_main.ui, "info"),
        ):
            result = installer_main.main()

        self.assertEqual(result, 0)
        self.assertEqual(install.call_count, 2)


if __name__ == "__main__":
    unittest.main()
