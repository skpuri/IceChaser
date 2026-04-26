"""
IceChaser Analytics — parse nginx access logs directly.
No third-party services needed. GeoIP via ip-api.com free batch API.
"""

import os
import re
import gzip
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from urllib.parse import urlparse

LOG_DIR = "/var/log/nginx"
OUTPUT_PATH = "/var/www/icechaser/data/analytics.json"
GEO_CACHE_PATH = "/var/www/icechaser/data/geo_cache.json"
GEO_BATCH_SIZE = 45
GEO_CACHE_TTL = 86400 * 7

LOG_PATTERN = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<date>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+) \S+" (?P<status>\d+) (?P<size>\d+) '
    r'"(?P<referrer>[^"]*)" "(?P<ua>[^"]*)"'
)

BOT_PATTERNS = re.compile(
    r'bot|crawl|spider|slurp|Googlebot|Bingbot|facebookexternalhit|Twitterbot|'
    r'Bytespider|SemrushBot|AhrefsBot|DotBot|MJ12bot|YandexBot|PetalBot|'
    r'python-requests|curl|wget|Go-http-client|Uptime|monitoring|HeadlessChrome',
    re.IGNORECASE
)

DEVICE_PATTERNS = {
    "iPhone": re.compile(r'iPhone'),
    "Android": re.compile(r'Android'),
    "iPad": re.compile(r'iPad'),
    "Mac": re.compile(r'Macintosh'),
    "Windows": re.compile(r'Windows NT'),
    "Linux": re.compile(r'Linux(?!.*Android)'),
}

OS_PATTERNS = {
    "iOS": re.compile(r'iPhone|iPad'),
    "Android": re.compile(r'Android(?!.*(?:iPhone|iPad))'),
    "Windows 10/11": re.compile(r'Windows NT 10|Windows NT 11'),
    "Windows 7/8": re.compile(r'Windows NT 6\.'),
    "macOS": re.compile(r'Macintosh(?!.*iPhone)'),
    "Linux": re.compile(r'Linux(?!.*Android)'),
    "Chrome OS": re.compile(r'CrOS'),
}

BROWSER_PATTERNS = {
    "Chrome": re.compile(r'Chrome/(?!.*Edg|.*OPR)'),
    "Edge": re.compile(r'Edg/'),
    "Firefox": re.compile(r'Firefox/'),
    "Safari": re.compile(r'Safari/(?!.*Chrome)'),
    "Samsung": re.compile(r'SamsungBrowser'),
    "Opera": re.compile(r'OPR/|Opera'),
}

SEARCH_ENGINES = {
    "google": "Google",
    "bing": "Bing",
    "yahoo": "Yahoo",
    "duckduckgo": "DuckDuckGo",
    "baidu": "Baidu",
    "yandex": "Yandex",
    "naver": "Naver",
    "startpage": "Startpage",
}

SOCIAL_DOMAINS = {
    "t.co": "Twitter/X",
    "twitter.com": "Twitter/X",
    "x.com": "Twitter/X",
    "facebook.com": "Facebook",
    "fb.com": "Facebook",
    "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn",
    "reddit.com": "Reddit",
    "old.reddit.com": "Reddit",
    "youtu.be": "YouTube",
    "youtube.com": "YouTube",
    "threads.net": "Threads",
    "bsky.app": "Bluesky",
    "discord.com": "Discord",
}


def parse_log_line(line):
    m = LOG_PATTERN.match(line)
    return m.groupdict() if m else None


def is_bot(ua):
    return bool(BOT_PATTERNS.search(ua))


def get_device(ua):
    for name, pattern in DEVICE_PATTERNS.items():
        if pattern.search(ua):
            return name
    return "Other"


def get_os(ua):
    for name, pattern in OS_PATTERNS.items():
        if pattern.search(ua):
            return name
    return "Other"


def get_browser(ua):
    for name, pattern in BROWSER_PATTERNS.items():
        if pattern.search(ua):
            return name
    return "Other"


def is_mobile(ua):
    return bool(re.search(r'iPhone|Android|Mobile', ua))


def classify_referrer(ref, site_host):
    if not ref or ref == '-' or not ref.strip():
        return "direct"
    try:
        parsed = urlparse(ref)
        ref_host = parsed.hostname or ""
        if not ref_host or ref_host == site_host or ref_host.endswith(site_host):
            return "internal"
        ref_lower = ref_host.lower()
        for domain, name in SOCIAL_DOMAINS.items():
            if domain in ref_lower:
                return ("social", name)
        for domain, name in SEARCH_ENGINES.items():
            if domain in ref_lower:
                return ("search", name)
        return ("referral", ref_host)
    except Exception:
        return "direct"


