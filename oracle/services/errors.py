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


class InvalidWatchlistName(ServiceError):
    pass


class InvalidSwap(ServiceError):
    pass


class InvalidQuery(ServiceError, ValueError):
    pass


class PriceUnavailable(ServiceError):
    pass


class NoPrices(ServiceError):
    pass


class ModelUnavailable(ServiceError):
    pass


class CannotRestart(ServiceError):
    pass
