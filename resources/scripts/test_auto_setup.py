"""Verify the unattended installer, including an HTTP JMAP bootstrap round trip."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import auto_setup as setup

REPO = Path(__file__).resolve().parents[2]
CONFIG = {
    "STALWART_DOMAIN": "example.test", "STALWART_R2_ACCOUNT_ID": "a" * 32,
    "STALWART_R2_BUCKET": "mail", "STALWART_R2_ACCESS_KEY_ID": "test-access",
    "STALWART_R2_SECRET_ACCESS_KEY": "test-secret-$'\\#",
    "STALWART_REQUEST_TLS_CERTIFICATE": False,
}


class AutoSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "installation"
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps(CONFIG))
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def prepare(self):
        setup.prepare(self.config_path, self.directory, REPO)

    def test_prefilled_config_managed_postgres_and_permissions(self):
        self.prepare()
        state = setup.read_json(self.directory / "deployment.json")
        self.assertEqual(state["environment"]["STALWART_HOSTNAME"], "mail.example.test")
        self.assertGreaterEqual(len(state["environment"]["STALWART_POSTGRES_PASSWORD"]), 32)
        compose = setup.read_json(self.directory / "compose.json")
        self.assertIn("postgres", compose["services"])
        self.assertIn("127.0.0.1:8080:8080", compose["services"]["server"]["ports"])
        self.assertNotIn("5432:5432", json.dumps(compose))
        for filename in [".env", "deployment.json", "compose.json"]:
            self.assertEqual((self.directory / filename).stat().st_mode & 0o777, 0o600)
        if os.environ.get("TEST_DOCKER_COMPOSE"):
            # Exercise Compose's actual dotenv parser, including literal secrets.
            result = subprocess.run(["docker", "compose", "--env-file", str(self.directory / ".env"),
                                     "-f", str(self.directory / "compose.json"), "config", "--format", "json"],
                                    check=True, capture_output=True, text=True)
            actual = json.loads(result.stdout)["services"]["server"]["environment"]
            self.assertEqual(actual["STALWART_R2_SECRET_ACCESS_KEY"], CONFIG["STALWART_R2_SECRET_ACCESS_KEY"])

    def test_rerun_preserves_passwords_and_refuses_changed_config(self):
        self.prepare()
        original = (self.directory / "deployment.json").read_bytes()
        self.prepare()
        self.assertEqual(original, (self.directory / "deployment.json").read_bytes())
        config = dict(CONFIG, STALWART_DOMAIN="another.test")
        self.config_path.write_text(json.dumps(config))
        with self.assertRaises(setup.SetupError):
            self.prepare()
        self.assertEqual(original, (self.directory / "deployment.json").read_bytes())

    def test_invalid_input_fails_before_creating_deployment(self):
        for config in [dict(CONFIG, STALWART_R2_SECRET_ACCESS_KEY="YOUR_KEY"),
                       dict(CONFIG, STALWART_DOMAIN="https://example.test"),
                       dict(CONFIG, STALWART_DOMAIN="127.0.0.1"),
                       dict(CONFIG, STALWART_POSTGRES_PORT="0"),
                       dict(CONFIG, STALWART_REQUEST_TLS_CERTIFICATE="false"),
                       dict(CONFIG, STALWART_R2_ENDPOINT="http://r2.test"),
                       dict(CONFIG, STALWART_R2_ACCESS_KEY_ID="secret\nline"),
                       dict(CONFIG, UNKNOWN="value")]:
            self.config_path.write_text(json.dumps(config))
            with self.assertRaises(setup.SetupError):
                self.prepare()
            self.assertFalse(self.directory.exists())

    def test_external_postgres_does_not_create_local_database(self):
        env = setup.normalize(dict(CONFIG, STALWART_POSTGRES_HOST="db.example.test", STALWART_POSTGRES_PASSWORD="external"))
        compose = setup.compose_config(env, REPO)
        self.assertNotIn("postgres", compose["services"])
        self.assertNotIn("depends_on", compose["services"]["server"])
        with self.assertRaises(setup.SetupError):
            setup.normalize(dict(CONFIG, STALWART_POSTGRES_HOST="db.example.test"))

    def test_bootstrap_protocol_saves_credentials_before_removing_recovery(self):
        self.prepare()
        state = setup.read_json(self.directory / "deployment.json")
        calls = []
        bootstrapped = False

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, value):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def do_GET(self):
                expected = "setup:" + state["setup_password"] if not bootstrapped else "admin@example.test:generated-password"
                expected = "Basic " + setup.base64.b64encode(expected.encode()).decode()
                if self.headers.get("Authorization") != expected:
                    self.send_error(401)
                    return
                self.respond({"accounts": {"account": {}}, "primaryAccounts": {"urn:stalwart:jmap": "account"},
                              "apiUrl": "https://mail.example.test/jmap/"})

            def do_POST(self):
                nonlocal bootstrapped
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(body)
                method, args, call_id = body["methodCalls"][0]
                if method == "x:Bootstrap/get":
                    result = {"list": [] if bootstrapped else [{"id": "singleton"}]}
                else:
                    bootstrapped = True
                    result = {"updated": {"singleton": {"username": "admin@example.test", "secret": "generated-password"}}}
                self.respond({"methodResponses": [[method, result, call_id]]})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        real_write = setup.private_write

        def checked_write(path, value):
            if Path(path).name == ".env" and "STALWART_RECOVERY_ADMIN" not in value:
                self.assertTrue((self.directory / "credentials.json").exists())
            real_write(path, value)

        with patch.object(setup, "private_write", side_effect=checked_write):
            setup.bootstrap(self.directory, url)
        credentials = setup.read_json(self.directory / "credentials.json")
        self.assertEqual(credentials["status"], "bootstrapped")
        self.assertNotIn("STALWART_RECOVERY_ADMIN", (self.directory / ".env").read_text())
        self.assertNotIn("setup_password", setup.read_json(self.directory / "deployment.json"))
        self.assertEqual(calls[1]["methodCalls"][0][1]["update"]["singleton"]["defaultDomain"], "example.test")
        setup.verify(self.directory, url)
        count = len(calls)
        self.prepare()
        setup.bootstrap(self.directory, url)
        self.assertEqual(len(calls), count, "A completed deployment must not be bootstrapped twice")
        self.assertEqual(setup.read_json(self.directory / "credentials.json")["status"], "ready")

    def test_bootstrap_error_keeps_recovery_login_for_retry(self):
        self.prepare()
        with patch.object(setup.Client, "session", return_value={"accounts": {"a": {}}}), \
             patch.object(setup.Client, "call", side_effect=[{"list": [{"id": "singleton"}]}, {"notUpdated": {"singleton": {"type": "invalidProperties"}}}]):
            with self.assertRaises(setup.SetupError):
                setup.bootstrap(self.directory)
        self.assertFalse((self.directory / "credentials.json").exists())
        self.assertIn("STALWART_RECOVERY_ADMIN", (self.directory / ".env").read_text())

    def test_credentials_are_never_sent_to_external_or_redirect_urls(self):
        with self.assertRaises(setup.SetupError):
            setup.Client("https://external.example.test", "admin", "secret")
        with self.assertRaises(setup.SetupError):
            setup.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://external.example.test")

    def test_shell_help_and_missing_args_do_not_install_packages(self):
        result = subprocess.run(["sh", str(REPO / "install-auto.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--config", result.stdout)
        result = subprocess.run(["sh", str(REPO / "install-auto.sh"), "--config"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires a value", result.stderr)


if __name__ == "__main__":
    unittest.main()