def load_geo_cache():
    try:
        with open(GEO_CACHE_PATH, 'r') as f:
            data = json.load(f)
        cutoff = datetime.now(timezone.utc).timestamp() - GEO_CACHE_TTL
        return {k: v for k, v in data.items() if v.get('_cached_at', 0) > cutoff}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_geo_cache(cache):
    try:
        os.makedirs(os.path.dirname(GEO_CACHE_PATH), exist_ok=True)
        with open(GEO_CACHE_PATH, 'w') as f:
            json.dump(cache, f)
    except Exception:
        pass


def lookup_geo(ips, cache):
    uncached = [ip for ip in ips if str(ip) not in cache]
    if not uncached:
        print(f"   🔍 Geo: all {len(ips)} IPs cached")
        return
    print(f"   🔍 Geo: {len(uncached)} new IPs to look up...")
    done = 0
    for i in range(0, len(uncached), GEO_BATCH_SIZE):
        batch = uncached[i:i + GEO_BATCH_SIZE]
        batch_payload = [{"query": str(ip), "fields": "query,country,countryCode,regionName,city,isp,org,status"} for ip in batch]
        try:
            resp = requests.post(
                "http://ip-api.com/batch",
                json=batch_payload,
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list):
                    for r in results:
                        ip_str = str(r.get("query", ""))
                        if not ip_str or ip_str in cache:
                            continue
                        if r.get("status") == "success":
                            cache[ip_str] = {
                                "country": r.get("country", ""),
                                "countryCode": r.get("countryCode", ""),
                                "region": r.get("regionName", ""),
                                "city": r.get("city", ""),
                                "isp": r.get("isp", ""),
                                "org": r.get("org", ""),
                                "_cached_at": datetime.now(timezone.utc).timestamp()
                            }
                        else:
                            cache[ip_str] = {"country": "Unknown", "countryCode": "??", "_cached_at": datetime.now(timezone.utc).timestamp()}
        except Exception as e:
            print(f"   ⚠ Batch failed: {e} — trying individual...")
            for ip_str in batch:
                if ip_str in cache:
                    continue
                try:
                    rv = requests.get(
                        f"http://ip-api.com/json/{ip_str}?fields=status,country,countryCode,regionName,city,isp,org",
                        timeout=5
                    )
                    if rv.status_code == 200:
                        r = rv.json()
                        if r.get("status") == "success":
                            cache[str(ip_str)] = {
                                "country": r.get("country", ""),
                                "countryCode": r.get("countryCode", ""),
                                "region": r.get("regionName", ""),
                                "city": r.get("city", ""),
                                "isp": r.get("isp", ""),
                                "org": r.get("org", ""),
                                "_cached_at": datetime.now(timezone.utc).timestamp()
                            }
                        else:
                            cache[str(ip_str)] = {"country": "Unknown", "countryCode": "??", "_cached_at": datetime.now(timezone.utc).timestamp()}
                except Exception:
                    cache[str(ip_str)] = {"country": "Unknown", "countryCode": "??", "_cached_at": datetime.now(timezone.utc).timestamp()}
                time.sleep(0.4)
        done += len(batch)
        print(f"   🔍 Geo: {done}/{len(uncached)} done")
        time.sleep(1.1)  # ip-api.com rate limit: 45req/sec for free tier


def read_log_files(days=14):
    lines = []
    log_path = os.path.join(LOG_DIR, "access.log")
    if os.path.exists(log_path):
        with open(log_path, 'r', errors='ignore') as f:
            lines.extend(f.readlines())
    for i in range(1, days + 3):
        for ext in ['', '.gz']:
            path = os.path.join(LOG_DIR, f"access.log.{i}{ext}")
            if not os.path.exists(path):
                continue
            try:
                opener = gzip.open if ext == '.gz' else open
                with opener(path, 'rt', errors='ignore') as f:
                    lines.extend(f.readlines())
            except Exception:
                continue
    return lines


