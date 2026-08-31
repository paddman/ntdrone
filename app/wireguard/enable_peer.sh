#!/usr/bin/env sh
set -eu

INTERFACE="${1:?wireguard interface is required}"
PUBLIC_KEY="${2:?peer public key is required}"
ALLOWED_IP="${3:?peer allowed IP is required}"

# This script must run with CAP_NET_ADMIN or via a narrowly scoped sudo rule.
wg set "$INTERFACE" peer "$PUBLIC_KEY" allowed-ips "$ALLOWED_IP"
