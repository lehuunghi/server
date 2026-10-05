"""Exercise the actual server after the disposable PostgreSQL/S3 store tests.

Run as root in an isolated CI runner: the server's normal listeners use ports
25/443, and the default tracer writes /var/log/stalwart. No real R2 keys needed.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

import auto_setup as setup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    if os.getuid() != 0:
        parser.error("Run on an isolated CI host as root (normal server ports and log path).")
    # Separate empty database: the store suite must never be reset by bootstrap.
    subprocess.run(["docker", "exec", "stalwart-test-postgres", "psql", "-U", "stalwart", "-d", "stalwart",
                    "-v", "ON_ERROR_STOP=1", "-c", "CREATE DATABASE auto_bootstrap;"], check=True)
    with tempfile.TemporaryDirectory(prefix="stalwart-bootstrap-live-") as temporary:
        directory = Path(temporary)
        config_path = directory / "input.json"
        config_path.write_text(json.dumps({
            "STALWART_DOMAIN": "example.test", "STALWART_HOSTNAME": "mail.example.test",
            "STALWART_POSTGRES_HOST": "127.0.0.1", "STALWART_POSTGRES_DATABASE": "auto_bootstrap",
            "STALWART_POSTGRES_USER": "stalwart", "STALWART_POSTGRES_PASSWORD": "stalwart",
            "STALWART_R2_ENDPOINT": "https://s3.example.test", "STALWART_R2_BUCKET": "stalwart",
            "STALWART_R2_ACCESS_KEY_ID": "minioadmin", "STALWART_R2_SECRET_ACCESS_KEY": "minioadmin",
            "STALWART_REQUEST_TLS_CERTIFICATE": False,
        }))
        setup.prepare(config_path, directory, Path(__file__).resolve().parents[2])
        state = setup.read_json(directory / "deployment.json")
        # Only this test fixture uses HTTP MinIO; production validation requires HTTPS R2.
        env = dict(os.environ, **state["environment"])
        env["STALWART_R2_ENDPOINT"] = "http://127.0.0.1:9000"
        env["STALWART_RECOVERY_ADMIN"] = "setup:" + state["setup_password"]
        config_file = directory / "config.json"
        process = None
        log_path = directory / "server.log"
        try:
            with log_path.open("w") as log:
                process = subprocess.Popen([str(binary), "--config", str(config_file)], env=env, stdout=log, stderr=log)
                setup.bootstrap(directory)
                credentials = setup.read_json(directory / "credentials.json")
                assert credentials["username"] == "admin@example.test"
                assert credentials["status"] == "bootstrapped"
                assert "postgres" in config_file.read_text().lower()
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=30)
                process = None
                env.pop("STALWART_RECOVERY_ADMIN")
                process = subprocess.Popen([str(binary), "--config", str(config_file)], env=env, stdout=log, stderr=log)
                setup.verify(directory)
                credentials = setup.read_json(directory / "credentials.json")
                assert credentials["status"] == "ready"
                assert credentials["api_url"] == "https://mail.example.test/jmap/"
                assert "STALWART_RECOVERY_ADMIN" not in (directory / ".env").read_text()
                # Idempotent rerun retains the same generated administrator.
                setup.prepare(config_path, directory, Path(__file__).resolve().parents[2])
                setup.bootstrap(directory)
                assert setup.read_json(directory / "credentials.json")["password"] == credentials["password"]
            print("Live PostgreSQL/S3 bootstrap, restart, administrator login and JMAP API passed.")
        except Exception:
            # Redact both temporary and real generated passwords before CI output.
            log_text = log_path.read_text()
            for secret in [state["setup_password"], setup.read_json(directory / "credentials.json").get("password", "")
                           if (directory / "credentials.json").exists() else ""]:
                if secret:
                    log_text = log_text.replace(secret, "[redacted]")
            print(log_text[-12000:])
            raise
        finally:
            if process and process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
