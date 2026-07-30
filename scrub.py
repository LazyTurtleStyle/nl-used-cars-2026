#!/usr/bin/env python3
"""
Build a publishable, PII-scrubbed release from the local scraper databases.

Reads:  ~/marktplaats/marktplaats.db, ~/gaspendaal/gaspendaal.db
Writes: dataset/nl_used_cars.db  (SQLite, two tables)
        dataset/marktplaats_listings.csv
        dataset/gaspedaal_listings.csv

Scrubbing rules are applied to every free-text field (title, description):
emails, phone numbers, postcodes, street addresses, licence plates, VINs,
web addresses and any remaining long digit runs are replaced by placeholders.
Direct identifiers (source listing id, listing URL) are dropped entirely; a
salted hash is published instead so rows stay joinable across releases without
pointing back at a specific advert.
"""

import csv
import hashlib
import os
import re
import sqlite3
import sys

HOME = os.path.expanduser("~")
OUT_DIR = os.path.join(HOME, "car-dataset", "dataset")

# The salt is what makes listing_uid a real pseudonym. Source listing ids are
# short public numbers, so anyone holding the salt could hash candidate ids and
# map rows straight back to adverts. It therefore must NOT live in this file --
# this script is published alongside the data. Keep it outside the repo and
# reuse the same value if you want ids stable across releases.
SALT = os.environ.get("SCRUB_SALT")
if not SALT:
    sys.exit(
        "SCRUB_SALT is not set.\n"
        "  Generate one once:  python3 -c \"import secrets;print(secrets.token_hex(32))\" > ~/.scrub_salt\n"
        "  Then run:           SCRUB_SALT=$(cat ~/.scrub_salt) python3 scrub.py\n"
        "Store ~/.scrub_salt outside the repository and never commit it."
    )

# --- scrubbing ---------------------------------------------------------------

# Dates are protected before the phone pass, otherwise "01-11-2026" (an APK
# expiry date, which is useful data) looks exactly like a 10-digit NL number.
DATE = re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b")
DATE_TOKEN = "\x00DATE%d\x00"

RULES = [
    # email before URL, or the domain half gets eaten first
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]*[\w-]"), "[EMAIL]"),
    (re.compile(r"\b(?:https?://|www\.)\S+", re.I), "[URL]"),
    (re.compile(r"\b[a-z0-9][a-z0-9-]{1,}\.(?:nl|com|be|de|eu|net|org)\b", re.I), "[URL]"),
    # VIN: 17 chars, no I/O/Q
    (re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b"), "[VIN]"),
    # international form, incl. the common "+31 (0)6 41 01 23 80" layout
    (re.compile(r"(?:\+\s?31|0031)[\s\-./]*(?:\(\s?0\s?\))?[\s\-./]*\d(?:[\s\-./]*\d){7,10}"), "[PHONE]"),
    # country code whose "+" was mangled by the source encoding ("~31 6 34 ...")
    (re.compile(r"[+~*]?\s?\b31[\s\-./]*(?:\(\s?0\s?\))?[\s\-./]*6(?:[\s\-./]*\d){8}"), "[PHONE]"),
    # bare mobile written as 6 + four pairs, the +31 having been lost entirely
    (re.compile(r"\b6(?:[\s\-.]\d{2}){4}\b"), "[PHONE]"),
    # "(0)6 34 23 17 12" where the +31 has already been stripped by the source
    (re.compile(r"\(\s?0\s?\)\s?[1-9](?:[\s\-./]*\d){7,9}"), "[PHONE]"),
    # NL phone: +31 / 0031 / 0 followed by 8-9 more digits, any separators
    (re.compile(r"(?:\+31|0031|\b0)[\s\-.]*\d(?:[\s\-.]*\d){7,9}\b"), "[PHONE]"),
    # A mobile prefix with too few digits to be a full number is a phone that
    # the scraper's 200-char truncation cut in half ("bel dan gerust 06222").
    (re.compile(r"\b06[\s\-.]?\d{2,8}\b"), "[PHONE]"),
    # postcode with no space (5234ga) is never anything else
    (re.compile(r"\b[1-9]\d{3}[a-z]{2}\b", re.I), "[POSTCODE]"),
    # street + house number
    (re.compile(
        r"\b[a-zà-ÿ']{2,}(?:straatweg|straat|laan|weg|plein|dijk|kade|singel|pad|hof|park|steeg|baan"
        r"|dreef|gracht|hoek|wal|markt|erf|veld|berg|dam|brink|kamp|horst|akker|burg|oord|stee)"
        r"\s*\d+\s*[a-z]?\b", re.I), "[ADDRESS]"),
    # anything numeric left that is too long to be a price, year or mileage
    (re.compile(r"\b\d{8,}\b"), "[NUMBER]"),
]

