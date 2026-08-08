"""import 하는 것만으로 shim 을 설치하는 모듈.

import mattermost_bolt.compat.install   # noqa: F401
from slack_bolt import App              # 실제로는 mattermost_bolt.App
"""

from __future__ import annotations

from .shim import install

install()
