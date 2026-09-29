#!/bin/sh
# Install the FlipOff auto-update timer. Run as root:
#   sudo deploy/auto-update/install.sh /home/you/flipoff
set -eu

dir=$(cd "${1:?usage: install.sh /path/to/flipoff-checkout}" && pwd)
here=$(cd "$(dirname "$0")" && pwd)

install -m 0755 "$here/flipoff-update.sh" /usr/local/bin/flipoff-update
install -m 0644 "$here/flipoff-update.service" /etc/systemd/system/
install -m 0644 "$here/flipoff-update.timer" /etc/systemd/system/
printf 'FLIPOFF_DIR=%s\nFLIPOFF_BRANCH=main\n' "$dir" > /etc/default/flipoff-update

systemctl daemon-reload
systemctl enable --now docker.service flipoff-update.timer
systemctl list-timers flipoff-update.timer --no-pager
