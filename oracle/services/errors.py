class ServiceError(Exception):
    """Base for failures a frontend reports to the user rather than as a traceback."""


class DatabaseTooNew(ServiceError):
    pass


class ConfigInvalid(ServiceError):
    pass


class JobAlreadyRunning(ServiceError):
    pass


class UniverseUnavailable(ServiceError):
    pass


class UniverseNotLoaded(ServiceError):
    def __init__(self) -> None:
        super().__init__("the universe is not loaded yet: run `oracle universe refresh`")


class UnknownFilterValue(ServiceError):
    pass


class UnknownSymbol(ServiceError):
    pass


class InvalidTag(ServiceError):
    pass


class UnknownWatchlist(ServiceError):
    pass


class InvalidWatchlistName(ServiceError):
    pass


class InvalidQuery(ServiceError, ValueError):
    pass
