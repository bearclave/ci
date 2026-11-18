#!/usr/bin/env bash
# shellcheck disable=SC2317,SC2329  # install_* are called indirectly by name
# Install pinned tools into a cacheable directory and put them on PATH. Every
# download is checked against the sha256 in the calling repository's
# tool-versions.env.
#
# Usage: install-tools.sh terragrunt trivy tflint
#
# TOOL_VERSIONS  the versions file (default .github/tool-versions.env)
# TOOLS_DIR      where the binaries go (default ~/.cache/iac-tools/bin)
set -euo pipefail

# shellcheck disable=SC1090
source "${TOOL_VERSIONS:-.github/tool-versions.env}"

BIN=${TOOLS_DIR:-$HOME/.cache/iac-tools/bin}
mkdir -p "$BIN"

fetch() {
  local url=$1 want=$2 out=$3
  curl -fsSL --retry 3 "$url" -o "$out"
  echo "$want  $out" | sha256sum -c - >/dev/null
}

install_terragrunt() {
  [ -x "$BIN/terragrunt" ] && return 0
  fetch "https://github.com/gruntwork-io/terragrunt/releases/download/v${TERRAGRUNT_VERSION}/terragrunt_linux_amd64" \
        "$TERRAGRUNT_SHA256" "$BIN/terragrunt"
}

install_trivy() {
  [ -x "$BIN/trivy" ] && return 0
  local t; t=$(mktemp -d)
  fetch "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
        "$TRIVY_SHA256" "$t/a.tgz"
  tar -xzf "$t/a.tgz" -C "$t" trivy && mv "$t/trivy" "$BIN/"
}

install_tflint() {
  [ -x "$BIN/tflint" ] && return 0
  local t; t=$(mktemp -d)
  fetch "https://github.com/terraform-linters/tflint/releases/download/v${TFLINT_VERSION}/tflint_linux_amd64.zip" \
        "$TFLINT_SHA256" "$t/a.zip"
  unzip -q "$t/a.zip" -d "$t" tflint && mv "$t/tflint" "$BIN/"
}

for tool in "$@"; do
  "install_$tool"
  chmod +x "$BIN/$tool"
  echo "$tool $("$BIN/$tool" --version 2>&1 | head -1)"
done

[ -n "${GITHUB_PATH:-}" ] && echo "$BIN" >>"$GITHUB_PATH"
exit 0
