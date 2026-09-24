class ServiceError(Exception):
    """Base for failures a frontend reports to the user rather than as a traceback."""


class DatabaseTooNew(ServiceError):
    pass


class ConfigInvalid(ServiceError):
    pass


class ConfigInUse(ServiceError):
    pass


class JobAlreadyRunning(ServiceError):
    pass


class UniverseUnavailable(ServiceError):
    pass


class UniverseNotLoaded(ServiceError):
    def __init__(self) -> None:
        super().__init__("the universe is not loaded yet: refresh the universe first")


class UnknownFilterValue(ServiceError):
    pass


class UnknownSymbol(ServiceError):
    pass


class InvalidTag(ServiceError):
    pass


class UnknownWatchlist(ServiceError):
    pass


class UnknownJob(ServiceError):
    pass


class UnknownResearchRun(ServiceError):
    pass


class UnknownBacktest(ServiceError):
    pass


class UnknownQuestion(ServiceError):
    pass


class UnknownModelProfile(ServiceError):
    pass


class SearchFailed(ServiceError):
    pass


class InvalidWatchlistName(ServiceError):
    pass


class InvalidSwap(ServiceError):
    pass


class InvalidQuery(ServiceError, ValueError):
    pass


class PriceUnavailable(ServiceError):
    def __init__(self, message: str, no_data: bool = False) -> None:
        super().__init__(message)
        self.no_data = no_data
        """Every source answered and none had data: the symbol, not the network."""


class NoPrices(ServiceError):
    pass


class ModelUnavailable(ServiceError):
    pass


class CannotRestart(ServiceError):
    pass


class NeverListed(ServiceError):
    pass


class UnknownLink(ServiceError):
    pass
