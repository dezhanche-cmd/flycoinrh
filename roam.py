"""
The fly, loose on the internet with a random lifespan.

A page is screenshotted, sampled through the fly's 892 retinotopic hex columns
into L1 and L2, and 165,122 neurons integrate. DNa02's left-right asymmetry
moves the cursor sideways, DNa01 moves it up the page, MDN reverses, and DNp09
- the stopping neuron - clicks. If the click lands on a link, the fly is
somewhere new. Nothing chooses where it goes. That is the whole point.

The fly has a random lifespan: each life lasts between LIFESPAN_MIN and
LIFESPAN_MAX seconds, after which it dies and a new one is born. Deaths are
logged to build/deaths.json.

RAILS, and why each one is here
-------------------------------
* No wallet. This browser never gets a key, a provider or an extension.
* No typing. The fly has no keyboard at all.
* No downloads, no popups, no file dialogs.
* A click is checked before it lands: anything that looks like a submit, an
  upload, a payment or a sign-in is vetoed and logged as a veto.
* A URL blocklist, checked on every navigation.
* A hop budget. When it runs out the fly is put back on a seed page.

  py roam.py                 open http://localhost:4660 and press START
  py roam.py --headful       watch the real browser too
"""
import argparse
import asyncio
import base64
import io
import json
import os
import random
import re
import time
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

import calibration
from flysim import FlyBrain
from envcfg import load_env
from mushroom import MushroomBody
from flyeye import FlyPilot

ROOT = Path(__file__).parent
OUT = ROOT / "build"

# Random lifespan: each life lasts between MIN and MAX seconds, then dies.
LIFESPAN_MIN_S = 1      # 1 second
LIFESPAN_MAX_S = 1800   # 30 minutes

# Death log file
DEATHS_FILE = OUT / "deaths.json"


def _atomic_write(path, data: bytes):
    """Write then rename, so /state never reads a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# Link-rich, text-heavy, safe places to be dropped into. The fly leaves them
# on its own within a few clicks; these only decide where a life starts.
SEEDS = [
    # Weighted toward Special:Random on purpose. Every time a hop budget runs
    # out the fly is put back on a seed, so if the seeds are a short fixed
    # list it lands on the same few pages forever - which is exactly what
    # happened. Special:Random is a different article every single time, so a
    # reset becomes somewhere new rather than somewhere familiar.
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://commons.wikimedia.org/wiki/Special:Random",
    "https://en.wikisource.org/wiki/Special:Random",
    "https://en.wikiquote.org/wiki/Special:Random",
    "https://www.gutenberg.org/browse/scores/top",
    "https://openlibrary.org/",
    "https://xkcd.com/",
]


# Checked against every URL the browser tries to commit to.
BLOCK = re.compile(
    r"(porn|xxx|adult|nsfw|escort|hentai|onlyfans|camsoda|chaturbate"
    r"|casino|bet365|poker|gambl|lottery"
    r"|checkout|/cart|/pay|payment|billing|invoice|subscribe"
    r"|signin|sign-in|login|log-in|signup|sign-up|register|/auth"
    r"|password|passwd|account/delete|unsubscribe"
    r"|\.exe$|\.dmg$|\.msi$|\.apk$|\.zip$|\.torrent$|magnet:"
    r"|\.epub|\.mobi|\.pdf$|\.iso$|/download|kindle"
    r"|mailto:|tel:)", re.I)

# A keyword blocklist cannot be made complete - "p0rn" walks straight through
# it - so it is the second line of defence, not the first. The first is this:
# the fly stays inside a set of domains unless someone deliberately opens it.
# Wikipedia alone is millions of pages that link everywhere, so this is still a
# real roam; it is just a roam with a fence.
ALLOW = {
    "en.wikipedia.org", "en.m.wikipedia.org", "commons.wikimedia.org",
    "en.wikisource.org", "en.wikiquote.org", "en.wikibooks.org",
    "www.wikidata.org", "species.wikimedia.org",
    "news.ycombinator.com",
    "www.gutenberg.org", "gutenberg.org",
    "openlibrary.org",
    "xkcd.com", "www.xkcd.com",
    "arxiv.org", "www.arxiv.org",
}
OPEN = load_env().get("FLY_ROAM_OPEN") == "1"


def env(name, default=""):
    v = load_env().get(name)
    return default if v is None or v == "" else str(v)


# Checked against the element under the cursor before a click is allowed.
VETO = re.compile(
    r"(submit|upload|sign in|sign up|log in|log out|subscribe|buy|purchase"
    r"|checkout|pay |donate|delete|remove|report|flag|send|post|reply"
    r"|comment|password|credit card)", re.I)


def allowed_host(url):
    """The fly stays inside the allowlist unless FLY_ROAM_OPEN=1."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if OPEN:
        return True
    return host in ALLOW


