#!/usr/bin/env python3
"""Prepare a deployment and finish Stalwart's real JMAP Bootstrap API.

Only Python's standard library is required. Secrets never go in process arguments.
"""

import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CAPABILITIES = ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]
ENV_KEYS = {
    "STALWART_DOMAIN", "STALWART_HOSTNAME", "STALWART_REQUEST_TLS_CERTIFICATE",
    "STALWART_POSTGRES_HOST", "STALWART_POSTGRES_PORT", "STALWART_POSTGRES_DATABASE",
    "STALWART_POSTGRES_USER", "STALWART_POSTGRES_PASSWORD", "STALWART_R2_ACCOUNT_ID",
    "STALWART_R2_ENDPOINT", "STALWART_R2_BUCKET", "STALWART_R2_ACCESS_KEY_ID",
    "STALWART_R2_SECRET_ACCESS_KEY",
}


class SetupError(Exception):
    pass


def private_write(path, value):
    path = Path(path)
    if not isinstance(value, str):
        value = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    # O_NOFOLLOW protects an existing temporary symlink on a rerun.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def valid_domain(value, name):
    value = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
    if len(value) > 253 or "." not in value or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
        for part in value.split(".")
    ):
        raise SetupError(f"{name} must be a fully qualified domain name.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise SetupError(f"{name} must be a domain, not an IP address.")


def normalize(config):
    if not isinstance(config, dict) or set(config) - ENV_KEYS:
        raise SetupError("Configuration must be a JSON object containing only documented STALWART_* keys.")
    for key, value in config.items():
        if key == "STALWART_REQUEST_TLS_CERTIFICATE":
            if not isinstance(value, bool):
                raise SetupError(f"{key} must be true or false.")
        elif not isinstance(value, str) or any(ord(ch) < 32 for ch in value):
            raise SetupError(f"{key} must be a string without control characters.")
    env = {key: value for key, value in config.items() if isinstance(value, str)}
    env["STALWART_DOMAIN"] = valid_domain(env.get("STALWART_DOMAIN", ""), "STALWART_DOMAIN")
    env["STALWART_HOSTNAME"] = valid_domain(
        env.get("STALWART_HOSTNAME") or "mail." + env["STALWART_DOMAIN"], "STALWART_HOSTNAME"
    )
    env["STALWART_PUBLIC_URL"] = "https://" + env["STALWART_HOSTNAME"]
    for key in ["STALWART_R2_BUCKET", "STALWART_R2_ACCESS_KEY_ID", "STALWART_R2_SECRET_ACCESS_KEY"]:
        if not env.get(key, "").strip() or env[key].startswith("YOUR_"):
            raise SetupError(f"Fill in {key} in the local JSON file.")
    endpoint = env.get("STALWART_R2_ENDPOINT", "").strip()
    account = env.get("STALWART_R2_ACCOUNT_ID", "").strip()
    if endpoint:
        url = urllib.parse.urlsplit(endpoint)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise SetupError("STALWART_R2_ENDPOINT must be an HTTPS S3 endpoint without credentials or query parameters.")
    elif not re.fullmatch(r"[a-fA-F0-9]{32}", account):
        raise SetupError("Fill in the 32-character Cloudflare account ID or STALWART_R2_ENDPOINT.")
    env.setdefault("STALWART_POSTGRES_HOST", "postgres")
    env.setdefault("STALWART_POSTGRES_PORT", "5432")
    env.setdefault("STALWART_POSTGRES_DATABASE", "stalwart")
    env.setdefault("STALWART_POSTGRES_USER", "stalwart")
    if not env["STALWART_POSTGRES_PORT"].isdigit() or not 1 <= int(env["STALWART_POSTGRES_PORT"]) <= 65535:
        raise SetupError("STALWART_POSTGRES_PORT must be between 1 and 65535.")
    for key in ["STALWART_POSTGRES_HOST", "STALWART_POSTGRES_DATABASE", "STALWART_POSTGRES_USER"]:
        if not env[key].strip():
            raise SetupError(f"{key} cannot be empty.")
    if env["STALWART_POSTGRES_HOST"] == "postgres":
        if env["STALWART_POSTGRES_PORT"] != "5432":
            raise SetupError("Managed PostgreSQL uses port 5432 inside Docker.")
        env["STALWART_POSTGRES_PASSWORD"] = env.get("STALWART_POSTGRES_PASSWORD") or secrets.token_hex(24)
    elif not env.get("STALWART_POSTGRES_PASSWORD"):
        raise SetupError("External PostgreSQL requires STALWART_POSTGRES_PASSWORD and an existing empty database.")
    return env


def write_env(directory, state):
    env = dict(state["environment"])
    if not (directory / "credentials.json").exists():
        env["STALWART_RECOVERY_ADMIN"] = "setup:" + state["setup_password"]
    # Compose dotenv single quotes keep literal $, spaces, # and backslashes.
    private_write(directory / ".env", "".join(
        f"{key}='" + value.replace("'", "\\'") + "'\n" for key, value in sorted(env.items())
    ))


def compose_config(env, source, project_name="stalwart-auto"):
    server = {
        "build": {"context": str(source), "dockerfile": "Dockerfile"},
        "env_file": ".env",
        "ports": ["127.0.0.1:8080:8080", "443:443", "25:25", "587:587", "465:465", "143:143", "993:993", "4190:4190"],
        "volumes": ["server-config:/etc/stalwart", "server-data:/var/lib/stalwart"],
        "restart": "unless-stopped",
    }
    volumes = {"server-config": {}, "server-data": {}}
    services = {"server": server}
    if env["STALWART_POSTGRES_HOST"] == "postgres":
        server["depends_on"] = {"postgres": {"condition": "service_healthy"}}
        services["postgres"] = {
            "image": "postgres:17-alpine",
            "environment": {
                "POSTGRES_DB": "${STALWART_POSTGRES_DATABASE}",
                "POSTGRES_USER": "${STALWART_POSTGRES_USER}",
                "POSTGRES_PASSWORD": "${STALWART_POSTGRES_PASSWORD}",
            },
            "volumes": ["postgres-data:/var/lib/postgresql/data"],
            "healthcheck": {
                "test": ["CMD-SHELL", 'pg_isready -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'],
                "interval": "5s", "timeout": "5s", "retries": 20,
            },
            "restart": "unless-stopped",
        }
        volumes["postgres-data"] = {}
    return {"name": project_name, "services": services, "volumes": volumes}


def prepare(config_path, directory, source):
    config = read_json(config_path)
    env = normalize(config)  # Validate before writing anything.
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    state_path = directory / "deployment.json"
    if state_path.exists():
        state = read_json(state_path)
        if state["config_hash"] != fingerprint or state["source"] != str(source):
            raise SetupError("This directory already has different settings. Reuse the original config; edit an existing deployment deliberately rather than resetting it.")
        if "setup_password" not in state and not (directory / "credentials.json").exists():
            raise SetupError("Saved administrator credentials are missing. Restore credentials.json; an existing database cannot be bootstrapped again.")
    else:
        if (directory / "credentials.json").exists() or (directory / ".env").exists():
            raise SetupError("Existing files have no deployment state; refusing to replace them.")
        state = {"config_hash": fingerprint, "source": str(source), "environment": env,
                 "request_tls_certificate": config.get("STALWART_REQUEST_TLS_CERTIFICATE", True),
                 "setup_password": secrets.token_hex(24)}
        private_write(state_path, state)
    write_env(directory, state)
    project_name = "stalwart-" + hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:12]
    private_write(directory / "compose.json", compose_config(state["environment"], source, project_name))
    print("Configuration ready. PostgreSQL and R2 credentials are stored locally with mode 0600.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SetupError("Unexpected redirect from the local setup API.")


class Client:
    def __init__(self, url, username, password):
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname not in {"127.0.0.1", "::1", "localhost"} or parsed.scheme not in {"http", "https"}:
            raise SetupError("Setup requests must use the local loopback interface.")
        self.url = url.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        # Local HTTPS uses the initial self-signed certificate. Public clients must
        # validate TLS normally after DNS/ACME has provisioned the real certificate.
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))

    def request(self, path, body=None):
        request = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                        headers={"Authorization": self.auth, "Content-Type": "application/json"})
        with self.opener.open(request, timeout=180 if body else 10) as response:
            return json.load(response)

    def session(self, timeout=180):
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            try:
                session = self.request("/jmap/session")
                if session.get("accounts"):
                    return session
                last_error = "No authenticated account returned."
            except (OSError, ValueError) as error:
                last_error = str(error)
            time.sleep(2)
        raise SetupError("Server did not become ready: " + last_error)

    def call(self, method, arguments):
        response = self.request("/jmap/", {"using": CAPABILITIES, "methodCalls": [[method, arguments, "setup"]]})
        calls = response.get("methodResponses", [])
        if len(calls) != 1 or calls[0][0] != method:
            raise SetupError("Unexpected JMAP response: " + json.dumps(calls)[:2000])
        return calls[0][1]


