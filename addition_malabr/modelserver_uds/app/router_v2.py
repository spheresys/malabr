
from logger import get_logger
from types_defs import HandlerType

logger = get_logger(__name__)

class Router:
    def __init__(self):
        self._routes = {}

    def register(self, label: str, handler: HandlerType):
        """Registers a handler for a specific label."""
        logger.debug(f"Registering handler for label '{label}'")
        self._routes[label] = handler

    def get_handler(self, label: str):
        """Retrieves the handler for a given payload type."""
        return self._routes.get(label)