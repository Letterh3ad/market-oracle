"""The cutoff probe's question set: two or three dated public events a month, each with its
known answer, the keywords a right reply contains, and where it was checked. Change VERSION
with any edit, so stored probes say which set they answered.

Events were picked to be hard to guess from earlier knowledge (no scheduled holds or
foregone results), since a lucky guess moves the measured cutoff later."""

from __future__ import annotations

from datetime import date

from oracle.research.cutoff import Event

VERSION = "2026-09-24.1"
CLAIMED_CUTOFF = date(2025, 5, 20)
"""Qwen3's stated training cutoff; the set starts four months before it. The measured cutoff is
never earlier than this, whatever the model answers."""


def _e(id_: str, day: str, question: str, answer: str, expect: tuple[str, ...], source: str) -> Event:
    return Event(id_, date.fromisoformat(day), question, answer, expect, source)


EVENTS: tuple[Event, ...] = (
    _e("palisades", "2025-01-07",
       "Which Los Angeles neighbourhood gave its name to the wildfire that began on 7 January 2025 and destroyed "
       "thousands of homes?",
       "Pacific Palisades", ("palisades",), "https://en.wikipedia.org/wiki/Palisades_Fire"),
    _e("stargate", "2025-01-21",
       "What name was given to the $500 billion AI infrastructure venture of OpenAI, SoftBank and Oracle "
       "announced at the White House on 21 January 2025?",
       "Stargate", ("stargate",), "https://en.wikipedia.org/wiki/Stargate_LLC"),
    _e("deepseek", "2025-01-27",
       "Which Chinese AI company's model caused Nvidia's record one-day fall in market value on 27 January 2025?",
       "DeepSeek", ("deepseek|deep seek",), "https://en.wikipedia.org/wiki/DeepSeek"),

    _e("super-bowl-lix", "2025-02-09", "Which team won Super Bowl LIX on 9 February 2025?",
       "Philadelphia Eagles", ("eagles",), "https://en.wikipedia.org/wiki/Super_Bowl_LIX"),
    _e("bybit", "2025-02-21",
       "Which cryptocurrency exchange lost about 1.5 billion dollars of Ether in a hack on 21 February 2025?",
       "Bybit", ("bybit",), "https://en.wikipedia.org/wiki/Bybit"),
    _e("germany-2025", "2025-02-23",
       "Whose party alliance won the most votes in Germany's federal election of 23 February 2025?",
       "Friedrich Merz (CDU/CSU)", ("merz",), "https://en.wikipedia.org/wiki/2025_German_federal_election"),

    _e("oscars-2025", "2025-03-02", "Which film won Best Picture at the Academy Awards on 2 March 2025?",
       "Anora", ("anora",), "https://en.wikipedia.org/wiki/97th_Academy_Awards"),
    _e("carney", "2025-03-09",
       "Who won the Liberal Party of Canada leadership election on 9 March 2025, succeeding Justin Trudeau?",
       "Mark Carney", ("carney",), "https://en.wikipedia.org/wiki/2025_Liberal_Party_of_Canada_leadership_election"),
    _e("wiz", "2025-03-18",
       "Which cybersecurity company did Google agree to buy for about 32 billion dollars on 18 March 2025?",
       "Wiz", ("wiz",), "https://en.wikipedia.org/wiki/Wiz,_Inc."),

    _e("liberation-day", "2025-04-02",
       "What name did Donald Trump give to 2 April 2025, the day he announced sweeping reciprocal tariffs?",
       "Liberation Day", ("liberation day",), "https://en.wikipedia.org/wiki/Liberation_Day_tariffs"),
    _e("pope-francis", "2025-04-21", "Which pope died on 21 April 2025?",
       "Pope Francis", ("francis",), "https://en.wikipedia.org/wiki/Death_and_funeral_of_Pope_Francis"),
    _e("iberian-blackout", "2025-04-28", "Which two countries lost power nationwide in the blackout of 28 April 2025?",
       "Spain and Portugal", ("spain", "portugal"), "https://en.wikipedia.org/wiki/2025_Iberian_Peninsula_blackout"),

    _e("leo-xiv", "2025-05-08", "What papal name did the cardinal elected pope on 8 May 2025 take?",
       "Leo XIV", ("leo",), "https://en.wikipedia.org/wiki/2025_papal_conclave"),
    _e("moodys", "2025-05-16", "To what rating did Moody's cut the United States' credit rating on 16 May 2025?",
       "Aa1", ("aa1",), "https://www.cnbc.com/2025/05/16/moodys-downgrades-united-states-credit-rating-on-increase-"
       "in-government-debt.html"),
    _e("romania-2025", "2025-05-18", "Who won Romania's presidential run-off on 18 May 2025?",
       "Nicușor Dan", ("nicusor dan",), "https://en.wikipedia.org/wiki/2025_Romanian_presidential_election"),

    _e("korea-2025", "2025-06-03", "Who won South Korea's snap presidential election on 3 June 2025?",
       "Lee Jae-myung", ("lee jae-myung|lee jae myung|lee jaemyung",),
       "https://en.wikipedia.org/wiki/2025_South_Korean_presidential_election"),
    _e("air-india-171", "2025-06-12",
       "In which Indian city did Air India Flight 171 crash shortly after take-off on 12 June 2025?",
       "Ahmedabad", ("ahmedabad",), "https://en.wikipedia.org/wiki/Air_India_Flight_171"),
    _e("rising-lion", "2025-06-13", "What code name did Israel give the strikes on Iran it began on 13 June 2025?",
       "Operation Rising Lion", ("rising lion",), "https://en.wikipedia.org/wiki/Twelve-Day_War"),

    _e("obbba", "2025-07-04",
       "What is the popular name of the tax and spending law Donald Trump signed on 4 July 2025?",
       "One Big Beautiful Bill Act", ("big beautiful bill",), "https://en.wikipedia.org/wiki/One_Big_Beautiful_Bill_Act"),
    _e("wimbledon-2025-women", "2025-07-12", "Who won the Wimbledon women's singles title on 12 July 2025?",
       "Iga Świątek", ("swiatek",),
       "https://en.wikipedia.org/wiki/2025_Wimbledon_Championships_%E2%80%93_Women%27s_singles"),

    _e("bls-firing", "2025-08-01",
       "The head of which US statistics agency did Donald Trump fire on 1 August 2025 after a weak jobs report?",
       "The Bureau of Labor Statistics (Erika McEntarfer)", ("labor statistics|bls|mcentarfer",),
       "https://en.wikipedia.org/wiki/Erika_McEntarfer"),
    _e("alaska-summit", "2025-08-15",
       "In which US state did Donald Trump and Vladimir Putin hold a summit on 15 August 2025?",
       "Alaska", ("alaska",), "https://en.wikipedia.org/wiki/2025_Russia%E2%80%93United_States_summit"),

    _e("lecornu", "2025-09-09", "Whom did Emmanuel Macron appoint prime minister of France on 9 September 2025?",
       "Sébastien Lecornu", ("lecornu",), "https://en.wikipedia.org/wiki/S%C3%A9bastien_Lecornu"),
    _e("kirk", "2025-09-10",
       "Which American conservative activist was shot dead at Utah Valley University on 10 September 2025?",
       "Charlie Kirk", ("kirk",), "https://en.wikipedia.org/wiki/Assassination_of_Charlie_Kirk"),
    _e("fed-2025-09", "2025-09-17",
       "What target range for the federal funds rate did the US Federal Reserve set on 17 September 2025?",
       "4.00% to 4.25%", ("4-4.25",),
       "https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm"),

    _e("nobel-peace-2025", "2025-10-10", "Who was awarded the Nobel Peace Prize announced on 10 October 2025?",
       "María Corina Machado", ("machado",), "https://www.nobelprize.org/prizes/peace/2025/summary/"),
    _e("louvre", "2025-10-19",
       "Which Paris museum had French crown jewels stolen in a daylight robbery on 19 October 2025?",
       "The Louvre", ("louvre",), "https://en.wikipedia.org/wiki/2025_Louvre_robbery"),
    _e("takaichi", "2025-10-21", "Who became Japan's first female prime minister on 21 October 2025?",
       "Sanae Takaichi", ("takaichi",), "https://en.wikipedia.org/wiki/Sanae_Takaichi"),

    _e("mamdani", "2025-11-04", "Who won the New York City mayoral election on 4 November 2025?",
       "Zohran Mamdani", ("mamdani",), "https://en.wikipedia.org/wiki/2025_New_York_City_mayoral_election"),
    _e("shutdown-2025", "2025-11-12",
       "How many days had the US federal government shutdown lasted when it ended on 12 November 2025?",
       "43 days", ("43",), "https://en.wikipedia.org/wiki/2025_United_States_federal_government_shutdown"),
    _e("wang-fuk-court", "2025-11-26",
       "Which Hong Kong housing estate in Tai Po was engulfed by a deadly fire on 26 November 2025?",
       "Wang Fuk Court", ("wang fuk",), "https://en.wikipedia.org/wiki/Wang_Fuk_Court_fire"),

    _e("fed-2025-12", "2025-12-10",
       "What target range for the federal funds rate did the US Federal Reserve set on 10 December 2025?",
       "3.50% to 3.75%", ("3.5-3.75",),
       "https://www.federalreserve.gov/newsevents/pressreleases/monetary20251210a.htm"),
    _e("chile-2025", "2025-12-14", "Who won Chile's presidential run-off on 14 December 2025?",
       "José Antonio Kast", ("kast",), "https://www.npr.org/2025/12/14/nx-s1-5644074/chile-kast-right"),

    _e("crans-montana", "2026-01-01",
       "In which Swiss ski resort did a bar fire kill about 40 people on New Year's night, 1 January 2026?",
       "Crans-Montana", ("crans-montana|crans montana",), "https://en.wikipedia.org/wiki/2026"),
    _e("maduro", "2026-01-03", "Which head of state was captured by US forces in Venezuela on 3 January 2026?",
       "Nicolás Maduro", ("maduro",),
       "https://en.wikipedia.org/wiki/2026_United_States_intervention_in_Venezuela"),
    _e("adamuz", "2026-01-18", "Near which Spanish town did trains derail on 18 January 2026, killing dozens?",
       "Adamuz", ("adamuz",), "https://en.wikipedia.org/wiki/2026"),

    _e("portugal-2026", "2026-02-08", "Who won Portugal's presidential run-off on 8 February 2026?",
       "António José Seguro", ("seguro",),
       "https://en.wikipedia.org/wiki/2026_Portuguese_presidential_election"),
    _e("paramount-wbd", "2026-02-27",
       "Which company agreed on 27 February 2026 to buy all of Warner Bros. Discovery for 31 dollars a share?",
       "Paramount Skydance", ("paramount",),
       "https://www.paramount.com/press/paramount-to-acquire-warner-bros-discovery-to-form-next-generation-global-"
       "media-and-entertainment-company"),
    _e("khamenei-killed", "2026-02-28",
       "Which Iranian leader was killed in US and Israeli strikes on Tehran on 28 February 2026?",
       "Supreme Leader Ali Khamenei", ("khamenei",), "https://en.wikipedia.org/wiki/2026"),

    _e("mojtaba", "2026-03-08",
       "Who did Iran's Assembly of Experts name as the country's new Supreme Leader on 8 March 2026?",
       "Mojtaba Khamenei", ("mojtaba",), "https://en.wikipedia.org/wiki/2026_Iranian_supreme_leader_election"),
    _e("oscars-2026", "2026-03-15", "Which film won Best Picture at the Academy Awards on 15 March 2026?",
       "One Battle After Another", ("one battle after another",),
       "https://en.wikipedia.org/wiki/98th_Academy_Awards"),

    _e("hungary-2026", "2026-04-12",
       "Whose party won Hungary's parliamentary election on 12 April 2026, ending Viktor Orbán's time in power?",
       "Péter Magyar (Tisza)", ("magyar|tisza",),
       "https://en.wikipedia.org/wiki/2026_Hungarian_parliamentary_election"),
    _e("masters-2026", "2026-04-12", "Who won the Masters golf tournament at Augusta on 12 April 2026?",
       "Rory McIlroy", ("mcilroy",), "https://en.wikipedia.org/wiki/2026_Masters_Tournament"),

    _e("eurovision-2026", "2026-05-16",
       "Which country won the Eurovision Song Contest final in Vienna on 16 May 2026?",
       "Bulgaria (Dara, \"Bangaranga\")", ("bulgaria",), "https://en.wikipedia.org/wiki/Eurovision_Song_Contest_2026"),
    _e("warsh", "2026-05-22",
       "Who was sworn in as chair of the US Federal Reserve on 22 May 2026, succeeding Jerome Powell?",
       "Kevin Warsh", ("warsh",), "https://www.federalreserve.gov/newsevents/pressreleases/other20260522a.htm"),
    _e("ucl-2026", "2026-05-30", "Which club won the UEFA Champions League final in Budapest on 30 May 2026?",
       "Paris Saint-Germain", ("paris saint-germain|paris saint germain|psg",),
       "https://en.wikipedia.org/wiki/2026_UEFA_Champions_League_final"),

    _e("spacex-ipo", "2026-06-12",
       "Under what ticker symbol did SpaceX shares start trading on Nasdaq on 12 June 2026?",
       "SPCX", ("spcx",), "https://en.wikipedia.org/wiki/Initial_public_offering_of_SpaceX"),
    _e("nba-2026", "2026-06-13", "Which team won the NBA Finals, clinching the title on 13 June 2026?",
       "New York Knicks", ("knicks",), "https://en.wikipedia.org/wiki/2026_NBA_Finals"),
    _e("colombia-2026", "2026-06-21", "Who won Colombia's presidential run-off on 21 June 2026?",
       "Abelardo de la Espriella", ("espriella",),
       "https://en.wikipedia.org/wiki/2026_Colombian_presidential_election"),

    _e("wimbledon-2026-women", "2026-07-11", "Who won the Wimbledon women's singles title on 11 July 2026?",
       "Linda Nosková", ("noskova",),
       "https://en.wikipedia.org/wiki/2026_Wimbledon_Championships_%E2%80%93_Women%27s_singles"),
    _e("world-cup-2026", "2026-07-19", "Who scored the only goal of the FIFA World Cup final on 19 July 2026?",
       "Ferran Torres (Spain)", ("torres",), "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_final"),

    _e("amazon-3t", "2026-08-03",
       "Which company passed 3 trillion dollars in stock market value for the first time on 3 August 2026?",
       "Amazon", ("amazon",), "https://www.bloomberg.com/news/articles/2026-08-03/amazon-joins-elite-list-of-stocks-"
       "to-top-3-trillion-in-value"),
    _e("eala", "2026-08-03",
       "Which player became the first from the Philippines to win a WTA Tour title, in Washington on 3 August 2026?",
       "Alexandra Eala", ("eala",), "https://en.wikipedia.org/wiki/Alexandra_Eala"),

    _e("us-open-2026-women", "2026-09-12", "Who won the US Open women's singles title on 12 September 2026?",
       "Elena Rybakina", ("rybakina",), "https://en.wikipedia.org/wiki/2026_US_Open_%E2%80%93_Women%27s_singles"),
    _e("sweden-2026", "2026-09-13",
       "Which party leader's centre-left bloc won Sweden's general election on 13 September 2026?",
       "Magdalena Andersson (Social Democrats)", ("andersson|social democrat",),
       "https://en.wikipedia.org/wiki/2026_Swedish_general_election"),
    _e("fed-2026-09", "2026-09-16",
       "What target range for the federal funds rate did the US Federal Reserve set on 16 September 2026?",
       "3.75% to 4.00%", ("3.75-4",),
       "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"),
)
