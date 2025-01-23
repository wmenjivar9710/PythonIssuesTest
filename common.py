"""
Common Functions for function_app Azure Functions Web App.
"""

import datetime
import enum
import json
import logging
import os
import re

from azure.identity import ManagedIdentityCredential
from azure.iot.hub.auth import AzureIdentityCredentialAdapter

from azure.devops.connection import Connection
from azure.devops.exceptions import AzureDevOpsServiceError

from log import setupLogger

required_fields = [
    "System.Id",
    "System.Title",
    "System.WorkItemType",
    "System.State",
    "System.Tags",
]


logger = setupLogger(__name__)
DEFAULT_ORG_URI = "https://dev.azure.com/microsoftit"
DEFAULT_CLIENT_ID = "TDEConciergeWorkbotADOMIUA"
DEFAULT_PROJECT_NAME = "OneITVSO"


# create enum for valid work item types
class workitemType(enum.Enum):
    USERSTORY = "User Story"
    TASK = "Task"


def checkMetrics(objs: list, cmp: list) -> bool:
    """Checks a items in a list equals another list

    Args:
        objs (list): objects to check
        cmp (list): items to compare against

    Returns:
        bool: True if match exists
    """
    return any(_o in cmp for _o in objs)


# Get the time delta from the task's creation to the the story's completion
def getTTD(metrics: dict) -> dict:
    """Get time delta metrics

    Args:
        metrics (dict): metrics datastructure

    Returns:
        ttd (dict) : metrics time delta.
    """
    if checkMetrics(
        (
            metrics["TTS"],
            metrics["DT"],
        ),
        ["Error", "N/A"],
    ):
        metrics["Error"].append("TTD: No TTD value due to other metric errors")
        metrics["TTD"] = "Error"
        return {"Error": "TTD: No TTD value due to other metric errors"}
    if checkMetrics(
        (
            metrics["TTS"],
            metrics["DT"],
        ),
        ["*"],
    ):
        metrics["TTD"] = "N/A"
        return {"TTD": "N/A"}

    tts = float(metrics["TTS"].split(" ")[0])
    dt = float(metrics["DT"].split(" ")[0])
    ttd = str(round(tts + dt, 1)) + " Days"

    logger.debug("Metrics[TTD]: %s", ttd)
    metrics["TTD"] = ttd
    return ttd


def formatTime(delta: datetime.timedelta) -> str:
    """Helper Function for calculating time delta

    Args:
        delta (timedelta): A time delta

    Returns:
        str: Days and Hours in timedelta
    """
    hours = int(delta.total_seconds() / 60 / 60)
    days = int(hours / 24)
    hours %= 24
    return f"{days} Days {int(hours)} hours"


def format_date(date: datetime.datetime) -> datetime.datetime:
    """Helper Function formating datetime

    Args:
        date (datetime): A datetime object.

    Returns:
        datetime: a updated datetime object.
    """
    return datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=datetime.timezone.utc
    )


def getMetrics(user_story_id: int, story_updates: list, task_updates: list):
    """Helper Function for processing user story metrics.

    Args:
        user_story_id (int): _description_
        story_updates (list): _description_
        task_updates (list): _description_

    Returns:

        (dict): user story assessment metrics.
    """
    assessment_metrics = {
        "User Story": user_story_id,
        "TTE": "",
        "TTS": "",
        "DT": "",
        "TTD": "",
        "Error": [],
    }
    getTTE(task_updates, assessment_metrics)
    getTTS(story_updates, assessment_metrics)
    getDT(story_updates, assessment_metrics)
    getTTD(assessment_metrics)
    return assessment_metrics


# Get the time difference between the story's active state
# and the story's new state
def getTTS(story_updates, assessment_metrics):
    logger.info("Calculating TTS...")
    new_date = None
    active_date = None
    # get the story's first new date and first active date
    for update in story_updates:
        if update.fields is None:
            continue
        if "System.State" not in update.fields:
            continue
        if not new_date and update.fields["System.State"].new_value == "New":
            try:
                new_val = update.fields["System.RevisedDate"].new_value
                new_date = format_date(new_val)
                logger.debug("TTS revised new_date %s", new_date)
            except KeyError:
                new_val = update.fields["System.ChangedDate"].new_value
                new_date = format_date(new_val)
                logger.debug("TTS changed new_date %s", new_date)
        if update.fields["System.State"].new_value == "Active":
            try:
                old_val = update.fields["System.RevisedDate"].old_value
                active_date = "", format_date(old_val)
                logger.debug("TTS revised active_date %s", active_date[1])
                break
            except KeyError:
                old_val = update.fields["System.ChangedDate"].old_value
                active_date = "", format_date(old_val)
                logger.debug("TTS changed active_date %s", active_date[1])
                break

    # if the story was never set to active, set the
    # active date to the current time
    if active_date is None:
        active_date = "*", datetime.datetime.now(datetime.timezone.utc)

    if new_date is None:
        logger.info(
            "TTS: Assessment Story: %s was not set as new",
            story_updates[0].fields["System.Id"].new_value,
        )

    time = formatTime(active_date[1] - new_date)  # type: ignore
    logger.debug(
        "Metric[TTS]:"
        + f"{active_date[1]}"
        + "-"
        + f"{new_date}"
        + ":"
        + active_date[0]
        + f"{time}"
    )
    assessment_metrics["TTS"] = active_date[0] + time
    return active_date[0] + time


