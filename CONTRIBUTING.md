# Contributing

Bug reports and pull requests are welcome.

## Running it

```bash
cp .env.example .env      # set AUTH_TOKEN
docker compose up --build
```

The display is at <http://localhost:8080/>, the control panel at `/admin`, and
API docs at `/docs`.

## Things worth knowing

* **The browser caches its JavaScript.** After changing anything under
  `static/`, `POST /api/refresh` or the running display keeps executing the old
  code and your change will look like it did nothing.
* **`PUT /api/state` replaces the whole document.** Prefer the narrow endpoints
  (`/api/display`, `/api/gif/brightness`) when adding features — they can't
  clobber someone's quotes.
* **Regenerate the spec** after changing any route:
  ```bash
  curl -sS http://localhost:8080/openapi.json \
    | python3 -c "import json,sys;print(json.dumps(json.load(sys.stdin),indent=2,sort_keys=True))" \
    > openapi.json
  ```
* **Don't edit the upstream renderer** (`Board.js`, `Tile.js`, `flapAudio.js`,
  `MessageRotator.js`) unless you have to. Keeping them identical to
  [magnum6actual/flipoff](https://github.com/magnum6actual/flipoff) makes it
  easy to pull improvements from there. See [NOTICE](./NOTICE).

## License

Contributions are dual licensed under Apache-2.0 OR MIT, matching the project.
