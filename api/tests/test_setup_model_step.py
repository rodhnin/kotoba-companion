"""The model step: asking WHICH model, not just whose, and asking it before the key.

First run asked which brain but never which of its models, so a stranger paid the default's cost
unseen. The catalogue has one source now; pin failures where its surfaces drift apart.

Order is load-bearing: the key-test endpoint validates against the current companion model, so the
model must be chosen BEFORE the key — a key without access to it fails at the key step, where a person
can still paste another, rather than on their first real sentence. The frontend renders before setup
status can answer, so it keeps its own copy of the model list, pinned here against drift.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from kotoba import paths
from kotoba.core import app_settings, llm, providers

from test_cli_first_run import Answers, a_caps, run_wizard

# Resolved through `paths`, never by counting parents by hand: a wheel install has no repository, so
# the count would name a directory that happens to exist and the read would raise FileNotFoundError
# from inside a test that has nothing to say about a wheel. `needs_component` skips exactly the pins
# that read the file; every backend pin below still runs there.
ONBOARDING = paths.REPO_ROOT / "components" / "Onboarding.tsx"
needs_component = pytest.mark.skipif(
    paths._CLONE is None, reason="the frontend component lives in the repository, not the wheel")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "setup.db"))
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    from fastapi.testclient import TestClient
    import kotoba.server as main

    with TestClient(main.app) as c:
        yield c

# The rows the screen mirrors, as they are written in the TSX: one object per model, in order.
_TS_ROW = re.compile(
    r'\{\s*id:\s*"((?:[^"\\]|\\.)*)",\s*note:\s*"((?:[^"\\]|\\.)*)",\s*'
    r'hint:\s*"((?:[^"\\]|\\.)*)",\s*context:\s*(\d+),\s*out:\s*([\d.]+)\s*\}'
)


def _ts_catalogue() -> dict:
    """The screen's own copy, parsed out of the component. Sliced per provider rather than read whole, so a
    row that drifted into the wrong provider's list is a failure and not a silent pass."""
    src = ONBOARDING.read_text(encoding="utf-8")
    block = src[src.index("const CATALOGUE: Catalogue = {"):]
    block = block[: block.index("\n};")]
    out = {}
    starts = {p: block.index(f"\n  {p}: {{") for p in ("openai", "xai")}
    for pid, start in starts.items():
        end = min((s for s in starts.values() if s > start), default=len(block))
        slice_ = block[start:end]
        default = re.search(r'default:\s*"([^"]+)"', slice_)
        out[pid] = {
            "default": default.group(1) if default else "",
            "models": [{"id": m[0], "note": m[1], "hint": m[2], "context": int(m[3]),
                        "out": float(m[4])}
                       for m in _TS_ROW.findall(slice_)],
        }
    return out


# --- one catalogue, three surfaces ------------------------------------------------------------------

@needs_component
def test_the_screen_offers_exactly_what_the_backend_offers():
    """The screen renders before `/api/setup/status` answers, so its list is a copy — and a copy is a
    thing that goes stale. Ids, her clause, the ratio, the context window and the OUTPUT PRICE all have
    to match — the price because two models can sit close enough that neither earns a ratio, and only the
    real number tells the screen which of them may wear the word `cheapest`."""
    backend = providers.catalogue()
    mirror = _ts_catalogue()
    assert set(mirror) == set(backend), "the screen knows a different set of providers"
    for pid, entry in backend.items():
        want = [{"id": m["id"], "note": m["note"], "hint": m["hint"], "context": m["context"],
                 "out": m["output_cost"]}
                for m in entry["models"]]
        assert mirror[pid]["models"] == want, f"{pid}: the mirrored catalogue has drifted from providers.py"
        assert mirror[pid]["default"] == entry["default"], f"{pid}: the screen defaults to another model"


def test_the_status_endpoint_carries_the_catalogue(client):
    """The model step is two screens after this call, and the brain is chosen in between — so one answer
    has to cover every provider, or the screen needs a round trip it cannot make before the choice."""
    got = client.get("/api/setup/status").json()
    assert got["catalogue"] == providers.catalogue()
    for entry in got["catalogue"].values():
        assert entry["models"], "a provider was offered with nothing to pick"


