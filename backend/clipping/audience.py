"""Where each account's viewers are (practitioner lesson: views outside the US, UK, Canada and Australia often pay
less or nothing). Read from the platform APIs when connected, from a screenshot of the platform's audience page
otherwise, or typed in. Ranking uses the share of Tier-1 viewers."""
from __future__ import annotations

import re

from psycopg.types.json import Jsonb

from .alerts import registry
from .core import AppError, audit, get_setting

TIER1 = {"US", "GB", "CA", "AU"}
NAMES = {
    "united states": "US", "usa": "US", "u.s.": "US", "us": "US", "america": "US", "الولايات المتحدة": "US", "أمريكا": "US", "امريكا": "US",
    "united kingdom": "GB", "uk": "GB", "gb": "GB", "great britain": "GB", "england": "GB", "المملكة المتحدة": "GB", "بريطانيا": "GB",
    "canada": "CA", "ca": "CA", "كندا": "CA", "australia": "AU", "au": "AU", "أستراليا": "AU", "استراليا": "AU",
    "saudi arabia": "SA", "sa": "SA", "السعودية": "SA", "egypt": "EG", "eg": "EG", "مصر": "EG",
    "united arab emirates": "AE", "uae": "AE", "ae": "AE", "الإمارات": "AE", "الامارات": "AE", "jordan": "JO", "jo": "JO", "الأردن": "JO", "الاردن": "JO",
    "iraq": "IQ", "العراق": "IQ", "kuwait": "KW", "الكويت": "KW", "qatar": "QA", "قطر": "QA", "morocco": "MA", "المغرب": "MA",
    "algeria": "DZ", "الجزائر": "DZ", "palestine": "PS", "فلسطين": "PS", "lebanon": "LB", "لبنان": "LB", "syria": "SY", "سوريا": "SY",
    "germany": "DE", "de": "DE", "france": "FR", "fr": "FR", "india": "IN", "in": "IN", "turkey": "TR", "türkiye": "TR", "تركيا": "TR",
    "brazil": "BR", "br": "BR", "mexico": "MX", "mx": "MX", "philippines": "PH", "ph": "PH", "indonesia": "ID", "pakistan": "PK",
    "bahrain": "BH", "البحرين": "BH", "oman": "OM", "عمان": "OM", "libya": "LY", "ليبيا": "LY", "tunisia": "TN", "تونس": "TN",
}
LINE = re.compile(r"^[ \t]*([^\d%\n]{2,40}?)[ \t]*[:\-–·]?[ \t]*(\d{1,3}(?:[.,]\d+)?)[ \t]*%", re.M)


def parse_countries(text: str) -> dict[str, float]:
    """'United States 42.1%' / 'السعودية ٣٠٪' lines from an audience page → {ISO code: percent}."""
    text = (text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٪٫", "0123456789%."))
    found: dict[str, float] = {}
    for name, pct in LINE.findall(text):
        key = NAMES.get(name.strip().lower().strip("•-* "))
        if key and key not in found:
            found[key] = float(pct.replace(",", "."))
    return found


def tier1_pct(countries: dict[str, float]) -> float:
    return round(sum(v for k, v in countries.items() if k.upper() in TIER1), 1)


def apply(conn, account_id, countries: dict[str, float], source: str) -> dict:
    if not countries:
        raise AppError("No country percentages were found. Use the audience page that lists countries with a %.", "no_countries")
    if sum(countries.values()) > 101:
        raise AppError("The percentages add up to more than 100. Check the screenshot.", "bad_countries")
    row = conn.execute(
        """update accounts set audience_countries = %s, audience_tier1_pct = %s, audience_source = %s, audience_updated_at = now()
           where id = %s returning id, platform, handle, audience_countries, audience_tier1_pct, audience_source""",
        (Jsonb(countries), tier1_pct(countries), source, account_id),
    ).fetchone()
    if row is None:
        raise AppError("Account not found.", "not_found")
    audit(conn, "account.audience", "accounts", account_id, {"source": source, "tier1_pct": float(row["audience_tier1_pct"])})
    return row


def from_api(conn, account: dict) -> dict:
    result, _ = registry.call(conn, "audience", "countries",
                              {"platform": account["platform"], "external_id": account.get("platform_account_id")},
                              account["owner_operator_id"])
    return apply(conn, account["id"], result["countries"], "api")


def refresh_all(conn) -> int:
    """Read audience countries again for accounts not typed in by hand, once every few days."""
    days = int(get_setting(conn, "audience_refresh_days"))
    due = conn.execute(
        """select * from accounts where status = 'active' and coalesce(audience_source, 'api') in ('api', 'estimate')
             and (audience_updated_at is null or audience_updated_at < now() - make_interval(days => %s))""", (days,)).fetchall()
    done = 0
    for account in due:
        try:
            from_api(conn, account)
            done += 1
        except AppError as exc:
            if getattr(exc, "code", "") not in ("no_token", "no_api"):   # a real failure: wait a full interval
                conn.execute("update accounts set audience_updated_at = now() where id = %s", (account["id"],))
    return done
