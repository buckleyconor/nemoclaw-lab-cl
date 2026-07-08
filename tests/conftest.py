"""Shared test configuration.

The Gateway's post-approval executor paces its operator narration with demo
sleeps (services/gateway/executor.py); zero them before any test module
builds an app.
"""

import os

os.environ["GATEWAY_NARRATION_DELAY_SCALE"] = "0"
