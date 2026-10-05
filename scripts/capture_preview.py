"""Records docs/preview.gif from the running app.

Expects the API, a worker and the web UI to be running against an empty
database and data directory (see README, "Running it"). It drives the real UI
with Playwright and assembles the screenshots with Pillow.

    python scripts/capture_preview.py --url http://localhost:5195
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[1]
MANUSCRIPT = ROOT / "examples" / "the-keeper.md"
WIDTH, HEIGHT = 1440, 900
SCALE = 0.62
CAPTION_H = 44


class Recorder:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.frames: list[tuple[Image.Image, int, str]] = []
        self.caption = ""

    async def shot(self, ms: int = 700) -> None:
        png = await self.page.screenshot()
        self.frames.append((Image.open(io.BytesIO(png)).convert("RGB"), ms, self.caption))

    async def while_running(self, done: str, interval: float = 0.35, limit: float = 90) -> None:
        """Takes frames until `done` (a CSS selector or JS predicate) holds."""
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            await self.shot(380)
            if await self.page.evaluate(done):
                await self.shot(1300)
                return
            await asyncio.sleep(interval)
        raise TimeoutError(done)


def compose(frames: list[tuple[Image.Image, int, str]], out: Path) -> None:
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 17)
    except OSError:
        font = ImageFont.load_default()
    rendered: list[Image.Image] = []
    durations: list[int] = []
    size = (int(WIDTH * SCALE), int(HEIGHT * SCALE))
    for image, ms, caption in frames:
        canvas = Image.new("RGB", (size[0], size[1] + CAPTION_H), (24, 24, 27))
        canvas.paste(image.resize(size, Image.Resampling.LANCZOS), (0, CAPTION_H))
        ImageDraw.Draw(canvas).text((16, 12), caption, font=font, fill=(236, 236, 236))
        quantized = canvas.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        rendered.append(quantized)
        durations.append(ms)
    rendered[0].save(
        out, save_all=True, append_images=rendered[1:], duration=durations, loop=0, optimize=True
    )


async def record(url: str) -> list[tuple[Image.Image, int, str]]:
    async with async_playwright() as p:
        chromium = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
        browser = await p.chromium.launch(executable_path=chromium)
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, color_scheme="light")
        rec = Recorder(page)

        rec.caption = "1  Import a manuscript (Markdown, text or EPUB)"
        await page.goto(url)
        await page.wait_for_selector(".dropzone")
        await rec.shot(1400)
        await page.set_input_files("input[type=file]", str(MANUSCRIPT))
        await page.wait_for_selector("[data-testid=segment]")
        await page.wait_for_timeout(300)
        await rec.shot(2200)

        rec.caption = "2  Pronunciation rules are applied before synthesis"
        await page.get_by_role("button", name="Add").click()
        await page.get_by_label("Term").fill("Siobhan")
        await page.get_by_label("Say as").fill("shi-VAWN")
        await rec.shot(1200)
        await page.get_by_role("button", name="Save rules").click()
        await page.wait_for_timeout(300)

        rec.caption = "3  Generate: segments are synthesized in a background job, progress over SSE"
        await page.get_by_role("button", name="Generate all").click()
        await rec.while_running(
            "document.querySelectorAll('[data-testid=segment][data-status=cached]').length === "
            "document.querySelectorAll('[data-testid=segment]').length"
            " && !document.querySelector('.inline-job')"
        )

        rec.caption = "4  Listen: waveform drawn from peaks computed on the server"
        await page.get_by_role("button", name="Play segment 1").click()
        for _ in range(4):
            await page.wait_for_timeout(450)
            await rec.shot(450)
        await page.get_by_role("button", name="Pause").click()

        rec.caption = "5  Edit one paragraph: only that segment loses its cached audio"
        await page.get_by_role("button", name="Chapter 2: The Boat").click()
        await page.wait_for_selector("[data-testid=segment]")
        await page.wait_for_timeout(400)
        row = page.get_by_test_id("segment").nth(1)
        await row.get_by_role("button", name="Edit").click()
        box = row.get_by_role("textbox")
        await box.fill(
            "Siobhan wrote the time in the logbook: 00:14. She wrote it again, slower, "
            "because her hand had shaken and the numbers looked like someone else's."
        )
        await rec.shot(1600)
        await row.get_by_role("button", name="Save").click()
        await page.wait_for_selector("[data-testid=segment][data-status=pending]")
        await rec.shot(1800)

        rec.caption = "6  Regenerate it: one synthesis request, everything else is a cache hit"
        await row.get_by_role("button", name="Generate").click()
        await rec.while_running(
            "!document.querySelector('[data-testid=segment][data-status=pending],"
            "[data-testid=segment][data-status=queued],[data-testid=segment][data-status=generating]')"
        )

        rec.caption = "7  Export: -18 LUFS chapter MP3s and an M4B with chapter markers"
        await page.get_by_role("button", name="Export audiobook").click()
        await rec.while_running("!!document.querySelector('.export-result')", interval=0.25)

        rec.caption = "8  Play the rendered chapter"
        await page.get_by_role("button", name="Play Chapter 2: The Boat").click()
        for _ in range(3):
            await page.wait_for_timeout(600)
            await rec.shot(600)
        await rec.shot(2200)
        await browser.close()
        return rec.frames


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:5195")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "preview.gif")
    parser.add_argument("--frames-dir", type=Path, default=None, help="also save every frame as PNG")
    args = parser.parse_args()
    frames = asyncio.run(record(args.url))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.frames_dir:
        args.frames_dir.mkdir(parents=True, exist_ok=True)
        for i, (image, _, _) in enumerate(frames):
            image.save(args.frames_dir / f"{i:03d}.png")
    compose(frames, args.out)
    print(f"{args.out} {len(frames)} frames {args.out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
