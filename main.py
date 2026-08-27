"""
╔══════════════════════════════════════════════════════════════╗
║          RALPH LAUREN VINTED SNIPER BOT v1.3                ║
║   Targets: Polo shirts, Rugby tops, Striped vintage,        ║
║            USA branded, Chief Keef city polos               ║
║   Max Price: £16  |  New listings only  |  Men's only       ║
║   Sizes: Age 14+ only  |  Max size: L  |  No women's        ║
║   Blocked: Accessories, hats, button-ups                    ║
║                                                                ║
║   NEW in v1.3: Discord reaction feedback loop                ║
║   👍 correct   🔼 rate higher   🔽 rate lower   🚫 block      ║
╚══════════════════════════════════════════════════════════════╝

HOW TO USE:
  1. pip install requests colorama discord.py
  2. Set DISCORD_WEBHOOK_URL (existing webhook — posts the snipes)
  3. Set DISCORD_BOT_TOKEN  (new — a free Discord bot token, needed
     ONLY to listen for your reaction feedback on those messages)
  4. Invite the bot to the same server/channel as the webhook, with
     "View Channel" + "Read Message History" permissions. No paid
     tier, no message content intent needed — default intents +
     reactions is enough.
  5. Run: python bot_ralph_lauren.py

FEEDBACK SYSTEM:
  Every snipe posted to Discord is tracked by its message ID along
  with which STEAL_SIGNALS keywords it matched. React to a snipe:

    👍  verdict was correct        → small reinforce (+0.2) to matched signals
    🔼  should've rated higher     → boost (+1.0) to matched signals
    🔽  should've rated lower      → penalise (-1.0) to matched signals
    🚫  should've been blocked     → zero the matched signals' weight AND
                                      add them to a personal blocklist so
                                      future listings with those keywords
                                      are filtered out entirely

  All of this is stored in feedback.json (adjusted weights + blocklist +
  a rolling history log) and reloaded on every restart and once per
  scan cycle, so your corrections compound over time. No database, no
  paid API — just your existing webhook plus a free bot token.

VERDICT LOGIC:
  🔥 FIRE  (blue  in Discord) = net profit ≥ £20  OR  cheap rugby (£1-7)  OR  city polo
  ✅ SOLID (green in Discord) = net profit ≥ £10
  ⚠️ TIGHT (amber in Discord) = net profit ≥ £4   (button-ups capped here)
  ❌ SKIP  (red   in Discord) = net profit < £4

PROFIT LOGIC (realistic UK resale margins):
  - City polo / Chief Keef (bought £5-16)  → resell eBay/Depop £50-90  → profit £30-60
  - Striped vintage polo (bought £8-16)    → resell eBay/Depop £35-60  → profit £19-44
  - Rugby top (bought £1-7)               → resell eBay/Depop £40-75  → profit £29-60 🔥
  - Rugby top (bought £8-16)              → resell eBay/Depop £40-75  → profit £20-45
  - USA branded RL (bought £8-16)         → resell eBay/Depop £30-55  → profit £14-39
  - Button-up shirt (bought £5-16)        → resell £15-25             → capped ⚠️ TIGHT
  After fees (~13% platform + postage ~£3.50): subtract ~£5-8 from above.
"""

import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta

import requests
from colorama import Fore, Style, init

init(autoreset=True)

# ─────────────────────────────────────────────
#  CONFIG — edit these before running
# ─────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_BOT_TOKEN   = os.environ.get("DISCORD_BOT_TOKEN", "")  # NEW — needed for reaction feedback
POLL_INTERVAL   = 12          # seconds between scans (don't go below 8)
MAX_PRICE_GBP   = 16.00
DOMAIN          = "www.vinted.co.uk"

FEEDBACK_FILE = Path(__file__).parent / "feedback.json"
TRACKED_MESSAGE_MAX_AGE_DAYS = 14   # prune old tracked messages so the file doesn't grow forever

# ─────────────────────────────────────────────
#  SEARCH QUERIES
# ─────────────────────────────────────────────
SEARCHES = [
    # (label,               search_text,                           priority)
    ("🎽 STRIPED POLO",    "ralph lauren striped polo vintage",    "HIGH"),
    ("🏉 RUGBY TOP",       "ralph lauren rugby top",               "HIGH"),
    ("🇺🇸 USA BRANDED",   "ralph lauren USA polo",                "HIGH"),
    ("🎤 CHIEF KEEF POLO", "ralph lauren polo shirt oversized",    "HIGH"),
    ("👔 GENERAL POLO",    "ralph lauren polo shirt",              "MED"),
    ("🧥 RL GENERAL",      "ralph lauren",                        "MED"),
]

