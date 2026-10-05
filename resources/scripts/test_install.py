"""Exercise installer helpers without root, network access or service changes."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "install.sh"
SH = os.environ.get("TEST_INSTALL_SH") or shutil.which("sh")
if not SH:
    raise SystemExit("A POSIX shell is required (set TEST_INSTALL_SH to its path)")


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stalwart-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        self.env.pop("STALWART_DOWNLOAD_BASE_URL", None)
        self.env["PATH"] = str(Path(SH).resolve().parent) + os.pathsep + self.env.get("PATH", "")
        source = INSTALL.read_text(encoding="utf-8")
        invocation = 'main "$@" || exit 1'
        self.assertTrue(source.rstrip().endswith(invocation))
        self.definitions = source.rstrip()[:-len(invocation)]

    def helper(self, script, *args, env=None):
        return subprocess.run(
            [SH, "-s", "--", *(str(arg).replace("\\", "/") for arg in args)],
            input=self.definitions + "\n" + script,
            text=True, encoding="utf-8", capture_output=True,
            env=env or self.env,
        )

    def test_shell_syntax_and_help_without_installation(self):
        syntax = subprocess.run([SH, "-n", str(INSTALL)], capture_output=True, env=self.env)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        result = subprocess.run(
            [SH, str(INSTALL), "--help"], capture_output=True,
            text=True, encoding="utf-8", env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--binary PATH", result.stdout)
        self.assertIn("--env-file PATH", result.stdout)
        self.assertIn("PostgreSQL and Cloudflare R2", result.stdout)

    def test_generated_env_has_connection_defaults_and_is_valid_shell(self):
        destination = self.root / "service.env"
        result = self.helper('write_env_file "$1"\n', destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = destination.read_text(encoding="utf-8")
        self.assertIn("STALWART_POSTGRES_HOST=localhost", text)
        self.assertIn("STALWART_POSTGRES_PORT=5432", text)
        self.assertIn("STALWART_R2_ENDPOINT=", text)
        self.assertIn("STALWART_R2_SECRET_ACCESS_KEY=", text)
        syntax = subprocess.run([SH, "-n", str(destination)], capture_output=True, env=self.env)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_local_binary_is_copied_without_download(self):
        source = self.root / "source binary"
        destination = self.root / "installed binary"
        source.write_bytes(b"local-build-test")
        chmod = 'chmod() { return 0; }\n' if os.name == "nt" else ""
        result = self.helper(
            chmod + 'downloader() { echo "unexpected download" >&2; return 99; }\n'
            'install_binary "$2" "$1" stalwart\n', source, destination,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(destination.read_bytes(), source.read_bytes())
        if os.name != "nt":
            self.assertEqual(destination.stat().st_mode & 0o777, 0o755)

    def test_first_install_copies_env_and_reinstall_preserves_existing_settings(self):
        source = self.root / "binary"
        source.write_bytes(b"local-build-test")
        provided_env = self.root / "connection.env"
        provided_env.write_text("MARKER=first\n", encoding="utf-8")
        prefix = self.root / "installation"
        chmod = 'chmod() { return 0; }\n' if os.name == "nt" else ""
        mocks = (
            chmod + 'id() { printf "0\\n"; }\n'
            'uname() { printf "Linux\\n"; }\n'
            'hostname() { printf "mail.example.test\\n"; }\n'
            'check_cmd() { return 0; }\n'
            'create_account() { return 0; }\n'
            'chown() { return 0; }\n'
            'create_service_linux_systemd() { return 0; }\n'
            'main --binary "$1" --env-file "$2" "$3"\n'
        )
        result = self.helper(mocks, source, provided_env, prefix)
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = prefix / "etc" / "stalwart.env"
        self.assertEqual(saved.read_text(encoding="utf-8"), "MARKER=first\n")
        provided_env.write_text("MARKER=second\n", encoding="utf-8")
        result = self.helper(mocks, source, provided_env, prefix)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(saved.read_text(encoding="utf-8"), "MARKER=first\n")
        self.assertIn("Keeping existing environment file", result.stdout)

    def test_download_source_belongs_to_fork_and_can_be_overridden(self):
        result = self.helper('printf "%s" "$BASE_URL"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "https://github.com/lehuunghi/server/releases/latest/download")
        env = self.env.copy()
        env["STALWART_DOWNLOAD_BASE_URL"] = "https://example.invalid/releases/test"
        result = self.helper('printf "%s" "$BASE_URL"\n', env=env)
        self.assertEqual(result.stdout, env["STALWART_DOWNLOAD_BASE_URL"])

    def test_missing_option_arguments_fail_before_installation(self):
        for option in ["--binary", "--env-file"]:
            result = subprocess.run(
                [SH, str(INSTALL), option], capture_output=True,
                text=True, encoding="utf-8", env=self.env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires a file path", result.stderr)


if __name__ == "__main__":
    unittest.main()
