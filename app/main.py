"""FlipOff kiosk backend: serves the board, admin UI, and state API with SSE."""
import asyncio
import json
import os
import re
import secrets
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Header, Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field

STATE_PATH = Path(os.environ.get("STATE_PATH", "/config/state.json"))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme")

# Uploaded gallery images live under the persistent /config volume so they
# survive image rebuilds and container recreates, exactly like state.json.
UPLOADS_DIR = Path(os.environ.get("UPLOADS_PATH", "/config/uploads"))
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB cap per image

# Floor for the gif dimmer. Zero would be indistinguishable from a dead
# panel, and this TV has no physical controls -- leave a visible ember so a
# too-dark setting is still recoverable from across the room.
GIF_BRIGHTNESS_MIN = 0.02

# Advertised in the OpenAPI `servers` block so a generated client needs no
# base-URL configuration. Set this to however the kiosk is actually reached
# (e.g. http://kiosk.local:8080); it is cosmetic and affects nothing else.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080")

DEFAULT_STATE = {
    "quotes": [
        {"id": str(uuid.uuid4()), "lines": ["GOD IS IN", "THE DETAILS .", "- LUDWIG MIES", ""]},
        {"id": str(uuid.uuid4()), "lines": ["STAY HUNGRY", "STAY FOOLISH", "- STEVE JOBS", ""]},
        {"id": str(uuid.uuid4()), "lines": ["GOOD DESIGN IS", "GOOD BUSINESS", "- THOMAS WATSON", ""]},
        {"id": str(uuid.uuid4()), "lines": ["LESS IS MORE", "", "- MIES VAN DER ROHE", ""]},
        {"id": str(uuid.uuid4()), "lines": ["MAKE IT SIMPLE", "BUT SIGNIFICANT", "- DON DRAPER", ""]},
        {"id": str(uuid.uuid4()), "lines": ["HAVE NO FEAR OF", "PERFECTION", "- SALVADOR DALI", ""]},
    ],
    "intervalSec": 8,
    "blackout": False,
    "muted": False,
    # 6 grid rows gives 2 rows of natural top/bottom margin around a typical
    # 3-line quote. Set to exactly your content-line count if you want edge
    # to edge text.
    "rows": 6,
    "cols": 22,
    "sideMarginPx": 120,
    "topMarginPx": 80,
    "letterScale": 0.78,
    "displayMode": "board",
    "gifUrl": "",
    "gifBrightness": 1.0,
}