def log_death(cause, url, steps, clicks, hops, blocked):
    """Record a death to build/deaths.json."""
    try:
        deaths = []
        if DEATHS_FILE.exists():
            try:
                deaths = json.loads(DEATHS_FILE.read_text())
            except Exception:
                deaths = []
        deaths.append({
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "epoch": int(time.time()),
            "cause": cause,
            "last_url": url,
            "steps": steps,
            "clicks": clicks,
            "hops": hops,
            "blocked": blocked,
        })
        # Keep last 1000 deaths
        deaths = deaths[-1000:]
        _atomic_write(DEATHS_FILE, json.dumps(deaths, indent=1).encode("utf-8"))
    except Exception as exc:
        say(f"failed to write death log: {exc}")


app = FastAPI()
# the public page reads /state and /frame.jpg from a different origin
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["*"])
STATE = {"brain": None, "pilot": None, "running": False,
         "life_start": 0.0, "life_age_s": 0.0, "life_death": None,
         "lifespan_s": 0.0}
CLIENTS = set()


async def broadcast(msg):
    """Tell every watcher at once."""
    dead = []
    text = json.dumps(msg)
    for ws in list(CLIENTS):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        CLIENTS.discard(ws)


def say(*parts):
    try:
        print(*parts, flush=True)
    except Exception:
        try:
            print(*[str(p).encode("ascii", "replace").decode() for p in parts],
                  flush=True)
        except Exception:
            pass


def load_brain():
    """Load the connectome once, with calibration measured in calibration.py."""
    if STATE["brain"] is None:
        say("loading the connectome ...")
        fb = FlyBrain()
        STATE["brain"] = fb
        STATE["pilot"] = FlyPilot(fb, sim_steps=60)
        STATE["xy"] = soma_xy(fb)
        STATE["gains"] = calibration.gains_for(fb, calibration.CHOSEN)
        say(f"brain ready: {len(fb.bodies):,} neurons")
        say(f"calibration: {calibration.CHOSEN}")
        try:
            STATE["mb"] = MushroomBody(fb, calibration=calibration.CHOSEN,
                                       store=OUT / "mb_gains.v2.npz")
            st = STATE["mb"].stats()
            say(f"mushroom body: {st['synapses']:,} KC->MBON synapses")
        except FileNotFoundError as exc:
            STATE["mb"] = None
            say("NO MUSHROOM BODY:", str(exc)[:200])
            say("the fly roams, but nothing can be learned until mb_sides.py has run")
    return STATE["brain"], STATE["pilot"]


