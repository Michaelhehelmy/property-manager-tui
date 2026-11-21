import json

import pytest

from app_core.assistant import brain as brain_mod
from app_core.assistant import executor, tool_registry, workflow_engine
from app_core.assistant.brain import (
    BRAIN_VERSION,
    Brain,
    build_default_brain,
    load_brain,
    normalize_brain,
    save_brain,
    sync_tools_into_brain,
    validate_brain,
)
from app_core.assistant.tool_registry import ToolRegistry, register_tools_dict
from app_core.assistant.workflow_engine import WorkflowEngine


def _sample_tools():
    return {
        "echo_tool": {
            "fn": lambda args=None: {"ok": True, "echo": (args or {})},
            "description": "Echo args",
            "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "additionalProperties": False},
        },
        "add_tool": {
            "fn": lambda args=None, ctx=None: {"ok": True, "sum": int((args or {}).get("a", 0)) + int((args or {}).get("b", 0))},
            "description": "Add numbers",
            "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]},
        },
    }


def test_build_and_validate_default_brain():
    b = build_default_brain(_sample_tools())
    assert b["version"] == BRAIN_VERSION
    assert b["agent"]["name"]
    assert isinstance(b["tools"], list)
    assert any(t["id"] == "search_app_knowledge" for t in b["tools"])
    assert any(t["id"] == "echo_tool" for t in b["tools"])
    assert validate_brain(b) == []


def test_validate_brain_errors():
    errs = validate_brain({"version": 999})
    assert any("version" in e for e in errs)
    errs = validate_brain([])
    assert errs
    bad = build_default_brain({})
    bad["tools"] = [{"id": ""}]
    assert any("id is required" in e for e in validate_brain(bad))
    bad = build_default_brain({})
    bad["tools"] = [{"id": "a", "type": "native"}, {"id": "a", "type": "native", "handler": "a"}]
    assert any("duplicate tool id" in e for e in validate_brain(bad))
    bad = build_default_brain({})
    bad["skills"] = [{"id": "s"}, {"id": "s"}]
    assert any("duplicate skill id" in e for e in validate_brain(bad))


def test_normalize_brain_and_filters():
    data = {
        "version": 1,
        "agent": {"name": "  "},
        "tools": [
            {"id": "t1", "type": "knowledge", "enabled": True, "description": "d"},
            "bad",
            {"id": ""},
        ],
        "skills": [{"id": "s1", "enabled": True}, "bad"],
        "knowledge": [{"id": "k1", "content": "hello"}, "bad"],
        "boundaries": ["b1", ""],
        "workflows": {"x": [1]},
    }
    n = normalize_brain(data, tools={})
    assert n["agent"]["name"] == "Manager Assistant"
    assert [t["id"] for t in n["tools"]] == ["t1"]
    assert [s["id"] for s in n["skills"]] == ["s1"]
    assert [k["id"] for k in n["knowledge"]] == ["k1"]
    assert n["boundaries"] == ["b1"]
    assert n["workflows"]["x"] == [1]


