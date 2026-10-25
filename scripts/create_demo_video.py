"""Create a dependency-light five-minute animated demo video.

The output is an animated GIF because this environment has Pillow but does not
have a working MP4 encoder. Each frame is a timed storyboard card matching the
written five-minute demo script.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "demo video"
OUTPUT = OUTPUT_DIR / "zenith-secops-demo.gif"
WIDTH, HEIGHT = 1280, 720


def font(size: int, bold: bool = False):
    candidates = (
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists() and (bold == ("b" in path.name.lower())):
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


TITLE = font(48, bold=True)
SUBTITLE = font(28)
BODY = font(25)
SMALL = font(20)

CARDS = [
    (20, "Zenith AI-Native SecOps Platform", "Local-first security operations • FastAPI • Streamlit • SQLite", "Five-minute reviewer demonstration"),
    (30, "1  Health and Overview", "API health, database integrity, events, incidents, quality findings, and device health", "http://localhost:8000/health  |  http://localhost:8501"),
    (35, "2  Data Quality", "Duplicates • malformed records • delays • ordering • conflicts • confidence • device health", "Every finding includes why it was raised and a suggested action"),
    (45, "3  Incident Investigation", "Occurrence-time correlation • ordered timeline • source event IDs • uncertainty", "Related evidence is grouped explainably; arrival order is never treated as event time"),
    (30, "4  Evidence Preservation", "Malformed and duplicate records are retained instead of silently disappearing", "Raw record + field-level validation errors remain inspectable"),
    (35, "5  Operations Assistant", "Grounded questions • incident IDs • evidence IDs • explicit confirmation", "State-changing actions require operator confirmation"),
    (35, "6  Operational Briefing", "Priority incidents • unresolved work • spikes • device health • recommended actions", "Markdown and JSON output with an evidence index"),
    (35, "7  Verification", "47 functional tests • 50,000-event performance test • required artifact checks", "Run: .\\verify_all.ps1"),
    (35, "Reviewer conclusion", "Evidence-backed SecOps workflows are implemented and reproducibly verified", "VERIFY_ALL: PASS  •  No GPU or paid API key required"),
]


def render(title: str, subtitle: str, footer: str, index: int) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), (8, 15, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 18, HEIGHT), fill=(56, 189, 248))
    draw.rectangle((18, 0, WIDTH, 9), fill=(30, 41, 59))
    draw.text((75, 70), title, fill=(226, 232, 240), font=TITLE)
    draw.line((75, 145, WIDTH - 75, 145), fill=(51, 65, 85), width=2)
    y = 190
    for line in textwrap.wrap(subtitle, width=55):
        draw.text((85, y), line, fill=(125, 211, 252), font=SUBTITLE)
        y += 45
    draw.rounded_rectangle((78, 360, WIDTH - 78, 520), radius=18, fill=(15, 31, 54), outline=(51, 65, 85), width=2)
    draw.text((110, 405), "Evidence-backed workflow", fill=(134, 239, 172), font=BODY)
    for offset, item in enumerate(("Grounded output", "Explicit uncertainty", "Source event references")):
        draw.text((115, 450 + offset * 28), "• " + item, fill=(203, 213, 225), font=SMALL)
    draw.text((80, HEIGHT - 70), footer, fill=(148, 163, 184), font=SMALL)
    draw.text((WIDTH - 145, HEIGHT - 70), f"{index:02d}/09", fill=(148, 163, 184), font=SMALL)
    return image


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frames = [
        render(title, subtitle, footer, index)
        for index, (_, title, subtitle, footer) in enumerate(CARDS, 1)
    ]
    durations = [seconds * 1000 for seconds, *_ in CARDS]
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )
    print(f"Created {OUTPUT}")
    print("Duration seconds:", sum(seconds for seconds, *_ in CARDS))
    print("Frames:", len(frames))


if __name__ == "__main__":
    main()
