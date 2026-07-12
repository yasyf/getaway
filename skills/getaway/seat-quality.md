# Business seat quality

The static hard-product map behind
[seat quality ranking](SKILL.md#seat-quality): what the business seat
actually is per carrier and aircraft, and whether it earns the cabin
name. Match a flight by its operating carrier (the flight-number
prefix) and the segment's `AircraftName`. Verified 2026-07-12 against
carrier seat maps, aeroLOPA layouts, and 2025–2026 cabin reviews;
retrofit programs move rows every quarter, so a Verify-marked flight
gets a `WebSearch` at plan time and a stamp more than a couple of
quarters old deserves a fresh look.

## Verdicts

| Verdict | Seat | Ranking effect |
|---|---|---|
| `suite` | Enclosed suite with a door (Qsuite, Club Suite, The Room) | Breaks near-ties upward |
| `solid` | True lie-flat with direct aisle access from every seat | None |
| `dated` | Lie-flat but dense or aging — 2-2-2 without all-aisle access, narrow old flats | Annotation only |
| `barely` | Barely business — yin-yang 2-3-2/2-4-2, 7-across flat, anything angled | Soft-demotes below every true flat; a soft airline avoid still sinks harder |

The verdict rates the longest business-cabin segment of an itinerary —
filter segments on `.Cabin` first; a narrowbody positioning leg or an
economy connector never rates a trip. A `yes` in the Verify column
marks a mix that can move the ranking — a `barely` config in play, or
configs two or more tiers apart; an adjacent-tier lottery
(suite-vs-solid, solid-vs-dated) rides in the Note instead. A Verify
row's verdict names the worse config in play: resolve the specific
flight by `WebSearch` (the carrier's seat map for that flight number
and date, recent cabin reviews), and rank it neutral when sources stay
split. Any carrier + aircraft absent from this table is unknown: rank
it neutral and resolve it during enrichment — never demote on absence.

## Verdict table

| Carrier | Aircraft | Product | Verdict | Verify | Note |
|---|---|---|---|---|---|
| British Airways | 777-200ER | Club Suite / old Club World | `barely` | yes | Heathrow frames are all Club Suite; Gatwick frames keep yin-yang Club World with no retrofit before 2029 — the base decides |
| British Airways | 777-300ER | Club Suite | `suite` | — | Retrofit complete |
| British Airways | 787-8 | Club Suite | `suite` | — | Retrofit completed early 2026 |
| British Airways | 787-9 | old Club World / Club Suite | `barely` | yes | Roughly 4–6 of 18 refitted mid-2026; the rest fly yin-yang until 2027 |
| British Airways | 787-10 | Club Suite | `suite` | — | Delivered with Club Suite only |
| British Airways | A350-1000 | Club Suite | `suite` | — | Delivered with Club Suite only |
| British Airways | A380 | old Club World | `barely` | — | The whole fleet flies yin-yang until a retrofit running mid-2026 to end-2027; re-check from 2027 |
| Qatar Airways | 777-300ER | Qsuite / old 2-2-2 / leased herringbone | `dated` | yes | About 40 of 57 frames carry Qsuite; the rest split old 2-2-2 flats and ex-lease herringbones |
| Qatar Airways | 777-200LR | Qsuite on 5 of 7 | `solid` | yes | The two non-Qsuite frames' seat is unconfirmed |
| Qatar Airways | A350-900 | Super Diamond, ~⅓ Qsuite | `solid` | — | Both configs are true flats — the lottery is suite-vs-solid |
| Qatar Airways | A350-1000 | Qsuite | `suite` | — | Fleet-wide |
| Qatar Airways | A380 | Super Diamond | `solid` | — | No Qsuite on the type |
| Qatar Airways | 787-8 | Super Diamond | `solid` | — | No Qsuite on the type |
| Qatar Airways | 787-9 | Sliding-door suite | `suite` | — | Newer non-Qsuite door product |
| Emirates | 777-300ER | old 2-3-2 / new 1-2-1 | `barely` | yes | Only ~20% refitted as of 2026; most flights still seat seven across with a middle seat |
| Emirates | 777-200LR | Pre-retrofit business | `dated` | yes | Config thin in sources; treat as dated until confirmed |
| Emirates | A380 | 1-2-1 staggered | `solid` | — | S Lounge retrofit begins August 2026 — both configs solid |
| Emirates | A350-900 | S Lounge 1-2-1 | `solid` | — | Uniform new-build |
| Etihad | 787-9 | Business Studio, newest frames door suites | `solid` | — | Version lottery, both true flats |
| Etihad | 787-10 | Business Studio | `solid` | — | |
| Etihad | A380 | Business Studio | `solid` | — | |
| Etihad | A350-1000 | Elevation door suite | `suite` | — | |
| Etihad | 777-300ER | Solstys staggered | `dated` | — | 2006-era design, all-aisle but aging |
| Etihad | A321LR | Opera 1-1 | `solid` | — | Every seat window and aisle at once |
| Japan Airlines | 787-8 | Shell Flat Neo | `barely` | — | Angled 2-2-2 — does not lie flat |
| Japan Airlines | 787-9 | Sky Suite III herringbone | `solid` | — | Door-suite refits land 2028 and later |
| Japan Airlines | 777-300ER | Sky Suite | `solid` | — | Type retiring through 2026 |
| Japan Airlines | A350-1000 | Door suites | `suite` | — | |
| ANA | 777-300ER | The Room on 10 of 13 | `solid` | — | Three frames keep the older staggered flat; The Room is a door suite |
| ANA | 787-8 | Staggered 1-2-1 | `solid` | — | |
| ANA | 787-9 | Staggered 1-2-1 | `solid` | — | The Room FX door suites arrive from August 2026 |
| Cathay Pacific | 777-300ER | Cirrus, Aria Suite on ~14 frames | `solid` | — | Both true flats; Aria adds doors, full fleet by end-2027 |
| Cathay Pacific | A350-900 / A350-1000 | Cirrus III | `solid` | — | |
| Cathay Pacific | A330 | Cirrus II / III | `solid` | — | Aria reaches the A330neos from 2026 |
| Lufthansa | A350-900 | Classic 2-2-2, Allegris on ~10 Munich frames | `dated` | — | V-foot 2-2-2 with no aisle access from window seats; Allegris frames rate solid |
| Lufthansa | 787-9 | Classic, partial Allegris | `dated` | — | Early Allegris frames had most new seats blocked from sale |
| Lufthansa | 747-8 | Classic 2-2-2, upper deck 2-2 | `dated` | — | Allegris business not before 2027 |
| Lufthansa | A340 | Classic 2-2-2 | `dated` | — | |
| Lufthansa | A330 | Classic 2-2-2 | `dated` | — | |
| Turkish Airlines | 777-300ER | 2-3-2 flat | `barely` | — | Seven across with middle seats; no aisle access from windows |
| Turkish Airlines | 787-9 | Symphony staggered 1-2-1 | `solid` | — | |
| Turkish Airlines | A350-900 | 1-2-1 | `solid` | — | Ex-Aeroflot frames carry door suites; Crystal suites arrive on new builds from end-2026 |
| Turkish Airlines | A330 | 2-2-2 flat, some angled | `barely` | yes | A documented subfleet is still angled, and swaps are common |
| United | 767-300ER / 767-400ER | Polaris | `solid` | — | |
| United | 777-200 / 777-300ER | Polaris | `solid` | — | The no-Polaris 777-200 subfleet flies domestic only |
| United | 787-8 / 787-9 / 787-10 | Polaris | `solid` | — | New SFO-based 787-9s add door suites and Polaris Studio from March 2026 |
| United | 757-200 | 2-2 flat | `dated` | — | Transatlantic; no aisle access from the window; A321XLR replaces it from late 2026 |
| Air France | 777-300ER | Cirrus, ~⅓ refitted to door suites | `solid` | — | The lottery is suite-vs-solid |
| Air France | 777-200ER | Cirrus | `solid` | — | Phasing out through 2026 |
| Air France | A350-900 | Door suite | `suite` | — | |
| Air France | 787-9 | Cirrus | `solid` | — | |
| Air France | A330-200 | Equinox 2-2-2 | `dated` | — | Flat but dense, sliding privacy panel only |
| KLM | 777-200ER / 777-300ER | New World Business Class door suites | `suite` | — | Standardized across the 777s by 2026 |
| KLM | 787-9 / 787-10 | Venture 1-2-1 | `solid` | — | |
| KLM | A330-200 / A330-300 | Diamond 2-2-2 diagonal | `dated` | — | No retrofit announced |
| Virgin Atlantic | A350-1000 | Upper Class 1-2-1 | `solid` | — | No door |
| Virgin Atlantic | A330-900 | Upper Class with doors, plus Retreat Suites | `suite` | — | |
| Virgin Atlantic | 787-9 | UCS herringbone | `dated` | — | Aging; refit not before 2028 |
| Virgin Atlantic | A330-300 | UCS herringbone | `dated` | — | Nearly retired |
| Delta | A350-900 / A330-900 | Delta One Suite | `suite` | — | |
| Delta | A330-200 / A330-300 | Herringbone, no door | `solid` | — | Door retrofit expected from 2027 |
| Delta | 767-300ER | Old Delta One flat | `dated` | — | Narrow and aging |
| Delta | 767-400ER | Refreshed Delta One | `solid` | — | Partial partitions, no door |
| American | 777-300ER | Super Diamond era, Flagship Suite retrofit from Feb 2026 | `solid` | — | Retrofit completes 2027; refitted frames rate suite |
| American | 777-200ER / 787-8 / 787-9 | Super Diamond / Concept D | `solid` | — | The 787-8's Concept D is the tightest of the three |
| American | 787-9P | Flagship Suite | `suite` | — | High-premium frames delivered since 2024 |
| Ethiopian | 787-8 | Cloud Nine 2-2-2, early frames angled | `barely` | yes | The first ten delivered angled; later frames flat but still 2-2-2 — swaps are random |
| Ethiopian | 787-9 | Mixed 1-2-1 and 2-2-2 | `dated` | — | Notorious for last-minute swaps onto the worse config |
| Ethiopian | 777-200LR / 777-300ER | 2-3-2 flat | `barely` | — | Middle seats, no aisle access from windows |
| Ethiopian | A350-900 | 2-2-2, some angled | `barely` | yes | The A350-1000 is the good one; type swaps happen |
| Ethiopian | A350-1000 | Optima 1-2-1 | `solid` | — | Swap risk to A350-900 configs |
| EgyptAir | 787-9 | Super Diamond with doors | `suite` | — | |
| EgyptAir | 777-300ER | Angle-flat 2-3-2 | `barely` | — | |
| EVA Air | 777-300ER | Royal Laurel herringbone | `solid` | — | |
| EVA Air | 787-9 / 787-10 | Royal Laurel staggered | `solid` | — | |
| EVA Air | A330-300 | 2-2-2, some angled | `barely` | yes | Regional Asia routes only |
| Singapore Airlines | A380 / 777-300ER / A350-900 / 787-10 | 1-2-1 | `solid` | — | Uniform across the long-haul types |
| Air Canada | 777 / 787 / A330-300 | Signature 1-2-1 herringbone | `solid` | — | The incoming 787-10s add Signature Plus door suites |

## Quirks that change the read

- The canonical `barely` is BA's old Club World: yin-yang 2-3-2/2-4-2
  where half the cabin flies backwards, legs dangle into a footwell
  between seat shells, and window seats climb over a neighbor. Any
  seat matching that shape rates `barely` regardless of carrier.
- A 2-3-2 flat is `barely` even when it lies fully flat (Turkish,
  Ethiopian, EgyptAir 777s): seven across with a middle seat is not a
  business product.
- Angled-flat anywhere is `barely` — a seat that doesn't lie flat
  doesn't count.
- Ethiopian swaps aircraft and configs days before departure; treat
  any Ethiopian verdict as provisional. It's also the shipped default
  soft `avoid_airlines` entry.
- Air France's old angled 2-3-2 leisure config (COI) is gone from
  nearly every route; if a seat map shows it, it's `barely`.
- seats.aero `AircraftName` strings are coarse ("Boeing 777-300ER") —
  they identify the type, never the sub-config. That gap is what the
  Verify column covers.

Sources: carrier seat maps and product pages,
[aeroLOPA](https://www.aerolopa.com/) layouts, and 2025–2026 cabin
reviews and retrofit reporting (One Mile at a Time, Simple Flying,
FlyerTalk fleet threads).