# Get the time difference between the work item's new state and completed state
def getTTE(updates, assessment_metrics):
    logger.info("Calculating TTE...")
    end_date = None
    # new_date = None
    created_date = None
    removed_date = None
    # Get the first new date, last closed date, and last removed date
    for update in updates:
        if update.fields is None:
            continue
        if created_date is None and "System.CreatedDate" in update.fields:
            new_val = update.fields["System.CreatedDate"].new_value
            created_date = format_date(new_val)

        if "System.State" not in update.fields:
            continue
        if update.fields["System.State"].new_value == "Closed":
            try:
                end_date = "", format_date(
                    update.fields["System.RevisedDate"].old_value
                )
            except KeyError:
                end_date = "", format_date(
                    update.fields["System.ChangedDate"].old_value
                )

        if update.fields["System.State"].new_value == "Removed":
            try:
                removed_date = format_date(
                    update.fields["System.RevisedDate"].old_value
                )

            except KeyError:
                removed_date = format_date(
                    update.fields["System.ChangedDate"].old_value
                )

    # If the task was removed, then the TTE is an error
    if removed_date is not None:
        if end_date is None or removed_date > end_date[1]:
            assessment_metrics["Error"].append(
                "TTE: Assessment Task: "
                + str(updates[0].fields["System.Id"].new_value)
                + " was set as removed"
            )
            assessment_metrics["TTE"] = "Error"
            return {
                "Error": "TTE: Assessment Task: "
                + str(updates[0].fields["System.Id"].new_value)
                + " was set as removed"
            }

    # If the task was not closed, then set the end date to the current time
    if end_date is None:
        end_date = "*", datetime.datetime.now(datetime.timezone.utc)

    if created_date is None:
        logger.info(
            "TTE: Assessment Task: %s was not set as new",
            updates[0].fields["System.Id"].new_value,
        )

    _t = formatTime(end_date[1] - created_date)  # type: ignore
    assessment_metrics["TTE"] = end_date[0] + _t
    return end_date[0] + _t


def validateUserStoryId(user_story_id: str) -> str:
    """
    Validates a user story ID.

    Args:
        user_story_id (str): The user story ID to validate.

    Returns:
        str: The sanitized user story ID.

    Raises:
        ValueError: If the user story ID is not a string,
        is not alphanumeric, or is too long.
    """
    # Check that the input is a string
    if not isinstance(user_story_id, str):
        logging.debug("User story ID is not a string")
        raise ValueError("User story ID must be a string")

    # Check that the input is alphanumeric
    if not re.match("^[a-zA-Z0-9]+$", user_story_id):
        logging.debug("User story ID is not alphanumeric")
        raise ValueError("User story ID must be alphanumeric")

    # Check that the input is not too long
    if len(user_story_id) > 15:
        logging.debug("User story ID is too long")
        raise ValueError("User story ID is too long")

    # Sanitize the input to remove any harmful characters
    user_story_id = re.sub(r"[^a-zA-Z0-9]+", "", user_story_id)

    return user_story_id


def getAssessmentTask(conn: Connection, project_name: str, work_item_id: int) -> int:
    """Get Assessment Task

    Args:
        conn (Connection): _description_
        project_name (str): _description_
        work_item_id (int): _description_

    Returns:
        int: _description_
    """
    # Get a reference to the work item tracking client
    _client = conn.clients.get_work_item_tracking_client()
    logger.debug("Connection to Work Item Tracking Client Established")

    task_id = None

    # Use the work item tracking client to query the work item properties
    work_item = _client.get_work_item(work_item_id, project_name, expand="all")
    logger.debug("Work Item: %s", work_item_id)

    for rel_item in work_item.relations:
        if (
            rel_item.rel == "System.LinkTypes.Hierarchy-Forward"
            and rel_item.attributes["name"] == "Child"
        ):
            child_workitem_id = rel_item.url.split("/")[-1]
            logger.debug("Child Work Item ID: %s", child_workitem_id)

            child_fields = getWorkitemFields(conn, project_name, child_workitem_id)
            _val = workitemType.TASK.value
            if (
                child_fields is not None
                and child_fields["System.WorkItemType"] == _val
                and "[Assessment]" in child_fields["System.Title"]
            ):
                task_id = child_fields["System.Id"]
                break

    return task_id


