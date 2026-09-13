#!/usr/bin/env bash
# Install the local tools used to develop SystemLens on macOS and Ubuntu.
#
# The CodeQL bundle is installed in the user's data directory rather than in
# the repository.  This keeps the checkout portable and lets `codeql` be
# shared by multiple projects.
set -euo pipefail

readonly CODEQL_JAVA_PACK_VERSION="9.3.0"
readonly CODEQL_RELEASE_URL="https://github.com/github/codeql-action/releases/latest/download"
readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

dry_run=false
install_codeql=true
install_uv=true
install_project_dependencies=true

usage() {
  cat <<'EOF'
Usage: scripts/install-dependencies.sh [OPTIONS]

Install SystemLens development dependencies on macOS and Ubuntu:
  - uv, the Python package manager
  - a Java 17 JDK, needed by the CodeQL Java extractor
  - the CodeQL CLI bundle and codeql/java-all@9.3.0

Options:
  --dry-run       Print commands without executing them.
  --skip-codeql   Do not install Java or CodeQL.
  --skip-uv       Do not install uv.
  --skip-project  Do not create the project development environment with uv.
  -h, --help      Show this help.

The script may ask for sudo on Ubuntu to install operating-system packages.
It downloads CodeQL from GitHub and uv from Astral's official installer.
EOF
}

run() {
  if "$dry_run"; then
    printf '+ '
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

while (($#)); do
  case "$1" in
    --dry-run) dry_run=true ;;
    --skip-codeql) install_codeql=false ;;
    --skip-uv) install_uv=false ;;
    --skip-project) install_project_dependencies=false ;;
    -h|--help) usage; exit 0 ;;
    *)
      printf 'Unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

cd "$REPOSITORY_ROOT"

case "$(uname -s)" in
  Darwin) platform="macos" ;;
  Linux)
    if [[ -r /etc/os-release ]] && grep -qiE '^ID(_LIKE)?=.*(ubuntu|debian)' /etc/os-release; then
      platform="ubuntu"
    else
      printf 'Unsupported Linux distribution. This script supports Ubuntu only.\n' >&2
      exit 1
    fi
    ;;
  *)
    printf 'Unsupported operating system: %s\n' "$(uname -s)" >&2
    exit 1
    ;;
esac

if "$install_uv" && ! command -v uv >/dev/null 2>&1; then
  if [[ "$platform" == "macos" ]] && command -v brew >/dev/null 2>&1; then
    run brew install uv
  else
    # shellcheck disable=SC2016 # The installer must receive its own script on stdin.
    run bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  fi
fi

uv_command="$(command -v uv || true)"
if [[ -z "$uv_command" && -x "$HOME/.local/bin/uv" ]]; then
  uv_command="$HOME/.local/bin/uv"
fi
if "$install_project_dependencies"; then
  if [[ -z "$uv_command" && "$dry_run" == true ]]; then
    uv_command="$HOME/.local/bin/uv"
  fi
  if [[ -z "$uv_command" ]]; then
    printf 'uv is required to create the development environment. Install it or use --skip-project.\n' >&2
    exit 1
  fi
  run "$uv_command" sync --group dev
fi

if "$install_codeql"; then
  if [[ "$platform" == "ubuntu" ]]; then
    if ! command -v apt-get >/dev/null 2>&1; then
      printf 'apt-get is required to install Ubuntu prerequisites.\n' >&2
      exit 1
    fi
    sudo_command=()
    if ((EUID != 0)); then
      sudo_command=(sudo)
    fi
    run "${sudo_command[@]}" apt-get update
    run "${sudo_command[@]}" apt-get install --yes ca-certificates curl tar gzip openjdk-17-jdk
  elif ! /usr/libexec/java_home -v 17 >/dev/null 2>&1; then
    if ! command -v brew >/dev/null 2>&1; then
      printf 'Homebrew is required on macOS to install a Java 17 JDK.\n' >&2
      exit 1
    fi
    run brew install --cask temurin@17
  fi

  # GitHub currently distributes the macOS bundle as x86_64.  Rosetta is
  # therefore required on Apple Silicon; `softwareupdate` is a no-op when it
  # is already present.
  if [[ "$platform" == "macos" && "$(uname -m)" == "arm64" ]] && ! pgrep -x oahd >/dev/null 2>&1; then
    run /usr/sbin/softwareupdate --install-rosetta --agree-to-license
  fi

  if ! command -v curl >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then
    printf 'curl and tar are required to install the CodeQL bundle.\n' >&2
    exit 1
  fi

  data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  bin_dir="$HOME/.local/bin"
  codeql_root="$data_home/systemlens/codeql"
  archive="${TMPDIR:-/tmp}/systemlens-codeql-bundle.tar.gz"
  case "$platform" in
    macos) archive_name="codeql-bundle-osx64.tar.gz" ;;
    ubuntu) archive_name="codeql-bundle-linux64.tar.gz" ;;
  esac

  if ! command -v codeql >/dev/null 2>&1 && [[ ! -x "$codeql_root/codeql/codeql" ]]; then
    run mkdir -p "$codeql_root" "$bin_dir"
    run curl --fail --location --retry 3 --output "$archive" "$CODEQL_RELEASE_URL/$archive_name"
    run tar -xzf "$archive" --strip-components=1 -C "$codeql_root"
    run rm -f "$archive"
    run ln -sfn "$codeql_root/codeql/codeql" "$bin_dir/codeql"
  fi

  codeql_command="$(command -v codeql || true)"
  if [[ -z "$codeql_command" && -x "$codeql_root/codeql/codeql" ]]; then
    codeql_command="$codeql_root/codeql/codeql"
  fi
  if "$dry_run" && [[ -z "$codeql_command" ]]; then
    codeql_command="$codeql_root/codeql/codeql"
  fi
  if [[ -z "$codeql_command" ]]; then
    printf 'CodeQL could not be found after installation. Add %s to PATH and run again.\n' "$bin_dir" >&2
    exit 1
  fi

  run "$codeql_command" pack download "codeql/java-all@$CODEQL_JAVA_PACK_VERSION"
  run "$codeql_command" resolve languages
fi

if "$install_codeql"; then
  printf 'If needed, add %s to PATH before running `systemlens index`.\n' "$HOME/.local/bin"
fi