def test_every_offered_model_is_one_its_own_provider_serves():
    """The mismatch this whole step is wired to avoid: a model belonging to the other company reaches the
    key check as a 404 and gets reported as a bad key."""
    for pid, spec in providers.PROVIDERS.items():
        for choice in spec.models + spec.code_models:
            assert providers.serves_model(choice.id, pid), f"{pid} does not serve {choice.id}"


def test_the_offer_list_is_cheapest_first():
    """The screen says so in her own words — 'they're cheapest first' — and the ratio badge is computed
    against the top row. Out of order, the copy is a lie and the badges are nonsense."""
    for pid, spec in providers.PROVIDERS.items():
        costs = [m.output_cost for m in spec.models]
        assert costs == sorted(costs), f"{pid} offers {costs}, which is not cheapest first"


def test_the_default_is_something_the_person_is_actually_shown():
    """A bare Enter takes the default, so a default absent from the list is a choice made off-screen."""
    for pid, spec in providers.PROVIDERS.items():
        assert spec.default_model in [m.id for m in spec.models], f"{pid}'s default is not on offer"


def test_only_the_cheapest_goes_unbadged_and_nothing_ever_reads_one_times():
    """A badge on every row is a badge that says nothing, and `1x the price` is worse than none: two
    models a few percent apart are the same price to anybody choosing between them."""
    for spec in providers.PROVIDERS.values():
        floor = min(m.output_cost for m in spec.models)
        for m in spec.models:
            hint = providers.cost_hint(spec, m)
            assert hint != "1x" and hint != "1.0x", f"{m.id} wears a badge that says nothing"
            if m.output_cost >= floor * 1.05:
                assert hint, f"{m.id} costs more than the cheapest and says nothing about it"


def test_a_model_that_drops_our_tools_is_never_offered():
    """`grok-4.20-multi-agent` takes only xAI's own server-side tools: the call SUCCEEDS and every tool of
    hers is silently gone. The Settings panel offered it — on either provider — before this step existed,
    and a companion that cannot use a single tool is the worst kind of pass."""
    every = [m.id for spec in providers.PROVIDERS.values() for m in spec.models + spec.code_models]
    assert not [m for m in every if "multi-agent" in m], f"a multi-agent model is on offer: {every}"
    if paths._CLONE:   # the backend half above is the claim; the screen's copy only exists in a clone
        assert "grok-4.20-multi-agent" not in ONBOARDING.read_text(encoding="utf-8")


def test_only_reasoning_models_are_offered_as_a_brain():
    """The canned per-tool narration is hardcoded ENGLISH and is suppressed only for reasoning models,
    which narrate in the user's own language. A non-reasoning model is cheaper and would leak English
    lines into a Spanish turn, so it stays reachable by typing an id and is never put on a card."""
    for pid, spec in providers.PROVIDERS.items():
        for choice in spec.models:
            assert providers.model_supports_reasoning(choice.id, pid), \
                f"{choice.id} is offered as a brain but takes no reasoning kwargs"


def test_a_coding_model_is_a_role_choice_and_not_a_first_run_answer():
    for spec in providers.PROVIDERS.values():
        assert not (set(m.id for m in spec.models) & set(m.id for m in spec.code_models))


def test_the_panel_reads_the_same_catalogue_as_first_run():
    """The third place the old hardcoded list lived. `_llm_settings` hands the panel each provider's own
    models, so switching provider in Settings changes what the model menus offer."""
    from kotoba.core.settings import _llm_settings

    class NoKeys:
        async def get_key(self, name):
            return None

    got = asyncio.run(_llm_settings({"provider": "xai"}, [], NoKeys()))
    by_id = {p["id"]: p for p in got["providers"]}
    for pid, entry in providers.catalogue().items():
        assert by_id[pid]["models"] == entry["models"]
        assert by_id[pid]["code_models"] == entry["code_models"]


# --- the write, and the mismatch it refuses ---------------------------------------------------------

def test_choosing_a_model_pins_it(client):
    client.post("/api/setup/provider", json={"provider": "xai"})
    got = client.post("/api/setup/model", json={"model": "grok-4.6"})
    assert got.status_code == 200 and got.json()["model"] == "grok-4.6"
    assert llm.model_name("companion") == "grok-4.6"