_stats = {name: 0 for name in
          ["EMAIL", "URL", "VIN", "PHONE", "PLATE", "POSTCODE", "ADDRESS", "NUMBER", "NAME"]}

# A Dutch licence plate is three dash-separated groups totalling six
# alphanumerics, mixing letters and digits: hf-969-j, 70-gtg-4, hfz-65-j,
# h-939-vh all occur in the source data. The group lengths vary by sidecode,
# so the shape is validated in code rather than pinned in the pattern.
PLATE = re.compile(r"\b([a-z0-9]{1,3})-([a-z0-9]{1,3})-([a-z0-9]{1,3})\b", re.I)
KENTEKEN_FIELD = re.compile(r"kenteken\s*:\s*[a-z0-9]*(?:-[a-z0-9]*)*", re.I)

# Spaced postcodes ("1531 na") collide with "uit 2009 te koop": a build year
# followed by a short Dutch word. Redact such a pair only when the number is
# outside the plausible build-year range, or the letters are not a common word.
POSTCODE_SPACED = re.compile(r"\b([1-9]\d{3})\s([a-z]{2})\b", re.I)
NL_SHORT_WORDS = {
    "te", "en", "in", "op", "is", "om", "of", "de", "na", "me", "ex", "tm",
    "km", "pk", "nm", "kw", "er", "ze", "we", "ik", "ja", "zo", "nu", "al",
    "af", "aa", "bj", "ca", "cc", "tv", "uv", "ah", "hp", "ps", "cm", "mm",
    "ad", "as", "at", "be", "bv", "da", "do", "ed", "el", "et", "ga", "gt",
    "ie", "it", "la", "le", "li", "lp", "ma", "mi", "mt", "nl", "no", "pa",
    "re", "ri", "se", "si", "so", "st", "ti", "to", "tu", "va", "vd", "wa",
}


# Seller names have no pattern of their own, but they cluster behind a sign-off
# ("mvg g snijders", "groetjes, betti", "mijn naam is alex nanninga"). Redact the
# words following such a marker, stopping at the scraped spec boilerplate so
# "mvg g snijders algemene informatie aantal deuren: 5" only loses the name.
SIGNOFF = re.compile(
    r"\b(mvg|m\.v\.g\.?|met vriendelijke groet(?:en)?|vriendelijke groet(?:en)?|groetjes"
    r"|mijn naam is|u spreekt met|namens)\b[,:]?\s*"
    r"((?:[a-zà-ÿ'’.\-]+[ ]+){0,3}[a-zà-ÿ'’.\-]+)", re.I)

# Words that mark the start of the scraper's own field dump, never part of a name.
# Only true spec-dump terms belong here. Particles like "de" and "van" must NOT
# be stop words: "mvg klaas de reus" would then keep the surname "de reus".
BOILERPLATE = {
    "algemene", "informatie", "technische", "gegevens", "kenteken", "merk",
    "model", "apk", "tellerstand", "carrosserievorm", "aantal", "deuren",
    "brandstofsoort", "transmissie", "vermogen", "kleur", "prijs", "bouwjaar",
    "tel", "telefoon", "bel", "info", "www",
}


def _signoff(m):
    marker, tail = m.group(1), m.group(2)
    kept = []
    for word in tail.split():
        if word.strip(".,'’-").lower() in BOILERPLATE:
            break
        kept.append(word)
    if not kept:
        return m.group(0)
    _stats["NAME"] += 1
    rest = tail[len(" ".join(kept)):]
    return f"{marker} [NAME]{rest}"


def _plate(m):
    groups = m.group(1), m.group(2), m.group(3)
    plate = "".join(groups)
    if len(plate) != 6:
        return m.group(0)
    if not (any(c.isdigit() for c in plate) and any(c.isalpha() for c in plate)):
        return m.group(0)
    _stats["PLATE"] += 1
    return "[PLATE]"


def _postcode_spaced(m):
    number, letters = int(m.group(1)), m.group(2).lower()
    if 1990 <= number <= 2030 and letters in NL_SHORT_WORDS:
        return m.group(0)
    _stats["POSTCODE"] += 1
    return "[POSTCODE]"


def scrub(text):
    """Replace every direct-contact pattern in `text` with a placeholder."""
    if not text:
        return text

    dates = []

    def _stash(m):
        dates.append(m.group(0))
        return DATE_TOKEN % (len(dates) - 1)

    text = DATE.sub(_stash, text)

    for pattern, placeholder in RULES:
        text, n = pattern.subn(placeholder, text)
        if n:
            _stats[placeholder.strip("[]")] += n

    # A "kenteken:" label is always followed by a plate, so redact the whole
    # field. Ad text is truncated at 200 chars, which can cut a plate in half
    # ("kenteken: 05-jfn-") and leave a fragment the shape check would miss.
    text, n = KENTEKEN_FIELD.subn("kenteken: [PLATE]", text)
    _stats["PLATE"] += n

    text = SIGNOFF.sub(_signoff, text)
    text = PLATE.sub(_plate, text)
    text = POSTCODE_SPACED.sub(_postcode_spaced, text)

    for i, original in enumerate(dates):
        text = text.replace(DATE_TOKEN % i, original)

    return re.sub(r"\s{2,}", " ", text).strip()


