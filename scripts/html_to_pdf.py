"""Print the HTML report to PDF with headless Chromium (same look as the HTML)."""
import sys, asyncio
from pathlib import Path
from playwright.async_api import async_playwright
async def main(src, dst):
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1200, "height": 900})
        await pg.goto(Path(src).resolve().as_uri()); await pg.emulate_media(media="screen")
        await pg.evaluate("document.querySelectorAll('details').forEach(d=>d.open=false)")
        await pg.pdf(path=dst, format="A4", print_background=True, margin={"top":"10mm","bottom":"10mm","left":"8mm","right":"8mm"}, scale=0.72)
        await b.close()
asyncio.run(main(sys.argv[1], sys.argv[2]))