def soma_xy(fb):
    """Each neuron's measured soma position, flattened to the screen."""
    try:
        import pandas as pd
        a = pd.read_feather(ROOT / "data" / "body-annotations.feather")
        a = a.drop_duplicates("bodyId").set_index("bodyId")
        loc = a["somaLocation"].reindex(fb.bodies)
        xy = np.full((fb.n, 2), np.nan, dtype=np.float32)
        for i, v in enumerate(loc.to_numpy()):
            if isinstance(v, (list, tuple, np.ndarray)) and len(v) >= 3:
                xy[i] = (float(v[0]), float(v[2]))
        ok = ~np.isnan(xy[:, 0])
        if ok.sum() < 100:
            return None
        lo, hi = np.nanmin(xy[ok], 0), np.nanmax(xy[ok], 0)
        xy = (xy - lo) / np.maximum(hi - lo, 1e-6)
        return xy
    except Exception as exc:
        say("no soma coordinates:", str(exc)[:90])
        return None


# --------------------------------------------------------------------------
# what is under the cursor, and may the fly click it
# --------------------------------------------------------------------------
UNDER_JS = """([x, y]) => {
  const e = document.elementFromPoint(x, y);
  if (!e) return null;
  const a = e.closest('a');
  const btn = e.closest('button,input,textarea,select,[role=button]');
  const txt = ((btn || a || e).innerText || (btn || e).value || '')
    .trim().slice(0, 80);
  return {
    tag: e.tagName,
    href: a ? a.href : null,
    control: !!btn,
    type: (btn && btn.type) || '',
    text: txt,
    label: (e.getAttribute('aria-label') || '') + ' ' + (e.name || ''),
  }; }"""


def may_click(under):
    """A click is allowed unless it looks like it commits something."""
    if not under:
        return False, "nothing there"
    href = under.get("href") or ""
    if href and BLOCK.search(href):
        return False, "blocked destination"
    if href and not allowed_host(href):
        return False, "outside the fence"
    blob = " ".join(str(under.get(k) or "") for k in ("text", "label", "type"))
    if VETO.search(blob):
        return False, f"veto: {blob.strip()[:40]}"
    if under.get("type", "").lower() in ("submit", "file", "password"):
        return False, "veto: form control"
    return True, "ok"


async def screenshot(page):
    raw = await page.screenshot(type="jpeg", quality=62)
    return raw


def to_gray(raw, w=None, h=None):
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("L")
    return np.asarray(im, dtype=np.float32) / 255.0