# ─────────────────────────────────────────────
#  BLOCKED: UNDER-14 SIZES
# ─────────────────────────────────────────────
BLOCKED_SIZE_KEYWORDS = [
    "baby", "infant", "toddler", "newborn",
    "0-3", "3-6", "6-9", "6-12", "12-18", "18-24", "9-12",
    "0m", "3m", "6m", "9m", "12m", "18m",
    "5t", "4t", "3t", "2t", "1t",
    "age 1", "age 2", "age 3", "age 4", "age 5",
    "age 6", "age 7", "age 8", "age 9", "age 10",
    "age 11", "age 12", "age 13",
    "1 year", "2 year", "3 year", "4 year", "5 year",
    "6 year", "7 year", "8 year", "9 year", "10 year",
    "11 year", "12 year", "13 year",
    "1-2", "2-3", "3-4", "4-5", "5-6", "6-7", "7-8",
    "8-9", "9-10", "10-11", "11-12", "12-13", "13-14",
    "kids", "children", "child", "junior",
]

# ─────────────────────────────────────────────
#  BLOCKED: XL AND ABOVE
# ─────────────────────────────────────────────
BLOCKED_SIZE_LARGE = [
    " xl", "/xl", "-xl", "_xl",
    "xxl", "xxxl", "xxxxl",
    "2xl", "3xl", "4xl", "5xl",
    "1x", "2x", "3x", "4x", "5x",
    "extra large", "extra-large",
    "plus size", "plus-size",
]

# ─────────────────────────────────────────────
#  BLOCKED: WOMEN'S
# ─────────────────────────────────────────────
BLOCKED_WOMENS_KEYWORDS = [
    "women's", "womens", "womenswear",
    "ladies", "ladieswear",
    "for her", "for women",
]

# ─────────────────────────────────────────────
#  BLOCKED: ACCESSORIES (hats, caps, scarves, bags, belts, etc.)
# ─────────────────────────────────────────────
BLOCKED_ACCESSORY_KEYWORDS = [
    # Headwear
    "hat", "cap", "caps", "beanie", "snapback", "fitted cap",
    "baseball cap", "trucker cap", "bucket hat", "flat cap",
    "dad hat", "visor", "beret", "fedora",
    # Neck/face
    "scarf", "scarves", "snood", "bandana", "neckerchief",
    # Bags
    "bag", "tote", "backpack", "handbag", "wallet", "purse",
    "clutch", "satchel", "holdall", "duffel", "fanny pack",
    # Belts & small accessories
    "belt", "keyring", "keychain", "lanyard", "pin badge",
    # Footwear
    "shoes", "trainers", "boots", "sneakers", "loafers",
    "sandals", "slippers", "socks",
    # Jewellery / watches
    "watch", "bracelet", "necklace", "ring", "earrings",
    "cufflinks", "tie clip",
    # Other
    "sunglasses", "glasses", "umbrella", "gloves",
]

# ─────────────────────────────────────────────
#  BLOCKED: BUTTON-UP SHIRTS (lower resell value)
# ─────────────────────────────────────────────
BUTTON_UP_KEYWORDS = [
    "button up", "button-up", "button down", "button-down",
    "dress shirt", "oxford shirt", "popover shirt",
    "flannel shirt", "western shirt", "chambray",
]

# ─────────────────────────────────────────────
#  BRAND CHECK
# ─────────────────────────────────────────────
REQUIRED_BRAND_KEYWORDS = [
    "ralph lauren", "polo ralph", "rl polo", "polo rl",
]

