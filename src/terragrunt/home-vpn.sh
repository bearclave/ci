#!/usr/bin/env bash
# The runner joins the home network as WireGuard peer ci and reaches the REST
# API of every managed device; run from the root of iac-home
# (docs/terragrunt.md#home-network-terragrunt-homeyml).
#
#   home-vpn.sh up|down
#
# Needs ARM_CLIENT_ID, ARM_TENANT_ID, id-token: write and wireguard-tools.
set -euo pipefail

net=_common/network.hcl
# The unit whose output public_key is the WireGuard server.
server_unit=${HOME_VPN_SERVER_UNIT:-infrastructure/router/wireguard}
conf=/etc/wireguard/wg-home.conf

# block_value <file> <block> <key>: a quoted or bare value inside "<block> = {".
block_value() {
  sed -n "/^ *$2 = {/,/^ *}/s/^ *$3 *= *\"\\{0,1\\}\\([^\"]*\\)\"\\{0,1\\}$/\\1/p" "$1" | head -1
}

# inline_value <file> <name> <key>: a quoted key of a one-line object "<name> = { ... }".
inline_value() {
  sed -n "s/^ *$2 *= *{.*$3 *= *\"\\([^\"]*\\)\".*/\\1/p" "$1" | head -1
}

# first_name <file> <name>: the first of names = [...] of a one-line object.
first_name() {
  sed -n "s/^ *$2 *= *{.*names *= *\\[\"\\([^\"]*\\)\".*/\\1/p" "$1" | head -1
}

# The device folders under infrastructure/ whose device.hcl says managed = true.
managed_devices() {
  local f
  for f in infrastructure/*/device.hcl; do
    [ -f "$f" ] || continue
    if grep -qE '^ *managed *= *true *$' "$f"; then dirname "$f"; fi
  done
}

case "${1:?usage: home-vpn.sh up|down}" in
  up)
    mapfile -t devices < <(managed_devices)
    if [ "${#devices[@]}" -eq 0 ]; then
      echo "::error::no device.hcl under infrastructure/ with managed = true"
      exit 1
    fi

    token=$(curl -sSf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
      "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=api://AzureADTokenExchange" | jq -r .value)
    az login --service-principal --username "$ARM_CLIENT_ID" --tenant "$ARM_TENANT_ID" \
      --federated-token "$token" --allow-no-subscriptions --output none

    address=$(inline_value "$net" ci address)
    endpoint="$(block_value "$net" wireguard endpoint):$(block_value "$net" wireguard listen_port)"
    port=$(sed -n 's/^ *webfig_port *= *\([0-9]*\)$/\1/p' "$net")

    # Per device: its address, its name (not in public DNS) and its CA, the
    # heredoc ca_bundle of device.hcl.
    allowed=() urls=() cas=()
    for d in "${devices[@]}"; do
      host=$(sed -n 's/^ *host *= *"\([^"]*\)".*/\1/p' "$d/device.hcl" | head -1)
      target=$(inline_value "$net" "$host" address)
      name=$(first_name "$net" "$host")
      if [ -z "$target" ] || [ -z "$name" ]; then
        echo "::error::$d: host $host has no address or name in $net"
        exit 1
      fi
      ca="$RUNNER_TEMP/ca-$(basename "$d").crt"
      sed -n '/ca_bundle *= *<<-*EOT/,/^ *EOT/{/<<-*EOT/d;/^ *EOT/d;s/^ *//;p;}' "$d/device.hcl" >"$ca"
      echo "$target $name" | sudo tee -a /etc/hosts >/dev/null
      allowed+=("$target/32") urls+=("https://$name:$port/") cas+=("$ca")
    done
    server=$(cd "$server_unit" && terragrunt output -raw public_key)

    vault_subscription=$(block_value account.hcl key_vault subscription_id)
    vault_name=$(block_value account.hcl key_vault name)
    secret() {
      az keyvault secret show --subscription "$vault_subscription" --vault-name "$vault_name" \
        --name "$1" --query value --output tsv | tr -d '\n'
    }
    # Read first: a failing substitution inside the heredoc would not stop the script.
    private_key=$(secret wg-key-ci)
    psk=$(secret wg-psk-ci)
    if [ -z "$private_key" ] || [ -z "$psk" ]; then
      echo "::error::WireGuard secrets missing"
      exit 1
    fi

    sudo install -m 600 /dev/null "$conf"
    sudo tee "$conf" >/dev/null <<EOF
[Interface]
PrivateKey = $private_key
Address = $address/32

[Peer]
PublicKey = $server
PresharedKey = $psk
Endpoint = $endpoint
AllowedIPs = $(IFS=,; echo "${allowed[*]}")
PersistentKeepalive = 25
EOF
    sudo wg-quick up wg-home
    # Every REST API has to answer before anything is planned.
    for i in "${!urls[@]}"; do
      ok=false
      for _ in 1 2 3 4 5 6; do
        if curl -sf -o /dev/null --max-time 5 --cacert "${cas[$i]}" "${urls[$i]}"; then
          ok=true
          break
        fi
        sleep 5
      done
      if [ "$ok" != true ]; then
        echo "::error::${urls[$i]} not reachable through WireGuard"
        exit 1
      fi
      echo "${urls[$i]} reachable through WireGuard"
    done
    ;;

  down)
    sudo wg-quick down wg-home 2>/dev/null || true
    sudo rm -f "$conf"
    ;;

  *)
    echo "usage: home-vpn.sh up|down" >&2
    exit 1
    ;;
esac
