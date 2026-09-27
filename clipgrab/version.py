"""
YouCut — Application version constant.

This is the single source of truth for the current app version.
Bump this before releasing a new version tag (e.g., git tag v1.0.1).
"""

# Application name and metadata
APP_NAME = "YouCut"
APP_AUTHOR = "sHUBH"

# Current application version
APP_VERSION = "1.0.0"

# GitHub repository coordinates for live release checking
GITHUB_REPO_OWNER = "Ady1107"
GITHUB_REPO_NAME = "YouCut"
GITHUB_API_LATEST_RELEASE = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"

# Direct release check endpoint
VERSION_CHECK_URL = GITHUB_API_LATEST_RELEASE
