#!/usr/bin/env sh
set -eu

INTERFACE="${1:?wireguard interface is required}"
PUBLIC_KEY="${2:?peer public key is required}"

# Removing the peer makes VPN access default-deny outside the approved slot.
wg set "$INTERFACE" peer "$PUBLIC_KEY" remove
