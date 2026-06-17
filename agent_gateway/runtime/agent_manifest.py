from agent_gateway.application.agent_manifest import *  # noqa: F401,F403

import sys as _sys

from agent_gateway.application import agent_manifest as _target

_sys.modules[__name__] = _target
