#!/bin/sh
# Unattended PostgreSQL/R2 installation for this fork, on Debian/Ubuntu.
set -eu

fail() { printf '%s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sh install-auto.sh --config /root/stalwart.json [--prefix /opt/stalwart]
       sh install-auto.sh --config FILE --source /path/to/server

Installs missing dependencies, builds this fork, starts PostgreSQL and Stalwart,
sets the domain, completes bootstrap, and prints administrator/API details.
The JSON config and generated credentials stay on this machine.
Options: --ref REF (default: main), --source PATH, --prefix PATH, --help
EOF
}

main() {
    config= prefix=/opt/stalwart source= ref=main
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --config|--prefix|--source|--ref)
                [ "$#" -ge 2 ] || fail "$1 requires a value"
                case "$1" in
                    --config) config=$2 ;; --prefix) prefix=$2 ;;
                    --source) source=$2 ;; --ref) ref=$2 ;;
                esac
                shift ;;
            -h|--help) usage; return 0 ;;
            *) fail "Unknown option: $1" ;;
        esac
        shift
    done
    [ -n "$config" ] && [ -f "$config" ] || fail 'Provide --config with a JSON configuration file.'
    [ "$(id -u)" -eq 0 ] || fail 'Run this installer with sudo or as root.'
    [ "$(uname -s)" = Linux ] || fail 'Automatic installation requires Debian or Ubuntu Linux.'
    . /etc/os-release
    case "$ID" in ubuntu|debian) ;; *) fail 'Automatic package installation supports Debian and Ubuntu.' ;; esac
    case "$(uname -m)" in x86_64|aarch64|arm64) ;; *) fail 'Docker build supports amd64 and arm64.' ;; esac
    case "$prefix" in /*) ;; *) fail '--prefix must be an absolute path.' ;; esac
    umask 077

    # Install only missing host dependencies. PostgreSQL/Rust run inside Docker.
    missing=
    for pair in 'python3:python3' 'git:git' 'curl:curl' 'flock:util-linux'; do
        cmd=${pair%%:*}; package=${pair#*:}
        command -v "$cmd" >/dev/null 2>&1 || missing="$missing $package"
    done
    if [ -n "$missing" ]; then
        apt-get update
        # Package names above are fixed constants, not user input.
        DEBIAN_FRONTEND=noninteractive apt-get install -y $missing ca-certificates
    fi
    mkdir -p "$prefix"
    chmod 0700 "$prefix"
    exec 9>"$prefix/install.lock"
    flock -n 9 || fail 'Another installation is running in this directory.'

    if [ -z "$source" ]; then
        source=$prefix/source
        if [ ! -d "$source" ]; then
            git clone --depth 1 --branch "$ref" https://github.com/lehuunghi/server.git "$source"
        fi
    fi
    source=$(cd "$source" && pwd)
    helper=$source/resources/scripts/auto_setup.py
    [ -f "$helper" ] || fail 'Source checkout is missing resources/scripts/auto_setup.py.'
    python3 "$helper" prepare --config "$config" --directory "$prefix" --source "$source"

    install_engine=false
    command -v docker >/dev/null 2>&1 || install_engine=true
    if [ "$install_engine" = true ] || ! docker compose version >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
        # Official signed Apt repository; do not replace existing Docker installs.
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
        apt-get update
        if [ "$install_engine" = true ]; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            systemctl enable --now docker
        else
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker-buildx-plugin docker-compose-plugin
        fi
    fi
    docker compose version >/dev/null 2>&1 || fail 'Existing Docker needs the Docker Compose v2 plugin.'
    docker info >/dev/null 2>&1 || fail 'Docker daemon is not running.'
    compose() { docker compose --env-file "$prefix/.env" -f "$prefix/compose.json" "$@"; }
    compose config --quiet
    printf '%s\n' 'Building the server with PostgreSQL/R2 support (first build can take several minutes)...'
    compose up --build --detach
    python3 "$helper" bootstrap --directory "$prefix"
    # bootstrap saves credentials before removing the temporary recovery login.
    compose up --detach --force-recreate --no-deps server
    python3 "$helper" verify --directory "$prefix"
    python3 "$helper" show --directory "$prefix"
}

main "$@"
