"""Render an offline narrated walkthrough, explicitly labeled preview until acceptance."""

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import textwrap
import wave
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "var/walkthrough"
OUTPUT = ROOT / "reports/walkthrough"


def chapters(final=False):
    evaluation = json.loads((ROOT / "reports/final/evaluation.json").read_text())
    verification = json.loads((ROOT / "reports/final/verification.json").read_text())
    if final and verification.get("final_evaluation_complete") is not True:
        raise ValueError("A final walkthrough requires the independently verified completed forward week.")
    candidate = evaluation["strategies"]["candidate"]
    forward_text = (f"The completed forward candidate ended at {float(candidate['equity_usd']):.2f} dollars, with a return of {float(candidate['net_return_fraction'])*100:.3f} percent. All seven daily coverage checks passed."
                    if final else "The replacement forward run is still collecting observations. There are no completed conclusions about its investment performance. The video will be regenerated with the actual results after the full week passes.")
    return [
        {"title": "Bitcoin: a testable strategy", "body": ["DCA, conditional swing trading and cash reserves", "Public observations; simulated execution", "Operational reliability and returns evaluated separately"], "source": "README.md / docs/modeling-guide.md", "narration": "This project asks whether a configurable Bitcoin strategy improves returns and drawdown relative to simpler alternatives. We assume Bitcoin remains operational and tradable. We do not assume it is profitable. All funds and fills are simulated. The project separates historical research, an operational paper engine, and a prospective evaluation. This walkthrough follows the money and explains the evidence, including unfavorable results."},
        {"title": "Budgets are spending permissions", "body": ["Initial simulated cash: $10,000", "Bull targets: 50% DCA / 30% swing / 20% reserve", "No immediate requirement to invest 80%", "Swing is disabled in the forward experiment"], "source": "config/forward.json / paper_engine.py:_rebalance_cash", "narration": "The account starts with ten thousand dollars and zero Bitcoin. In a bullish regime, five thousand dollars is assigned to DCA, three thousand to swing, and two thousand to reserve. These are budget targets, not immediate investments. DCA spends gradually. Swing needs its own entry conditions and is disabled in this forward run, so its money stays in cash. Budget reviews consider existing holdings and pending orders before moving available cash."},
        {"title": "Features use completed observations", "body": ["Daily trend: price relative to a 200-day average", "Momentum: 30-day closing-price return", "Volatility: ATR, including gaps from prior close", "Daily context becomes available after UTC day-end"], "source": "bitcoin_agent/features.py", "narration": "Daily trend compares the closing price with its two hundred day average. Momentum measures the return over thirty days. Both positive conditions identify a bullish regime; both negative identify a bearish regime. Large daily true ranges can select a high volatility regime. Hourly indicators use only completed candles. Breakout and volume references exclude the decision candle. Missing hours are recorded rather than invented, and short hourly indicators restart after gaps."},
        {"title": "DCA: timing and spending limits", "body": ["$5,000 reference x 2% = approximately $100 per buy", "Scheduled every 24 hours, or eligible 3% dip", "Dip reference: the last actual DCA fill price", "12-hour shared cooldown; $700 rolling seven-day cap"], "source": "paper_engine.py:_entries / config/forward.json", "narration": "With a five thousand dollar DCA reference, a two percent tranche gives a purchase budget of one hundred dollars, including fees. A scheduled purchase is due approximately every twenty four hours. A completed hourly close at least three percent below the last actual DCA fill can also qualify. Both routes share a twelve hour cooldown and the same rolling spending cap. Fourteen percent of the reference allows seven hundred dollars over seven days. The engine also checks available cash, valid data and portfolio limits."},
        {"title": "Execution costs change the decision", "body": ["Buy at ask plus 0.1% modeled slippage", "Sell at bid minus 0.1% modeled slippage", "0.6% assumed fee on each fill", "Decimal amounts, reservations, rounding and partial fills"], "source": "bitcoin_agent/paper_types.py / paper_store.py", "narration": "A buy starts from the observed ask and adds adverse slippage. A sell starts from the bid and subtracts it. The simulation assumes zero point six percent fees and zero point one percent slippage on each fill. These are experimental costs, not a verified account fee tier. Decimal arithmetic and conservative rounding keep money and quantity precise. Pending buys reserve cash, and pending sells reserve inventory. Immutable fills allow an independent reconstruction of the account after a restart."},
        {"title": "Swing: signal, risk and net target", "body": ["EMA20 > EMA50; 20-hour breakout; relative volume >= 1.5", "Risk budget: 0.5% of current equity", "Stop: 1.5 ATR; net target: 2R; time exit: 72 hours", "Reject targets beyond 8 ATR; currently disabled"], "source": "features.py / paper_types.py:swing_plan", "narration": "The candidate swing entry looks for upward trend, a breakout above the previous twenty hours, and elevated volume. ATR measures movement size, not direction. Position sizing begins with a planned loss budget of half a percent of portfolio equity. The stop is one point five ATR below entry. The profit target must cover costs and twice the modeled stop loss. A target more than eight ATR away is rejected. These filters do not prove profitability, and the current forward experiment leaves swing entries disabled."},
        {"title": "The historical search can choose cash", "body": ["47 distinct profiles; 23 nonoverlapping development trials", "Train only on outcomes already complete at each fit", "Score = mean log return - 0.75 x mean drawdown", "Regime model falls back when context samples are sparse"], "source": "bitcoin_agent/research.py / config/research.json", "narration": "The offline research compares forty seven distinct profiles across allocations, dip thresholds, volatility multipliers and defensive exits. It uses twenty three nonoverlapping development episodes. Each fit sees only outcomes already complete at that date. The score rewards mean logarithmic return and subtracts a drawdown penalty. That penalty expresses a declared preference, not a universal optimum. A context model needs enough matching earlier episodes and otherwise falls back to the global ranking. Cash is a valid winner. This search is not continuously retuning the operational account."},
        {"title": "Read the favorable and unfavorable evidence", "body": ["Frozen holdout: feature allocation +14.37%; drawdown 14.66%", "Buy and hold: +9.82%; drawdown 29.48%", "Longer selector development drawdown: 72.30%", "Initial swing-only holdout: -20.64% after costs"], "source": "reports/milestone-2/review.md", "chart": "reports/milestone-2/holdout.png", "narration": "In the frozen historical holdout, feature allocation returned fourteen point three seven percent with fourteen point six six percent maximum drawdown. Buy and hold returned nine point eight two percent with twenty nine point four eight percent drawdown. However, the longer development selector reached seventy two point three percent drawdown. The initial swing rule lost twenty point six four percent in the holdout after costs. These limitations are why the historical winner was not simply promoted into live operation. The holdout is now observed and cannot become a fresh independent test for later revisions."},
        {"title": "Existing holdings need defensive exits", "body": ["New-buy BTC cap: 60%; minimum cash: 20%", "Trim exposure above 62%", "Rapid drop, confirmed bear and drawdown exits", "Capital floor halt; cooldown and distinct recovery days"], "source": "paper_engine.py:_risk_actions / docs/modeling-guide.md", "narration": "Pausing purchases alone leaves previously accumulated Bitcoin exposed. The operational policy can request exits from existing holdings. New purchases respect a sixty percent Bitcoin exposure cap and twenty percent minimum cash. Price appreciation can trigger a trim above sixty two percent exposure. Rapid decline, confirmed bearish days and portfolio drawdown can request liquidation. The original capital floor can halt the experiment. Recovery requires cooldown and distinct qualifying days. A price gap or unavailable market can still produce a worse exit than the threshold."},
        {"title": "A real forward week cannot be backfilled", "body": ["Candidate, scheduled DCA, buy-and-hold and cash books", "Identical prospective quotes and modeled costs", "Seven separate days, each with >=99% quote-age coverage", "Interrupted original window preserved as failed evidence"], "source": "reports/final/review.md / bitcoin_agent/forward.py", "narration": "The forward experiment starts fresh accounts for the candidate and three comparison books. Each receives the same prospective observations and modeled costs. All seven anchored twenty four hour days must reach at least ninety nine percent quote age coverage. The earlier window failed after a long interruption and remains archived. A separate recovery rehearsal confirmed that Task Scheduler can replace a stopped supervisor without changing balances or duplicating fills. Historical warm up and artificial tests never count as prospective days. " + forward_text},
        {"title": "Reproduce, inspect and interpret", "body": ["README: setup and reproduction commands", "Formula guide: equations and actual saved examples", "Final review: daily coverage, accounting and comparisons", "Optional integrations remain explicitly unverified"], "source": "README.md / docs/modeling-guide.md / reports/final/review.md", "narration": "The README provides setup and reproduction commands. The formula guide connects reported values to equations and saved examples. The final evaluation includes daily coverage, independent ledger reconciliation, source and configuration checksums, and strategy comparisons. Tests cover accounting, causality, restart behavior, risk controls and release gates. Real messaging, paid model calls and Docker execution remain unverified optional paths. A successful week establishes behavior under those observed conditions. It does not establish durable investment performance or a personal allocation recommendation."},
    ]


