# Running FlipOff as an appliance

What it takes to leave FlipOff on a small Linux box behind a TV and never touch
it again: it comes back after a reboot or crash, and it picks up new commits on
its own. Tested on Ubuntu 24.04 with Docker's `docker.io` and
`docker-compose-v2` packages.

## Always up

* **Backend.** `docker.service` enabled at boot, and the compose service already
  has `restart: unless-stopped`. Start it with `docker compose up -d` so the
  container belongs to compose; a hand-run `docker run` container blocks
  `docker compose` from taking over (same `container_name`).
* **Display.** [`kiosk/`](./kiosk) holds a working tty1 kiosk:
  * `getty-autologin.conf` goes in
    `/etc/systemd/system/getty@tty1.service.d/override.conf` (change `kiosk`
    to the account that runs the display). getty restarts on exit, so a crashed
    X session logs straight back in.
  * `bash_profile` becomes that account's `~/.bash_profile` and runs `startx`
    on tty1.
  * `xinitrc` becomes `~/.xinitrc`. It waits for `/healthz` with no timeout,
    then keeps Chromium in a relaunch loop.

  Packages: `xinit openbox unclutter curl`, plus Chromium
  (`snap install chromium`).

## Always up to date

[`auto-update/`](./auto-update) installs a systemd timer that fast-forwards the
checkout from `origin/main` and runs `docker compose up -d --build` only when
the commit differs from the one last deployed.

```bash
sudo deploy/auto-update/install.sh "$PWD"
```

* Runs 2 minutes after boot and nightly at 04:00, each plus up to 15 minutes
  of random delay. A night missed while powered off runs at next boot.
* Git runs as the checkout's owner, so their SSH keys and config apply.
* It never resets, stashes, or discards anything. A dirty tree, a branch other
  than `main`, or a diverged history fails the unit and says why.
* A failed build is retried on the next run, since it compares against the
  last *deployed* commit (`/var/lib/flipoff-update/deployed-commit`), not the
  last fetched one.

```bash
systemctl list-timers flipoff-update.timer   # next run
journalctl -u flipoff-update                  # what each run did
sudo systemctl start flipoff-update           # update now
```

OS packages are a separate concern. On Ubuntu, `unattended-upgrades` applies
security updates daily by default; check with
`systemctl is-enabled unattended-upgrades`.

## HTTPS on a tailnet

With Tailscale, `tailscale serve` gives the board a real certificate without
exposing it to the internet:

```bash
sudo tailscale serve --bg 8080
```

The board is then at `https://<machine>.<tailnet>.ts.net/`, reachable only from
the tailnet. The setting persists across reboots. Do not use `tailscale funnel`
for this: that publishes the board to the internet. Set `PUBLIC_BASE_URL` in
`.env` to the HTTPS address so the API docs advertise it.

For a box that should stay on the tailnet indefinitely, disable key expiry
for the machine in the Tailscale admin console (Machines, then the machine's
menu, then Disable key expiry). The CLI cannot change that setting.
