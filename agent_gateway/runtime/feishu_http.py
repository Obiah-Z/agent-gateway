from agent_gateway.interfaces.feishu.http import *  # noqa: F401,F403

import sys as _sys

from agent_gateway.interfaces.feishu import http as _target

_sys.modules[__name__] = _target