class Quote(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    lines: list[str]


class State(BaseModel):
    quotes: list[Quote]
    intervalSec: int = Field(ge=2, le=600)
    blackout: bool
    muted: bool
    rows: int = Field(default=4, ge=2, le=8)
    cols: int = Field(default=22, ge=10, le=40)
    sideMarginPx: int = Field(default=120, ge=0, le=600)
    topMarginPx: int = Field(default=80, ge=0, le=600)
    letterScale: float = Field(default=0.78, ge=0.3, le=0.95)
    displayMode: Literal["board", "gif"] = "board"
    gifUrl: str = Field(default="", max_length=2000)
    gifBrightness: float = Field(default=1.0, ge=GIF_BRIGHTNESS_MIN, le=1.0)


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            stored = json.loads(STATE_PATH.read_text())
            # Forward-compat: fill in any fields added after this file was written.
            merged = dict(DEFAULT_STATE)
            merged.update(stored)
            return merged
        except json.JSONDecodeError:
            pass
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(DEFAULT_STATE, indent=2))
    return dict(DEFAULT_STATE)


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def _list_images() -> list[dict]:
    """Saved gallery images, newest first. url is relative to this origin."""
    if not UPLOADS_DIR.exists():
        return []
    items = []
    for p in UPLOADS_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXT:
            st = p.stat()
            items.append(
                {
                    "name": p.name,
                    "url": f"/uploads/{p.name}",
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _store_name(original: str) -> str:
    """Collision-resistant, traversal-safe storage name derived from the
    uploaded filename: <sanitized-stem>-<8 hex>.<ext>."""
    ext = Path(original).suffix.lower()
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(original).stem).strip("-")[:40]
    return f"{stem or 'image'}-{uuid.uuid4().hex[:8]}{ext}"


class Broadcaster:
    """Fan-out SSE event bus. One queue per subscriber."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, event: str, data: dict) -> None:
        payload = {"event": event, "data": json.dumps(data)}
        for q in list(self._subscribers):
            await q.put(payload)


API_DESCRIPTION = """\
Split-flap quote board and full-screen image kiosk for any TV.

Two display modes: **board** (rotating split-flap quotes) and **gif** (a
full-screen image or animation). Switch between them with `POST /api/display`,
dim the image with `POST /api/gif/brightness`.

## Auth

Reads are open on the LAN. Every mutating endpoint needs
`Authorization: Bearer <token>`, matched against the `AUTH_TOKEN` environment
variable the container was started with. A missing header returns 401, a wrong token 403.

## Notes for automated callers

* `PUT /api/state` replaces the **entire** document -- read `GET /api/state`
  first and send it back mutated, or you will drop the user's quotes. Prefer
  `POST /api/display` and `POST /api/gif/brightness`, which change one thing.
* `POST /api/images` takes the file as the **raw request body**, not multipart,
  with the filename in the `?filename=` query parameter.
* `GET /api/events` is a Server-Sent Events stream; every state change is
  published there, so a UI can follow along without polling.
* State changes persist to disk and survive container restarts.
"""

TAGS = [
    {"name": "display", "description": "What the TV is currently showing."},
    {"name": "state", "description": "The full state document."},
    {"name": "gallery", "description": "Uploaded images available to display."},
    {"name": "controls", "description": "Transport and screen controls."},
    {"name": "system", "description": "Health, events, and the kiosk pages."},
]

app = FastAPI(
    title="FlipOff Kiosk",
    version="1.1.0",
    description=API_DESCRIPTION,
    openapi_tags=TAGS,
    servers=[{"url": PUBLIC_BASE_URL, "description": "this kiosk"}],
)


class NoCacheStaticFiles(StaticFiles):
    """Serve static files with aggressive no-cache headers so chromium
    never reuses a stale module across rebuilds."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp


app.mount("/static", NoCacheStaticFiles(directory="/app/static"), name="static")

# Uploaded images are served from the persistent volume. Create the dir first
# so StaticFiles can mount it on a fresh install with no uploads yet.
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", NoCacheStaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

templates = Jinja2Templates(directory="/app/app/templates")

state: dict = _load_state()
bus = Broadcaster()


bearer_scheme = HTTPBearer(
    scheme_name="BearerToken",
    description="The value of the AUTH_TOKEN environment variable.",
    # auto_error would raise 403 for a *missing* header; this API has always
    # answered 401 for that and 403 only for a wrong token. Keep it that way.
    auto_error=False,
)


def require_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="missing bearer token")
    if not secrets.compare_digest(credentials.credentials, AUTH_TOKEN):
        raise HTTPException(status_code=403, detail="invalid token")


NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/", response_class=HTMLResponse)
async def kiosk(request: Request) -> HTMLResponse:
    resp = templates.TemplateResponse(
        "kiosk.html",
        {"request": request, "initial_state_json": json.dumps(state)},
    )
    for k, v in NO_CACHE_HEADERS.items():
        resp.headers[k] = v
    return resp


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request) -> HTMLResponse:
    resp = templates.TemplateResponse("admin.html", {"request": request})
    for k, v in NO_CACHE_HEADERS.items():
        resp.headers[k] = v
    return resp


@app.get("/api/state")
async def get_state() -> dict:
    return state


@app.put("/api/state")
async def put_state(new_state: State, _: None = Depends(require_token)) -> dict:
    state.clear()
    state.update(new_state.model_dump())
    _save_state(state)
    await bus.publish("state", state)
    return state


