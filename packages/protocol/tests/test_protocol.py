import json

import pytest
from robotcell_protocol import (
    FusionFrame,
    JobStatus,
    JobStatusUpdate,
    JobSubmit,
    ProtocolError,
    RobotActivity,
    RobotTelemetry,
    can_transition,
    decode,
    encode,
    topics,
)


def test_round_trip_keeps_message_and_ids():
    submit = JobSubmit(
        job_id="j1", robot_id="robot-1", pick="A", place="B", created_at=1, start_deadline=2
    )
    raw = encode(submit, msg_id="m1", ts=123)

    decoded = decode(raw)

    assert decoded.msg_id == "m1"
    assert decoded.ts == 123
    assert decoded.message == submit


def test_telemetry_round_trips_with_a_tool_position():
    telemetry = RobotTelemetry(
        robot_id="robot-1", state=RobotActivity.BUSY, tool=(2.5, 6.0), holding=True, at=5
    )

    decoded = decode(encode(telemetry)).message

    assert decoded == telemetry
    assert topics.robot_telemetry("robot-1") == "robots/robot-1/telemetry"


def test_envelope_shape_is_stable():
    raw = encode(JobStatusUpdate(job_id="j1", robot_id="robot-1", status="queued", seq=2, at=5))
    body = json.loads(raw)

    assert set(body) == {"v", "type", "msg_id", "ts", "data"}
    assert body["type"] == "job.status"
    assert body["data"]["status"] == "queued"


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b'{"v": 2, "type": "job.cancel", "msg_id": "x", "ts": 1, "data": {"job_id": "j"}}',
        b'{"v": 1, "type": "job.explode", "msg_id": "x", "ts": 1, "data": {}}',
        b'{"v": 1, "type": "job.cancel", "msg_id": "x", "ts": 1, "data": {}}',
    ],
)
def test_bad_payloads_raise_protocol_error(payload):
    with pytest.raises(ProtocolError):
        decode(payload)


def test_lifecycle_moves_forward_only():
    assert can_transition(JobStatus.PENDING, JobStatus.RECEIVED)
    assert can_transition(JobStatus.RECEIVED, JobStatus.REJECTED)
    assert can_transition(JobStatus.QUEUED, JobStatus.CANCELLED)
    assert can_transition(JobStatus.RUNNING, JobStatus.DONE)
    assert not can_transition(JobStatus.RUNNING, JobStatus.CANCELLED)
    assert not can_transition(JobStatus.DONE, JobStatus.RUNNING)
    assert not can_transition(JobStatus.QUEUED, JobStatus.RECEIVED)
    assert all(not can_transition(s, JobStatus.PENDING) for s in JobStatus)


def test_site_topics_parse_back():
    parsed = topics.parse_site_topic(topics.robot_state("site-001", "robot-2"))
    assert parsed == topics.SiteTopic("site-001", topics.Channel.ROBOT_STATE, "robot-2")
    assert topics.parse_site_topic(topics.events("site-001")).channel == topics.Channel.EVENTS
    assert topics.parse_site_topic("rc/v1/other/site-001/events") is None
    assert topics.robot_from_fault_topic(topics.sim_robot_fault("robot-1")) == "robot-1"


def test_fusion_frame_accepts_the_site_format():
    raw = {
        "ts": 1763030400123,
        "t_ms": 1763030400093,
        "objects": [
            {
                "entity_id": 0,
                "gid": 1042,
                "class": "person",
                "kf_pos": [12.4, 6.8],
                "avg_pos": [12.3, 6.8],
                "velocity": [0.6, -0.2],
                "speed": 0.63,
                "motion_dir": 1,
                "yaw_heading": 341.6,
                "yaw_heading_known": True,
                "yaw_body": 0.0,
                "yaw_body_known": False,
                "yaw_rate": 0.0,
                "imu": False,
                "attributes": {"vest": 1, "is_loaded": -1},
            }
        ],
    }

    frame = FusionFrame.model_validate(raw)

    assert frame.objects[0].class_ == "person"
    assert json.loads(frame.to_json())["objects"][0]["class"] == "person"
