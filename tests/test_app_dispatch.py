"""App 디스패치 — 데코레이터 계약과 인자 주입.

이 파일이 프로젝트의 핵심 약속을 지킨다: **핸들러 본문은 Slack Bolt 와 동일하다.**
"""

from __future__ import annotations

import re
from typing import Any

import pytest

import fixtures
from conftest import FakeClient, drain
from mattermost_bolt import App, BoltError

# -- message / event -------------------------------------------------------


def test_message_handler_receives_slack_shaped_message(app: App, fake_client: FakeClient) -> None:
    seen: list[dict[str, Any]] = []

    @app.message("spike")
    def handler(message, say):
        seen.append(message)
        say(f"echo: {message['text']}")

    app.handle_ws_frame(fixtures.ws_posted())
    drain(app)

    assert len(seen) == 1
    assert seen[0]["user"] == fixtures.USER_ID
    assert seen[0]["channel"] == fixtures.CHANNEL_ID
    posted = fake_client.of("chat_postMessage")
    assert len(posted) == 1
    assert posted[0]["channel"] == fixtures.CHANNEL_ID
    assert posted[0]["text"].startswith("echo: spike message")


def test_message_pattern_is_a_regex_search(app: App) -> None:
    hits: list[str] = []

    @app.message(re.compile(r"deploy (\w+)"))
    def handler(context):
        hits.append(context["matches"][0])

    app.handle_ws_frame(fixtures.ws_posted(message="please deploy prod now"))
    drain(app)
    assert hits == ["prod"]


def test_message_without_pattern_matches_everything(app: App) -> None:
    calls: list[int] = []

    @app.message()
    def handler(message):
        calls.append(1)

    app.handle_ws_frame(fixtures.ws_posted(message="anything at all"))
    drain(app)
    assert calls == [1]


def test_bot_messages_are_ignored_by_default(app: App) -> None:
    """봇이 자기 메시지에 반응하면 무한 루프가 된다."""
    calls: list[int] = []

    @app.message("hello")
    def handler(message):
        calls.append(1)

    app.handle_ws_frame(fixtures.ws_posted(message="hello", props={"from_bot": "true"}))
    drain(app)
    assert calls == []


def test_own_messages_are_dropped(app: App) -> None:
    calls: list[int] = []

    @app.message("hello")
    def handler(message):
        calls.append(1)

    # app 픽스처의 봇 id 와 같은 작성자
    app.handle_ws_frame(fixtures.ws_posted(message="hello", user_id="botuser00000000000000000a"))
    drain(app)
    assert calls == []


def test_event_decorator_matches_type(app: App) -> None:
    reactions: list[str] = []

    @app.event("reaction_added")
    def handler(event):
        reactions.append(event["reaction"])

    app.handle_ws_frame(fixtures.ws_reaction_added())
    drain(app)
    assert reactions == ["+1"]


def test_event_subtype_constraint(app: App) -> None:
    changed: list[int] = []

    @app.event({"type": "message", "subtype": "message_changed"})
    def handler(event):
        changed.append(1)

    app.handle_ws_frame(fixtures.ws_posted())  # subtype 없음 → 매칭 안 됨
    app.handle_ws_frame(fixtures.ws_post_edited())
    drain(app)
    assert changed == [1]


def test_say_supports_thread_ts(app: App, fake_client: FakeClient) -> None:
    @app.message("spike")
    def handler(message, say):
        say("reply", thread_ts=message["ts"])

    app.handle_ws_frame(fixtures.ws_posted())
    drain(app)
    assert fake_client.last["thread_ts"] == fixtures.POST_ID


# -- command ---------------------------------------------------------------


def test_command_over_http_returns_ack_body(http_app: App) -> None:
    @http_app.command("/boltspike")
    def handler(ack, command):
        ack(f"got {command['text']}")

    response = http_app.handle_command(fixtures.http_command_form("hello world"))
    assert response.status == 200
    assert response.body == {"text": "got hello world", "response_type": "ephemeral"}


def test_command_ack_in_channel(http_app: App) -> None:
    @http_app.command("/boltspike")
    def handler(ack):
        ack("공개 응답", response_type="in_channel")

    response = http_app.handle_command(fixtures.http_command_form())
    assert response.body["response_type"] == "in_channel"


