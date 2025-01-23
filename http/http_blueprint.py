"""
An Azure Functions Web App for processing
User Stories that have been process with linked Logic App.
"""

import azure.functions as func

from log import setupLogger
from common import (
    validateUserStoryId,
    processUserStory,
)

logger = setupLogger(__name__)
bp = func.Blueprint()


@bp.function_name(name="adoMetrics")
@bp.route(route="adoMetrics", auth_level=func.AuthLevel.FUNCTION)
def adoMetrics(req: func.HttpRequest) -> func.HttpResponse:
    """
    Processes an HTTP request and returns an HTTP response
    containing the output of the BuildIntakeMetrics function.

    Args:
        req (func.HttpRequest): The HTTP request to process.

    Returns:
        func.HttpResponse: The HTTP response containing the
        output of the BuildIntakeMetrics function.
    """
    logger.info("Python HTTP trigger function processed a request.")
    user_story_id = None
    try:
        if req.method == "GET":
            user_story_id = req.params.get("id", None)
        elif req.method == "POST":
            user_story_id = req.get_json().get("id", None)
        if user_story_id is None:
            return func.HttpResponse(status_code=400)
    except KeyError as exc:
        logger.warning("Parameter id got(%s), Raised %s", req.get_body(), exc)
        return func.HttpResponse(
            body='{"error": "Invalid Request Parameters"}', status_code=400
        )
    sanitized_user_story_id = validateUserStoryId(user_story_id)
    output = processUserStory(int(sanitized_user_story_id))
    logger.info("Output: %s", output)
    return output
