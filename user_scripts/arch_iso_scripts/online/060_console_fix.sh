#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# MODULE: PRE-INSTALL CONFIG (VCONSOLE)
# -----------------------------------------------------------------------------
set -euo pipefail

echo ">> configuring /mnt/etc/vconsole.conf..."

# Ensure directory exists (it should from disk mount, but safety first)
mkdir -p /mnt/etc

# Write config
cat << 'EOF' > /mnt/etc/vconsole.conf
KEYMAP=us
FONT=ter-v22b
EOF

# Verify
if grep -q "KEYMAP=us" /mnt/etc/vconsole.conf && grep -q "FONT=ter-v22b" /mnt/etc/vconsole.conf; then
    echo "   [OK] vconsole.conf created with KEYMAP=us and FONT=ter-v22b."
else
    echo "   [ERR] Failed to create vconsole.conf"
    exit 1
fi
