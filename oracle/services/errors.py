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