def test_save_and_load_brain(tmp_path, monkeypatch):
    p = tmp_path / "agent_brain.json"
    tools = _sample_tools()
    b = build_default_brain(tools)
    b["agent"]["name"] = "Test Agent"
    save_brain(b, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    loaded = load_brain(path=p, tools=tools, force=True)
    assert isinstance(loaded, Brain)
    assert loaded.name == "Test Agent"
    assert loaded.get_tool("echo_tool")["id"] == "echo_tool"
    assert loaded.get_skill("app_knowledge_lookup") is not None
    assert loaded.workflows
    assert loaded.boundaries
    assert loaded.tools(enabled_only=True)
    # cache hit
    again = load_brain(path=p, tools=tools)
    assert again is loaded
    # invalid file falls back
    p.write_text("{bad", encoding="utf-8")
    fallback = load_brain(path=p, tools=tools, force=True)
    assert fallback.name == "Manager Assistant"


def test_sync_tools_into_brain(tmp_path):
    p = tmp_path / "agent_brain.json"
    tools = _sample_tools()
    b = build_default_brain({"echo_tool": tools["echo_tool"]})
    save_brain(b, path=p)
    merged = sync_tools_into_brain(tools, path=p, enable_new=False)
    ids = {t["id"] for t in merged["tools"]}
    assert "add_tool" in ids
    add = [t for t in merged["tools"] if t["id"] == "add_tool"][0]
    assert add["enabled"] is False


def test_tool_registry_basic():
    reg = ToolRegistry()
    calls = []

    @reg.register("a", meta={"description": "A"})
    def a(args=None):
        calls.append(args)
        return {"ok": True}

    reg.register("b", lambda args=None: {"ok": args})
    reg.register_many({"c": lambda args=None: {"ok": "c"}, "bad": None})
    assert reg.has("a")
    assert reg.get("a").fn({})["ok"] is True
    assert sorted(reg.handler_ids()) == ["a", "b", "c"]
    assert len(reg.items()) == 3
    reg.clear()
    assert reg.handler_ids() == []
    assert calls == [{}]


def test_register_tools_dict():
    reg = ToolRegistry()
    old = tool_registry.REGISTRY
    try:
        tool_registry.REGISTRY = reg
        n = register_tools_dict(_sample_tools(), source="test")
        assert n == 2
        assert reg.has("echo_tool")
    finally:
        tool_registry.REGISTRY = old


def test_executor_native_and_errors(tmp_path, monkeypatch):
    tools = _sample_tools()
    p = tmp_path / "agent_brain.json"
    b = build_default_brain(tools)
    save_brain(b, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    # registry
    reg = ToolRegistry()
    for tid, spec in tools.items():
        reg.register(tid, spec["fn"], meta={"description": spec.get("description"), "parameters": spec.get("parameters", {})})
    monkeypatch.setattr(executor, "REGISTRY", reg)

    br = load_brain(path=p, tools=tools, force=True)
    res = executor.execute("echo_tool", {"x": "1"}, brain=br)
    assert res["ok"] is True and res["echo"]["x"] == "1"
    res = executor.execute("add_tool", {"a": 2, "b": 3}, brain=br)
    assert res["sum"] == 5
    missing = executor.execute("add_tool", {"a": 2}, brain=br)
    assert missing["error"] == "invalid_args"
    unknown = executor.execute("nope", {}, brain=br)
    assert "Unknown tool" in unknown["error"]
    # disabled
    b2 = build_default_brain(tools)
    for t in b2["tools"]:
        if t["id"] == "echo_tool":
            t["enabled"] = False
    save_brain(b2, path=p)
    br2 = load_brain(path=p, tools=tools, force=True)
    dis = executor.execute("echo_tool", {}, brain=br2)
    assert "disabled" in dis["error"].lower()


def test_executor_knowledge_and_composite(tmp_path, monkeypatch):
    tools = _sample_tools()
    reg = ToolRegistry()
    for tid, spec in tools.items():
        reg.register(tid, spec["fn"], meta={"description": spec.get("description"), "parameters": spec.get("parameters", {})})
    monkeypatch.setattr(executor, "REGISTRY", reg)
    p = tmp_path / "agent_brain.json"
    b = build_default_brain(tools)
    b["knowledge"] = [
        {"id": "k1", "title": "Backup", "section": "Restore", "content": "Use backup and restore", "source_path": "docs/BACKUP.md", "tags": ["backup"]},
        {"id": "k2", "title": "Update", "content": "Update app", "source_path": "docs/UPDATES.md"},
    ]
    b["tools"].append({
        "id": "combo",
        "type": "composite",
        "enabled": True,
        "read_only": True,
        "steps": [
            {"as": "s1", "tool": "echo_tool", "args": {"x": "${args.q}"}},
            {"as": "s2", "tool": "add_tool", "args": {"a": 1, "b": 2}},
        ],
    })
    save_brain(b, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    br = load_brain(path=p, tools=tools, force=True)

    k = executor.execute("search_app_knowledge", {"query": "backup restore", "top_k": 1}, brain=br)
    assert k["ok"] is True and k["count"] >= 1
    c = executor.execute("combo", {"q": "hi"}, brain=br)
    assert c["ok"] is True and c["outputs"]["s2"]["sum"] == 3

    bad = executor.execute("combo", {}, brain=Brain({**br.data, "tools": [{"id":"combo","type":"composite","enabled":True,"steps":[]}]}))
    assert bad["ok"] is False


def test_workflow_engine_basic():
    brain = {
        "skills": [
            {
                "id": "wf",
                "enabled": True,
                "entry_state": "s1",
                "states": {
                    "s1": {"required": ["x"], "gate": "required_fields_present", "next": "s2"},
                    "s2": {"gate": "owner_approved", "next_on_approved": "done", "next_on_rejected": "s1"},
                    "done": {"terminal": True},
                },
            }
        ]
    }
    eng = WorkflowEngine()
    st = eng.start(brain, "wf", data={"spec": {"x": 1}})
    assert st.current_state == "s1"
    st = eng.advance(brain, st)
    assert st.current_state == "s2"
    st = eng.advance(brain, st)
    assert st.status == "waiting_approval"
    eng.set_data(st, "approval", {"approved": True})
    st = eng.advance(brain, st, event="approved")
    assert st.status == "completed"

    st2 = eng.start(brain, "wf", data={"spec": {}})
    st2 = eng.advance(brain, st2)
    assert st2.errors

    with pytest.raises(ValueError):
        eng.start(brain, "missing")


def test_brain_validation_and_normalize_branches():
    assert "manifest must be a JSON object" in validate_brain("nope")[0]
    b = build_default_brain({})
    b["agent"] = "bad"
    b["workflows"] = []
    b["boundaries"] = "bad"
    b["tools"] = "bad"
    b["skills"] = "bad"
    b["knowledge"] = "bad"
    errs = validate_brain(b)
    assert any("agent must be an object" in e for e in errs)
    assert any("workflows must be an object" in e for e in errs)

    b2 = build_default_brain({})
    b2["tools"] = [
        "bad",
        {"type": "native", "handler": "h"},  # missing id
        {"id": "t1", "type": "weird", "handler": "h"},
        {"id": "t2", "type": "native"},
        {"id": "t3", "type": "knowledge", "enabled": "yes", "read_only": "no", "tags": "bad"},
    ]
    b2["skills"] = ["bad", {"name": "x"}, {"id": "s1", "enabled": "yes", "uses_tools": "bad"}]
    errs2 = validate_brain(b2)
    assert any("tools[0] must be an object" in e for e in errs2)
    assert any("tools[1].id is required" in e for e in errs2)
    assert any("type invalid" in e for e in errs2)
    assert any("handler required" in e for e in errs2)
    assert any("enabled must be boolean" in e for e in errs2)
    assert any("read_only must be boolean" in e for e in errs2)
    assert any("tags must be list[str]" in e for e in errs2)
    assert any("skills[0] must be an object" in e for e in errs2)
    assert any("skills[1].id is required" in e for e in errs2)
    assert any("uses_tools must be list[str]" in e for e in errs2)

    n = normalize_brain([], tools={})
    assert n["agent"]["name"] == "Manager Assistant"
    n2 = normalize_brain({"version": 1, "agent": {"name": "X"}, "tools": [{"id": "x", "type": "knowledge"}]}, tools={})
    assert n2["tools"][0]["type"] == "knowledge"


def test_brain_save_invalid_and_get(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    with pytest.raises(ValueError):
        save_brain({"version": 999}, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    b = build_default_brain({})
    save_brain(b, path=p)
    got = brain_mod.get_brain(force=True)
    assert got.name == "Manager Assistant"


def test_executor_edge_cases(tmp_path, monkeypatch):
    tools = _sample_tools()
    p = tmp_path / "agent_brain.json"
    b = build_default_brain(tools)
    b["tools"].append({
        "id": "boom", "type": "native", "handler": "boom", "enabled": True, "read_only": True,
        "args_schema": {"type":"object","properties":{}}
    })
    b["tools"].append({
        "id": "comp2", "type": "composite", "enabled": True, "read_only": True,
        "steps": [{"tool": "", "args": {}}, {"as": "s1", "tool": "echo_tool", "args": {"x": "${missing.val}"}, "stop_on_error": True}]
    })
    b["knowledge"] = [{"id":"k1","content":"alpha beta","tags":["t"],"keywords":["alpha"]}]
    save_brain(b, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    reg = ToolRegistry()
    for tid, spec in tools.items():
        reg.register(tid, spec["fn"], meta={"parameters": spec.get("parameters", {})})
    reg.register("boom", lambda args=None: (_ for _ in ()).throw(RuntimeError("kaboom")))
    monkeypatch.setattr(executor, "REGISTRY", reg)
    br = load_brain(path=p, tools=tools, force=True)

    assert executor.search_knowledge("", brain=br)["ok"] is False
    bad_brain = Brain({**br.data, "tools": br.data.get("tools", []) + [{"id":"badtype","type":"mystery","enabled":True,"read_only":True,"handler":"x","args_schema":{"type":"object","properties":{}}}]})
    res = executor.execute("badtype", {}, brain=bad_brain)
    assert res["error"].startswith("Unsupported tool type")
    boom = executor.execute("boom", {}, brain=br)
    assert "failed" in boom["error"]
    comp = executor.execute("comp2", {}, brain=br)
    assert comp["ok"] is True and len(comp["steps"]) == 2
    # handler unavailable
    b2 = build_default_brain({})
    b2["tools"].append({"id":"ghost","type":"native","handler":"ghost","enabled":True,"read_only":True,"args_schema":{"type":"object","properties":{}}})
    save_brain(b2, path=p)
    br2 = load_brain(path=p, tools={}, force=True)
    reg2 = ToolRegistry()
    monkeypatch.setattr(executor, "REGISTRY", reg2)
    ghost = executor.execute("ghost", {}, brain=br2)
    assert ghost["error"] == "handler_unavailable"


def test_executor_validate_args_types(tmp_path, monkeypatch):
    tools = _sample_tools()
    p = tmp_path / "agent_brain.json"
    b = build_default_brain(tools)
    b["tools"].append({
        "id":"typed","type":"native","handler":"typed","enabled":True,"read_only":True,
        "args_schema":{"type":"object","properties":{"n":{"type":"integer"},"s":{"type":"string"},"b":{"type":"boolean"}}}
    })
    save_brain(b, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    reg = ToolRegistry()
    reg.register("typed", lambda args=None: {"ok": True, "args": args})
    for tid, spec in tools.items():
        reg.register(tid, spec["fn"], meta={"parameters": spec.get("parameters", {})})
    monkeypatch.setattr(executor, "REGISTRY", reg)
    br = load_brain(path=p, tools=tools, force=True)
    bad = executor.execute("typed", {"n": True}, brain=br)
    assert bad["error"] == "invalid_args"
    bad2 = executor.execute("typed", {"s": 1}, brain=br)
    assert bad2["error"] == "invalid_args"


def test_workflow_engine_gates_and_branches():
    eng = WorkflowEngine()
    brain = {"skills":[{"id":"wf","entry_state":"s1","states":{
        "s1":{"gate":"type_allowed","next":"s2"},
        "s2":{"gate":"plan_has_files_tests_risks","next":"s3"},
        "s3":{"gate":"checks_pass","next_on_pass":"s4","next_on_fail":"s1"},
        "s4":{"gate":"owner_approved","next_on_approved":"done","next_on_rejected":"s1"},
        "done":{"terminal":True}
    }}]}
    st = eng.start(brain, "wf", data={"spec":{"type":"bad"}})
    st = eng.advance(brain, st)
    assert "unsupported type" in st.errors[0]
    st.data["spec"]["type"] = "native"
    st = eng.advance(brain, st)
    assert st.current_state == "s2"
    st = eng.advance(brain, st)
    assert "plan missing" in st.errors[0]
    st.data["plan"] = {"files":["a"],"tests":["t"],"risks":["r"]}
    st = eng.advance(brain, st)
    assert st.current_state == "s3"
    st = eng.advance(brain, st)
    assert "validation checks not passing" in st.errors[0]
    st.data["validation"] = {"ok": True}
    st = eng.advance(brain, st)
    assert st.current_state == "s4"
    st = eng.advance(brain, st)
    assert st.status == "waiting_approval"
    st.data["approval"] = {"rejected": True}
    st = eng.advance(brain, st, event="rejected")
    assert st.current_state == "s1" or st.errors

    # unknown gate / missing workflow / invalid state / no transition
    brain2 = {"skills":[{"id":"wf2","entry_state":"a","states":{"a":{"gate":"nope","next":"b"},"b":{}}}]}
    st2 = eng.start(brain2, "wf2")
    st2 = eng.advance(brain2, st2)
    assert any("unknown gate" in e for e in st2.errors)
    st2.current_state = "missing"
    st2 = eng.advance(brain2, st2)
    assert st2.status == "failed"
    brain3 = {"skills":[{"id":"wf3","entry_state":"a","states":{"a":{"gate":"always_pass"}}}]}
    st3 = eng.start(brain3, "wf3")
    st3 = eng.advance(brain3, st3)
    assert st3.status == "failed"


def test_brain_get_brain_and_sync_paths(tmp_path, monkeypatch):
    p = tmp_path / "brain.json"
    b = build_default_brain({"a": {"fn": lambda args=None: {}, "description": "a", "parameters": {"type":"object","properties":{}}}})
    save_brain(b, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    got = brain_mod.get_brain(force=True)
    assert got.get_tool("missing") is None
    assert got.get_skill("missing") is None
    merged = sync_tools_into_brain({"a": {"fn": lambda args=None: {}, "description": "a", "parameters": {}}, "b": {"fn": lambda args=None: {}, "description": "b", "parameters": {}}}, path=p, enable_new=True)
    ids = {t["id"] for t in merged["tools"]}
    assert "b" in ids


def test_executor_composite_and_template_paths(tmp_path, monkeypatch):
    tools = _sample_tools()
    reg = ToolRegistry()
    for tid, spec in tools.items():
        reg.register(tid, spec["fn"], meta={"parameters": spec.get("parameters", {})})
    reg.register("plain", lambda args=None: 5)
    monkeypatch.setattr(executor, "REGISTRY", reg)
    p = tmp_path / "agent_brain.json"
    b = build_default_brain(tools)
    b["tools"].append({"id":"plain","type":"native","handler":"plain","enabled":True,"read_only":True,"args_schema":{"type":"object","properties":{}}})
    b["tools"].append({
        "id":"comp3","type":"composite","enabled":True,"read_only":True,
        "steps":[
            {"as":"s1","tool":"echo_tool","args":{"x":"${args.q}","arr":["${args.q}"],"obj":{"k":"${args.q}"}}},
            {"as":"s2","tool":"plain","args":{}},
        ]
    })
    save_brain(b, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    br = load_brain(path=p, tools=tools, force=True)
    res = executor.execute("comp3", {"q": "Z"}, brain=br)
    assert res["ok"] is True
    assert res["outputs"]["s1"]["echo"]["arr"] == ["Z"]
    assert res["outputs"]["s2"]["ok"] is True and res["outputs"]["s2"]["result"] == 5


def test_workflow_engine_more_branches():
    eng = WorkflowEngine()
    brain = {"skills":[{"id":"wf","entry_state":"a","states":{"a":{"gate":"always_pass","next_on_pass":"b"},"b":{"terminal":True}}}]}
    st = eng.start(brain, "wf")
    st = eng.advance(brain, st)
    assert st.status == "completed"
    st2 = eng.start(brain, "wf")
    st2.status = "completed"
    assert eng.advance(brain, st2).status == "completed"
    step = eng.current_step(brain, st2)
    assert step is None or isinstance(step, dict)


def test_brain_load_validation_warning_and_cache(tmp_path, monkeypatch):
    p = tmp_path / "brain.json"
    # invalid version triggers validation warning path but normalize still returns brain
    p.write_text(json.dumps({"version": 999, "agent": {"name": "X"}, "tools": [], "skills": [], "knowledge": [], "workflows": {}}), encoding="utf-8")
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    b = load_brain(path=p, tools={}, force=True)
    assert b.name == "X"
    b2 = load_brain(path=p, tools={}, force=False)
    assert b2 is b


def test_executor_scoring_and_template_paths(tmp_path, monkeypatch):
    tools = _sample_tools()
    reg = ToolRegistry()
    for tid, spec in tools.items():
        reg.register(tid, spec["fn"], meta={"parameters": spec.get("parameters", {})})
    reg.register("ctx_tool", lambda args=None, ctx=None: {"ok": True, "ctx": ctx, "args": args})
    monkeypatch.setattr(executor, "REGISTRY", reg)
    p = tmp_path / "brain.json"
    b = build_default_brain(tools)
    b["tools"].append({"id":"ctx_tool","type":"native","handler":"ctx_tool","enabled":True,"read_only":True,"args_schema":{"type":"object","properties":{}}})
    b["knowledge"] = [
        {"id":"k1","title":"Same","content":"Same","tags":[],"keywords":[]},
        {"id":"k2","title":"Backup guide","content":"restore backup db","tags":["backup"],"keywords":["restore"]},
    ]
    b["tools"].append({"id":"comp4","type":"composite","enabled":True,"read_only":True,"steps":[
        {"tool":"ctx_tool","args":{"v":"${ctx.user}","missing":"${args.nope}"}},
        "bad",
        {"tool":"echo_tool","args":"bad"}
    ]})
    save_brain(b, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    br = load_brain(path=p, tools=tools, force=True)
    # scoring paths
    r1 = executor.search_knowledge("Same", brain=br)
    assert r1["ok"] and r1["count"] >= 1
    r2 = executor.search_knowledge("backup restore db", brain=br)
    assert r2["ok"]
    # composite template branches
    res = executor.execute("comp4", {}, brain=br, ctx={"user": "u1"})
    assert res["ok"] is True
    # _template_value direct
    assert executor._template_value({"a":["${args.x}"]}, args={"x":1}, steps={}, ctx={}) == {"a":[1]}


def test_workflow_engine_current_step_and_failures():
    eng = WorkflowEngine()
    brain = {"skills":[{"id":"wf","entry_state":"a","states":{"a":{"gate":"always_pass"}}}]}
    st = eng.start(brain, "wf")
    step = eng.current_step(brain, st)
    assert step and step["name"] == "a"
    assert eng.current_step({"skills":[]}, st) is None
    st.current_state = "missing"
    assert eng.current_step(brain, st) is None
    st2 = eng.start(brain, "wf")
    st2 = eng.advance(brain, st2)
    assert st2.status == "failed"  # no transition
    st3 = eng.start(brain, "wf")
    st3.current_state = "missing"
    st3 = eng.advance(brain, st3)
    assert st3.status == "failed"
    st4 = eng.start(brain, "wf")
    st4.status = "completed"
    assert eng.advance(brain, st4).status == "completed"


def test_brain_validation_warning_path(tmp_path, monkeypatch):
    p = tmp_path / "brain.json"
    bad = build_default_brain({})
    bad["version"] = 999  # validation warning
    p.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    b = load_brain(path=p, tools={}, force=True)
    assert b.name
    # agent name empty validation
    errs = validate_brain({"version": 1, "agent": {"name": "  "}, "tools": [], "skills": [], "knowledge": [], "workflows": {}, "boundaries": []})
    assert any("agent.name" in e for e in errs)


def test_executor_more_branches(tmp_path, monkeypatch):
    tools = _sample_tools()
    reg = ToolRegistry()
    for tid, spec in tools.items():
        reg.register(tid, spec["fn"], meta={"parameters": spec.get("parameters", {})})
    reg.register("typed2", lambda args=None: {"ok": True})
    monkeypatch.setattr(executor, "REGISTRY", reg)
    p = tmp_path / "brain.json"
    b = build_default_brain(tools)
    b["tools"].append({"id":"typed2","type":"native","handler":"typed2","enabled":True,"read_only":True,
        "args_schema":{"type":"object","properties":{"n":{"type":"number"},"i":{"type":"integer"}}}})
    b["tools"].append({"id":"comp5","type":"composite","enabled":True,"read_only":True,"steps":[{"tool":"echo_tool","args":"not-a-dict"}]})
    save_brain(b, path=p)
    monkeypatch.setattr(brain_mod, "BRAIN_PATH", p)
    monkeypatch.setattr(brain_mod, "_BRAIN_CACHE", None)
    monkeypatch.setattr(brain_mod, "_BRAIN_MTIME", None)
    br = load_brain(path=p, tools=tools, force=True)
    assert executor._score_text("", "x") == 0.0
    assert executor._score_text("q", "", "q") >= 1.0
    bad = executor.execute("typed2", {"n": True}, brain=br)  # bool rejected as number
    assert bad.get("error") == "invalid_args"
    res = executor.execute("comp5", {}, brain=br)
    assert res["ok"] is True


def test_workflow_more_missing_paths():
    eng = WorkflowEngine()
    brain = {"skills":[{"id":"wf","entry_state":"a","states":{"a":{"gate":"always_pass","next":"b"},"b":{}}}]}
    st = eng.start(brain, "wf")
    # current_step missing workflow
    st.skill_id = "missing"
    assert eng.current_step(brain, st) is None
    st.skill_id = "wf"
    st.current_state = "zzz"
    assert eng.current_step(brain, st) is None
    st2 = eng.start(brain, "wf")
    st2.skill_id = "missing"
    st2 = eng.advance(brain, st2)
    assert st2.status == "failed"
    st3 = eng.start(brain, "wf")
    st3.current_state = "zzz"
    st3 = eng.advance(brain, st3)
    assert st3.status == "failed"