# --------------------------------------------------------------------------
# the roam
# --------------------------------------------------------------------------
async def roam(steps_per_page=44, headful=False, seed=None):
    from playwright.async_api import async_playwright

    fb, pilot = load_brain()
    rng = random.Random(seed if seed is not None else int(time.time()))

    send = broadcast

    # Random lifespan for this life
    lifespan_s = rng.uniform(LIFESPAN_MIN_S, LIFESPAN_MAX_S)
    life_start = time.time()
    STATE["life_start"] = life_start
    STATE["life_death"] = None
    STATE["lifespan_s"] = lifespan_s

    async def log(m):
        say("  " + str(m))
        stats["events"].append({"t": time.strftime("%H:%M:%S"), "m": str(m)[:110]})
        stats["events"] = stats["events"][-40:]
        await send({"type": "log", "msg": str(m)})

    stats = {"steps": 0, "clicks": 0, "vetoes": 0, "hops": 0,
             "blocked": 0, "scrolled": 0, "started": life_start,
             "visited": [], "events": [], "firing": []}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not headful,
            # a container gives /dev/shm 64 MB and Chromium crashes on it
            args=["--disable-dev-shm-usage", "--no-sandbox"])
        # No storage, no wallet, no extension, no downloads. A fresh context
        # with nothing in it: the fly cannot be logged in as anyone.
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            accept_downloads=False,
            java_script_enabled=True,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36 flybrain/1.0"),
        )
        page = await ctx.new_page()
        page.on("dialog", lambda d: asyncio.create_task(d.dismiss()))

        # A popup is not somewhere the fly chose to go, so it gets closed
        def _popup(p):
            if p is not page:
                asyncio.create_task(p.close())
        ctx.on("page", _popup)

        async def goto(url, why=""):
            if BLOCK.search(url) or not allowed_host(url):
                stats["blocked"] += 1
                await log(f"blocked: {url[:70]}")
                return False
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1200)
                stats["hops"] += 1
                title = (await page.title())[:70]
                stats["visited"].append({"url": page.url, "title": title,
                                         "at": int(time.time())})
                stats["visited"] = stats["visited"][-40:]
                await send({"type": "place", "url": page.url, "title": title,
                            "why": why})
                await log(f"arrived: {title} - {page.url[:64]}")
                return True
            except Exception as exc:
                await log(f"could not open: {str(exc)[:70]}")
                return False

        async def reset(why):
            """Every restart goes through one door. A new life or a hop reset."""
            url = rng.choice(SEEDS)
            return await goto(url, f"{why}")

        await reset("a new life")

        cx, cy = 640.0, 400.0
        px_, py_ = cx, cy
        on_page = 0

        # Screencast, not screenshots.
        latest = {"jpg": None, "n": 0}
        cdp = await ctx.new_cdp_session(page)
        loop_ = asyncio.get_running_loop()

        def on_cast(params):
            try:
                latest["jpg"] = base64.b64decode(params["data"])
                latest["n"] += 1
                asyncio.run_coroutine_threadsafe(
                    cdp.send("Page.screencastFrameAck",
                             {"sessionId": params["sessionId"]}), loop_)
            except Exception:
                pass

        cdp.on("Page.screencastFrame", on_cast)
        await cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": 55,
            "maxWidth": 1280, "maxHeight": 800, "everyNthFrame": 1})

        async def pump():
            """Push the newest frame to watchers, at most ten times a second."""
            last = -1
            while STATE["running"]:
                if latest["jpg"] is not None and latest["n"] != last:
                    last = latest["n"]
                    await send({"type": "view",
                                "jpg": base64.b64encode(latest["jpg"]).decode(),
                                "cx": cx, "cy": cy})
                await asyncio.sleep(0.1)

        cap = asyncio.create_task(pump())
        for _ in range(60):
            if latest["jpg"] is not None:
                break
            await asyncio.sleep(0.1)
        if latest["jpg"] is None:
            latest["jpg"] = await screenshot(page)

        while STATE["running"]:
            # Check lifespan
            life_age_s = time.time() - life_start
            STATE["life_age_s"] = life_age_s
            if life_age_s >= lifespan_s:
                await log(f"life ended after {life_age_s:.0f}s (max {lifespan_s:.0f}s)")
                break

            raw = latest["jpg"]
            img = to_gray(raw)

            seed_ = rng.randrange(1 << 30)
            dx, dy, click, hz, info = pilot.step(
                img, cx, cy, gains=STATE["gains"], seed=seed_, detail=True)
            # Guard against NaN from empty motor selections
            if dx != dx or dy != dy:  # NaN check
                dx, dy = 0.0, 0.0
            cx = float(np.clip(cx + dx, 8, 1272))
            cy = float(np.clip(cy + dy, 8, 792))
            stats["steps"] += 1
            on_page += 1

            # A fly that walks off the bottom of what it can see should get
            # more page, not stick to the edge.
            EDGE = 110
            if cy > 800 - EDGE and dy > 0:
                await page.mouse.wheel(0, 300)
                cy = 800 - EDGE - 140
                stats["scrolled"] += 1
            elif cy < EDGE and dy < 0:
                await page.mouse.wheel(0, -300)
                cy = EDGE + 140
                stats["scrolled"] += 1

            # a sample of the neurons that actually fired, at their measured
            # soma positions - the scatter is a readout, not an animation
            scatter = []
            xy = STATE.get("xy")
            fired = info.get("fired")
            if xy is not None and fired is not None and len(fired):
                take = fired if len(fired) <= 420 else rng.sample(
                    list(fired), 420)
                for i in take:
                    x, y = xy[i]
                    if not np.isnan(x):
                        scatter.append([round(float(x), 3), round(float(y), 3)])

            # Mushroom body observation (if available)
            mb = STATE.get("mb")
            if mb is not None:
                mb.observe(info.get("fired"))
                mb.forget()

            stats["firing"].append(info["firing"])
            stats["firing"] = stats["firing"][-72:]

            neural = {
                "firing": info["firing"], "total": fb.n,
                "history": stats["firing"],
                "vision": info.get("vision"),
                "spikes_per_sec": round(info["spikes_per_sec"]),
                "mean_mv": round(info["mean_mv"], 1),
                "visual": info["visual"], "motor": info["motor"],
                "dn": {k: round(v, 1) for k, v in hz.items()},
                "out": {k: round(info[k], 3) for k in
                        ("turn_l", "turn_r", "forward", "reverse", "click")},
                "scatter": scatter,
                "learning": (STATE["mb"].stats()
                             if STATE.get("mb") is not None else None),
            }

            # Animate cursor movement between positions
            steps_ = 9
            for j in range(1, steps_ + 1):
                t_ = j / steps_
                t_ = t_ * t_ * (3 - 2 * t_)
                await page.mouse.move(px_ + (cx - px_) * t_,
                                      py_ + (cy - py_) * t_)
                await send({"type": "cursor",
                            "cx": px_ + (cx - px_) * t_,
                            "cy": py_ + (cy - py_) * t_})
                await asyncio.sleep(0.028)
            px_, py_ = cx, cy

            frame = {"type": "frame", "neural": neural,
                     "events": stats["events"][-18:],
                     "visited": stats["visited"][-8:],
                     "cx": cx, "cy": cy,
                     "hz": {k: round(v, 1) for k, v in hz.items()},
                     "stats": {k: stats[k] for k in
                               ("steps", "clicks", "vetoes", "hops",
                                "blocked", "scrolled")},
                     "url": page.url,
                     "life_age_s": round(life_age_s, 1),
                     "lifespan_s": round(STATE.get("lifespan_s", 0), 1),
            }
            await send(frame)

            # Click handling
            if click:
                under = await page.evaluate(UNDER_JS, [cx, cy])
                ok, why = may_click(under)
                if ok:
                    stats["clicks"] += 1
                    before = page.url
                    await log(f"click on {(under.get('text') or under['tag'])[:44]}")
                    try:
                        await page.mouse.click(cx, cy)
                        await page.wait_for_timeout(1800)
                    except Exception:
                        pass
                    if page.url != before:
                        if BLOCK.search(page.url) or not allowed_host(page.url):
                            stats["blocked"] += 1
                            await log(f"landed somewhere blocked, going back")
                            try:
                                await page.go_back(timeout=15000)
                            except Exception:
                                await reset("bounced")
                        else:
                            stats["hops"] += 1
                            title = (await page.title())[:70]
                            stats["visited"].append(
                                {"url": page.url, "title": title,
                                 "at": int(time.time())})
                            stats["visited"] = stats["visited"][-40:]
                            await send({"type": "place", "url": page.url,
                                        "title": title, "why": "followed a link"})
                            await log(f"followed a link to {title}")
                        on_page = 0
                        cx, cy = 640.0, 400.0
                else:
                    stats["vetoes"] += 1
                    await log(f"did not click - {why}")

            # A fly that has run out of page gets put somewhere else.
            if on_page >= steps_per_page:
                on_page = 0
                cx, cy = 640.0, 400.0
                await reset("hop budget spent")

            # Save mushroom body periodically
            if mb is not None and stats["steps"] % 40 == 0:
                mb.save()

            # Publish state
            publish(stats, raw, page.url, hz, neural)
            await asyncio.sleep(0.05)

        cap.cancel()
        await ctx.close()
        await browser.close()

    # Life ended - log death
    cause = "lifespan expired" if life_age_s >= lifespan_s else "unknown"
    last_url = getattr(page, 'url', '') if 'page' in dir() else ""
    log_death(cause, last_url, stats["steps"],
              stats["clicks"], stats["hops"], stats["blocked"])
    STATE["life_death"] = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "age_s": round(life_age_s, 1),
        "cause": cause,
    }
    await send({"type": "done", "stats": stats,
                "life_death": STATE["life_death"]})


