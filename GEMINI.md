# Zscaler URL Checker - K-12 Edition

This project is a tool to check URL categorization and filtering status in Zscaler ZIA, specifically tailored for K-12 educational environments (Elementary, Middle School, and High School levels).

## Project Overview

- **Purpose**: Determine if a URL is blocked or cautioned for different school levels by querying the Zscaler ZIA API and evaluating filtering rules.
- **Interfaces**:
  - **CLI**: `cli.py` for command-line checks and batch processing.
  - **Web**: `web.py` for a Flask-based setup wizard and dashboard.
- **Core Logic**: `zscaler_client.py` handles API authentication (Legacy API Key and OAuth 2.0), URL lookup, and rule evaluation through the `ZscalerClient` class.

## Technical Stack

- **Language**: Python 3.10+
- **Frameworks**: Flask (Web UI), Requests (API calls)
- **API**: Zscaler ZIA Cloud Service API (v1)
- **Configuration**: Local JSON file (`zscaler_checker_config.json`) for credentials and mappings.

## Core Mandates & Engineering Standards

### Security & Privacy
- **Credentials**: Never log, print, or commit `zscaler_checker_config.json` or any API keys/OAuth secrets.
- **Environment**: Be mindful of sensitive data in configuration files.

### Development Guidelines
- **API Reliability**: Always implement robust error handling for Zscaler API calls (e.g., rate limits, timeouts, authentication failures). Use the `ZscalerClient` class for all API interactions.
- **Authentication**: Maintain support for both legacy API key obfuscation and OAuth 2.0 flows.
- **Rule Evaluation**: Ensure logic in `zscaler_client.py` accurately reflects Zscaler's rule matching (e.g., matching by location, groups, categories).
- **Code Style**: Adhere to PEP 8 standards for Python code. Use clear, descriptive variable names and document complex logic with docstrings and comments.
- **Web UI**: Keep `index.html` and `setup.html` updated alongside changes in `web.py`. Note that templates are currently in the root directory.

### Testing & Validation
- **Verification**: Always test changes against a live Zscaler environment if possible, or use mocks/simulated API responses for unit tests.
- **Regressions**: Ensure that changes to `zscaler_client.py` do not break either the CLI or Web interfaces.

## Workflow

1.  **Research**: Understand the Zscaler API documentation for any new endpoints or field changes.
2.  **Strategy**: Plan how to integrate new features or fixes across the client, CLI, and Web layers.
3.  **Execution**: Apply surgical changes to the relevant files.
4.  **Validation**: Verify correctness using CLI commands and the Web UI.
