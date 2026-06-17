from agent_gateway.application.control_plane import *  # noqa: F401,F403

import sys as _sys

from agent_gateway.application import control_plane as _target

_sys.modules[__name__] = _target
