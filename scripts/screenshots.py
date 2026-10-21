"""Capture README screenshots from a running server with demo data (python -m sangam.demo).

    python scripts/screenshots.py --key sk_test_... [--url http://localhost:8000] [--chrome PATH]

Drives a headless Chrome over the DevTools protocol, so the only dependency is `websockets`."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import httpx
import websockets

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
CANDIDATES = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium"]


class CDP:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def __call__(self, method: str, **params):
        self.n += 1
        my_id = self.n
        await self.ws.send(json.dumps({"id": my_id, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == my_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})


async def shoot(cdp: CDP, url: str, name: str, *, width=1440, height=900, dark=False, mobile=False, js: str | None = None,
                wait: float = 1.6, full: bool = False):
    await cdp("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=2, mobile=mobile)
    await cdp("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark" if dark else "light"}])
    await cdp("Page.navigate", url=url)
    await asyncio.sleep(wait)
    if js:
        await cdp("Runtime.evaluate", expression=js, awaitPromise=True)
        await asyncio.sleep(wait)
    clip = None
    if full:
        h = (await cdp("Runtime.evaluate", expression="document.documentElement.scrollHeight", returnByValue=True))["result"]["value"]
        clip = {"x": 0, "y": 0, "width": width, "height": min(h, 2400), "scale": 1}
    shot = await cdp("Page.captureScreenshot", format="png", **({"clip": clip, "captureBeyondViewport": True} if clip else {}))
    (OUT / f"{name}.png").write_bytes(base64.b64decode(shot["data"]))
    print("saved", name)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--key", required=True)
    ap.add_argument("--chrome")
    args = ap.parse_args()
    chrome = args.chrome or next((c for c in CANDIDATES if Path(c).exists() or shutil.which(c)), None)
    if not chrome:
        raise SystemExit("Chrome/Edge not found; pass --chrome")
    OUT.mkdir(parents=True, exist_ok=True)

    api = httpx.Client(base_url=args.url, headers={"Authorization": f"Bearer {args.key}"})
    pi = api.post("/v1/payment_intents", json={"amount": 249900, "currency": "INR", "description": "Premium tea hamper",
                                               "customer": {"email": "asha@example.in"}}).json()
    upi_pi = api.post("/v1/payment_intents", json={"amount": 49900, "currency": "INR", "description": "Masala chai x10",
                                                   "payment_methods": ["upi"]}).json()
    api.put("/v1/merchant/routing", json={"routing": {"upi": ["upi_direct"]}})
    api.post(f"/v1/payment_intents/{upi_pi['id']}/confirm", json={"method": "upi"})
    api.put("/v1/merchant/routing", json={"routing": {"upi": ["razorpay", "upi_direct"]}})
    invoice = next(i for i in api.get("/v1/invoices").json()["data"] if i["status"] == "open" and len(i["line_items"]) > 1)
    link = next(l for l in api.get("/v1/payment_links").json()["data"] if l["amount"])

    profile = tempfile.mkdtemp(prefix="sangam-shots-")
    proc = subprocess.Popen([chrome, "--headless=new", "--remote-debugging-port=9339", f"--user-data-dir={profile}",
                             "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen("http://127.0.0.1:9339/json"))
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
            cdp = CDP(ws)
            await cdp("Page.enable")
            login = f"sessionStorage.setItem('sangam_key', {json.dumps(args.key)}); location.reload();"
            await shoot(cdp, f"{args.url}/dashboard", "dashboard-overview", js=login, wait=2.0)

            def nav(page_name: str) -> str:
                return f"document.querySelector('[data-page={page_name}]').click()"
            for page_name in ("payments", "subscriptions", "gateways", "invoices", "recon", "developers"):
                await shoot(cdp, f"{args.url}/dashboard", f"dashboard-{page_name}", js=nav(page_name), wait=1.4)
            await shoot(cdp, f"{args.url}/dashboard", "dashboard-overview-dark", dark=True, wait=2.0)
            await shoot(cdp, pi["checkout_url"], "checkout", width=1280, height=820)
            await shoot(cdp, upi_pi["checkout_url"], "checkout-upi", width=1280, height=900)
            await shoot(cdp, pi["checkout_url"], "checkout-mobile", width=390, height=844, mobile=True)
            await shoot(cdp, pi["checkout_url"], "checkout-dark", width=1280, height=820, dark=True)
            await shoot(cdp, invoice["hosted_invoice_url"], "invoice", width=1100, height=1000)
            await shoot(cdp, link["url"], "payment-link", width=420, height=760, mobile=True)
    finally:
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    asyncio.run(main())