def test_a_model_from_the_other_company_is_refused_rather_than_stored(client):
    """Stored, it would surface as a 404 at the key check and be reported as a bad key — the defect
    `first_run.pin_model` was written for, arriving through a different door."""
    client.post("/api/setup/provider", json={"provider": "xai"})
    before = llm.model_name("companion")
    got = client.post("/api/setup/model", json={"model": "gpt-5.4"})
    assert got.status_code == 400
    assert "OpenAI" in got.json()["detail"], "the refusal never says who does serve it"
    assert llm.model_name("companion") == before, "a refused model was written anyway"


def test_the_brain_can_ride_with_the_model_so_the_pair_is_atomic(client):
    """The screen chose the brain one step earlier with a fire-and-forget POST. A click faster than that
    round trip would have the backend check a Grok id against OpenAI and 400 — silently, because nothing
    reads the result — leaving the model unpinned. Sending both makes it one decision."""
    assert client.get("/api/setup/status").json()["provider"] == "openai"
    got = client.post("/api/setup/model", json={"provider": "xai", "model": "grok-4.6"})
    assert got.status_code == 200
    assert got.json() == {"ok": True, "provider": "xai", "model": "grok-4.6"}
    assert llm.model_name("companion") == "grok-4.6"


def test_an_unknown_provider_riding_along_is_refused(client):
    assert client.post("/api/setup/model", json={"provider": "acme", "model": "gpt-5.4"}).status_code == 400


def test_an_empty_model_is_refused(client):
    assert client.post("/api/setup/model", json={"model": "  "}).status_code == 400


# --- the terminal asks it too, in the same order ----------------------------------------------------

def test_the_wizard_asks_the_model_between_the_brain_and_the_key(monkeypatch, tmp_path):
    """The key round trip runs against the configured model, so the answer has to land before it. Pinned
    on the ORDER of what she says, because a step that lands after the key is a step that tests nothing."""
    answers = Answers(typed=["2", "3", "", "", ""], keys=["xai-good"])
    ok, out, _db, calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True and calls == [("xai", "xai-good")]
    assert out.index("Now which of theirs") < out.index("Paste the key")
    assert llm.model_name("companion") == providers.PROVIDERS["xai"].models[2].id