@app.post("/api/blackout")
async def toggle_blackout(_: None = Depends(require_token)) -> dict:
    state["blackout"] = not state["blackout"]
    _save_state(state)
    await bus.publish("state", state)
    return {"blackout": state["blackout"]}


@app.post("/api/mute")
async def toggle_mute(_: None = Depends(require_token)) -> dict:
    state["muted"] = not state["muted"]
    _save_state(state)
    await bus.publish("state", state)
    return {"muted": state["muted"]}


@app.post("/api/next")
async def next_quote(_: None = Depends(require_token)) -> dict:
    await bus.publish("next", {})
    return {"ok": True}


@app.post("/api/prev")
async def prev_quote(_: None = Depends(require_token)) -> dict:
    await bus.publish("prev", {})
    return {"ok": True}


@app.post("/api/refresh")
async def refresh_kiosk(_: None = Depends(require_token)) -> dict:
    """Tell the kiosk page to hard-reload (pick up new static assets/CSS)."""
    await bus.publish("reload", {})
    return {"ok": True}


class DisplayPatch(BaseModel):
    """One-call switch of what the TV is showing."""

    mode: Literal["board", "gif"] | None = None
    # Gallery filename, e.g. "logo-6251cafa.svg".
    image: str | None = Field(default=None, max_length=300)
    # Any direct image URL, external or a local /uploads/... path.
    url: str | None = Field(default=None, max_length=2000)
    # Step through the gallery without knowing what is currently up.
    cycle: Literal["next", "prev"] | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"mode": "board"},
                {"image": "logo-6251cafa.svg"},
                {"url": "https://example.com/animation.gif"},
                {"cycle": "next"},
            ]
        }
    }


def _resolve_gallery_url(name: str) -> str:
    """Map a gallery filename to its served URL, rejecting traversal."""
    if "/" in name or "\\" in name or ".." in name or not name:
        raise HTTPException(status_code=400, detail="bad image name")
    target = UPLOADS_DIR / name
    if target.parent != UPLOADS_DIR or not target.is_file():
        raise HTTPException(status_code=404, detail=f"no gallery image named '{name}'")
    return f"/uploads/{name}"


@app.post("/api/display")
async def set_display(patch: DisplayPatch, _: None = Depends(require_token)) -> dict:
    """Switch mode and/or image in one call.

    PUT /api/state can already do this, but only by round-tripping the whole
    document, so a caller that just wants "show the logo" has to fetch, mutate
    and repost every quote -- and races anyone editing them. Accepts a gallery
    filename, a direct URL, or a relative cycle through the gallery.
    """
    if patch.mode is None and patch.image is None and patch.url is None and patch.cycle is None:
        raise HTTPException(
            status_code=400, detail="provide at least one of: mode, image, url, cycle"
        )

    previous = {"displayMode": state.get("displayMode"), "gifUrl": state.get("gifUrl", "")}

    if patch.cycle is not None:
        images = _list_images()
        if not images:
            raise HTTPException(status_code=409, detail="gallery is empty")
        urls = [img["url"] for img in images]
        try:
            idx = urls.index(state.get("gifUrl", ""))
        except ValueError:
            # Not currently on a gallery image -- "next" starts at the top.
            idx = -1 if patch.cycle == "next" else 0
        step = 1 if patch.cycle == "next" else -1
        state["gifUrl"] = urls[(idx + step) % len(urls)]
        state["displayMode"] = "gif"
    elif patch.image is not None:
        state["gifUrl"] = _resolve_gallery_url(patch.image)
        state["displayMode"] = "gif"
    elif patch.url is not None:
        state["gifUrl"] = patch.url
        state["displayMode"] = "gif"

    if patch.mode is not None:
        state["displayMode"] = patch.mode

    if state["displayMode"] == "gif" and not state.get("gifUrl"):
        state["displayMode"] = previous["displayMode"] or "board"
        raise HTTPException(status_code=409, detail="no image selected; nothing to show")

    _save_state(state)
    await bus.publish("state", state)
    return {
        "displayMode": state["displayMode"],
        "gifUrl": state.get("gifUrl", ""),
        "gifBrightness": float(state.get("gifBrightness", 1.0)),
        "previous": previous,
    }