def publish(stats, jpg, url, hz, neural=None):
    """Leave the latest frame and a summary on disk."""
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        _atomic_write(OUT / "roam_frame.jpg", jpg)
        state_ = {
            "url": url,
            "steps": stats["steps"], "clicks": stats["clicks"],
            "vetoes": stats["vetoes"], "hops": stats["hops"],
            "blocked": stats["blocked"], "scrolled": stats["scrolled"],
            "uptime_s": int(time.time() - stats["started"]),
            "visited": stats["visited"][-12:],
            "events": stats["events"][-18:],
            "hz": {k: round(v, 1) for k, v in hz.items()},
            "neural": neural,
            "life_age_s": round(STATE.get("life_age_s", 0), 1),
            "lifespan_s": round(STATE.get("lifespan_s", 0), 1),
            "life_death": STATE.get("life_death"),
            "updated": int(time.time()),
        }
        _atomic_write(OUT / "roam_state.json",
                      json.dumps(state_, indent=1).encode("utf-8"))
    except Exception:
        pass


# --------------------------------------------------------------------------
# the local view
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(str(ROOT / "web" / "roam.html"))


@app.get("/status")
def status():
    return {"running": STATE["running"],
            "seeds": len(SEEDS),
            "brain": bool(STATE["brain"]),
            "life_age_s": round(STATE.get("life_age_s", 0), 1),
            "lifespan_s": round(STATE.get("lifespan_s", 0), 1),
            "life_death": STATE.get("life_death"),
            }