def uid(source, listing_id):
    return hashlib.sha256(f"{SALT}:{source}:{listing_id}".encode()).hexdigest()[:16]


# --- extraction --------------------------------------------------------------

MARKTPLAATS_COLS = ["listing_uid", "first_seen", "last_seen", "title", "price",
                    "km", "year", "fuel", "transmission", "body", "city", "description"]

GASPEDAAL_COLS = ["listing_uid", "first_seen", "last_seen", "title", "make",
                  "model", "year", "price", "km", "fuel", "transmission",
                  "power_kw", "body_type", "color"]


def read_marktplaats(path):
    db = sqlite3.connect(path)
    rows = db.execute("""
        SELECT item_id, COALESCE(first_seen, scraped_at), scraped_at, title, price,
               km, year, fuel, transmission, body, city, description
        FROM listings ORDER BY COALESCE(first_seen, scraped_at)
    """).fetchall()
    db.close()
    for r in rows:
        yield (uid("marktplaats", r[0]), r[1], r[2], scrub(r[3]), r[4], r[5],
               r[6], r[7], r[8], r[9], r[10], scrub(r[11]))


def read_gaspedaal(path):
    db = sqlite3.connect(path)
    rows = db.execute("""
        SELECT listing_id, COALESCE(first_seen, scraped_at), scraped_at, title,
               make, model, year, price, km, fuel, transmission, power_kw,
               body_type, color
        FROM listings ORDER BY COALESCE(first_seen, scraped_at)
    """).fetchall()
    db.close()
    for r in rows:
        yield (uid("gaspedaal", r[0]), r[1], r[2], scrub(r[3])) + tuple(r[4:])


# --- output ------------------------------------------------------------------

SCHEMA = """
CREATE TABLE marktplaats_listings (
    listing_uid  TEXT PRIMARY KEY,  -- salted hash of the source listing id
    first_seen   TEXT,              -- ISO8601, first scrape that saw this advert
    last_seen    TEXT,              -- ISO8601, most recent scrape
    title        TEXT,
    price        INTEGER,           -- EUR, asking price
    km           INTEGER,           -- odometer reading
    year         INTEGER,           -- build year
    fuel         TEXT,
    transmission TEXT,
    body         TEXT,
    city         TEXT,              -- seller's city (no finer location published)
    description  TEXT               -- seller's ad text, truncated to 200 chars, PII scrubbed
);
CREATE TABLE gaspedaal_listings (
    listing_uid  TEXT PRIMARY KEY,
    first_seen   TEXT,
    last_seen    TEXT,
    title        TEXT,
    make         TEXT,
    model        TEXT,
    year         INTEGER,
    price        INTEGER,
    km           INTEGER,
    fuel         TEXT,
    transmission TEXT,
    power_kw     INTEGER,
    body_type    TEXT,
    color        TEXT
);
CREATE INDEX idx_mp_price ON marktplaats_listings(price);
CREATE INDEX idx_mp_km    ON marktplaats_listings(km);
CREATE INDEX idx_mp_year  ON marktplaats_listings(year);
CREATE INDEX idx_gp_make  ON gaspedaal_listings(make, model);
CREATE INDEX idx_gp_price ON gaspedaal_listings(price);
"""


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    mp = list(read_marktplaats(os.path.join(HOME, "marktplaats", "marktplaats.db")))
    gp = list(read_gaspedaal(os.path.join(HOME, "gaspendaal", "gaspendaal.db")))

    db_path = os.path.join(OUT_DIR, "nl_used_cars.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    out = sqlite3.connect(db_path)
    out.executescript(SCHEMA)
    out.executemany(
        "INSERT INTO marktplaats_listings VALUES (%s)" % ",".join("?" * len(MARKTPLAATS_COLS)), mp)
    out.executemany(
        "INSERT INTO gaspedaal_listings VALUES (%s)" % ",".join("?" * len(GASPEDAAL_COLS)), gp)
    out.commit()
    out.execute("VACUUM")
    out.close()

    write_csv(os.path.join(OUT_DIR, "marktplaats_listings.csv"), MARKTPLAATS_COLS, mp)
    write_csv(os.path.join(OUT_DIR, "gaspedaal_listings.csv"), GASPEDAAL_COLS, gp)

    print(f"marktplaats_listings: {len(mp):,} rows")
    print(f"gaspedaal_listings:   {len(gp):,} rows")
    print("\nredactions applied:")
    for k, v in sorted(_stats.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<9} {v:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
