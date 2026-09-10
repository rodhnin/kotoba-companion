"""One platform answer, read by all three places that have to agree.

She is taught a shell, a shell interprets what she wrote, and a gate decides what runs without asking,
reading the same line. They were three different languages the moment a Windows host existed: nothing
told her the platform, her commands went to whatever COMSPEC named, and the gate parsed POSIX.

The trap: the platform that matters is the SANDBOX's, never this process's. Under
`KOTOBA_SANDBOX=docker` her commands land in a Linux container on a Windows host, so answering with the
host teaches the wrong shell and locks the gate on nothing that runs — `shell_is_windows` is the one
function that must get this right, forced at its one origin since no Windows host is available here."""
from __future__ import annotations

import asyncio
import base64

import pytest
from conftest import posix_only

from kotoba.core import app_settings
from kotoba.core.sandbox import local
from kotoba.core.sandbox.local import LocalSandbox, _powershell_argv
from kotoba.soul import prompt

SOUL = {"name": "Kotoba", "language": "auto", "personality": "Warm.",
        "address_style": "By name.", "emotional_rules": "Delight.", "quirks": "A soft hmph."}


def _build(**kwargs) -> str:
    return prompt.build_system_prompt(SOUL, "- Name: Jordan", ["likes matcha"], **kwargs)


def _decoded(argv: list[str]) -> str:
    """What PowerShell will actually read back out of -EncodedCommand."""
    return base64.b64decode(argv[-1]).decode("utf-16-le")


@pytest.fixture(autouse=True)
def _pin_the_shell_dialect():
    """Overrides the suite-wide pin, which replaces the very function this file exists to measure.
    Everywhere else the dialect is fixed so a POSIX assertion cannot be judged by a Windows host; here
    the derivation from host and sandbox backend IS the subject."""


@pytest.fixture
def windows_host(monkeypatch):
    """The HOST is Windows. What runs there still depends on the sandbox — that is the point."""
    monkeypatch.setattr(local, "is_windows_host", lambda: True)


# ── the trap: the sandbox decides, not the host ────────────────────────────────────────────────────

def test_a_linux_container_on_a_windows_host_is_not_windows(windows_host):
    """`docker` bind-mounts the workdir into a Linux image whatever the host is, so reporting the host
    here would teach her PowerShell for a container that has never heard of it."""
    app_settings.set_runtime("sandbox", "docker")
    assert local.shell_is_windows() is False


def test_the_local_backend_on_a_windows_host_is_windows(windows_host):
    app_settings.set_runtime("sandbox", "local")
    assert local.shell_is_windows() is True


def test_nothing_runs_anywhere_under_the_none_backend(windows_host):
    app_settings.set_runtime("sandbox", "none")
    assert local.shell_is_windows() is False


@posix_only("a POSIX host (os.name != nt)")
def test_a_posix_host_never_asks_the_setting_at_all(monkeypatch):
    """The host test comes first so the question costs no file read on the platform everyone is on —
    every approval decision now asks it, several times."""
    def _boom():
        raise AssertionError("backend_name() was consulted on a POSIX host")

    monkeypatch.setattr("kotoba.core.sandbox.backend_name", _boom)
    assert local.shell_is_windows() is False


# ── the interpreter is named, not inherited ────────────────────────────────────────────────────────

def test_windows_runs_powershell_by_argv_instead_of_whatever_comspec_names(tmp_path, monkeypatch,
                                                                          windows_host):
    seen = {}

    async def _exec(*argv, **kwargs):
        seen["argv"] = list(argv)
        seen["kwargs"] = kwargs
        return "proc"

    async def _shell(command, **kwargs):
        raise AssertionError("create_subprocess_shell ran on Windows — that is cmd.exe via COMSPEC")

    monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _shell)
    sb = LocalSandbox(tmp_path)
    assert asyncio.run(sb._spawn("Get-Content notes.txt", tmp_path)) == "proc"
    argv = seen["argv"]
    assert argv[0] == "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    assert "-NoProfile" in argv and "-NonInteractive" in argv
    assert seen["kwargs"]["cwd"] == str(tmp_path)


def test_the_interpreter_is_an_absolute_path_and_not_a_name_to_look_up(monkeypatch):
    """A PATH lookup would let something the model wrote choose the interpreter."""
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    monkeypatch.delenv("WINDIR", raising=False)
    assert _powershell_argv("Get-Date")[0].startswith("C:\\Windows\\")
    monkeypatch.setenv("WINDIR", "D:\\Win\\")
    assert _powershell_argv("Get-Date")[0].startswith("D:\\Win\\System32\\")


@posix_only("a POSIX host (os.name != nt)")
def test_posix_still_goes_through_sh_c_untouched(tmp_path, monkeypatch):
    seen = {}

    async def _shell(command, **kwargs):
        seen["command"] = command
        return "proc"

    async def _exec(*argv, **kwargs):
        raise AssertionError("POSIX must keep using create_subprocess_shell")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _shell)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    sb = LocalSandbox(tmp_path)
    assert asyncio.run(sb._spawn("ls -la | wc -l", tmp_path)) == "proc"
    assert seen["command"] == "ls -la | wc -l"


