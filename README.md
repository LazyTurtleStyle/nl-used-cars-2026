# Dutch Budget Used-Car Listings (2026)

26,249 used-car adverts from the Dutch market, collected daily between
**27 March and 30 July 2026** from [Marktplaats](https://www.marktplaats.nl) and
[Gaspedaal](https://www.gaspedaal.nl). Every listing is a petrol car in the
**€3,500–6,000** band — the bottom end of the Dutch used market, where public
data is thin.

All personal data has been removed. See [Privacy & scrubbing](#privacy--scrubbing).

## Files

| File | Rows | Description |
|---|---|---|
| `dataset/nl_used_cars.db` | — | SQLite, both tables, indexed |
| `dataset/marktplaats_listings.csv` | 24,671 | Marktplaats adverts, incl. seller ad text |
| `dataset/gaspedaal_listings.csv` | 1,578 | Gaspedaal adverts, richer structured fields |
| `scrub.py` | — | The script that produced this release, for auditability |

The two tables are **separate sources, not deduplicated against each other**.
A car advertised on both sites appears once per source, with different ids.

## Schema

### `marktplaats_listings` — 24,671 rows

| Column | Type | Notes |
|---|---|---|
| `listing_uid` | TEXT | Salted SHA-256 of the source listing id (pseudonym, 16 hex chars) |
| `first_seen` | TEXT | ISO 8601, first scrape that saw this advert |
| `last_seen` | TEXT | ISO 8601, most recent scrape that saw it |
| `title` | TEXT | Advert headline |
| `price` | INTEGER | Asking price in EUR (3,500–6,000; mean 5,066) |
| `km` | INTEGER | Odometer reading (mean 123,008) |
| `year` | INTEGER | Build year (2005–2022) |
| `fuel` | TEXT | Always `Benzine` — see [Limitations](#limitations) |
| `transmission` | TEXT | `Handgeschakeld` 21,931 · `Automaat` 2,671 · empty 69 |
| `body` | TEXT | Body style |
| `city` | TEXT | Seller's city — the finest location published |
| `description` | TEXT | Seller's ad text, truncated to 200 chars by the scraper, then scrubbed |

### `gaspedaal_listings` — 1,578 rows

Same identity and timestamp columns, plus structured fields Marktplaats does not
expose: `make`, `model`, `power_kw`, `color`, `body_type`. No `description`.
31 makes; most common are Peugeot (192), Fiat (169), Opel (155), Citroën (152),
Renault (142), Ford (133).

`price`, `km`, `year` and `city` have **no nulls** in either table.

## Quick start

```python
import sqlite3, pandas as pd

db = sqlite3.connect("dataset/nl_used_cars.db")
df = pd.read_sql("SELECT * FROM marktplaats_listings", db)

# depreciation curve: price against mileage, by build year
df.groupby("year").apply(lambda g: g["price"].corr(g["km"]))
```

`first_seen` and `last_seen` let you estimate **time-on-market**: an advert whose
`last_seen` stops well before the collection end date most likely sold.
Be careful at the edges — adverts still live on 2026-07-30 are right-censored,
and ones posted before 2026-04-05 are left-censored.

## Privacy & scrubbing

The raw ad text contained dealer and private-seller contact details. Publishing
that unchanged would put personal data on GitHub, so before release every
free-text field (`title`, `description`) was passed through `scrub.py`, which
replaces these patterns with placeholders:

| Placeholder | Redactions | What it was |
|---|---|---|
| `[PLATE]` | 2,232 | Dutch licence plates — personal data, they resolve to an owner via the RDW register |
| `[PHONE]` | 667 | Mobile and landline numbers, incl. `+31 (0)6` forms and numbers cut in half by truncation |
| `[POSTCODE]` | 253 | Postcodes |
| `[URL]` | 188 | Websites and domains |
| `[EMAIL]` | 105 | Email addresses |
| `[ADDRESS]` | 105 | Street names with house numbers |
| `[REFERENCE]` | 77 | Dealer stock codes (`referentienummer: …`) — not personal data, but a direct lookup key into a dealer's inventory |
| `[NAME]` | 21 | Seller names behind a sign-off (`mvg …`, `groetjes …`, `mijn naam is …`) |
| `[NUMBER]` | 4 | Remaining digit runs too long to be a price, year or mileage |

Two identifiers were **dropped entirely** rather than scrubbed: the source
listing id and the advert URL. In their place `listing_uid` is a *salted* hash,
so rows stay joinable across releases but the original advert cannot be
recovered by hashing candidate ids.

Build years survive scrubbing intact — `uit 2009 te koop` reads like a postcode
and `01-11-2026` (an APK expiry date) reads like a phone number, so both cases
are handled explicitly.

`scrub.py` reads its salt from the `SCRUB_SALT` environment variable and refuses
to run without one. The salt is deliberately **not** in this repository: source
listing ids are short public numbers, so a published salt would let anyone hash
candidate ids and undo the pseudonymisation. Reproducing the exact `listing_uid`
values therefore requires the original salt; reproducing the *scrubbing* does
not — any salt will do.

**Business names are deliberately kept.** A dealer's trade name ("Autobedrijf
Nanninga in Winneweer") is company information, not personal data, and it is part
of what makes the dataset useful. Personal names are redacted even when they
match the trade name.

**Residual risk.** Free text cannot be scrubbed with certainty. Personal names
are only caught where a sign-off marks them; a name written mid-sentence without
one has no reliable pattern and may survive. The output was audited a second time
against a broader net than the scrubber itself uses — phone shapes, IBAN, BSN,
VIN, social handles, addresses and name markers — and everything it flagged was
either fixed or confirmed as a false positive (option codes like `(025)`, "incl.
btw", "uit 2009 te koop"). That is not the same as a guarantee. If you find
personal data in this dataset, open an issue and it will be removed.

## Limitations

- **`fuel` is constant.** Every row is `Benzine`; the scraper only ever queried
  petrol cars. The column is kept for schema clarity but carries no information.
- **Narrow price band.** €3,500–6,000 by construction. Do not read the price
  distribution as representative of the Dutch market — it is a hard filter, and
  both tails are cut off.
- **Snapshot cadence.** Adverts were scraped once daily. An advert posted and
  sold within a day may be missing entirely.
- **`km` and `year` are seller-reported** and not validated against the NAP
  odometer register. Expect some optimistic mileages.
- **Not deduplicated across sources**, and a relisted car gets a new id, so
  repeat adverts for the same physical vehicle exist.
- **Descriptions are truncated at 200 characters** by the original scraper. The
  full ad text was never stored.

## Provenance & legal

Collected for personal use — tracking a budget car purchase — and published
afterwards because the data is more useful to others than sitting on a disk.
Scraped from public listing pages at a low, daily rate.

This is a derived, aggregated dataset of factual vehicle attributes with
identifiers and contact data removed. It is not a mirror of either site, contains
no images, and links back to no adverts. Marktplaats' terms restrict bulk reuse
of their content; if you intend to use this commercially, that is your call to
make and your risk to assess. Redistribution as-is is discouraged — link here
instead, so that removal requests actually propagate.

## Licence

| What | Licence | File |
|---|---|---|
| The dataset (`dataset/`) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | `LICENSE` |
| The code (`scrub.py`) | MIT | `LICENSE-CODE` |

Use it for anything, including commercially, as long as you credit this repo.
CC 4.0 is used specifically because it grants the EU **sui generis database
right** (Section 4 of the licence) alongside copyright — the right most likely to
attach to a compiled dataset like this one. Attribution is the one condition, and
it exists for a practical reason: it keeps a path back to this repo so that
corrections and personal-data removal requests reach the data people are using.

**What this licence does not cover.** The seller-written `description` text is
authored by the individual advertisers, not by me, so it is not mine to
sublicense — the licence above covers the compilation, the structured fields and
the code. In practice these are 200-character factual ad fragments with
identifiers stripped, but if you plan to redistribute the description column at
scale, that distinction is yours to weigh.
