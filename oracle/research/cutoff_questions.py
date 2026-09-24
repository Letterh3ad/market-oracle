"""The cutoff probe's question set: three dated public events a month, each with its known
answer, the keywords a right reply contains, and where it was checked. Change VERSION with any
edit, so stored probes say which set they answered.

A question asks for what only the event itself revealed: a final score, a vote count, a
runner-up, a name nobody could have predicted. Never a repeat winner, a value that was already
true before, a favourite, or an answer the question implies: a lucky or inferred answer moves
the measured cutoff later and can leave no room for replays. Months before the claimed cutoff
only show the model knows them; the claim is the floor either way."""

from __future__ import annotations

from datetime import date

from oracle.research.cutoff import Event

VERSION = "2026-09-24.2"


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

    _e("super-bowl-lix", "2025-02-09", "What was the final score of Super Bowl LIX, played on 9 February 2025?",
       "Philadelphia Eagles 40, Kansas City Chiefs 22", ("40", "22"), "https://en.wikipedia.org/wiki/Super_Bowl_LIX"),
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
    _e("eurovision-2025", "2025-05-17", "Which country won the Eurovision Song Contest final in Basel on 17 May 2025?",
       "Austria (JJ, \"Wasted Love\")", ("austria|wasted love",),
       "https://en.wikipedia.org/wiki/Eurovision_Song_Contest_2025"),
    _e("romania-2025", "2025-05-18", "Who won Romania's presidential run-off on 18 May 2025?",
       "Nicușor Dan", ("nicusor dan|dan nicusor",),
       "https://en.wikipedia.org/wiki/2025_Romanian_presidential_election"),

    _e("korea-2025-runner-up", "2025-06-03",
       "Who finished second in South Korea's snap presidential election on 3 June 2025?",
       "Kim Moon-soo", ("kim moon-soo|kim moon soo|kim moonsoo",),
       "https://en.wikipedia.org/wiki/2025_South_Korean_presidential_election"),
    _e("air-india-171", "2025-06-12",
       "In which Indian city did Air India Flight 171 crash shortly after take-off on 12 June 2025?",
       "Ahmedabad", ("ahmedabad",), "https://en.wikipedia.org/wiki/Air_India_Flight_171"),
    _e("rising-lion", "2025-06-13", "What code name did Israel give the strikes on Iran it began on 13 June 2025?",
       "Operation Rising Lion", ("rising lion",), "https://en.wikipedia.org/wiki/Twelve-Day_War"),

    _e("camp-mystic", "2025-07-04",
       "Which girls' summer camp on the Guadalupe River in Texas lost 27 campers and counsellors in the flash "
       "flood of 4 July 2025?",
       "Camp Mystic", ("mystic",), "https://en.wikipedia.org/wiki/Camp_Mystic"),
    _e("wimbledon-2025-women", "2025-07-12", "Who won the Wimbledon women's singles title on 12 July 2025?",
       "Iga Świątek", ("swiatek",),
       "https://en.wikipedia.org/wiki/2025_Wimbledon_Championships_%E2%80%93_Women%27s_singles"),
    _e("sanseito", "2025-07-20",
       "Which right-wing populist party, campaigning on \"Japanese First\", won 14 seats in Japan's upper house "
       "election on 20 July 2025?",
       "Sanseito", ("sanseito",), "https://en.wikipedia.org/wiki/2025_Japanese_House_of_Councillors_election"),

    _e("mcentarfer", "2025-08-01",
       "Whom did Donald Trump fire as commissioner of the Bureau of Labor Statistics on 1 August 2025?",
       "Erika McEntarfer", ("mcentarfer",), "https://en.wikipedia.org/wiki/Erika_McEntarfer"),
    _e("boe-2025-08-vote", "2025-08-07",
       "By what vote did the Bank of England's Monetary Policy Committee cut Bank Rate on 7 August 2025, after "
       "its first ever second round of voting?",
       "5-4", ("5-4|five to four|five votes to four|5 votes to 4",),
       "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2025/august-2025"),
    _e("alaska-summit", "2025-08-15",
       "In which US state did Donald Trump and Vladimir Putin hold a summit on 15 August 2025?",
       "Alaska", ("alaska",), "https://en.wikipedia.org/wiki/2025_Russia%E2%80%93United_States_summit"),

    _e("lecornu", "2025-09-09", "Whom did Emmanuel Macron appoint prime minister of France on 9 September 2025?",
       "Sébastien Lecornu", ("lecornu",), "https://en.wikipedia.org/wiki/S%C3%A9bastien_Lecornu"),
    _e("kirk", "2025-09-10",
       "Which American conservative activist was shot dead at Utah Valley University on 10 September 2025?",
       "Charlie Kirk", ("kirk",), "https://en.wikipedia.org/wiki/Assassination_of_Charlie_Kirk"),
    _e("miran-dissent", "2025-09-17",
       "Which Federal Reserve governor cast the only dissent at the 17 September 2025 FOMC meeting, wanting a "
       "half-point cut?",
       "Stephen Miran", ("miran",),
       "https://www.cnbc.com/2025/09/17/new-trump-appointee-miran-calls-for-half-point-cut-in-only-dissent-as-"
       "rest-of-fed-bands-together.html"),

    _e("nobel-literature-2025", "2025-10-09", "Who was awarded the Nobel Prize in Literature on 9 October 2025?",
       "László Krasznahorkai", ("krasznahorkai",),
       "https://www.nobelprize.org/prizes/literature/2025/summary/"),
    _e("nobel-peace-2025", "2025-10-10", "Who was awarded the Nobel Peace Prize announced on 10 October 2025?",
       "María Corina Machado", ("machado",), "https://www.nobelprize.org/prizes/peace/2025/summary/"),
    _e("nobel-economics-2025", "2025-10-13",
       "Name one of the economists awarded the economics Nobel on 13 October 2025.",
       "Joel Mokyr, Philippe Aghion and Peter Howitt", ("mokyr|aghion|howitt",),
       "https://www.nobelprize.org/prizes/economic-sciences/2025/press-release/"),

    _e("world-series-2025", "2025-11-01",
       "Which team did the Los Angeles Dodgers beat in Game 7 of the World Series on 1 November 2025?",
       "Toronto Blue Jays", ("blue jays|toronto",), "https://en.wikipedia.org/wiki/2025_World_Series_Game_7"),
    _e("shutdown-2025", "2025-11-12",
       "How many days had the US federal government shutdown lasted when it ended on 12 November 2025?",
       "43 days", ("43|forty-three|forty three",),
       "https://en.wikipedia.org/wiki/2025_United_States_federal_government_shutdown"),
    _e("wang-fuk-court", "2025-11-26",
       "Which Hong Kong housing estate in Tai Po was engulfed by a deadly fire on 26 November 2025?",
       "Wang Fuk Court", ("wang fuk",), "https://en.wikipedia.org/wiki/Wang_Fuk_Court_fire"),

    _e("netflix-warner", "2025-12-05",
       "Which company agreed on 5 December 2025 to buy Warner Bros.' film and television studios and HBO Max?",
       "Netflix", ("netflix",), "https://about.netflix.com/en/news/netflix-to-acquire-warner-bros"),
    _e("chile-2025-share", "2025-12-14",
       "What share of the vote did the winner of Chile's presidential run-off on 14 December 2025 receive, in "
       "percent?",
       "58.17% (José Antonio Kast)", ("58|58.2|58.17",),
       "https://en.wikipedia.org/wiki/2025_Chilean_presidential_election"),
    _e("bondi", "2025-12-14",
       "At which Sydney beach did gunmen attack a Hanukkah celebration on 14 December 2025?",
       "Bondi Beach", ("bondi",), "https://en.wikipedia.org/wiki/2025_Bondi_Beach_shooting"),

    _e("crans-montana", "2026-01-01",
       "In which Swiss ski resort did a bar fire kill about 40 people on New Year's night, 1 January 2026?",
       "Crans-Montana", ("crans-montana|crans montana",), "https://en.wikipedia.org/wiki/2026"),
    _e("absolute-resolve", "2026-01-03",
       "What was the name of the US military operation that captured Nicolás Maduro on 3 January 2026?",
       "Operation Absolute Resolve", ("absolute resolve",),
       "https://en.wikipedia.org/wiki/2026_United_States_intervention_in_Venezuela"),
    _e("adamuz", "2026-01-18", "Near which Spanish town did trains derail on 18 January 2026, killing dozens?",
       "Adamuz", ("adamuz",), "https://en.wikipedia.org/wiki/2026"),

    _e("super-bowl-lx", "2026-02-08", "What was the final score of Super Bowl LX, played on 8 February 2026?",
       "Seattle Seahawks 29, New England Patriots 13", ("29", "13"), "https://en.wikipedia.org/wiki/Super_Bowl_LX"),
    _e("portugal-2026", "2026-02-08", "Who won Portugal's presidential run-off on 8 February 2026?",
       "António José Seguro", ("seguro",),
       "https://en.wikipedia.org/wiki/2026_Portuguese_presidential_election"),
    _e("el-mencho", "2026-02-22",
       "Which Mexican cartel leader was killed by the Mexican army in Tapalpa, Jalisco on 22 February 2026?",
       "Nemesio \"El Mencho\" Oseguera Cervantes", ("mencho|oseguera",),
       "https://en.wikipedia.org/wiki/2026_Jalisco_operation"),

    _e("iris-dena", "2026-03-04",
       "Which Iranian Navy frigate did the US submarine USS Charlotte sink off Sri Lanka on 4 March 2026?",
       "IRIS Dena", ("dena",), "https://en.wikipedia.org/wiki/Sinking_of_IRIS_Dena"),
    _e("gold-cup-2026", "2026-03-13", "Which horse won the Cheltenham Gold Cup on 13 March 2026?",
       "Gaelic Warrior", ("gaelic warrior",), "https://en.wikipedia.org/wiki/2026_Cheltenham_Gold_Cup"),
    _e("best-actor-2026", "2026-03-15", "Who won Best Actor at the Academy Awards on 15 March 2026?",
       "Michael B. Jordan (Sinners)", ("jordan",), "https://en.wikipedia.org/wiki/98th_Academy_Awards"),

    _e("ncaa-2026", "2026-04-06",
       "What was the final score of the NCAA men's basketball championship game on 6 April 2026?",
       "Michigan 69, UConn 63", ("69", "63"),
       "https://en.wikipedia.org/wiki/2026_NCAA_Division_I_men%27s_basketball_championship_game"),
    _e("hungary-2026-seats", "2026-04-12",
       "How many of the 199 seats did the winning party take in Hungary's parliamentary election on 12 April 2026?",
       "141 (Tisza)", ("141",), "https://en.wikipedia.org/wiki/2026_Hungarian_parliamentary_election"),
    _e("london-marathon-2026", "2026-04-26",
       "What was the men's winning time at the London Marathon on 26 April 2026?",
       "1:59:30 (Sabastian Sawe)", ("1:59:30|1 hour 59 minutes 30",),
       "https://en.wikipedia.org/wiki/2026_London_Marathon"),

    _e("warsh-vote", "2026-05-13",
       "By what vote did the US Senate confirm Kevin Warsh as Federal Reserve chair on 13 May 2026?",
       "54-45", ("54", "45"), "https://www.cnn.com/2026/05/22/economy/kevin-warsh-sworn-in-fed-chair"),
    _e("eurovision-2026", "2026-05-16",
       "Which country won the Eurovision Song Contest final in Vienna on 16 May 2026?",
       "Bulgaria (Dara, \"Bangaranga\")", ("bulgaria|bangaranga",),
       "https://en.wikipedia.org/wiki/Eurovision_Song_Contest_2026"),
    _e("ucl-2026-arsenal-goal", "2026-05-30",
       "Who scored Arsenal's goal in the UEFA Champions League final in Budapest on 30 May 2026?",
       "Kai Havertz", ("havertz",), "https://en.wikipedia.org/wiki/2026_UEFA_Champions_League_final"),

    _e("spacex-ipo", "2026-06-12",
       "Under what ticker symbol did SpaceX shares start trading on Nasdaq on 12 June 2026?",
       "SPCX", ("spcx",), "https://en.wikipedia.org/wiki/Initial_public_offering_of_SpaceX"),
    _e("nba-2026", "2026-06-13", "Which team won the NBA Finals, clinching the title on 13 June 2026?",
       "New York Knicks", ("knicks",), "https://en.wikipedia.org/wiki/2026_NBA_Finals"),
    _e("colombia-2026", "2026-06-21", "Who won Colombia's presidential run-off on 21 June 2026?",
       "Abelardo de la Espriella", ("espriella",),
       "https://en.wikipedia.org/wiki/2026_Colombian_presidential_election"),

    _e("norway-brazil", "2026-07-05",
       "Which team did Norway beat 2-1 on 5 July 2026 to reach its first World Cup quarter-final?",
       "Brazil", ("brazil",), "https://www.aljazeera.com/sports/2026/7/5/haaland-scores-twice-as-norway-stun-"
       "brazil-2-1-in-world-cup-2026-last-16"),
    _e("wimbledon-2026-women", "2026-07-11", "Who won the Wimbledon women's singles title on 11 July 2026?",
       "Linda Nosková", ("noskova",),
       "https://en.wikipedia.org/wiki/2026_Wimbledon_Championships_%E2%80%93_Women%27s_singles"),
    _e("world-cup-2026", "2026-07-19", "Who scored the only goal of the FIFA World Cup final on 19 July 2026?",
       "Ferran Torres (Spain)", ("torres",), "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_final"),

    _e("purja", "2026-08-01",
       "Which famous Nepali-British mountaineer was confirmed on 1 August 2026 to have died in an avalanche on "
       "Broad Peak?",
       "Nirmal \"Nims\" Purja", ("purja|nimsdai",), "https://en.wikipedia.org/wiki/2026_Broad_Peak_avalanche"),
    _e("eala", "2026-08-03",
       "Which player became the first from the Philippines to win a WTA Tour title, in Washington on 3 August 2026?",
       "Alexandra Eala", ("eala",), "https://en.wikipedia.org/wiki/Alexandra_Eala"),
    _e("little-league-2026", "2026-08-30",
       "Which team won the Little League World Series championship game on 30 August 2026?",
       "Curaçao (Pariba Little League, Willemstad)", ("curacao|willemstad|pariba",),
       "https://en.wikipedia.org/wiki/2026_Little_League_World_Series"),

    _e("us-open-2026-women", "2026-09-12", "Who won the US Open women's singles title on 12 September 2026?",
       "Elena Rybakina", ("rybakina",), "https://en.wikipedia.org/wiki/2026_US_Open_%E2%80%93_Women%27s_singles"),
    _e("us-open-2026-men", "2026-09-13", "Who won the US Open men's singles title on 13 September 2026?",
       "Alexander Zverev", ("zverev",), "https://en.wikipedia.org/wiki/2026_US_Open_%E2%80%93_Men%27s_singles"),
    _e("boe-2026-09-vote", "2026-09-17",
       "By what vote did the Bank of England's Monetary Policy Committee leave Bank Rate unchanged on "
       "17 September 2026?",
       "6-3, with three members voting to raise it", ("6-3|six to three|six votes to three|6 votes to 3",),
       "https://www.cnbc.com/2026/09/17/bank-of-england-interest-rate-decision-fed-rate-hike-uk-inflation.html"),
)