def test_command_matches_with_or_without_slash(http_app: App) -> None:
    calls: list[int] = []

    @http_app.command("boltspike")  # 슬래시 없이 등록
    def handler(ack):
        calls.append(1)
        ack()

    http_app.handle_command(fixtures.http_command_form())
    assert calls == [1]


def test_respond_uses_response_url(http_app: App, fake_client: FakeClient) -> None:
    @http_app.command("/boltspike")
    def handler(ack, respond):
        ack()
        respond("나중에 온 응답")

    http_app.handle_command(fixtures.http_command_form())
    calls = fake_client.of("respond_to_url")
    assert len(calls) == 1
    assert calls[0]["body"]["text"] == "나중에 온 응답"


def test_pseudo_socket_mode_routes_slash_text_to_command(app: App, fake_client: FakeClient) -> None:
    """socket 모드에서는 WebSocket 텍스트로 명령을 처리한다 (결정 D3)."""
    seen: list[str] = []

    @app.command("/deploy")
    def handler(ack, command):
        ack()
        seen.append(command["text"])

    app.handle_ws_frame(fixtures.ws_posted(message="/deploy prod"))
    drain(app)
    assert seen == ["prod"]


def test_pseudo_socket_mode_ignores_unregistered_commands(app: App) -> None:
    calls: list[int] = []

    @app.command("/deploy")
    def handler(ack):
        calls.append(1)
        ack()

    app.handle_ws_frame(fixtures.ws_posted(message="/unknown thing"))
    drain(app)
    assert calls == []


def test_unhandled_command_returns_helpful_body(http_app: App) -> None:
    response = http_app.handle_command(fixtures.http_command_form())
    assert response.status == 200
    assert "리스너가 없습니다" in response.body["text"]


def test_unhandled_can_be_strict() -> None:
    strict = App(
        token="t" * 26,
        server_url="http://mm.test",
        raise_error_for_unhandled_request=True,
    )
    strict._bot_user_id = "b" * 26
    response = strict.handle_command(fixtures.http_command_form())
    # 예외는 에러 핸들러 경로로 흡수되고 200 이 나간다 — Mattermost 가 500 을 표시하지 않도록.
    assert response.status == 200


# -- action / view ---------------------------------------------------------


def test_action_handler_gets_block_actions(http_app: App) -> None:
    seen: list[dict[str, Any]] = []

    @http_app.action("spike_button")
    def handler(ack, action, body):
        ack()
        seen.append(action)

    response = http_app.handle_action(fixtures.http_action_body())
    assert response.status == 200
    assert seen[0]["action_id"] == "spike_button"
    assert seen[0]["value"] == "spike-value"


def test_action_respond_updates_original_message(http_app: App) -> None:
    @http_app.action("spike_button")
    def handler(ack, respond):
        ack()
        respond("처리 완료", replace_original=True)

    response = http_app.handle_action(fixtures.http_action_body())
    assert response.body["update"]["message"] == "처리 완료"


def test_action_respond_sends_ephemeral_by_default(http_app: App) -> None:
    @http_app.action("spike_button")
    def handler(ack, respond):
        ack()
        respond("당신만 봅니다")

    response = http_app.handle_action(fixtures.http_action_body())
    assert response.body["ephemeral_text"] == "당신만 봅니다"


def test_action_matcher_by_dict(http_app: App) -> None:
    calls: list[int] = []

    @http_app.action({"action_id": "spike_button", "block_id": "spike_block"})
    def handler(ack):
        calls.append(1)
        ack()

    http_app.handle_action(fixtures.http_action_body())
    assert calls == [1]


def test_view_submission_errors(http_app: App) -> None:
    from mattermost_bolt.blocks.kit import view_to_dialog

    view = {
        "type": "modal",
        "callback_id": "spike_dialog",
        "title": {"type": "plain_text", "text": "T"},
        "blocks": [
            {
                "type": "input",
                "block_id": "name_block",
                "label": {"type": "plain_text", "text": "이름"},
                "element": {"type": "plain_text_input", "action_id": "name_input"},
            }
        ],
    }
    dialog, _ = view_to_dialog(view, action_url="http://app.test/mmbolt/dialogs")

    @http_app.view("spike_dialog")
    def handler(ack, view):
        ack(response_action="errors", errors={"name_block": "너무 짧습니다"})

    response = http_app.handle_dialog(
        fixtures.http_dialog_body(state=dialog["state"], submission={"e0": "가"})
    )
    assert response.body == {"errors": {"e0": "너무 짧습니다"}}


