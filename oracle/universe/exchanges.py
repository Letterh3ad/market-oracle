"""FinanceDatabase exchange codes (Yahoo's) with the country each sits in and a tier:
1 a main national board, 2 a secondary or growth board, 3 a regional venue, OTC, or a
board that mostly carries foreign cross-listings. Used to choose a company's primary
listing. FinanceDatabase's own market names are wrong for a few (NGM is Nasdaq Global
Market, not Nordic Growth Market; EBS is SIX Swiss)."""

from __future__ import annotations

EXCHANGES: dict[str, tuple[str, int]] = {
    "NYQ": ("United States", 1),
    "NMS": ("United States", 1),
    "NGM": ("United States", 1),
    "NCM": ("United States", 2),
    "ASE": ("United States", 2),
    "PCX": ("United States", 2),
    "BTS": ("United States", 2),
    "PNK": ("United States", 3),
    "OBB": ("United States", 3),
    "OEM": ("United States", 3),
    "OID": ("United States", 3),
    "OQB": ("United States", 3),
    "OQX": ("United States", 3),
    "NAE": ("United States", 3),
    "LSE": ("United Kingdom", 1),
    "AQS": ("United Kingdom", 3),
    "IOB": ("United Kingdom", 3),
    "GER": ("Germany", 1),
    "FRA": ("Germany", 3),
    "STU": ("Germany", 3),
    "BER": ("Germany", 3),
    "MUN": ("Germany", 3),
    "DUS": ("Germany", 3),
    "HAM": ("Germany", 3),
    "HAN": ("Germany", 3),
    "PAR": ("France", 1),
    "ENX": ("France", 2),
    "AMS": ("Netherlands", 1),
    "BRU": ("Belgium", 1),
    "LIS": ("Portugal", 1),
    "MIL": ("Italy", 1),
    "TLO": ("Italy", 3),
    "MCE": ("Spain", 1),
    "EBS": ("Switzerland", 1),
    "VIE": ("Austria", 1),
    "ISE": ("Ireland", 1),
    "STO": ("Sweden", 1),
    "SAT": ("Sweden", 3),
    "CPH": ("Denmark", 1),
    "HEL": ("Finland", 1),
    "OSL": ("Norway", 1),
    "ICE": ("Iceland", 1),
    "TAL": ("Estonia", 1),
    "RIS": ("Latvia", 1),
    "LIT": ("Lithuania", 1),
    "PRA": ("Czech Republic", 1),
    "BUD": ("Hungary", 1),
    "ATH": ("Greece", 1),
    "IST": ("Turkey", 1),
    "MCX": ("Russia", 1),
    "TLV": ("Israel", 1),
    "SAU": ("Saudi Arabia", 1),
    "DOH": ("Qatar", 1),
    "CAI": ("Egypt", 1),
    "JNB": ("South Africa", 1),
    "TOR": ("Canada", 1),
    "VAN": ("Canada", 2),
    "CNQ": ("Canada", 2),
    "NEO": ("Canada", 2),
    "MEX": ("Mexico", 1),
    "SAO": ("Brazil", 1),
    "BUE": ("Argentina", 1),
    "SGO": ("Chile", 1),
    "CCS": ("Venezuela", 1),
    "JPX": ("Japan", 1),
    "FKA": ("Japan", 3),
    "SAP": ("Japan", 3),
    "SHH": ("China", 1),
    "SHZ": ("China", 1),
    "HKG": ("Hong Kong", 1),
    "TAI": ("Taiwan", 1),
    "TWO": ("Taiwan", 2),
    "KSC": ("South Korea", 1),
    "KOE": ("South Korea", 2),
    "NSE": ("India", 1),
    "NSI": ("India", 1),
    "BSE": ("India", 1),
    "SES": ("Singapore", 1),
    "KLS": ("Malaysia", 1),
    "SET": ("Thailand", 1),
    "JKT": ("Indonesia", 1),
    "ASX": ("Australia", 1),
    "NZE": ("New Zealand", 1),
    "CSE": ("Sri Lanka", 1),
}

# Tie-break when a company has no listing at home: the deepest markets first, so a
# foreign company listed in New York and Frankfurt resolves to New York.
BY_DEPTH: tuple[str, ...] = (
    "NYQ", "NMS", "NGM", "SHH", "JPX", "HKG", "LSE", "SHZ", "NSE", "PAR", "TOR", "GER",
    "KSC", "TAI", "ASX", "AMS", "EBS", "SAU", "STO", "MIL", "MCE", "BSE", "JNB", "SAO",
)

UNKNOWN_TIER = 9


def exchange_country(code: str | None) -> str | None:
    entry = EXCHANGES.get(code or "")
    return entry[0] if entry else None


def exchange_rank(code: str | None) -> tuple[int, int]:
    """Lower is better: tier, then depth among the named markets."""
    tier = EXCHANGES.get(code or "", (None, UNKNOWN_TIER))[1]
    depth = BY_DEPTH.index(code) if code in BY_DEPTH else len(BY_DEPTH)
    return tier, depth
