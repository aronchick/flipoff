# flipoff-kiosk

A self-hosted split-flap display for any TV, with a backend so you can change
what it shows **without editing source**.

Two display modes:

* **board** — rotating split-flap quotes, with the mechanical flap sound
* **gif** — a full-screen image or animation, with adjustable brightness

Everything is driven over HTTP, so a script, a home-automation rule, or an AI
agent can put something on the screen, dim it at night, and switch it back.

## Credit where it's due

The split-flap rendering engine is the work of
**[magnum6actual/flipoff](https://github.com/magnum6actual/flipoff)** — the
tile animation, the flap audio, the whole look. That project is a client-only
web app: it's beautiful, and you configure it by editing `js/constants.js` and
reloading.

This repo keeps that renderer essentially untouched and wraps it in the pieces
you need to run it as an appliance:

| | upstream | here |
|---|---|---|
| Quotes / grid size | edit `constants.js`, reload | runtime state, editable from a web UI or API |
| Persistence | none | JSON state on a volume, survives restarts |
| Full-screen images | — | upload gallery + brightness control |
| Remote control | — | HTTP API + Server-Sent Events |
| Deployment | serve the folder | Docker / compose |

If you just want the display, use upstream — it's simpler and it's the
original. Use this if you want to drive it from something else.

## Quick start

```bash
git clone https://github.com/aronchick/flipoff.git flipoff-kiosk
cd flipoff-kiosk

cp .env.example .env
sed -i.bak "s/^AUTH_TOKEN=.*/AUTH_TOKEN=$(openssl rand -hex 32)/" .env && rm -f .env.bak

docker compose up -d
```

* the display: <http://localhost:8080/>
* the control panel: <http://localhost:8080/admin>
* API docs: <http://localhost:8080/docs>

Leaving `AUTH_TOKEN` unset is not fatal, but the container will warn on every
start — the default is well known, and every write endpoint is unprotected
until you set it.

For an actual kiosk, point a browser at `/` in fullscreen. On a Raspberry Pi or
a spare mini PC, Chromium in kiosk mode on tty1 works well:

```bash
chromium --kiosk --incognito --noerrdialogs \
  --autoplay-policy=no-user-gesture-required http://localhost:8080/
```

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `AUTH_TOKEN` | `changeme` | Bearer token for every write endpoint. **Set this.** |
| `PORT` | `8080` | Host port compose publishes on |
| `STATE_PATH` | `/config/state.json` | Where state is persisted |
| `UPLOADS_PATH` | `/config/uploads` | Where gallery images are stored |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | Advertised in the OpenAPI `servers` block |

Mount `/config` on a volume — state and uploaded images live there, so they
survive image rebuilds. Compose maps it to `./config`, which is gitignored.

## API

Full spec at `/openapi.json` (also committed as [`openapi.json`](./openapi.json)),
Swagger UI at `/docs`, ReDoc at `/redoc`. Reads are open; every write needs
`Authorization: Bearer $AUTH_TOKEN`. A missing header returns 401, a wrong
token 403.

```bash
API=http://localhost:8080
AUTH="Authorization: Bearer $AUTH_TOKEN"
JSON="Content-Type: application/json"
```

**Switch what's showing**

```bash
curl -sS -X POST -H "$AUTH" -H "$JSON" -d '{"mode":"board"}'                    $API/api/display
curl -sS -X POST -H "$AUTH" -H "$JSON" -d '{"image":"logo-a1b2c3d4.svg"}'       $API/api/display
curl -sS -X POST -H "$AUTH" -H "$JSON" -d '{"url":"https://example.com/x.gif"}' $API/api/display
curl -sS -X POST -H "$AUTH" -H "$JSON" -d '{"cycle":"next"}'                    $API/api/display
```

**Dim the image**

```bash
curl -sS -X POST -H "$AUTH" -H "$JSON" -d '{"brightness":0.4}' $API/api/gif/brightness
curl -sS -X POST -H "$AUTH" -H "$JSON" -d '{"delta":-0.1}'     $API/api/gif/brightness
```

Applied as a CSS `brightness()` filter with a 0.35s transition. The floor is
**0.02, not 0** — a TV in kiosk mode has no physical controls, and a fully
black screen is indistinguishable from a dead panel.

**Everything else**

```
GET    /api/state            full document      PUT /api/state to replace it
GET    /api/images           gallery listing
POST   /api/images?filename=NAME                file as the RAW body, not multipart
DELETE /api/images/{name}
POST   /api/blackout|mute                       toggles
POST   /api/next|prev                           step the quote rotation
POST   /api/refresh                             force the display to hard-reload
GET    /api/events                              Server-Sent Events state stream
GET    /healthz
```

Two things worth knowing if you're automating this:

* `PUT /api/state` replaces the **entire** document. Read `GET /api/state`,
  mutate, send it back — or use `/api/display` and `/api/gif/brightness`, which
  change one thing and can't clobber your quotes.
* The browser caches its JavaScript. After changing anything under `static/`,
  `POST /api/refresh` or the running display keeps executing the old code.

## Development

See [CONTRIBUTING.md](./CONTRIBUTING.md). Source is `COPY`d into the image, so
code changes need a rebuild:

```bash
docker compose up -d --build
```

## License

Dual licensed: **Apache-2.0 OR MIT**, at your option — see
[LICENSE-APACHE](./LICENSE-APACHE) and [LICENSE-MIT](./LICENSE-MIT).

One carve-out, because it isn't ours to relicense: the split-flap rendering
engine from [magnum6actual/flipoff](https://github.com/magnum6actual/flipoff)
remains MIT only. [NOTICE](./NOTICE) lists exactly which files those are.

---

Built with love from the folks at **[Expanso](https://expanso.io)**.
