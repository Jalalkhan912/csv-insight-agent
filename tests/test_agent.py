"""Tests for the agent API. All LLM calls are mocked."""
import io
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import fastapi_agent_main as m

CSV = b"Pclass,Survived\n1,1\n1,0\n2,1\n3,0\n3,0\n"


def fake_completion(content=None, tool_calls=None):
    msg = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        model_dump=lambda **_: {"role": "assistant", "content": content,
                                "tool_calls": [{"id": t.id} for t in tool_calls or []]},
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "CHARTS_DIR", tmp_path / "charts")
    m.UPLOAD_DIR.mkdir()
    m.CHARTS_DIR.mkdir()
    m.active_sessions.clear()
    return TestClient(m.app)


def upload(api, name="titanic.csv", body=CSV):
    return api.post("/upload", files={"file": (name, io.BytesIO(body), "text/csv")})


def test_general_chat_reuses_session_and_stays_general(api, monkeypatch):
    seen = []

    def create(**kwargs):
        seen.append(kwargs)
        return fake_completion("hi")

    monkeypatch.setattr(m.client.chat.completions, "create", create)

    first = api.post("/chat", json={"message": "hello"}).json()
    second = api.post("/chat", json={"session_id": first["session_id"], "message": "again"}).json()

    assert first["mode"] == second["mode"] == "general"
    assert second["session_id"] == first["session_id"]
    assert "tools" not in seen[1]  # general mode never gets data tools
    # second call carries the earlier turn as memory
    assert [x["content"] for x in seen[1]["messages"] if x["role"] == "user"] == ["hello", "again"]


def test_upload_strips_path_from_filename(api):
    res = upload(api, name="../../evil.csv")
    assert res.status_code == 200
    assert res.json()["filename"] == "evil.csv"
    saved = list(m.UPLOAD_DIR.iterdir())
    assert len(saved) == 1 and saved[0].parent == m.UPLOAD_DIR


def test_upload_rejects_non_csv(api):
    assert upload(api, name="data.txt").status_code == 400


def test_lookup_truncates_large_results(api, monkeypatch):
    big = "n\n" + "\n".join(str(i) for i in range(500)) + "\n"
    sid = upload(api, body=big.encode()).json()["session_id"]
    monkeypatch.setattr(m, "generate_sql_query", lambda *a: "```sql\nSELECT * FROM t\n```".replace("t", m.active_sessions[sid]["table_name"]))

    out = m.lookup_given_dataset("all rows", sid)
    assert "500 rows total" in out
    assert len(m.active_sessions[sid]["last_result"]) == 500


def test_lookup_rejects_non_select(api, monkeypatch):
    sid = upload(api).json()["session_id"]
    monkeypatch.setattr(m, "generate_sql_query", lambda *a: "DROP TABLE x")
    assert m.lookup_given_dataset("x", sid).startswith("Error accessing data")


def test_chart_runs_once_on_query_result(api, monkeypatch):
    sid = upload(api).json()["session_id"]
    m.active_sessions[sid]["last_result"] = pd.DataFrame({"Pclass": [1, 2, 3], "rate": [0.5, 1.0, 0.0]})

    seen_columns, config_calls = [], []

    def create_chart(data, goal, columns):
        config_calls.append(goal)
        seen_columns.append(columns)
        return "plt.bar(df['Pclass'], df['rate'])"

    monkeypatch.setattr(m, "create_chart", create_chart)

    responses = iter([
        fake_completion(tool_calls=[tool_call("1", "generate_visualization",
                                              '{"data": "x", "visualization_goal": "rate by class"}')]),
        fake_completion("Here is the chart."),
    ])
    monkeypatch.setattr(m.client.chat.completions, "create", lambda **_: next(responses))

    res = api.post("/chat", json={"session_id": sid, "message": "chart it"}).json()

    assert res["mode"] == "data_analysis"
    assert len(res["chart_urls"]) == 1
    assert config_calls == ["rate by class"]  # generated exactly once
    assert seen_columns == [["Pclass", "rate"]]  # plotted the query result
    assert len(list(m.CHARTS_DIR.iterdir())) == 1


def test_delete_session_removes_file(api):
    sid = upload(api).json()["session_id"]
    assert api.delete(f"/session/{sid}").status_code == 200
    assert list(m.UPLOAD_DIR.iterdir()) == []
    assert api.get(f"/session/{sid}").status_code == 404


def test_rate_limit_gives_friendly_message(api, monkeypatch):
    class RateLimited(Exception):
        status_code = 429

    def create(**_):
        raise RateLimited("RESOURCE_EXHAUSTED")

    monkeypatch.setattr(m.client.chat.completions, "create", create)
    res = api.post("/chat", json={"message": "hello"})
    assert res.status_code == 200
    assert "quota" in res.json()["response"]
