"""
IceChaser Infographic Renderer
Generates branded Twitter/Instagram infographic images for NHL teams.
"""

import json
import os
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from PIL.ImageOps import autocontrast

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR = "/var/www/icechaser/data"
LOGOS_DIR = os.path.join(DATA_DIR, "logos")
OUT_DIR = os.path.join(DATA_DIR, "infographics")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Fonts ───────────────────────────────────────────────────────────────────────
FONT_DIR = "/usr/share/fonts/truetype/liberation"
def font(name, size, bold=False):
    base = os.path.join(FONT_DIR, name)
    if bold and os.path.exists(base + "-Bold.ttf"):
        return ImageFont.truetype(base + "-Bold.ttf", size)
    if os.path.exists(base + "-Regular.ttf"):
        return ImageFont.truetype(base + "-Regular.ttf", size)
    return ImageFont.load_default()

FONT_BODY   = lambda s: font("LiberationSans-Regular", s)
FONT_BOLD   = lambda s: font("LiberationSans-Bold", s, True)
FONT_ITALIC = lambda s: font("LiberationSans-Italic", s)

# ── Team colors (NHL team primary colors) ──────────────────────────────────────
TEAM_COLORS = {
    "ANA": "#000000", "BOS": "#FFB818", "BUF": "#003087", "CAR": "#CC0000",
    "CBJ": "#002D62", "CGY": "#C8102E", "CHI": "#CF4520", "COL": "#6F263D",
    "DAL": "#006847", "DET": "#C8102E", "EDM": "#FF4C00", "FLA": "#B9975A",
    "LAK": "#111111", "MIN": "#025C38", "MTL": "#001E62", "NJD": "#CE1126",
    "NSH": "#FFB81C", "NYI": "#00539B", "NYR": "#0038A8", "OTT": "#C69214",
    "PHI": "#FA4616", "PIT": "#FCB014", "SEA": "#001425", "SJS": "#006D75",
    "STL": "#002F87", "TBL": "#00205B", "TOR": "#00205B", "UTA": "#00205B",
    "VAN": "#001851", "VGK": "#B4975A", "WPG": "#041E42", "WSH": "#041E42",
}

def team_color(abbr):
    return TEAM_COLORS.get(abbr, "#041E42")

def lighten(hex_color, amount=0.3):
    """Blend hex color toward white."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    r = int(r + (255 - r) * amount)
    g = int(g + (255 - g) * amount)
    b = int(b + (255 - b) * amount)
    return f"#{r:02X}{g:02X}{b:02X}"

def darken(hex_color, amount=0.2):
    """Darken a hex color."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    r = max(0, int(r * (1 - amount)))
    g = max(0, int(g * (1 - amount)))
    b = max(0, int(b * (1 - amount)))
    return f"#{r:02X}{g:02X}{b:02X}"

def hex_to_rgb(h):
    return (int(h[1:3],16), int(h[3:5],16), int(h[5:7],16))

# ── Logo loading ────────────────────────────────────────────────────────────────
def load_logo(abbr, size=90):
    path = os.path.join(LOGOS_DIR, f"{abbr}.png")
    if not os.path.exists(path):
        # fallback: colored circle with initials
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        color = hex_to_rgb(team_color(abbr))
        draw.ellipse([4, 4, size-4, size-4], fill=color)
        return img
    try:
        img = Image.open(path).convert("RGBA")
        img = img.resize((size, size), Image.LANCZOS)
        return img
    except Exception:
        return None

# ── Layout constants ───────────────────────────────────────────────────────────
W, H = 1200, 675
PAD = 32
LOGO_SIZE = 100

