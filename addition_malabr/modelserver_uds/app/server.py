from router_v2 import Router
from socket_server_v2 import Server
from handler import read_data, send_data, qa_model
import config
from types_defs import RoutesType

SOCKET_PATH = config.SOCKET_PATH

ROUTES: RoutesType = {
    "LABEL_READ_DATA": read_data.handle,
    "LABEL_SEND_DATA": send_data.handle,
    "LABEL_LOAD_MODEL_BERT": qa_model.load_model,
    "LABEL_INFER_MODEL_BERT": qa_model.infer
}
    
if __name__ == "__main__":
    app_router = Router()

    # Register handlers
    for label in ROUTES.keys():
        app_router.register(label, ROUTES[label])

    server = Server(SOCKET_PATH, app_router, max_workers=5)
    server.start()
