from agent_gateway.interfaces.websocket.server import *  # noqa: F401,F403

import sys as _sys

from agent_gateway.interfaces.websocket import server as _target

_sys.modules[__name__] = _target
