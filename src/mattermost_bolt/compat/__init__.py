"""``slack_bolt`` 이름을 그대로 쓰는 호환 레이어 (실행계획서 결정 D6).

``mattermost_bolt.compat.install`` 을 import 하면 ``slack_bolt`` /
``slack_sdk`` 가 이 패키지로 치환된다. import 문조차 고치지 않고
기존 앱을 그대로 돌려보기 위한 **과도기·검증용** 수단이다.

운영 코드에서는 ``from mattermost_bolt import App`` 처럼 명시적으로 쓰는 편이
스택 트레이스와 디버깅이 훨씬 명확하다.
"""

from __future__ import annotations

from .shim import install, uninstall

__all__ = ["install", "uninstall"]