# ── Scenario infographic ────────────────────────────────────────────────────────
def render_scenario_infographic(team_data: dict, out_path: str):
    """
    Renders a full scenario infographic for a team.
    Layout: top section with team logo + name + big odds,
    bottom section split into Best Case (green) and Worst Case (red).
    """
    abbr  = team_data["teamAbbrev"]
    name  = team_data["teamName"]
    odds  = team_data["playoffOdds"]
    best  = team_data.get("best_case_tonight", odds)
    worst = team_data.get("worst_case_tonight", odds)
    scenarios = team_data.get("game_scenarios", [])
    has_game  = team_data.get("has_game_tonight", False)

    base_color = team_color(abbr)
    dark_color = darken(base_color, 0.25)
    light_color = lighten(base_color, 0.15)

    # ── Canvas ────────────────────────────────────────────────────────────────
    img = Image.new("RGB", (W, H), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    # ── Top color band ───────────────────────────────────────────────────────
    band_h = 180
    band = Image.new("RGB", (W, band_h), dark_color)
    band_draw = ImageDraw.Draw(band)
    # subtle diagonal stripe
    for i in range(0, W + H, 24):
        band_draw.line([(i, 0), (i - band_h, band_h)], fill=light_color, width=1)
    # mask to top band
    mask = Image.new("L", (W, band_h), 0)
    ImageDraw.Draw(mask).rectangle([0, 0, W, band_h], fill=255)
    img.paste(band, (0, 0), mask)

    # ── Team logo ────────────────────────────────────────────────────────────
    logo = load_logo(abbr, LOGO_SIZE)
    if logo:
        logo_x = PAD
        logo_y = (band_h - LOGO_SIZE) // 2
        if logo.mode == "RGBA":
            img.paste(logo, (logo_x, logo_y), logo)
        else:
            img.paste(logo, (logo_x, logo_y))

    # ── Team name ─────────────────────────────────────────────────────────────
    fn_big = FONT_BOLD(64)
    fn_sub = FONT_BOLD(28)
    fn_med = FONT_BOLD(22)
    fn_sm  = FONT_BODY(18)
    fn_xs  = FONT_BODY(14)

    name_x = PAD + LOGO_SIZE + 24
    draw.text((name_x, PAD + 8), name, font=fn_big, fill="white")

    # Subtitle
    subtitle = "NHL Playoff Odds — IceChaser.com"
    draw.text((name_x, PAD + 76), subtitle, font=fn_sm, fill=hex_to_rgb(lighten(base_color, 0.4)))

    # ── Big odds number (right side of top band) ──────────────────────────────
    pct_str = f"{odds:.1f}%"
    bbox = draw.textbbox((0, 0), pct_str, font=FONT_BOLD(88))
    pct_w = bbox[2] - bbox[0]
    draw.text((W - PAD - pct_w, PAD - 8), pct_str, font=FONT_BOLD(88), fill="white")
    draw.text((W - PAD - pct_w, PAD + 80), "playoff odds", font=fn_sm, fill=hex_to_rgb(lighten(base_color, 0.4)))

    # ── Impact range bar ─────────────────────────────────────────────────────
    bar_top = band_h + 20
    bar_h = 14
    bar_x = PAD
    bar_w = W - PAD * 2

    # background track
    draw.rounded_rectangle([bar_x, bar_top, bar_x + bar_w, bar_top + bar_h], radius=7, fill="#E8E8E8")
    # worst→best range bar
    # map worst/best to 0-100 range for width
    pct_range = max(best - worst, 0.1)
    bar_fill_w = int(bar_w * min(pct_range / 100.0, 1.0))
    draw.rounded_rectangle([bar_x, bar_top, bar_x + bar_fill_w, bar_top + bar_h], radius=7, fill="#CCCCCC")
    # current odds marker
    pct_marker = int(bar_w * odds / 100.0)
    marker_x = bar_x + pct_marker - 3
    draw.ellipse([marker_x, bar_top - 3, marker_x + 6, bar_top + bar_h + 3], fill=dark_color)

    # range labels
    draw.text((bar_x, bar_top + bar_h + 4), f"Worst: {worst:.1f}%", font=fn_xs, fill="#888888")
    draw.text((bar_x + bar_w - 80, bar_top + bar_h + 4), f"Best: {best:.1f}%", font=fn_xs, fill="#888888")

    # ── Bottom split: Best / Worst Case panels ───────────────────────────────
    panel_top = bar_top + bar_h + 36
    panel_h = H - panel_top - 50
    panel_w = (W - PAD * 3) // 2
    gap = PAD

    # Best case (left, green)
    best_color = "#1A7A4A"
    best_bg = lighten("#1A7A4A", 0.88)
    draw.rounded_rectangle([PAD, panel_top, PAD + panel_w, panel_top + panel_h], radius=12, fill=best_bg)
    # Green accent bar on left
    draw.rounded_rectangle([PAD, panel_top, PAD + 6, panel_top + panel_h], radius=3, fill=best_color)
    draw.text((PAD + 20, panel_top + 12), "BEST CASE TONIGHT", font=FONT_BOLD(16), fill=best_color)
    draw.text((PAD + 20, panel_top + 36), f"+{best - odds:.1f} pts above baseline", font=fn_xs, fill="#555555")

    # Worst case (right, red)
    worst_color = "#C0392B"
    worst_bg = lighten("#C0392B", 0.90)
    draw.rounded_rectangle([PAD + panel_w + gap, panel_top, W - PAD, panel_top + panel_h], radius=12, fill=worst_bg)
    draw.rounded_rectangle([PAD + panel_w + gap, panel_top, PAD + panel_w + gap + 6, panel_top + panel_h], radius=3, fill=worst_color)
    draw.text((PAD + panel_w + gap + 20, panel_top + 12), "WORST CASE TONIGHT", font=FONT_BOLD(16), fill=worst_color)
    draw.text((PAD + panel_w + gap + 20, panel_top + 36), f"{worst - odds:.1f} pts below baseline", font=fn_xs, fill="#555555")

    # Big percentage in each panel
    draw.text((PAD + 20, panel_top + 60), f"{best:.1f}%", font=FONT_BOLD(52), fill=best_color)
    draw.text((PAD + panel_w + gap + 20, panel_top + 60), f"{worst:.1f}%", font=FONT_BOLD(52), fill=worst_color)

    # ── Game rows in each panel ────────────────────────────────────────────────
    # Filter to games relevant to this team (own game or conference games)
    own_games = [s for s in scenarios if s.get("is_own_game")]
    other_games = [s for s in scenarios if not s.get("is_own_game")]
    game_rows = own_games + other_games
    game_rows = game_rows[:4]  # cap at 4 games

    row_y = panel_top + 130
    row_h = min(50, (panel_h - 130) // max(len(game_rows), 1))

    for row_idx, game in enumerate(game_rows):
        home = game["home_team"]
        away = game["away_team"]
        is_own = game.get("is_own_game", False)

        # determine which panel: own games go in both, others pick a side
        # For simplicity: show own game scenario impacts in both panels
        if row_idx % 2 == 0:
            panel_x = PAD + 20
        else:
            panel_x = PAD + panel_w + gap + 20
        row_x = panel_x

        # opponent (the team that matters for this row)
        opp = away if is_own else (home if row_idx % 2 == 0 else away)
        opp_logo = load_logo(opp, 36)
        if opp_logo:
            img.paste(opp_logo, (row_x, row_y), opp_logo)

        opp_name = game.get("away_team_name", opp) if not is_own or opp == away else game.get("home_team_name", opp)
        draw.text((row_x + 44, row_y + 6), opp, font=FONT_BOLD(16), fill="#222222")
        draw.text((row_x + 44, row_y + 26), opp_name[:18], font=fn_xs, fill="#666666")

        # scenario impact bars (simplified: show delta)
        delta_key = "away_delta_reg" if not is_own else "away_delta_reg"
        if is_own:
            home_delta = game.get("home_delta_reg", 0)
            away_delta = game.get("away_delta_reg", 0)
            delta_val = away_delta if opp == away else home_delta
        else:
            delta_val = game.get("home_delta_reg" if opp == home else "away_delta_reg", 0)

        bar_color = best_color if delta_val > 0 else worst_color
        delta_str = f"+{delta_val:.1f}" if delta_val > 0 else f"{delta_val:.1f}"
        bbox = draw.textbbox((0, 0), delta_str, font=FONT_BOLD(14))
        dw = bbox[2] - bbox[0]
        draw.text((row_x + panel_w - dw - 20, row_y + 10), delta_str, font=FONT_BOLD(14), fill=bar_color)

        row_y += row_h

    # ── IceChaser watermark ───────────────────────────────────────────────────
    watermark = "IceChaser.com | @ChaserAnalytics"
    bbox = draw.textbbox((0, 0), watermark, font=fn_xs)
    ww = bbox[2] - bbox[0]
    draw.text(((W - ww) // 2, H - 30), watermark, font=fn_xs, fill="#AAAAAA")

    # ── Save ─────────────────────────────────────────────────────────────────
    img.save(out_path, "PNG", optimize=True)
    print(f"  ✓ {abbr}: {out_path}")


# ── Big numbers infographic (for social posts) ──────────────────────────────────
def render_big_numbers_infographic(team_data: dict, out_path: str):
    """
    Simple, punchy infographic with just the big number and trend.
    Good for quick social posts.
    """
    abbr = team_data["teamAbbrev"]
    name = team_data["teamName"]
    odds = team_data["playoffOdds"]
    best = team_data.get("best_case_tonight", odds)
    worst = team_data.get("worst_case_tonight", odds)
    has_game = team_data.get("has_game_tonight", False)

    base_color = team_color(abbr)
    dark_color = darken(base_color, 0.2)

    img = Image.new("RGB", (W, H), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    # Full-bleed color band on left
    band_w = 6
    draw.rectangle([0, 0, band_w, H], fill=dark_color)

    # Background: subtle grid pattern
    for x in range(0, W, 40):
        draw.line([(x, 0), (x, H)], fill="#F5F5F5", width=1)
    for y in range(0, H, 40):
        draw.line([(0, y), (W, y)], fill="#F5F5F5", width=1)

    # Team logo (large, centered left)
    logo = load_logo(abbr, 160)
    if logo:
        img.paste(logo, (PAD + band_w + 10, H // 2 - 80), logo)

    # Big percentage
    pct_str = f"{odds:.1f}%"
    draw.text((320, H // 2 - 100), pct_str, font=FONT_BOLD(120), fill="#111111")

    # Label
    draw.text((325, H // 2 - 20), "playoff odds", font=FONT_BODY(28), fill="#555555")

    # Range
    range_color = lighten("#1A7A4A", 0.3) if odds >= 50 else lighten("#C0392B", 0.3)
    range_str = f"Tonight's range: {worst:.0f}% – {best:.0f}%"
    draw.text((325, H // 2 + 20), range_str, font=FONT_BODY(22), fill="#333333")

    # Team name
    draw.text((325, H // 2 + 60), name, font=FONT_BOLD(28), fill=dark_color)

    # Has game indicator
    if has_game:
        draw.rounded_rectangle([325, H // 2 + 100, 500, H // 2 + 130], radius=6, fill=lighten(base_color, 0.85))
        draw.text((335, H // 2 + 105), "🕐 Game tonight", font=FONT_BODY(16), fill=dark_color)

    # Watermark
    draw.text((325, H - 40), "IceChaser.com", font=FONT_BODY(16), fill="#AAAAAA")

    img.save(out_path, "PNG", optimize=True)
    print(f"  ✓ {abbr} big-numbers: {out_path}")


# ── Main ────────────────────────────────────────────────────────────────────────
def generate_all_infographics():
    data_path = os.path.join(DATA_DIR, "playoff_odds.json")
    with open(data_path) as f:
        data = json.load(f)

    teams = data.get("teams", [])
    print(f"Generating infographics for {len(teams)} teams...")

    for team in teams:
        abbr = team["teamAbbrev"]
        try:
            render_scenario_infographic(team, os.path.join(OUT_DIR, f"{abbr}_scenario.png"))
        except Exception as e:
            print(f"  ✗ {abbr} scenario: {e}")

        try:
            render_big_numbers_infographic(team, os.path.join(OUT_DIR, f"{abbr}_big.png"))
        except Exception as e:
            print(f"  ✗ {abbr} big: {e}")

    print(f"\nDone! Saved to {OUT_DIR}")


if __name__ == "__main__":
    generate_all_infographics()