@app.get("/state")
def state():
    p = OUT / "roam_state.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"updated": 0}


@app.get("/frame.jpg")
def frame():
    p = OUT / "roam_frame.jpg"
    if p.exists():
        return Response(p.read_bytes(), media_type="image/jpeg")
    return Response(b"", media_type="image/jpeg")


@app.get("/deaths")
def deaths():
    """Return death log entries."""
    try:
        if DEATHS_FILE.exists():
            return json.loads(DEATHS_FILE.read_text())
    except Exception:
        pass
    return []


@app.websocket("/ws")
async def socket(ws: WebSocket):
    """A watcher. There is nothing to send: the fly is already roaming."""
    await ws.accept()
    CLIENTS.add(ws)
    try:
        while True:
            await ws.receive_text()
    except Exception:
        pass
    finally:
        CLIENTS.discard(ws)


@app.on_event("startup")
async def begin():
    """
    Start roaming as soon as the process is up, and keep roaming.

    There is no start button and no stop button. If a run dies - a page hangs,
    a browser falls over - it waits a few seconds and starts a new life rather
    than sitting there waiting to be told.
    """
    if load_env().get("FLY_ALLOW_BROWSER") != "1":
        say("FLY_ALLOW_BROWSER is not 1 - not opening a browser")
        return

    async def forever():
        while True:
            STATE["running"] = True
            try:
                await roam()
            except Exception as exc:
                import traceback
                say("roam ended:", str(exc)[:160])
                traceback.print_exc()
            STATE["running"] = False
            say("starting another life in 6s")
            await asyncio.sleep(6)

    asyncio.create_task(forever())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    # a host that assigns the port says so in PORT
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT", "4660")))
    ap.add_argument("--headful", action="store_true")
    a = ap.parse_args()
    STATE["port"] = a.port
    load_brain()
    say(f"the fly roams - open http://localhost:{a.port}")
    # loopback on a desk, every interface in a container
    uvicorn.run(app, host=os.environ.get("FLY_HOST", "127.0.0.1"),
                port=a.port, log_level="warning")