def account_arguments(session):
    account_id = session.get("primaryAccounts", {}).get("urn:stalwart:jmap") or next(iter(session.get("accounts", {})), None)
    return {"accountId": account_id} if account_id else {}


def bootstrap(directory, url="http://127.0.0.1:8080"):
    state = read_json(directory / "deployment.json")
    credentials_path = directory / "credentials.json"
    if credentials_path.exists():
        write_env(directory, state)
        print("Saved administrator credentials found; bootstrap will not be repeated.")
        return
    client = Client(url, "setup", state["setup_password"])
    arguments = account_arguments(client.session())
    response = client.call("x:Bootstrap/get", arguments)
    items = response.get("list", [])
    if len(items) != 1 or not items[0].get("id"):
        raise SetupError("Server is already configured or not in bootstrap mode; refusing to reset it.")
    object_id = items[0]["id"]
    env = state["environment"]
    patch = {"defaultDomain": env["STALWART_DOMAIN"], "serverHostname": env["STALWART_HOSTNAME"],
             "requestTlsCertificate": state["request_tls_certificate"], "generateDkimKeys": True}
    # The fork supplies PostgreSQL/R2 defaults and environment secret references.
    response = client.call("x:Bootstrap/set", dict(arguments, update={object_id: patch}))
    admin = response.get("updated", {}).get(object_id)
    if not isinstance(admin, dict) or not admin.get("username") or not admin.get("secret"):
        raise SetupError("Bootstrap failed; check PostgreSQL/R2 settings. " + json.dumps(response.get("notUpdated", {}))[:2000])
    base = env["STALWART_PUBLIC_URL"]
    private_write(credentials_path, {
        "status": "bootstrapped", "domain": env["STALWART_DOMAIN"], "hostname": env["STALWART_HOSTNAME"],
        "username": admin["username"], "password": admin["secret"], "admin_url": base + "/admin",
        "session_url": base + "/jmap/session", "api_url": base + "/jmap/",
        "oauth_token_url": base + "/auth/token", "authentication": "HTTP Basic over HTTPS",
    })
    write_env(directory, state)  # Remove temporary login only after saving admin.
    state.pop("setup_password", None)
    private_write(directory / "deployment.json", state)
    print("Bootstrap complete. Administrator credentials saved; temporary setup login removed.")