def test_the_wizard_offers_every_model_the_screen_does(monkeypatch, tmp_path):
    """Nothing is truncated: a list she trims silently is a price a person cannot see."""
    answers = Answers(typed=["1", "", "", "", ""], keys=["sk-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers, width=200)
    for choice in providers.PROVIDERS["openai"].models:
        assert choice.id in out, f"{choice.id} was never shown"


def test_a_typed_id_she_does_not_list_is_taken_as_typed(monkeypatch, tmp_path):
    """The list is what she OFFERS, never all they sell — so the escape has to really work."""
    answers = Answers(typed=["1", "gpt-4.1", "", "", ""], keys=["sk-good"])
    ok, _out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True and llm.model_name("companion") == "gpt-4.1"


def test_a_typed_id_from_the_other_company_is_refused_with_the_reason(monkeypatch, tmp_path):
    answers = Answers(typed=["2", "gpt-5.4", "1", "", "", ""], keys=["xai-good"])
    ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers)
    assert ok is True
    assert "is OpenAI's" in out and "pick OpenAI" in out
    assert llm.model_name("companion") == providers.PROVIDERS["xai"].default_model


def test_a_bare_enter_keeps_a_model_that_was_already_configured(monkeypatch, tmp_path):
    """`kotoba setup` is also how somebody comes back for the voice key. Pressing Enter through a step
    they answered months ago must not quietly move them onto something else."""
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    app_settings.set_runtime("model", "grok-4.5")
    answers = Answers(typed=["2", "", "", "", ""], keys=["xai-good"])
    run_wizard(monkeypatch, tmp_path, answers)
    assert llm.model_name("companion") == "grok-4.5"


def test_the_prompt_names_what_a_bare_enter_will_take(monkeypatch, tmp_path):
    """An unlisted configured model has no row number to show, so the hint has to name the model itself
    — otherwise Enter takes something the screen never mentioned."""
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    app_settings.set_runtime("model", "grok-4.20-0309-non-reasoning")
    answers = Answers(typed=["2", "", "", "", ""], keys=["xai-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers, width=200)
    rows = [line for line in out.splitlines() if "which one?" in line]
    assert any("grok-4.20-0309-non-reasoning" in line for line in rows), \
        f"the prompt never named the default: {rows}"
    assert llm.model_name("companion") == "grok-4.20-0309-non-reasoning"


def test_nothing_in_the_model_step_overflows_a_narrow_window(monkeypatch, tmp_path):
    """The longest id we ship is thirteen characters and its clause is a sentence; at forty columns the
    clause wraps onto its own rail rows rather than being cut, which is this screen's standing rule. A bare URL
    is the one documented exemption — broken across two rows it stops being one selectable run."""
    from rich.cells import cell_len

    answers = Answers(typed=["2", "4", "", "", ""], keys=["xai-good"])
    _ok, out, _db, _calls = run_wizard(monkeypatch, tmp_path, answers, caps=a_caps(width=40))
    long = [line for line in out.splitlines() if cell_len(line) > 40 and "http" not in line]
    assert not long, f"{len(long)} row(s) ran off a 40-column window: {long[:2]}"


# --- the web flow counts its own steps --------------------------------------------------------------

@needs_component
def test_the_web_flow_grew_a_step_everywhere_it_counts():
    """`STEPS` drives the dots, the `N of N` line and the badge on each card, so one entry added in one
    place and missed in another makes the count lie."""
    src = ONBOARDING.read_text(encoding="utf-8")
    ids = re.findall(r'\{ id: "(\w+)", label: "([^"]+)"', src)
    assert [i for i, _ in ids] == [
        "brain", "model", "key", "you", "me", "language", "face", "voice", "ready",
    ]
    # The property, not the spelling: the count comes from the list. Pinning the exact interpolation
    # made a rewrite of the same line read as a regression.
    counter = re.search(r"\{stepIndex \+ 1\}[^\n]*|\$\{stepIndex \+ 1\}[^\n]*", src)
    assert counter, "the strip no longer says which step you are on"
    assert "STEPS.length" in counter.group(0), "the counter was hardcoded instead of read from STEPS"


@needs_component
def test_the_recap_says_which_model_she_ended_up_with():
    src = ONBOARDING.read_text(encoding="utf-8")
    recap = src[src.index("function knownRows"):]
    assert 'label: "Model"' in recap[: recap.index("\n}")], "the Ready card recaps a step it never mentions"


@needs_component
@pytest.mark.parametrize("name", ["ASK", "NOTES"])
def test_she_has_words_of_her_own_for_the_new_step(name):
    """Every other step is asked in her voice and explained in a note card with its own kaomoji. A step
    that borrowed another's copy would be the one screen that reads like a form."""
    src = ONBOARDING.read_text(encoding="utf-8")
    block = src[src.index(f"const {name}"):]
    assert "model:" in block[: block.index("\n};")], f"{name} has no entry for the model step"


@needs_component
def test_the_model_answer_carries_the_brain_it_was_chosen_for():
    """`/api/setup/model` validates the id against the provider it is given, so the post has to carry the
    brain the person just picked rather than let the backend guess from whatever is stored. A `gpt-` id
    sent while the stored provider is xAI is the 404 the pin exists to prevent."""
    src = ONBOARDING.read_text(encoding="utf-8")
    assert 'post("/api/setup/model", { provider:' in src, "the model answer no longer carries its brain"


def test_max_is_reachable_on_openai_and_folds_for_xai():
    """`max` is the top reasoning tier of `gpt-5.6-luna`, the default model. The allowlist stopped at
    `xhigh`, so two of the six levels the model accepts were unreachable from Settings or the CLI.
    xAI does not serve it — the clamp has to fold it rather than forward a value api.x.ai would
    refuse."""
    from kotoba.core import app_settings, providers

    assert "max" in providers.PROVIDERS["openai"].effort_values
    assert "max" not in providers.PROVIDERS["xai"].effort_values
    assert providers.normalize_effort("max", "openai") == "max"
    assert providers.normalize_effort("max", "xai") == "xhigh"

    before = app_settings.runtime_all().get("reasoning_effort")
    try:
        app_settings.set_runtime("reasoning_effort", "max")
        assert app_settings.runtime_all()["reasoning_effort"] == "max"
    finally:
        app_settings.set_runtime("reasoning_effort", before or "")