def analyze(days=14, site_filter="icechaser.com"):
    print(f"📊 Analyzing IceChaser traffic ({days} days)...")
    lines = read_log_files(days)

    daily_visitors = defaultdict(set)
    daily_pageviews = defaultdict(int)
    hourly_traffic = defaultdict(int)
    pages = defaultdict(int)
    social_referrers = defaultdict(int)
    search_referrers = defaultdict(int)
    referral_domains = defaultdict(int)
    devices = defaultdict(int)
    oses = defaultdict(int)
    browsers = defaultdict(int)
    mobile_vs_desktop = {"mobile": 0, "desktop": 0}
    countries = defaultdict(int)
    cities = defaultdict(int)
    isps = defaultdict(int)
    total_requests = 0
    all_ips = set()

    for line in lines:
        if site_filter and site_filter not in line:
            continue
        parsed = parse_log_line(line)
        if not parsed:
            continue
        ua = parsed["ua"]
        if is_bot(ua):
            continue
        ip = parsed["ip"]
        path = parsed["path"]
        status = parsed["status"]
        referrer = parsed["referrer"]

        tz = ZoneInfo("America/Los_Angeles")
        try:
            dt_utc = datetime.strptime(parsed["date"].split()[0], "%d/%b/%Y:%H:%M:%S")
            dt = dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        except ValueError:
            continue

        date_str = dt.strftime("%Y-%m-%d")
        total_requests += 1
        all_ips.add(ip)
        daily_visitors[date_str].add(ip)

        is_page = not any(path.endswith(ext) for ext in [
            '.js', '.css', '.json', '.png', '.jpg', '.svg', '.ico',
            '.woff', '.woff2', '.map', '.webp', '.gif'
        ])
        if is_page and status in ('200', '301', '304'):
            daily_pageviews[date_str] += 1
            pages[path] += 1
            hourly_traffic[dt.hour] += 1

        devices[get_device(ua)] += 1
        oses[get_os(ua)] += 1
        browsers[get_browser(ua)] += 1
        if is_mobile(ua):
            mobile_vs_desktop["mobile"] += 1
        else:
            mobile_vs_desktop["desktop"] += 1

        ref_result = classify_referrer(referrer, site_filter)
        if isinstance(ref_result, tuple):
            rtype, rname = ref_result
            if rtype == "social":
                social_referrers[rname] += 1
            elif rtype == "search":
                search_referrers[rname] += 1
            elif rtype == "referral":
                referral_domains[rname] += 1

    # GeoIP
    geo_cache = load_geo_cache()
    lookup_geo(list(all_ips), geo_cache)
    save_geo_cache(geo_cache)

    for ip in all_ips:
        geo = geo_cache.get(str(ip), {})
        countries[geo.get("country", "Unknown")] += 1
        city = geo.get("city", "")
        cc = geo.get("countryCode", "")
        if city and cc and cc != "??":
            cities[f"{city}, {cc}"] += 1
        isp = geo.get("isp", "")
        if isp:
            isps[isp] += 1

    dates_sorted = sorted(daily_visitors.keys())
    total_visitors = len(all_ips)
    total_pageviews = sum(daily_pageviews.values())

    result = {
        "generated_at": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat(),
        "period_days": days,
        "summary": {
            "total_unique_visitors": total_visitors,
            "total_pageviews": total_pageviews,
            "total_requests": total_requests,
            "avg_daily_visitors": round(total_visitors / max(1, len(dates_sorted)), 1),
            "avg_daily_pageviews": round(total_pageviews / max(1, len(dates_sorted)), 1),
        },
        "daily": [{"date": d, "visitors": len(daily_visitors[d]), "pageviews": daily_pageviews[d]} for d in dates_sorted],
        "hourly": [{"hour": h, "pageviews": hourly_traffic[h]} for h in range(24)],
        "top_pages": sorted([{"path": p, "views": c} for p, c in pages.items() if c > 1], key=lambda x: -x["views"])[:25],
        "referrers": {
            "social": sorted(dict(social_referrers).items(), key=lambda x: -x[1]),
            "search": sorted(dict(search_referrers).items(), key=lambda x: -x[1]),
            "referral": sorted(dict(referral_domains).items(), key=lambda x: -x[1])[:20],
        },
        "devices": dict(sorted(devices.items(), key=lambda x: -x[1])),
        "oses": dict(sorted(oses.items(), key=lambda x: -x[1])),
        "browsers": dict(sorted(browsers.items(), key=lambda x: -x[1])),
        "mobile_vs_desktop": mobile_vs_desktop,
        "geography": {
            "countries": sorted(dict(countries).items(), key=lambda x: -x[1])[:20],
            "cities": sorted(dict(cities).items(), key=lambda x: -x[1])[:15],
            "isps": sorted(dict(isps).items(), key=lambda x: -x[1])[:10],
        },
    }
    return result


def save_analytics(data):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2)


def main():
    data = analyze(days=14, site_filter="icechaser.com")
    s = data["summary"]
    print(f"\n   {s['total_unique_visitors']} unique visitors ({s['avg_daily_visitors']}/day)")
    print(f"   {s['total_pageviews']} pageviews")
    print(f"\n   Top countries:")
    for c, n in data["geography"]["countries"][:5]:
        print(f"     {c}: {n}")
    print(f"\n   Social: {data['referrers']['social']}")
    print(f"   Search: {data['referrers']['search']}")
    save_analytics(data)
    print(f"\n   ✓ Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