# ── what the interpreter receives ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    'Write-Output "hi there"',
    "Get-Content 'C:\\Users\\me\\a b.txt'",
    'Select-String -Pattern "a\\"b" -Path notes.txt',
    "Get-ChildItem\nGet-Location",
    "Write-Output 'árbol ⽇'",
])
def test_the_command_reaches_powershell_byte_for_byte(command):
    """-EncodedCommand is why quoting cannot drift: no argv joiner and no command-line re-parse stands
    between what the approval card showed and what the interpreter reads."""
    assert _decoded(_powershell_argv(command)).startswith(command)


def test_the_exit_code_of_a_native_program_is_not_lost():
    """powershell.exe reports its OWN success as 0/1, so a failed program can come back as exit 0 — a
    failure reported as a success, which is the failure this codebase tracks hardest."""
    assert _decoded(_powershell_argv("git push")).endswith("\nexit $LASTEXITCODE")


def test_the_epilogue_cannot_be_commented_out_by_the_last_line():
    """On the same line a trailing `#` would swallow it, and the exit code would be lost precisely on
    the commands a person hand-wrote."""
    decoded = _decoded(_powershell_argv("Get-ChildItem # list what is here"))
    assert decoded.splitlines()[-1] == "exit $LASTEXITCODE"


def test_the_encoded_payload_needs_no_quoting_of_its_own():
    """base64 carries no space and no quote, so the argv joiner has nothing to escape and nothing to
    get wrong — the one place a second parser could still have crept back in."""
    payload = _powershell_argv('Write-Output "a b"')[-1]
    assert payload and not set(payload) - set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def test_python_is_invoked_with_the_call_operator_on_windows(tmp_path, monkeypatch, windows_host):
    """PowerShell PRINTS a quoted path instead of running it, so execute_code would have produced the
    path as its output and no result at all."""
    seen = {}

    async def _run(command, cwd=".", timeout=60):
        seen["command"] = command
        return None

    sb = LocalSandbox(tmp_path)
    monkeypatch.setattr(sb, "run", _run)
    asyncio.run(sb.run_code("print(2 + 2)"))
    assert seen["command"].startswith('& "')

    monkeypatch.setattr(local, "is_windows_host", lambda: False)
    asyncio.run(sb.run_code("print(2 + 2)"))
    assert seen["command"].startswith('"')


# ── the environment the new interpreter needs, and the scrub that still holds ──────────────────────

def test_the_windows_child_is_given_what_powershell_needs(monkeypatch, windows_host):
    monkeypatch.setenv("PSMODULEPATH", "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\Modules")
    monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
    env = local._scrubbed_env()
    assert "PSMODULEPATH" in env and "SYSTEMROOT" in env


def test_the_scrub_still_drops_secrets_on_windows(monkeypatch, windows_host):
    monkeypatch.setenv("PSMODULEPATH", "C:\\Modules")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-reach-a-child")
    env = local._scrubbed_env()
    assert not [k for k in env if "KEY" in k.upper()]


# ── the prompt: she is told the platform, in the part of it that gets cached ───────────────────────

@posix_only("a POSIX host (os.name != nt)")
def test_the_prompt_says_nothing_about_the_platform_on_posix():
    """The shell she is taught IS the shell that runs here, so the block would be tokens charged every
    turn for no behaviour — and bytes the frozen snapshots would have to move."""
    built = _build()
    assert "POWERSHELL" not in built.upper()
    assert "THIS MACHINE RUNS" not in built


def test_the_prompt_teaches_powershell_when_that_is_what_will_run(monkeypatch):
    monkeypatch.setattr(local, "shell_is_windows", lambda: True)
    built = _build()
    assert "THIS MACHINE RUNS WINDOWS" in built
    assert "PowerShell" in built and "backslashes" in built


def test_the_platform_block_sits_in_the_cacheable_prefix(monkeypatch):
    """Everything above `# Now` is a prefix a provider can cache and the clock below it is what changes
    every turn. An operating system does not change during a session, so a platform block in the
    volatile tail would make the rest of the prompt uncacheable for a fact fixed before the first word."""
    monkeypatch.setattr(local, "shell_is_windows", lambda: True)
    built = _build()
    assert built.index("THIS MACHINE RUNS WINDOWS") < built.index("\n# Now")


def test_no_platform_block_where_there_is_no_shell(monkeypatch):
    """Naming the shell of a session that has none is how "she says something untrue" gets manufactured."""
    monkeypatch.setattr(local, "shell_is_windows", lambda: True)
    built = _build(available_tools={"web_search", "execute_code"})
    assert "THIS MACHINE RUNS WINDOWS" not in built


def test_the_prompt_and_the_gate_read_the_same_origin(monkeypatch):
    """If these two ever came from different functions, one of them would be teaching a shell the other
    was not guarding — which is the whole defect this change closes."""
    from kotoba.core import approval

    monkeypatch.setattr(local, "shell_is_windows", lambda: True)
    assert approval._windows_shell() is True
    assert "THIS MACHINE RUNS WINDOWS" in _build()
