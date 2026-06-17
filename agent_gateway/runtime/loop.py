from agent_gateway.application.loop import *  # noqa: F401,F403

import sys as _sys

from agent_gateway.application import loop as _target

_sys.modules[__name__] = _target