# ─────────────────────────────────────────────
#  STEAL SIGNAL SYSTEM (BASE WEIGHTS)
#  These are the defaults. At runtime, effective weights are these
#  BASE values with any per-signal overrides from feedback.json
#  layered on top (see get_effective_signals()).
# ─────────────────────────────────────────────
STEAL_SIGNALS = {
    # ── Tier 1: Strongest signals (3pts) ──
    "big pony":       3,
    "diagonal":       3,
    "sash":           3,
    "polo cup":       3,
    "polo challenge": 3,
    "paris":          3,
    "dubai":          3,
    "london":         3,
    "new york":       3,
    "chicago":        3,
    "miami":          3,
    "tokyo":          3,
    "rome":           3,
    "barcelona":      3,
    "berlin":         3,
    "sydney":         3,
    "milan":          3,
    "atlanta":        3,
    "boston":         3,
    "shanghai":       3,
    "moscow":         3,
    "madrid":         3,

    # ── Tier 2: Strong supporting signals (2pts) ──
    "crest":          2,
    "badge":          2,
    "embroidered":    2,
    "embroidery":     2,
    "striped":        2,
    "stripe":         2,
    "colour block":   2,
    "color block":    2,
    "colourblock":    2,
    "colorblock":     2,
    "multicolour":    2,
    "multi colour":   2,
    "multi-colour":   2,
    "panel":          2,
    "flag":           2,
    "pwing":          2,
    "custom fit":     2,
    "slim fit":       2,
    "number":         2,
    "double rl":      2,
    "rrl":            2,

    # ── Tier 3: General quality signals (1pt) ──
    "rugby":          1,
    "vintage":        1,
    "usa":            1,
    "cable knit":     1,
    "made in usa":    1,
    "country":        1,
    "oversized":      1,
    "limited":        1,
    "rare":           1,
}

# Thresholds
SIGNAL_FIRE_THRESHOLD  = 5
SIGNAL_SOLID_THRESHOLD = 2

RESELL_TABLE = {
    "high_signal":  (45, 90),
    "mid_signal":   (30, 55),
    "rugby":        (40, 75),
    "double rl":    (50, 90),
    "rrl":          (50, 90),
    "cable":        (30, 55),
    "button_up":    (12, 22),
    "default":      (18, 32),
}

POSTAGE_COST  = 3.50
EBAY_FEE_RATE = 0.1269
CHEAP_RUGBY_MAX_PRICE = 7.00


# ─────────────────────────────────────────────
#  FEEDBACK SYSTEM
# ─────────────────────────────────────────────
feedback_lock = threading.Lock()

_DEFAULT_FEEDBACK = {
    "signal_weights": {},        # signal_name -> overridden weight (float)
    "blocklist_keywords": [],    # keywords added via 🚫
    "tracked_messages": {},      # message_id (str) -> {title, price, rating, matched_signals, timestamp}
    "history": [],               # rolling log of every reaction processed
}


def load_feedback() -> dict:
    with feedback_lock:
        if not FEEDBACK_FILE.exists():
            return json.loads(json.dumps(_DEFAULT_FEEDBACK))
        try:
            with open(FEEDBACK_FILE, "r") as f:
                data = json.load(f)
            for key, default_val in _DEFAULT_FEEDBACK.items():
                data.setdefault(key, json.loads(json.dumps(default_val)))
            return data
        except Exception as e:
            print(f"{Fore.RED}[FEEDBACK] Failed to load feedback.json: {e} — starting fresh")
            return json.loads(json.dumps(_DEFAULT_FEEDBACK))


def save_feedback(data: dict) -> None:
    with feedback_lock:
        try:
            with open(FEEDBACK_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"{Fore.RED}[FEEDBACK] Failed to save feedback.json: {e}")


def prune_tracked_messages(data: dict) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=TRACKED_MESSAGE_MAX_AGE_DAYS)
    tracked = data.get("tracked_messages", {})
    kept = {}
    for msg_id, info in tracked.items():
        try:
            ts = datetime.fromisoformat(info.get("timestamp", ""))
        except Exception:
            continue
        if ts >= cutoff:
            kept[msg_id] = info
    data["tracked_messages"] = kept
    return data


# In-memory cache, refreshed once per scan cycle so we're not hitting
# disk on every single item during scoring.
_cached_weights = dict(STEAL_SIGNALS)
_cached_blocklist = []


def refresh_feedback_cache():
    global _cached_weights, _cached_blocklist
    data = load_feedback()
    merged = dict(STEAL_SIGNALS)
    merged.update(data.get("signal_weights", {}))
    _cached_weights = merged
    _cached_blocklist = data.get("blocklist_keywords", [])


def get_effective_signals() -> dict:
    return _cached_weights


def get_blocklist() -> list:
    return _cached_blocklist


