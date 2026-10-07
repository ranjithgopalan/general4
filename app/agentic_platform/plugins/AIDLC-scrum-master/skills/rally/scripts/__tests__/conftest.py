"""
Shared pytest fixtures for Rally CLI tests.
"""

import pytest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# Removed mock_rally_api fixture (rally_cli.py deleted)


@pytest.fixture
def sample_story():
    """Sample Rally user story data."""
    return {
        "FormattedID": "US12345",
        "Name": "Test Story",
        "PlanEstimate": 3,
        "ScheduleState": "Defined",
        "Owner": {"_refObjectName": "John Doe"},
        "PortfolioItem": {"_refObjectName": "Feature Name"},
        "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/hierarchicalrequirement/12345"
    }


@pytest.fixture
def sample_stories():
    """Sample list of Rally user stories."""
    return [
        {
            "FormattedID": "US12345",
            "Name": "Test Story 1",
            "PlanEstimate": 3,
            "ScheduleState": "Defined",
            "Owner": {"_refObjectName": "John Doe"},
            "PortfolioItem": {"_refObjectName": "Feature Name"},
            "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/hierarchicalrequirement/12345"
        },
        {
            "FormattedID": "US12346",
            "Name": "Test Story 2",
            "PlanEstimate": 5,
            "ScheduleState": "In-Progress",
            "Owner": {"_refObjectName": "Jane Smith"},
            "PortfolioItem": {"_refObjectName": "Feature Name"},
            "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/hierarchicalrequirement/12346"
        }
    ]


@pytest.fixture
def sample_tasks():
    """Sample Rally task data."""
    return [
        {
            "FormattedID": "TA101",
            "Name": "Setup",
            "Estimate": 8.0,
            "State": "Defined",
            "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/task/101"
        },
        {
            "FormattedID": "TA102",
            "Name": "Implementation",
            "Estimate": 12.0,
            "State": "Defined",
            "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/task/102"
        },
        {
            "FormattedID": "TA103",
            "Name": "Testing",
            "Estimate": 4.0,
            "State": "Defined",
            "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/task/103"
        }
    ]


@pytest.fixture
def sample_feature():
    """Sample Rally feature data."""
    return {
        "FormattedID": "F12345",
        "Name": "Test Feature",
        "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/portfolioitem/feature/12345",
        "Release": {
            "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/release/100",
            "Name": "2026.PI1"
        },
        "Iteration": {
            "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/iteration/200",
            "Name": "2026.PI1.Iteration1"
        }
    }


@pytest.fixture
def sample_epic():
    """Sample Rally epic data."""
    return {
        "FormattedID": "E12345",
        "Name": "Test Epic",
        "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/portfolioitem/epic/12345"
    }


@pytest.fixture
def sample_iteration():
    """Sample Rally iteration data."""
    return {
        "Name": "2026.PI1.Iteration1",
        "ObjectID": "12345",
        "StartDate": "2026-01-13",
        "EndDate": "2026-01-26",
        "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/iteration/12345"
    }


@pytest.fixture
def sample_user():
    """Sample Rally user data."""
    return {
        "UserName": "john.doe",
        "DisplayName": "John Doe",
        "EmailAddress": "john.doe@company.com",
        "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/user/12345"
    }


@pytest.fixture
def sample_release():
    """Sample Rally release data."""
    return {
        "Name": "2026.PI1",
        "ReleaseStartDate": "2026-01-01",
        "ReleaseDate": "2026-03-31",
        "ObjectID": "12345",
        "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/release/12345"
    }
