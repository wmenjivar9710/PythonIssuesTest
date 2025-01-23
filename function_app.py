import azure.functions as func
from http_blueprint import bp
from log import setupLogger

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

app.register_functions(bp)
logger = setupLogger(__name__)
