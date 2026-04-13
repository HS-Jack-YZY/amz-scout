#!/usr/bin/env bash
# amz-scout Lightsail host bootstrap — Phase 6, idempotent.
#
# What this does (safe to rerun):
#   1. Installs Docker Engine + Compose plugin (skipped if already present).
#   2. Adds the current sudo user to the `docker` group.
#   3. Formats and mounts the attached block-storage disk at /mnt/amz-scout-data
#      ONLY if the disk is blank (guarded by `blkid`).
#   4. Creates the SQLite output directory with permissive perms for the
#      container's root user.
#   5. Adds an /etc/fstab entry with `nofail` so the host still boots if the
#      disk detaches.
#
# What this does NOT do:
#   - Clone the repo (the operator does this manually so they pick the branch).
#   - Copy secrets (operator scp's .env after this script finishes).
#   - Start docker compose (operator runs `docker compose up -d --build`).
#
# Usage (from runbook):
#   sudo BLOCK_DEVICE=/dev/xvdf bash deploy/first-time-setup.sh
#
# Override the block device by passing BLOCK_DEVICE=/dev/nvme1n1 etc.

set -euo pipefail

BLOCK_DEVICE="${BLOCK_DEVICE:-/dev/xvdf}"
MOUNT_POINT="/mnt/amz-scout-data"
DATA_SUBDIR="${MOUNT_POINT}/output"
TARGET_USER="${SUDO_USER:-ubuntu}"

log() {
    printf '\n[setup] %s\n' "$*"
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "ERROR: must run as root (use sudo)" >&2
        exit 1
    fi
}

install_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        log "Docker + Compose plugin already installed — skipping"
        return
    fi

    log "Installing Docker Engine + Compose plugin"
    apt-get update
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        lsb-release

    install -m 0755 -d /etc/apt/keyrings
    if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
            | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
    fi

    local arch codename
    arch="$(dpkg --print-architecture)"
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${codename} stable" \
        > /etc/apt/sources.list.d/docker.list

    apt-get update
    apt-get install -y --no-install-recommends \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
}

add_user_to_docker_group() {
    if id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
        log "User '$TARGET_USER' already in docker group"
        return
    fi
    log "Adding user '$TARGET_USER' to docker group (re-login required to take effect)"
    usermod -aG docker "$TARGET_USER"
}

format_disk_if_blank() {
    if [[ ! -b "$BLOCK_DEVICE" ]]; then
        log "WARNING: $BLOCK_DEVICE is not a block device — skipping format/mount"
        log "         Attach the block-storage disk in Lightsail and rerun."
        return 1
    fi

    if blkid "$BLOCK_DEVICE" >/dev/null 2>&1; then
        log "$BLOCK_DEVICE already has a filesystem — refusing to reformat"
        return 0
    fi

    log "Formatting $BLOCK_DEVICE with ext4 (one-time)"
    mkfs.ext4 -L amz-scout-data "$BLOCK_DEVICE"
}

ensure_mount() {
    mkdir -p "$MOUNT_POINT"

    if mount | grep -q " on ${MOUNT_POINT} "; then
        log "$MOUNT_POINT already mounted"
    else
        log "Mounting $BLOCK_DEVICE at $MOUNT_POINT"
        mount "$BLOCK_DEVICE" "$MOUNT_POINT"
    fi

    if ! grep -q "^${BLOCK_DEVICE} " /etc/fstab; then
        log "Adding /etc/fstab entry (nofail so host still boots if disk detaches)"
        printf '%s %s ext4 defaults,nofail 0 2\n' \
            "$BLOCK_DEVICE" "$MOUNT_POINT" >> /etc/fstab
    fi
}

prepare_output_dir() {
    mkdir -p "$DATA_SUBDIR"
    chmod 755 "$DATA_SUBDIR"
    log "Output directory ready: $DATA_SUBDIR (perm 755)"
}

main() {
    require_root
    install_docker
    add_user_to_docker_group

    if format_disk_if_blank; then
        ensure_mount
        prepare_output_dir
    else
        log "Skipping mount + output prep because $BLOCK_DEVICE is missing."
        log "Rerun this script after attaching the disk in Lightsail console."
        exit 1
    fi

    log "DONE."
    log "Next steps:"
    log "  1. Re-login (or 'newgrp docker') so '$TARGET_USER' picks up docker group"
    log "  2. cd to the cloned repo and 'ln -s $DATA_SUBDIR output' (replace local output/)"
    log "  3. scp .env from your laptop to the repo root, then 'chmod 600 .env'"
    log "  4. Set DOMAIN= and DEPLOY_EMAIL= in .env"
    log "  5. docker compose up -d --build"
}

main "$@"
