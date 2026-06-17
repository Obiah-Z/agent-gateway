from agent_gateway.application.dispatcher import *  # noqa: F401,F403

import sys as _sys

from agent_gateway.application import dispatcher as _target

_sys.modules[__name__] = _target