def font(size, bold=False, mono=False):
    name = "consola.ttf" if mono else "segoeuib.ttf" if bold else "segoeui.ttf"
    return ImageFont.truetype("C:/Windows/Fonts/" + name, size)


def slide(chapter, index, total, final):
    image = Image.new("RGB", (1920, 1080), "#f4f6fa")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1920, 12), fill="#215cba")
    draw.text((100, 65), "BITCOIN  /  RESEARCH TO PAPER EXECUTION", font=font(25, True), fill="#215cba")
    draw.text((100, 135), chapter["title"], font=font(59, True), fill="#122033")
    chart = chapter.get("chart")
    if chart:
        plot = Image.open(ROOT / chart).convert("RGB")
        plot.thumbnail((1720, 650), Image.Resampling.LANCZOS)
        image.paste(plot, ((1920 - plot.width) // 2, 260))
    else:
        top = 300
        for line in chapter["body"]:
            draw.rounded_rectangle((100, top, 120, top + 20), radius=5, fill="#215cba")
            wrapped = textwrap.wrap(line, width=79)
            for part in wrapped:
                draw.text((155, top - 14), part, font=font(37), fill="#24364d")
                top += 65
            top += 35
    draw.line((100, 950, 1820, 950), fill="#ccd4e1", width=2)
    draw.text((100, 973), chapter["source"], font=font(25, mono=True), fill="#4a5e78")
    label = "FINAL / OBSERVED EVIDENCE" if final else "PREVIEW / FORWARD EVALUATION IN PROGRESS"
    draw.text((100, 1020), label, font=font(23, True), fill="#215cba")
    draw.text((1740, 1017), f"{index:02d}/{total:02d}", font=font(25, True), fill="#4a5e78")
    return image


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    content = chapters(args.final)
    MEDIA.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    script = ["# Bitcoin walkthrough", "", "Status: " + ("final accepted experiment" if args.final else "preview; regenerate after the actual week passes"), "", "Narration is synthesized locally with Microsoft David Desktop. No paid API is used.", ""]
    for index, chapter in enumerate(content, 1):
        chapter["audio_path"] = str(MEDIA / f"{index:02d}.wav")
        chapter["image_path"] = str(MEDIA / f"{index:02d}.png")
        slide(chapter, index, len(content), args.final).save(chapter["image_path"])
        script += [f"## {index}. {chapter['title']}", "", chapter["narration"], "", "Source: " + chapter["source"], ""]
    (MEDIA / "chapters.json").write_text(json.dumps(content, indent=2), encoding="utf-8")
    (OUTPUT / "script.md").write_text("\n".join(script), encoding="utf-8")
    Image.open(content[0]["image_path"]).save(OUTPUT / "poster.png")
    if not args.render:
        print("Prepared narrated script and eleven slides. Use --render to create the video.")
        return
    powershell = shutil.which("pwsh")
    if powershell is None:
        raise ValueError("Install PowerShell 7 with the System.Speech assembly for offline narration.")
    subprocess.run([powershell, "-NoProfile", "-File", str(ROOT / "tools/render_walkthrough_audio.ps1"), "-ChaptersPath", str(MEDIA / "chapters.json")], check=True, timeout=180)
    sys.path.insert(0, str(ROOT / "var/media-libs"))
    import imageio_ffmpeg
    encoder = imageio_ffmpeg.get_ffmpeg_exe()
    durations, segments, subtitles, offset = [], [], [], 0.0
    for index, chapter in enumerate(content, 1):
        with wave.open(chapter["audio_path"]) as audio:
            duration = audio.getnframes() / audio.getframerate()
            if duration < 5 or audio.getnchannels() != 1:
                raise ValueError("Unexpected narration audio")
        target = MEDIA / f"segment-{index:02d}.mp4"
        subprocess.run([encoder, "-v", "error", "-y", "-loop", "1", "-framerate", "10", "-i", chapter["image_path"], "-i", chapter["audio_path"], "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-crf", "25", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest", str(target)], check=True, timeout=120)
        sentences = re.split(r"(?<=[.!?])\s+", chapter["narration"])
        weight = sum(len(s) for s in sentences)
        cursor = offset
        for sentence in sentences:
            end = cursor + duration * len(sentence) / weight
            subtitles += [str(len(subtitles) // 4 + 1), timestamp(cursor) + " --> " + timestamp(end), "\n".join(textwrap.wrap(sentence, 85)), ""]
            cursor = end
        segments.append("file '" + target.as_posix() + "'")
        durations.append(duration)
        offset += duration
        print(f"Encoded chapter {index}/{len(content)} ({duration:.1f}s)", flush=True)
    (MEDIA / "segments.txt").write_text("\n".join(segments), encoding="utf-8")
    video = OUTPUT / ("walkthrough.mp4" if args.final else "walkthrough-preview.mp4")
    subprocess.run([encoder, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(MEDIA / "segments.txt"), "-c", "copy", "-movflags", "+faststart", str(video)], check=True, timeout=120)
    subprocess.run([encoder, "-v", "error", "-i", str(video), "-f", "null", "-"], check=True, timeout=120)
    (OUTPUT / "captions.srt").write_text("\n".join(subtitles), encoding="utf-8")
    record = {"generated_at": datetime.now(timezone.utc).isoformat(), "status": "final" if args.final else "preview", "chapters": len(content), "duration_seconds": sum(durations), "resolution": "1920x1080", "fps": 10, "narration": "Offline Windows Microsoft David Desktop", "imageio_ffmpeg": "0.6.0, isolated media folder", "full_video_decode_passed": True, "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(), "video_bytes": video.stat().st_size, "uploaded": False}
    (OUTPUT / "verification.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
