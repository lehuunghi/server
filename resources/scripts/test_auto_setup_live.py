"""Verify remote storage, deleted local config, administrator/API and optional relay.

Run as root in an isolated CI runner: normal listeners use ports 25/443.
PostgreSQL and the S3-compatible fixture are disposable, not production services.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

import auto_setup as setup


def exercise(binary, with_relay):
    database = "auto_bootstrap_relay" if with_relay else "auto_bootstrap"
    subprocess.run(["docker", "exec", "stalwart-test-postgres", "psql", "-U", "stalwart", "-d", "stalwart",
                    "-v", "ON_ERROR_STOP=1", "-c", "CREATE DATABASE " + database + ";"], check=True)
    with tempfile.TemporaryDirectory(prefix="stalwart-bootstrap-live-") as temporary:
        directory = Path(temporary)
        config_path = directory / "input.json"
        config = {
            "STALWART_DOMAIN": "example.test", "STALWART_HOSTNAME": "mail.example.test",
            "STALWART_POSTGRES_HOST": "127.0.0.1", "STALWART_POSTGRES_DATABASE": database,
            "STALWART_POSTGRES_USER": "stalwart", "STALWART_POSTGRES_PASSWORD": "stalwart",
            "STALWART_R2_ENDPOINT": "https://s3.example.test", "STALWART_R2_BUCKET": "stalwart",
            "STALWART_R2_ACCESS_KEY_ID": "minioadmin", "STALWART_R2_SECRET_ACCESS_KEY": "minioadmin",
            "STALWART_REQUEST_TLS_CERTIFICATE": False, "STALWART_STATELESS": True,
        }
        if with_relay:
            config.update(STALWART_SMTP_RELAY_HOST="127.0.0.1", STALWART_SMTP_RELAY_PORT="2526",
                          STALWART_SMTP_RELAY_USER="relay-user", STALWART_SMTP_RELAY_PASSWORD="disposable-relay-secret",
                          STALWART_SMTP_RELAY_TLS="starttls")
        config_path.write_text(json.dumps(config))
        source = Path(__file__).resolve().parents[2]
        setup.prepare(config_path, directory, source)
        state = setup.read_json(directory / "deployment.json")
        # Production validation requires HTTPS; only this fixture uses HTTP MinIO.
        env = dict(os.environ, **setup.runtime_environment(state, False))
        env["STALWART_R2_ENDPOINT"] = "http://127.0.0.1:9000"
        env.pop("STALWART_DATA_STORE", None)
        config_file = directory / "config.json"
        process = None
        log_path = directory / "server.log"
        try:
            with log_path.open("w") as log:
                process = subprocess.Popen([str(binary), "--config", str(config_file)], env=env, stdout=log, stderr=log)
                initial_client = setup.Client("http://127.0.0.1:8080", "setup", state["setup_password"])
                initial_arguments = setup.account_arguments(initial_client.session())
                singleton_id = initial_client.call("x:Bootstrap/get", initial_arguments)["list"][0]["id"]
                setup.bootstrap(directory)
                credentials = setup.read_json(directory / "credentials.json")
                assert credentials["username"] == "admin@example.test"
                assert credentials["status"] == "bootstrapped"
                assert "postgres" in config_file.read_text().lower()
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=30)
                process = None
                # Simulate a new container losing every local Stalwart config file.
                config_file.unlink()
                env.pop("STALWART_RECOVERY_ADMIN")
                state_after = setup.read_json(directory / "deployment.json")
                env.update(setup.runtime_environment(state_after, True))
                env["STALWART_R2_ENDPOINT"] = "http://127.0.0.1:9000"
                process = subprocess.Popen([str(binary), "--config", str(config_file)], env=env, stdout=log, stderr=log)
                setup.verify(directory)
                assert not config_file.exists(), "Environment startup must not recreate a local config file"
                credentials = setup.read_json(directory / "credentials.json")
                assert credentials["status"] == "ready"
                assert credentials["api_url"] == "https://mail.example.test/jmap/"
                assert "STALWART_RECOVERY_ADMIN" not in (directory / ".env").read_text()
                client = setup.Client("https://127.0.0.1:443", credentials["username"], credentials["password"])
                arguments = setup.account_arguments(client.session())
                routes = client.call("x:MtaRoute/get", arguments)["list"]
                names = {route["name"] for route in routes}
                assert {"mx", "local"} <= names
                assert ("installation-relay" in names) == with_relay
                strategies = client.call("x:MtaOutboundStrategy/get", dict(arguments, ids=[singleton_id]))["list"]
                assert len(strategies) == 1, strategies
                strategy = strategies[0]
                if with_relay:
                    relay = next(route for route in routes if route["name"] == "installation-relay")
                    assert relay["address"] == "127.0.0.1" and relay["port"] == 2526
                    assert not relay["allowInvalidCerts"] and not relay["implicitTls"]
                    assert strategy["route"]["else"] == "'installation-relay'"
                    assert strategy["route"]["match"]["0"]["then"] == "'local'"
                    assert strategy["route"]["match"]["0"]["if"] == "is_local_domain(rcpt_domain)"
                    tls = client.call("x:MtaTlsStrategy/get", arguments)["list"]
                    tls = next(value for value in tls if value["name"] == "installation-relay-tls")
                    assert tls["startTls"] == "require"
                assert not any("registry.build-warning" in line and "MtaOutboundStrategy" in line
                               for line in log_path.read_text().splitlines()), "Outbound strategy failed to compile"
                tracer = client.call("x:Tracer/get", arguments)["list"][0]
                assert tracer["@type"] == "Stdout"
                # Reruns preserve credentials and the remote startup descriptor.
                setup.prepare(config_path, directory, source)
                setup.bootstrap(directory)
                assert setup.read_json(directory / "credentials.json")["password"] == credentials["password"]
                assert "STALWART_DATA_STORE" in (directory / ".env").read_text()
            print("Remote PostgreSQL/S3 startup without local config and " +
                  ("SMTP relay configuration" if with_relay else "default MX routing") + " passed.")
        except Exception:
            log_text = log_path.read_text()
            secrets = [state["setup_password"], config.get("STALWART_SMTP_RELAY_PASSWORD", "")]
            if (directory / "credentials.json").exists():
                secrets.append(setup.read_json(directory / "credentials.json").get("password", ""))
            for secret in secrets:
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    if os.getuid() != 0:
        parser.error("Run on an isolated CI host as root.")
    for with_relay in [False, True]:
        exercise(args.binary.resolve(), with_relay)


if __name__ == "__main__":
    main()
