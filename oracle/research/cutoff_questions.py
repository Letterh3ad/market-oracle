"""The cutoff probe's question set: three dated public events a month, each with its known
answer, the keywords a right reply contains, where it was checked, and its kind. Change VERSION
with any edit, so stored probes say which set they answered.

A question asks for what only the event itself revealed, preferably a number (a score, a vote,
a price, a toll). Never a repeat winner or result, a value already true before, a favourite, or
a name the question's description narrows to one: a lucky or inferred answer moves the measured
cutoff later and can leave no room for replays. From the default claimed cutoff on, each month
asks one question of each kind, so no single kind of guess passes a month. Months before it
only show the model knows them; the claim is the floor either way."""

from __future__ import annotations

from datetime import date

from oracle.research.cutoff import Event

VERSION = "2026-09-24.3"


def _e(id_: str, day: str, kind: str, question: str, answer: str, expect: tuple[str, ...], source: str) -> Event:
    return Event(id_, date.fromisoformat(day), question, answer, expect, source, kind)


EVENTS: tuple[Event, ...] = (
    _e("palisades", "2025-01-07", "other",
       "Which Los Angeles neighbourhood gave its name to the wildfire that began on 7 January 2025 and destroyed "
       "thousands of homes?",
       "Pacific Palisades", ("palisades",), "https://en.wikipedia.org/wiki/Palisades_Fire"),
    _e("stargate", "2025-01-21", "other",
       "What name was given to the $500 billion AI infrastructure venture of OpenAI, SoftBank and Oracle "
       "announced at the White House on 21 January 2025?",
       "Stargate", ("stargate",), "https://en.wikipedia.org/wiki/Stargate_LLC"),
    _e("deepseek", "2025-01-27", "name",
       "Which Chinese AI company's model caused Nvidia's record one-day fall in market value on 27 January 2025?",
       "DeepSeek", ("deepseek|deep seek",), "https://en.wikipedia.org/wiki/DeepSeek"),

    _e("super-bowl-lix", "2025-02-09", "number", "What was the final score of Super Bowl LIX, played on 9 February "
       "2025?", "Philadelphia Eagles 40, Kansas City Chiefs 22", ("40", "22"),
       "https://en.wikipedia.org/wiki/Super_Bowl_LIX"),
    _e("bybit", "2025-02-21", "name",
       "Which cryptocurrency exchange lost about 1.5 billion dollars of Ether in a hack on 21 February 2025?",
       "Bybit", ("bybit",), "https://en.wikipedia.org/wiki/Bybit"),
    _e("germany-2025", "2025-02-23", "name",
       "Whose party alliance won the most votes in Germany's federal election of 23 February 2025?",
       "Friedrich Merz (CDU/CSU)", ("merz",), "https://en.wikipedia.org/wiki/2025_German_federal_election"),

    _e("oscars-2025", "2025-03-02", "name", "Which film won Best Picture at the Academy Awards on 2 March 2025?",
       "Anora", ("anora",), "https://en.wikipedia.org/wiki/97th_Academy_Awards"),
    _e("carney", "2025-03-09", "name",
       "Who won the Liberal Party of Canada leadership election on 9 March 2025, succeeding Justin Trudeau?",
       "Mark Carney", ("carney",), "https://en.wikipedia.org/wiki/2025_Liberal_Party_of_Canada_leadership_election"),
    _e("wiz", "2025-03-18", "name",
       "Which cybersecurity company did Google agree to buy for about 32 billion dollars on 18 March 2025?",
       "Wiz", ("wiz",), "https://en.wikipedia.org/wiki/Wiz,_Inc."),

    _e("liberation-day", "2025-04-02", "other",
       "What name did Donald Trump give to 2 April 2025, the day he announced sweeping reciprocal tariffs?",
       "Liberation Day", ("liberation day",), "https://en.wikipedia.org/wiki/Liberation_Day_tariffs"),
    _e("pope-francis", "2025-04-21", "name", "Which pope died on 21 April 2025?",
       "Pope Francis", ("francis",), "https://en.wikipedia.org/wiki/Death_and_funeral_of_Pope_Francis"),
    _e("iberian-blackout", "2025-04-28", "other",
       "Which two countries lost power nationwide in the blackout of 28 April 2025?",
       "Spain and Portugal", ("spain", "portugal"), "https://en.wikipedia.org/wiki/2025_Iberian_Peninsula_blackout"),

    _e("leo-xiv", "2025-05-08", "other", "What papal name did the cardinal elected pope on 8 May 2025 take?",
       "Leo XIV", ("leo",), "https://en.wikipedia.org/wiki/2025_papal_conclave"),
    _e("eurovision-2025", "2025-05-17", "name",
       "Which country won the Eurovision Song Contest final in Basel on 17 May 2025?",
       "Austria (JJ, \"Wasted Love\")", ("austria|wasted love",),
       "https://en.wikipedia.org/wiki/Eurovision_Song_Contest_2025"),
    _e("romania-2025-share", "2025-05-18", "number",
       "What share of the vote, in percent, did the winner of Romania's presidential run-off on 18 May 2025 get?",
       "53.6% (Nicușor Dan)", ("53.x",), "https://en.wikipedia.org/wiki/2025_Romanian_presidential_election"),

    _e("korea-2025-share", "2025-06-03", "number",
       "What share of the vote, in percent, did the winner of South Korea's presidential election on 3 June 2025 "
       "get?",
       "49.42% (Lee Jae-myung)", ("49.x",), "https://en.wikipedia.org/wiki/2025_South_Korean_presidential_election"),
    _e("french-open-2025-women", "2025-06-07", "name",
       "Who won the French Open women's singles title on 7 June 2025?",
       "Coco Gauff", ("gauff",), "https://en.wikipedia.org/wiki/2025_French_Open_%E2%80%93_Women%27s_singles"),
    _e("rising-lion", "2025-06-13", "other",
       "What code name did Israel give the strikes on Iran it began on 13 June 2025?",
       "Operation Rising Lion", ("rising lion",), "https://en.wikipedia.org/wiki/Twelve-Day_War"),

    _e("camp-mystic", "2025-07-04", "other",
       "Which girls' summer camp on the Guadalupe River in Texas lost 27 campers and counsellors in the flash "
       "flood of 4 July 2025?",
       "Camp Mystic", ("mystic",), "https://en.wikipedia.org/wiki/Camp_Mystic"),
    _e("wimbledon-2025-women-score", "2025-07-12", "number",
       "What was the score of the Wimbledon women's singles final on 12 July 2025?",
       "6-0, 6-0 (Iga Świątek over Amanda Anisimova)", ("6-0|double bagel",),
       "https://en.wikipedia.org/wiki/2025_Wimbledon_Championships_%E2%80%93_Women%27s_singles"),
    _e("astronomer", "2025-07-16", "name",
       "Which company's chief executive was caught on the kiss cam at a Coldplay concert near Boston on "
       "16 July 2025?",
       "Astronomer (Andy Byron)", ("astronomer",),
       "https://www.bostonglobe.com/2025/07/18/arts/astronomer-coldplay-investigate-ceo/"),

    _e("antoni", "2025-08-11", "name",
       "Whom did Donald Trump name on 11 August 2025 as his pick to run the Bureau of Labor Statistics?",
       "E.J. Antoni", ("antoni",), "https://www.cnbc.com/2025/08/11/trump-antoni-bls-statistics.html"),
    _e("alaska-summit", "2025-08-15", "other",
       "In which US state did Donald Trump and Vladimir Putin hold a summit on 15 August 2025?",
       "Alaska", ("alaska",), "https://en.wikipedia.org/wiki/2025_Russia%E2%80%93United_States_summit"),
    _e("intel-stake-price", "2025-08-22", "number",
       "At what price per share did the US government agree on 22 August 2025 to buy its stake in Intel?",
       "$20.47", ("20.47",), "https://newsroom.intel.com/corporate/intel-and-trump-administration-reach-historic-"
       "agreement"),

    _e("lecornu", "2025-09-09", "name",
       "Whom did Emmanuel Macron appoint prime minister of France on 9 September 2025?",
       "Sébastien Lecornu", ("lecornu",), "https://en.wikipedia.org/wiki/S%C3%A9bastien_Lecornu"),
    _e("kirk-campus", "2025-09-10", "other",
       "At which university was the American conservative activist Charlie Kirk shot dead on 10 September 2025?",
       "Utah Valley University", ("utah valley",), "https://en.wikipedia.org/wiki/Assassination_of_Charlie_Kirk"),
    _e("fed-2025-09-vote", "2025-09-17", "number",
       "By what vote did the Federal Open Market Committee cut interest rates on 17 September 2025?",
       "11-1 (Stephen Miran dissenting)", ("11-1|eleven to one",),
       "https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm"),

    _e("nobel-peace-2025", "2025-10-10", "name", "Who was awarded the Nobel Peace Prize announced on 10 October 2025?",
       "María Corina Machado", ("machado",), "https://www.nobelprize.org/prizes/peace/2025/summary/"),
    _e("louvre-value", "2025-10-19", "number",
       "At how many million euros did Paris prosecutors value the jewels stolen from the Louvre on 19 October 2025?",
       "88 million euros", ("88",),
       "https://www.france24.com/en/live-news/20251021-louvre-heist-losses-estimated-at-88-million-euros"),
    _e("melissa", "2025-10-28", "other",
       "In which country did Hurricane Melissa make landfall as a Category 5 storm on 28 October 2025?",
       "Jamaica", ("jamaica",), "https://en.wikipedia.org/wiki/Hurricane_Melissa"),

    _e("world-series-2025", "2025-11-01", "name",
       "Which team did the Los Angeles Dodgers beat in Game 7 of the World Series on 1 November 2025?",
       "Toronto Blue Jays", ("blue jays|toronto",), "https://en.wikipedia.org/wiki/2025_World_Series_Game_7"),
    _e("shutdown-2025", "2025-11-12", "number",
       "How many days had the US federal government shutdown lasted when it ended on 12 November 2025?",
       "43 days", ("43|forty-three|forty three",),
       "https://en.wikipedia.org/wiki/2025_United_States_federal_government_shutdown"),
    _e("wang-fuk-court", "2025-11-26", "other",
       "Which Hong Kong housing estate in Tai Po was engulfed by a deadly fire on 26 November 2025?",
       "Wang Fuk Court", ("wang fuk",), "https://en.wikipedia.org/wiki/Wang_Fuk_Court_fire"),

    _e("netflix-warner", "2025-12-05", "name",
       "Which company agreed on 5 December 2025 to buy Warner Bros.' film and television studios and HBO Max?",
       "Netflix", ("netflix",), "https://about.netflix.com/en/news/netflix-to-acquire-warner-bros"),
    _e("time-poy-2025", "2025-12-11", "other",
       "Whom did Time magazine name its 2025 Person of the Year on 11 December 2025?",
       "The Architects of AI", ("architects",),
       "https://time.com/7339621/person-of-the-year-2025-ai-architects-choice/"),
    _e("chile-2025-share", "2025-12-14", "number",
       "What share of the vote did the winner of Chile's presidential run-off on 14 December 2025 receive, in "
       "percent?",
       "58.17% (José Antonio Kast)", ("58.x",), "https://en.wikipedia.org/wiki/2025_Chilean_presidential_election"),

    _e("crans-montana-toll", "2026-01-01", "number",
       "How many people died in the New Year's bar fire in Crans-Montana, Switzerland, on 1 January 2026?",
       "40, later 41", ("40|41|forty|forty-one|forty one",),
       "https://en.wikipedia.org/wiki/2026_Crans-Montana_bar_fire"),
    _e("absolute-resolve", "2026-01-03", "other",
       "What was the name of the US military operation that captured Nicolás Maduro on 3 January 2026?",
       "Operation Absolute Resolve", ("absolute resolve",),
       "https://en.wikipedia.org/wiki/2026_United_States_intervention_in_Venezuela"),
    _e("australian-open-2026-women", "2026-01-31", "name",
       "Who won the Australian Open women's singles title on 31 January 2026?",
       "Elena Rybakina", ("rybakina",),
       "https://en.wikipedia.org/wiki/2026_Australian_Open_%E2%80%93_Women%27s_singles"),

    _e("super-bowl-lx", "2026-02-08", "number",
       "What was the final score of Super Bowl LX, played on 8 February 2026?",
       "Seattle Seahawks 29, New England Patriots 13", ("29", "13"), "https://en.wikipedia.org/wiki/Super_Bowl_LX"),
    _e("portugal-2026", "2026-02-08", "name", "Who won Portugal's presidential run-off on 8 February 2026?",
       "António José Seguro", ("seguro",),
       "https://en.wikipedia.org/wiki/2026_Portuguese_presidential_election"),
    _e("tapalpa", "2026-02-22", "other",
       "In which town in Jalisco did the Mexican army kill the leader of the Jalisco New Generation Cartel on "
       "22 February 2026?",
       "Tapalpa", ("tapalpa",), "https://en.wikipedia.org/wiki/2026_Jalisco_operation"),

    _e("iris-dena", "2026-03-04", "other",
       "Which Iranian Navy frigate did the US submarine USS Charlotte sink off Sri Lanka on 4 March 2026?",
       "IRIS Dena", ("dena",), "https://en.wikipedia.org/wiki/Sinking_of_IRIS_Dena"),
    _e("gold-cup-2026-margin", "2026-03-13", "number",
       "By how many lengths did the winner of the Cheltenham Gold Cup on 13 March 2026 win?",
       "Eight lengths (Gaelic Warrior)", ("8|eight",), "https://en.wikipedia.org/wiki/2026_Cheltenham_Gold_Cup"),
    _e("best-actor-2026", "2026-03-15", "name", "Who won Best Actor at the Academy Awards on 15 March 2026?",
       "Michael B. Jordan (Sinners)", ("jordan",), "https://en.wikipedia.org/wiki/98th_Academy_Awards"),

    _e("ncaa-2026", "2026-04-06", "name",
       "Which team won the NCAA men's basketball championship game on 6 April 2026?",
       "Michigan (69-63 over UConn)", ("michigan",),
       "https://en.wikipedia.org/wiki/2026_NCAA_Division_I_men%27s_basketball_championship_game"),
    _e("artemis-ii-recovery", "2026-04-10", "other",
       "Which US Navy ship recovered the Artemis II crew after they splashed down on 10 April 2026?",
       "USS John P. Murtha", ("murtha",),
       "https://www.nasa.gov/image-article/artemis-ii-astronauts-aboard-uss-john-p-murtha/"),
    _e("hungary-2026-seats", "2026-04-12", "number",
       "How many of the 199 seats did the winning party take in Hungary's parliamentary election on 12 April 2026?",
       "141 (Tisza)", ("141",), "https://en.wikipedia.org/wiki/2026_Hungarian_parliamentary_election"),

    _e("kentucky-derby-2026", "2026-05-02", "name", "Which horse won the Kentucky Derby on 2 May 2026?",
       "Golden Tempo", ("golden tempo",), "https://en.wikipedia.org/wiki/2026_Kentucky_Derby"),
    _e("warsh-vote", "2026-05-13", "number",
       "By what vote did the US Senate confirm Kevin Warsh as Federal Reserve chair on 13 May 2026?",
       "54-45", ("54", "45"), "https://www.cnn.com/2026/05/22/economy/kevin-warsh-sworn-in-fed-chair"),
    _e("eurovision-2026-song", "2026-05-16", "other",
       "What was the title of the song that won the Eurovision Song Contest final in Vienna on 16 May 2026?",
       "\"Bangaranga\" (Dara, Bulgaria)", ("bangaranga",),
       "https://en.wikipedia.org/wiki/Eurovision_Song_Contest_2026"),

    _e("spacex-ipo", "2026-06-12", "other",
       "Under what ticker symbol did SpaceX shares start trading on Nasdaq on 12 June 2026?",
       "SPCX", ("spcx",), "https://en.wikipedia.org/wiki/Initial_public_offering_of_SpaceX"),
    _e("nba-2026", "2026-06-13", "name", "Which team won the NBA Finals, clinching the title on 13 June 2026?",
       "New York Knicks", ("knicks",), "https://en.wikipedia.org/wiki/2026_NBA_Finals"),
    _e("colombia-2026-share", "2026-06-21", "number",
       "What share of the vote, in percent, did the winner of Colombia's presidential run-off on 21 June 2026 get?",
       "49.66% (Abelardo de la Espriella)", ("49.x",),
       "https://en.wikipedia.org/wiki/2026_Colombian_presidential_election"),

    _e("google-pricerunner", "2026-07-01", "other",
       "A court in which country ordered Google on 1 July 2026 to pay about 1.5 billion dollars in damages to "
       "the price comparison site PriceRunner?",
       "Sweden (the Patent and Market Court in Stockholm)", ("sweden|swedish|stockholm",),
       "https://www.france24.com/en/live-news/20260701-swedish-court-orders-google-pay-1-46-bn-for-favouring-its-"
       "price-comparisons"),
    _e("wimbledon-2026-women", "2026-07-11", "name", "Who won the Wimbledon women's singles title on 11 July 2026?",
       "Linda Nosková", ("noskova",),
       "https://en.wikipedia.org/wiki/2026_Wimbledon_Championships_%E2%80%93_Women%27s_singles"),
    _e("fed-2026-07-vote", "2026-07-29", "number",
       "By what vote did the Federal Open Market Committee decide on interest rates on 29 July 2026?",
       "9-3 (held, with three dissents)", ("9-3|nine to three",),
       "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm"),

    _e("cleveland-sirens", "2026-08-04", "other",
       "What name did Cleveland's WNBA expansion team unveil on 4 August 2026?",
       "Cleveland Sirens", ("sirens",), "https://en.wikipedia.org/wiki/Cleveland_Sirens"),
    _e("austria-heat-record", "2026-08-04", "number",
       "What temperature, in degrees Celsius, set Austria's all-time heat record near Vienna on 4 August 2026?",
       "41 °C", ("41|40.8",), "https://www.thelocal.at/20260804/vienna-records-austrias-new-all-time-high-of-40-8c/"),
    _e("little-league-2026", "2026-08-30", "name",
       "Which team won the Little League World Series championship game on 30 August 2026?",
       "Curaçao (Pariba Little League, Willemstad)", ("curacao|willemstad|pariba",),
       "https://en.wikipedia.org/wiki/2026_Little_League_World_Series"),

    _e("kingdome", "2026-09-01", "other",
       "In which Philippine city did a 75,000-seat arena billed as the world's largest indoor arena open on "
       "1 September 2026?",
       "Davao City", ("davao",), "https://www.thestadiumbusiness.com/2026/09/01/new-75000-seat-arena-opens-in-the-"
       "philippines/"),
    _e("us-open-2026-men", "2026-09-13", "name", "Who won the US Open men's singles title on 13 September 2026?",
       "Alexander Zverev", ("zverev",), "https://en.wikipedia.org/wiki/2026_US_Open_%E2%80%93_Men%27s_singles"),
    _e("boe-2026-09-vote", "2026-09-17", "number",
       "By what vote did the Bank of England's Monetary Policy Committee leave Bank Rate unchanged on "
       "17 September 2026?",
       "6-3, with three members voting to raise it", ("6-3|six to three|six votes to three|6 votes to 3",),
       "https://www.cnbc.com/2026/09/17/bank-of-england-interest-rate-decision-fed-rate-hike-uk-inflation.html"),
)