def register_tracked_message(message_id: str, title: str, price: float,
                              rating: str, matched_signal_names: list):
    data = load_feedback()
    data["tracked_messages"][message_id] = {
        "title": title,
        "price": price,
        "rating": rating,
        "matched_signals": matched_signal_names,
        "timestamp": datetime.utcnow().isoformat(),
    }
    data = prune_tracked_messages(data)
    save_feedback(data)


REACTION_DELTAS = {
    "👍": 0.2,
    "🔼": 1.0,
    "🔽": -1.0,
    "🚫": None,  # special-cased: zero out + blocklist
}


def apply_reaction_feedback(message_id: str, emoji: str) -> bool:
    """
    Looks up the tracked message, nudges signal weights, and (for 🚫)
    adds matched keywords to the blocklist. Returns True if a tracked
    item was found and processed.
    """
    if emoji not in REACTION_DELTAS:
        return False

    data = load_feedback()
    tracked = data["tracked_messages"].get(message_id)
    if not tracked:
        return False

    matched = tracked.get("matched_signals", [])
    weights = data.setdefault("signal_weights", {})

    if emoji == "🚫":
        for sig in matched:
            weights[sig] = 0.0
        bl = data.setdefault("blocklist_keywords", [])
        for sig in matched:
            if sig not in bl:
                bl.append(sig)
    else:
        delta = REACTION_DELTAS[emoji]
        for sig in matched:
            base = STEAL_SIGNALS.get(sig, 1)
            current = weights.get(sig, base)
            weights[sig] = round(max(0.0, current + delta), 2)

    data.setdefault("history", []).append({
        "timestamp": datetime.utcnow().isoformat(),
        "message_id": message_id,
        "title": tracked.get("title"),
        "reaction": emoji,
        "matched_signals": matched,
        "rating_at_time": tracked.get("rating"),
    })
    # keep history from growing unbounded
    data["history"] = data["history"][-2000:]

    save_feedback(data)
    refresh_feedback_cache()
    return True


# ─────────────────────────────────────────────
#  PROFIT / SIGNAL SCORING
# ─────────────────────────────────────────────
def get_price(item):
    price_data = item.get("price", 0)
    if isinstance(price_data, (int, float)):
        return float(price_data)
    if isinstance(price_data, str):
        try:
            return float(price_data)
        except ValueError:
            return 0.0
    if isinstance(price_data, dict):
        for key in ("amount", "value", "numeric"):
            if key in price_data:
                try:
                    return float(price_data[key])
                except (TypeError, ValueError):
                    pass
    return 0.0


def score_signals(title: str) -> tuple:
    """
    Scan title for steal signals using the CURRENT effective weights
    (base weights + any feedback-driven overrides), and return
    (total_score, matched_display_list, matched_raw_names).
    """
    title_lower = title.lower()
    weights = get_effective_signals()
    matched_display = []
    matched_names = []
    total = 0.0
    for signal, pts in weights.items():
        if pts <= 0:
            continue  # zeroed out via 🚫 feedback — no longer counts
        if signal in title_lower:
            matched_display.append(f"{signal}(+{pts})")
            matched_names.append(signal)
            total += pts
    return total, matched_display, matched_names


def is_blocklisted(title: str, description: str = "") -> bool:
    text = f"{title} {description}".lower()
    return any(kw in text for kw in get_blocklist())


def is_cheap_rugby(title: str, buy_price: float) -> bool:
    return "rugby" in title.lower() and buy_price <= CHEAP_RUGBY_MAX_PRICE


def is_button_up(title: str) -> bool:
    return any(kw in title.lower() for kw in BUTTON_UP_KEYWORDS)