# Get the time difference between the story's
# active state and the story's closed state
def getDT(updates, assessment_metrics):
    logger.info("Calculating DT...")
    active_date = None
    blocked_date = None
    end_date = None
    # get the story's first active date, final
    # closed date, and final blocked date
    for update in updates:
        logger.debug("DT updates\n\n %s", update)
    for update in updates:
        if update.fields is None:
            continue
        if "System.State" not in update.fields:
            continue
        sys_state = update.fields["System.State"].new_value
        if active_date is None and sys_state == "Active":
            try:
                old_val = update.fields["System.RevisedDate"].old_value
                active_date = format_date(old_val)
                logger.debug("DT revised active_date %s", active_date)
            except KeyError:
                old_val = update.fields["System.ChangedDate"].old_value
                active_date = format_date(old_val)
                logger.debug("DT changed active_date %s", active_date)
        if update.fields["System.State"].new_value == "Closed":
            try:
                end_date = "", format_date(
                    update.fields["System.RevisedDate"].old_value
                )
                logger.debug("DT revised end_date %s", end_date[1])
            except KeyError:
                end_date = "", format_date(
                    update.fields["System.ChangedDate"].old_value
                )
                logger.debug("DT changed end_date %s", end_date[1])
        if update.fields["System.State"].new_value == "Blocked":
            try:
                blocked_date = "", format_date(
                    update.fields["System.RevisedDate"].old_value
                )
                logger.debug("DT revised end_date %s", blocked_date[1])
            except KeyError:
                blocked_date = "", format_date(
                    update.fields["System.ChangedDate"].old_value
                )
                logger.debug("DT changed end_date %s", blocked_date[1])

    # if the story was never active but eventually closed, return Error
    if active_date is None and end_date is not None:
        assessment_metrics["Error"].append(
            f"DT: User Story: {updates[0].fields['System.Id'].new_value} "
            "was never set to active before it was closed"
        )
        assessment_metrics["DT"] = "Error"
        new_val = updates[0].fields["System.Id"].new_value
        return {
            f"Error: DT: User Story: {new_val}"
            " was never set to active before it was closed"
        }

    # if the story was blocked and never closed, return N/A
    if blocked_date is not None:
        end_dt_cmpr = end_date is not None and blocked_date[1] > end_date[1]
        if end_date is None or (end_dt_cmpr):
            assessment_metrics["DT"] = "N/A"
            return {"DT": "N/A"}

    # if the story was never active, return N/A
    if active_date is None:
        assessment_metrics["DT"] = "N/A"
        return {"DT": "N/A"}

    # if the story hasn't been closed yet, set the end date to now
    if end_date is None:
        end_date = "*", datetime.datetime.now(datetime.timezone.utc)

    time = formatTime(end_date[1] - active_date)
    logger.debug(
        "Metric[DT]:"
        + f"{end_date[1]}"
        + "-"
        + f"{active_date}"
        + ":"
        + end_date[0]
        + f"{time}"
    )
    assessment_metrics["DT"] = end_date[0] + time
    return end_date[0] + time


def getWorkitemFields(conn: Connection, project_name: str, work_item_id: int) -> list:
    """_summary_

    Args:
        conn (Connection): Azure DevOps Connection object.
        project_name (str): Work Project Name
        work_item_id (int): Work Item ID

    Returns:
        list: list of fields in work item.
    """
    # Get a reference to the work item tracking client
    _cl = conn.clients.get_work_item_tracking_client()
    logger.debug("Connection to Work Item Tracking Client Established")

    # Use the work item tracking client to query the work item properties
    try:
        work_item = _cl.get_work_item(work_item_id, project_name, expand="all")
    except AzureDevOpsServiceError as exc:
        logger.warning(exc)
        return None

    return work_item.fields if work_item.fields else None