class BrightnessPatch(BaseModel):
    """Absolute or relative change to the full-screen image brightness."""

    brightness: float | None = Field(default=None, ge=GIF_BRIGHTNESS_MIN, le=1.0)
    delta: float | None = Field(default=None, ge=-1.0, le=1.0)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"brightness": 0.4},
                {"delta": -0.1},
            ]
        }
    }


@app.get("/api/gif/brightness")
async def get_gif_brightness() -> dict:
    return {"gifBrightness": float(state.get("gifBrightness", 1.0))}


@app.post("/api/gif/brightness")
async def set_gif_brightness(
    patch: BrightnessPatch, _: None = Depends(require_token)
) -> dict:
    """Dim the displayed image without reposting the whole state.

    PUT /api/state requires the full document (every quote included), which is
    the wrong shape for "make the TV darker" -- it is heavy from a phone and it
    races with anyone editing quotes. `delta` lets a caller nudge the screen
    without first reading the current value.
    """
    if patch.brightness is None and patch.delta is None:
        raise HTTPException(status_code=400, detail="provide 'brightness' or 'delta'")
    current = float(state.get("gifBrightness", 1.0))
    target = patch.brightness if patch.brightness is not None else current + patch.delta
    value = round(max(GIF_BRIGHTNESS_MIN, min(1.0, target)), 3)
    state["gifBrightness"] = value
    _save_state(state)
    await bus.publish("state", state)
    return {"gifBrightness": value, "previous": current}


@app.get("/api/images")
async def list_images() -> list[dict]:
    """Public list of saved gallery images (newest first)."""
    return _list_images()


@app.post("/api/images")
async def upload_image(
    request: Request,
    filename: str = "",
    _: None = Depends(require_token),
) -> dict:
    """Store an uploaded image. The file is sent as the raw request body and
    its original name comes in via the `filename` query param — this keeps the
    app free of a multipart dependency."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_EXT))
        raise HTTPException(
            status_code=400, detail=f"unsupported type '{ext or filename}'; allowed: {allowed}"
        )
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 8 MB)")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 8 MB)")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    name = _store_name(filename)
    (UPLOADS_DIR / name).write_bytes(body)
    return {"ok": True, "image": {"name": name, "url": f"/uploads/{name}"}, "images": _list_images()}


@app.delete("/api/images/{name}")
async def delete_image(name: str, _: None = Depends(require_token)) -> dict:
    if "/" in name or "\\" in name or ".." in name or not name:
        raise HTTPException(status_code=400, detail="bad image name")
    target = UPLOADS_DIR / name
    if target.parent != UPLOADS_DIR or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    target.unlink()
    # If the deleted image was the one on screen, fall back to the board so the
    # TV never points at a now-missing file.
    if state.get("gifUrl") == f"/uploads/{name}":
        state["gifUrl"] = ""
        state["displayMode"] = "board"
        _save_state(state)
        await bus.publish("state", state)
    return {"ok": True, "images": _list_images()}


@app.get("/api/events")
async def events(request: Request) -> EventSourceResponse:
    async def stream():
        q = await bus.subscribe()
        try:
            # Send current state immediately so new subscribers hydrate
            yield {"event": "state", "data": json.dumps(state)}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            bus.unsubscribe(q)

    return EventSourceResponse(stream())


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Flatten pydantic errors into a single readable line so the admin UI
    # can show what went wrong instead of "422 Unprocessable Entity".
    bits = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
        bits.append(f"{loc}: {err.get('msg', 'invalid')}")
    return JSONResponse(
        status_code=422,
        content={"error": "validation failed", "detail": "; ".join(bits) or str(exc)},
    )