def estimate_profit(title: str, buy_price: float, label: str) -> dict:
    title_lower = title.lower()
    signal_score, matched_display, matched_names = score_signals(title)
    btn_up  = is_button_up(title)
    rugby   = is_cheap_rugby(title, buy_price)

    if btn_up:
        resell_key = "button_up"
    elif signal_score >= SIGNAL_FIRE_THRESHOLD:
        resell_key = "high_signal"
    elif signal_score >= SIGNAL_SOLID_THRESHOLD:
        resell_key = "mid_signal"
    elif "rugby" in title_lower:
        resell_key = "rugby"
    elif "double rl" in title_lower or "rrl" in title_lower:
        resell_key = "double rl" if "double rl" in title_lower else "rrl"
    elif "cable" in title_lower:
        resell_key = "cable"
    else:
        resell_key = "default"

    low, high = RESELL_TABLE[resell_key]
    net_low  = round(low  * (1 - EBAY_FEE_RATE) - POSTAGE_COST - buy_price, 2)
    net_high = round(high * (1 - EBAY_FEE_RATE) - POSTAGE_COST - buy_price, 2)
    roi_low  = round((net_low  / buy_price) * 100) if buy_price > 0 else 0
    roi_high = round((net_high / buy_price) * 100) if buy_price > 0 else 0

    if btn_up:
        rating = "⚠️ TIGHT"
    elif signal_score >= SIGNAL_FIRE_THRESHOLD or rugby:
        rating = "🔥 FIRE"
    elif net_low >= 20 or signal_score >= SIGNAL_SOLID_THRESHOLD:
        rating = "✅ SOLID" if net_low >= 10 else "⚠️ TIGHT"
    elif net_low >= 10:
        rating = "✅ SOLID"
    elif net_low >= 4:
        rating = "⚠️ TIGHT"
    else:
        rating = "❌ SKIP"

    special_tag = ""
    if signal_score >= SIGNAL_FIRE_THRESHOLD:
        top = ", ".join(s.split("(")[0] for s in matched_display[:3])
        special_tag = f"🏆 PREMIUM PIECE — {top}"
    elif rugby:
        special_tag = "💸 CHEAP RUGBY STEAL"
    elif matched_display:
        top = ", ".join(s.split("(")[0] for s in matched_display[:2])
        special_tag = f"✨ Signals: {top}"

    return {
        "resell_low": low, "resell_high": high,
        "profit_low": net_low, "profit_high": net_high,
        "roi_low": roi_low, "roi_high": roi_high,
        "rating": rating,
        "special_tag": special_tag,
        "signal_score": signal_score,
        "matched_signals": matched_display,
        "matched_signal_names": matched_names,
    }


# ─────────────────────────────────────────────
#  VINTED API FETCH
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": f"https://{DOMAIN}/",
    "Origin":  f"https://{DOMAIN}",
}

session = requests.Session()
session.headers.update(HEADERS)
seen_ids: set = set()


def fetch_listings(search_text: str, max_price: float) -> list:
    url = (
        f"https://{DOMAIN}/api/v2/catalog/items"
        f"?search_text={requests.utils.quote(search_text)}"
        f"&price_to={max_price}"
        f"&currency=GBP"
        f"&order=newest_first"
        f"&per_page=30"
        f"&page=1"
    )
    try:
        r = session.get(url, timeout=10)
        if r.status_code == 200:
            return r.json().get("items", [])
        elif r.status_code == 401:
            print(f"{Fore.YELLOW}[AUTH] 401 received, re-fetching session cookies...")
            _refresh_session()
    except Exception as e:
        print(f"{Fore.RED}[ERROR] fetch_listings: {e}")
    return []


def _refresh_session():
    try:
        session.get(f"https://{DOMAIN}/", timeout=10)
    except Exception:
        pass


# ─────────────────────────────────────────────
#  FILTERS
# ─────────────────────────────────────────────
def is_infant_or_underage(item: dict) -> bool:
    text = " ".join([
        (item.get("title") or ""),
        (item.get("description") or ""),
        (item.get("size_title") or ""),
    ]).lower()
    return any(kw in text for kw in BLOCKED_SIZE_KEYWORDS)


def is_oversized(item: dict) -> bool:
    text = " " + (item.get("size_title") or "").lower() + " " + (item.get("title") or "").lower() + " "
    return any(kw in text for kw in BLOCKED_SIZE_LARGE)


def is_womens(item: dict) -> bool:
    dept = (item.get("department") or "").lower()
    dept_name = (item.get("department_name") or "").lower()
    if "women" in dept or "female" in dept or "women" in dept_name:
        return True
    text = " ".join([
        (item.get("title") or ""),
        (item.get("description") or ""),
        dept, dept_name,
    ]).lower()
    return any(kw in text for kw in BLOCKED_WOMENS_KEYWORDS)


def is_accessory(item: dict) -> bool:
    text = " ".join([
        (item.get("title") or ""),
        (item.get("description") or ""),
        (item.get("category_title") or ""),
    ]).lower()
    return any(kw in text for kw in BLOCKED_ACCESSORY_KEYWORDS)