def getWorkItemHistory(
    connection: Connection, project_name: str, work_item_id: int
) -> list:
    """Get WorkItem history.

    Args:
        connection (azure.devops.connection.Connection): _description_
        project_name (str): Project Name for User Story
        work_item_id (int): User Story Work Item ID

    Returns:
        (dict): _description_
    """
    # Get a reference to the work item tracking client
    _cl = connection.clients.get_work_item_tracking_client()
    # Get the updates made to the work item
    updates = _cl.get_updates(work_item_id, project_name)

    # Find the updates for the System.Title field and log them
    for field in required_fields:
        logger.debug("Updates for %s:", field)
        for update in updates:
            if update.fields is not None and field in update.fields:
                logger.debug(
                    f"{update.revised_date}"
                    + ": "
                    + f"{update.fields[field].old_value}"
                    + "->"
                    + f"{update.fields[field].new_value}"
                )

    return updates


def getConnection() -> Connection:
    org_url = os.environ.get("ORGANIZATION_URI", DEFAULT_ORG_URI)
    logger.debug("Obtained Organization URI")

    # user-assigned managed identity client ID
    user_assigned_client_id = os.environ.get(
        "USER_ASSIGNED_CLIENT_ID", DEFAULT_CLIENT_ID
    )

    # Get the user-assigned managed identity credential
    credential = ManagedIdentityCredential(client_id=user_assigned_client_id)

    # Get the key vault and secret name from environment variables
    logger.debug("Obtained Key Vault URI")
    logger.debug("Obtained Secret Name")

    # Adapt the credential for use with Azure DevOps
    creds = AzureIdentityCredentialAdapter(credential)

    # Establish a connection to Azure DevOps using managed identity
    # creds = BasicTokenAuthentication({"access_token": token.token})
    # connection = Connection(base_url=organization_url, creds=creds)

    # Create a connection to Azure DevOps
    return Connection(base_url=org_url, creds=creds)


def getProjectName() -> str:
    # Project name
    project_name = os.environ.get("PROJECT_NAME", DEFAULT_PROJECT_NAME)
    logger.debug("Obtained Project Name")
    return project_name


def processUserStory(user_story_id: int) -> str:
    """Process User Story with additional metrics.

    Args:
        user_story_id (int): Azure System ID

    Returns:
        str: User Story Metrics HTTP Response
    """
    logger.info("Starting BuildIntakeMetrics")
    logger.debug("User Story ID: %s", user_story_id)

    # Azure DevOps organization URL
    project_name = getProjectName()
    conn = getConnection()
    logger.debug("Connection to Azure DevOps Established")

    # Get the work item fields and return error if there isn't one
    userstory_fields = getWorkitemFields(conn, project_name, user_story_id)
    if userstory_fields is None:
        dbg_msg = "Error: Couldn't get workitem fields for User Story: %s"
        logger.debug(dbg_msg, user_story_id)
        return json.dumps(
            {
                "User Story": user_story_id,
                "TTE": "Error",
                "TTS": "Error",
                "DT": "Error",
                "TTD": "Error",
                "Error": [f"Error workitem fields : {user_story_id}"],
            },
            indent=3,
        )
    if userstory_fields["System.WorkItemType"] != workitemType.USERSTORY.value:
        logger.debug("Error: Work Item: %s is not a User Story", user_story_id)
        return json.dumps(
            {
                "User Story": user_story_id,
                "TTE": "Error",
                "TTS": "Error",
                "DT": "Error",
                "TTD": "Error",
                "Error": ["Work Item: {user_story_id} is not a User Story"],
            },
            indent=3,
        )

    logger.debug("User Story ID: %s\n", userstory_fields["System.Id"])
    logger.debug("User Story: %s\n", userstory_fields["System.Title"])

    # Get the Assessment Task ID and return error if there isn't one
    assessment_task_id = getAssessmentTask(conn, project_name, user_story_id)
    if assessment_task_id is None:
        logger.warning(
            "Error: No Assessment Task found in User Story: %s", user_story_id
        )
        return json.dumps(
            {
                "User Story": user_story_id,
                "TTE": "Error",
                "TTS": "Error",
                "DT": "Error",
                "TTD": "Error",
                "Error": [f"No Task found in User Story: {user_story_id}"],
            },
            indent=3,
        )

    # Get the history of the User Story and
    # Assessment Task then get the metrics
    logger.debug("User Story History: ")
    story_updates = getWorkItemHistory(conn, project_name, user_story_id)
    logger.debug("Work Item History: ")
    task_updates = getWorkItemHistory(conn, project_name, assessment_task_id)
    logger.info("Calculating Metrics...")
    assessment_metrics = getMetrics(user_story_id, story_updates, task_updates)

    logger.info("Metrics Calculated")

    # Log the metrics and return the output
    metric_out = json.dumps(assessment_metrics, indent=3)
    logger.debug("Output Metrics: %s", metric_out)
    return metric_out