def test_view_closed_handler(http_app: App) -> None:
    calls: list[int] = []

    @http_app.view_closed("spike_dialog")
    def handler(ack):
        calls.append(1)
        ack()

    http_app.handle_dialog(fixtures.http_dialog_body(cancelled=True))
    assert calls == [1]


# -- 미들웨어 / 에러 -------------------------------------------------------


def test_global_middleware_runs_before_listener(app: App) -> None:
    order: list[str] = []

    @app.use
    def mw(context, next):
        order.append("middleware")
        context["injected"] = 7
        next()

    @app.message("spike")
    def handler(context):
        order.append(f"listener:{context['injected']}")

    app.handle_ws_frame(fixtures.ws_posted())
    drain(app)
    assert order == ["middleware", "listener:7"]


def test_middleware_can_short_circuit(app: App) -> None:
    calls: list[int] = []

    @app.use
    def blocker(next):
        return  # next() 를 부르지 않는다

    @app.message("spike")
    def handler(message):
        calls.append(1)

    app.handle_ws_frame(fixtures.ws_posted())
    drain(app)
    assert calls == []


def test_listener_middleware(http_app: App) -> None:
    order: list[str] = []

    def only_admin(context, next):
        order.append("check")
        next()

    @http_app.command("/boltspike", middleware=[only_admin])
    def handler(ack):
        order.append("run")
        ack()

    http_app.handle_command(fixtures.http_command_form())
    assert order == ["check", "run"]


def test_error_handler_receives_exception(app: App) -> None:
    captured: list[Exception] = []

    @app.error
    def on_error(error, logger):
        captured.append(error)

    @app.message("spike")
    def handler(message):
        raise RuntimeError("boom")

    app.handle_ws_frame(fixtures.ws_posted())
    drain(app)
    assert len(captured) == 1
    assert str(captured[0]) == "boom"


def test_listener_exception_does_not_break_http_response(http_app: App) -> None:
    @http_app.command("/boltspike")
    def handler(ack):
        raise RuntimeError("boom")

    response = http_app.handle_command(fixtures.http_command_form())
    assert response.status == 200
    assert "오류" in response.body["text"]


# -- 설정 ------------------------------------------------------------------


def test_app_requires_token_and_server_url() -> None:
    with pytest.raises(BoltError, match="server_url"):
        App(token="x")


def test_socket_mode_warns_about_action_listeners(
    caplog: pytest.LogCaptureFixture, fake_client: FakeClient
) -> None:
    """조용히 동작하지 않는 것이 가장 나쁘다 — 반드시 경고한다."""
    instance = App(
        token="t" * 26,
        server_url="http://mm.test",
        client=fake_client,  # type: ignore[arg-type]
        mode="socket",
    )

    @instance.action("x")
    def handler(ack):
        ack()

    with caplog.at_level("WARNING", logger="mattermost_bolt"):
        instance._warn_on_mode("socket")
    assert any("socket" in record.getMessage() for record in caplog.records)


def test_auto_mode_picks_http_when_actions_exist(fake_client: FakeClient) -> None:
    instance = App(
        token="t" * 26,
        server_url="http://mm.test",
        client=fake_client,  # type: ignore[arg-type]
    )
    assert instance.resolved_mode() == "socket"

    @instance.action("x")
    def handler(ack):
        ack()

    assert instance.resolved_mode() == "http"


def test_integration_urls_are_derived_from_request_url(http_app: App) -> None:
    assert http_app.command_url == "http://app.test/mmbolt/commands"
    assert http_app.action_url == "http://app.test/mmbolt/actions"
    assert http_app.dialog_url == "http://app.test/mmbolt/dialogs"


def test_unknown_handler_argument_is_a_clear_error(app: App) -> None:
    errors: list[Exception] = []

    @app.error
    def on_error(error):
        errors.append(error)

    @app.message("spike")
    def handler(message, nonexistent_arg):
        pass

    app.handle_ws_frame(fixtures.ws_posted())
    drain(app)
    assert isinstance(errors[0], TypeError)
    assert "nonexistent_arg" in str(errors[0])


def test_handler_with_kwargs_gets_everything(app: App) -> None:
    seen: list[dict[str, Any]] = []

    @app.message("spike")
    def handler(**kwargs):
        seen.append(kwargs)

    app.handle_ws_frame(fixtures.ws_posted())
    drain(app)
    assert {"say", "client", "logger", "context", "body", "event", "message"} <= set(seen[0])