def is_ralph_lauren(item: dict) -> bool:
    title = (item.get("title") or "").lower()
    brand = (item.get("brand_title") or "").lower()
    return (
        any(kw in brand for kw in REQUIRED_BRAND_KEYWORDS) or
        any(kw in title for kw in REQUIRED_BRAND_KEYWORDS)
    )


# ─────────────────────────────────────────────
#  DISCORD NOTIFICATION (webhook — with wait=true to get message id back)
# ─────────────────────────────────────────────
def send_discord(item: dict, label: str, profit: dict):
    price = get_price(item)
    title = item.get("title", "Unknown")
    url   = item.get("url") or f"https://{DOMAIN}/items/{item.get('id')}"
    photo = ""
    if item.get("photos"):
        photo = item["photos"][0].get("url") or item["photos"][0].get("full_size_url", "")

    color_map = {
        "🔥 FIRE":  0x0099FF,
        "✅ SOLID": 0x00C851,
        "⚠️ TIGHT": 0xFFBB33,
        "❌ SKIP":  0xCC0000,
    }
    embed_color = color_map.get(profit["rating"], 0x888888)

    desc_lines = [f"**{title}**", f"[🔗 View on Vinted]({url})"]
    if profit.get("special_tag"):
        desc_lines.insert(0, f"**{profit['special_tag']}**")
    desc_lines.append("\n_React 👍 correct · 🔼 rate higher · 🔽 rate lower · 🚫 should be blocked_")

    embed = {
        "title": f"{label}  |  £{price:.2f}",
        "description": "\n".join(desc_lines),
        "color": embed_color,
        "fields": [
            {"name": "💰 Buy Price",    "value": f"£{price:.2f}",                                           "inline": True},
            {"name": "📦 Resell Range", "value": f"£{profit['resell_low']}–£{profit['resell_high']} (eBay/Depop)", "inline": True},
            {"name": "📈 Net Profit",   "value": f"£{profit['profit_low']}–£{profit['profit_high']}",        "inline": True},
            {"name": "🎯 ROI",          "value": f"{profit['roi_low']}%–{profit['roi_high']}%",              "inline": True},
            {"name": "⚡ Verdict",      "value": profit["rating"],                                          "inline": True},
            {"name": "🔍 Signal Score", "value": f"{profit['signal_score']}pts — {', '.join(s.split('(')[0] for s in profit['matched_signals']) or 'none'}", "inline": False},
        ],
        "thumbnail": {"url": photo} if photo else {},
        "footer": {"text": f"Ralph Lauren Bot v1.3 • {datetime.now().strftime('%H:%M:%S')}"},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    payload = {
        "username":   "RL Sniper 🎽",
        "avatar_url": "https://i.imgur.com/4M34hi2.png",
        "embeds": [embed],
    }

    try:
        r = requests.post(f"{DISCORD_WEBHOOK_URL}?wait=true", json=payload, timeout=8)
        if r.status_code not in (200, 204):
            print(f"{Fore.YELLOW}[DISCORD] Non-200: {r.status_code}")
            return
        message_id = None
        try:
            message_id = str(r.json().get("id"))
        except Exception:
            pass

        if message_id and DISCORD_BOT_TOKEN:
            register_tracked_message(
                message_id=message_id,
                title=title,
                price=price,
                rating=profit["rating"],
                matched_signal_names=profit["matched_signal_names"],
            )
    except Exception as e:
        print(f"{Fore.RED}[DISCORD ERROR] {e}")


# ─────────────────────────────────────────────
#  DISCORD REACTION LISTENER (bot client)
# ─────────────────────────────────────────────
def start_reaction_listener():
    """
    Runs a discord.py client that only listens for raw reaction adds
    on messages we're tracking (i.e. snipes we posted). This is what
    turns your 👍🔼🔽🚫 reactions into weight adjustments.
    """
    try:
        import discord
    except ImportError:
        print(f"{Fore.RED}[FEEDBACK] discord.py not installed — run: pip install discord.py")
        print(f"{Fore.YELLOW}[FEEDBACK] Reaction feedback disabled. Scanner will still run.")
        return

    intents = discord.Intents.default()
    intents.reactions = True
    intents.guilds = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"{Fore.CYAN}[FEEDBACK] Reaction listener online as {client.user}")

    @client.event
    async def on_raw_reaction_add(payload):
        if payload.user_id == client.user.id:
            return  # ignore the bot's own reactions

        emoji_str = str(payload.emoji)
        if emoji_str not in REACTION_DELTAS:
            return  # not one of our feedback emojis

        message_id = str(payload.message_id)
        applied = apply_reaction_feedback(message_id, emoji_str)
        if applied:
            print(f"{Fore.MAGENTA}[FEEDBACK] {emoji_str} applied to message {message_id} — weights updated")
        # silently ignore reactions on messages we don't have tracked
        # (e.g. old messages from before this feature existed)

    try:
        client.run(DISCORD_BOT_TOKEN, log_handler=None)
    except Exception as e:
        print(f"{Fore.RED}[FEEDBACK] Reaction listener crashed: {e}")