def verify(directory, url="https://127.0.0.1:443"):
    credentials = read_json(directory / "credentials.json")
    client = Client(url, credentials["username"], credentials["password"])
    session = client.session()
    result = client.call("x:Bootstrap/get", account_arguments(session))
    if result.get("list"):
        raise SetupError("Server still reports bootstrap mode after restart.")
    credentials["status"] = "ready"
    credentials["account_ids"] = list(session["accounts"])
    # Store the endpoint URLs actually advertised by the configured server.
    for key, output in [("apiUrl", "api_url"), ("uploadUrl", "upload_url"), ("downloadUrl", "download_url")]:
        if session.get(key):
            credentials[output] = session[key]
    private_write(directory / "credentials.json", credentials)
    print("Administrator login and JMAP API verified after restart.")


def show(directory):
    credentials = read_json(directory / "credentials.json")
    if credentials["status"] != "ready":
        raise SetupError("Bootstrap credentials were saved, but verification is incomplete. Rerun the installation to restart and verify.")
    print("\nInstallation complete")
    for label, key in [("Domain", "domain"), ("Administration", "admin_url"), ("Administrator", "username"),
                       ("Password", "password"), ("JMAP session", "session_url"), ("JMAP API", "api_url"),
                       ("OAuth token endpoint", "oauth_token_url"), ("API authentication", "authentication")]:
        print(f"{label}: {credentials[key]}")
    print("Credentials file: " + str(directory / "credentials.json"))
    print("HTTPS certificate: automatic ACME issuance requires the domain's DNS to point to this server.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "bootstrap", "verify", "show"])
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--url", help="Loopback URL override for integration testing")
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            if not args.config or not args.source:
                parser.error("prepare requires --config and --source")
            prepare(args.config, args.directory, args.source.resolve())
        elif args.operation == "bootstrap":
            bootstrap(args.directory, args.url or "http://127.0.0.1:8080")
        elif args.operation == "verify":
            verify(args.directory, args.url or "https://127.0.0.1:443")
        else:
            show(args.directory)
    except (SetupError, OSError, ValueError) as error:
        print("Setup stopped: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