# ─────────────────────────────────────────────
#  MAIN SCAN LOOP
# ─────────────────────────────────────────────
def run():
    print(f"\n{Fore.CYAN}{'═'*60}")
    print(f"{Fore.CYAN}  🎽  RALPH LAUREN VINTED SNIPER v1.3 — STARTED")
    print(f"{Fore.CYAN}  Max Price: £{MAX_PRICE_GBP}  |  Interval: {POLL_INTERVAL}s")
    print(f"{Fore.CYAN}  Filters: Men's only | Age 14+ | Max size L | No accessories")
    print(f"{Fore.CYAN}  🔥 FIRE = blue | city polos + cheap rugbies auto-FIRE")
    fb = load_feedback()
    print(f"{Fore.CYAN}  Feedback: {len(fb.get('signal_weights', {}))} weight overrides, "
          f"{len(fb.get('blocklist_keywords', []))} blocked keywords")
    print(f"{Fore.CYAN}{'═'*60}\n")

    refresh_feedback_cache()
    _refresh_session()
    cycle = 0

    while True:
        cycle += 1
        refresh_feedback_cache()  # pick up any reactions applied since last cycle
        found_this_cycle = 0
        print(f"{Fore.WHITE}[{datetime.now().strftime('%H:%M:%S')}] Cycle #{cycle} scanning {len(SEARCHES)} queries...")

        for label, search_text, priority in SEARCHES:
            items = fetch_listings(search_text, MAX_PRICE_GBP)

            for item in items:
                item_id = str(item.get("id", ""))
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                title = item.get("title") or ""
                description = item.get("description") or ""

                # ── FILTERS ──
                if is_infant_or_underage(item):
                    continue
                if is_oversized(item):
                    continue
                if is_womens(item):
                    continue
                if is_accessory(item):
                    continue
                if not is_ralph_lauren(item):
                    continue
                if is_blocklisted(title, description):
                    continue  # blocked via 🚫 feedback

                price = get_price(item)
                if price <= 0 or price > MAX_PRICE_GBP:
                    continue

                # ── SCORE & PROFIT ──
                profit = estimate_profit(title, price, label)

                found_this_cycle += 1
                verdict_color = (
                    Fore.BLUE   if "FIRE"  in profit["rating"] else
                    Fore.GREEN  if "SOLID" in profit["rating"] else
                    Fore.YELLOW
                )
                tag = f"  [{profit['special_tag']}]" if profit.get("special_tag") else ""
                print(
                    f"  {verdict_color}{profit['rating']}  {label}  "
                    f"£{price:.2f}  →  profit £{profit['profit_low']}-£{profit['profit_high']}"
                    f"{tag}  |  {title[:45]}"
                )

                if DISCORD_WEBHOOK_URL:
                    send_discord(item, label, profit)

            time.sleep(1.5)

        if found_this_cycle == 0:
            print(f"  {Fore.WHITE}No new items this cycle.")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    if not DISCORD_WEBHOOK_URL:
        print(f"{Fore.YELLOW}[WARN] DISCORD_WEBHOOK_URL not set — snipes will only print to console.")
    if not DISCORD_BOT_TOKEN:
        print(f"{Fore.YELLOW}[WARN] DISCORD_BOT_TOKEN not set — reaction feedback is disabled. "
              f"Set it to enable 👍🔼🔽🚫 learning.")

    if DISCORD_BOT_TOKEN:
        # Scanner runs in the background; the bot client owns the main thread
        # because discord.py needs to run its own asyncio event loop.
        scanner_thread = threading.Thread(target=run, daemon=True)
        scanner_thread.start()
        start_reaction_listener()
    else:
        run()
