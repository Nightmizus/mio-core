from subprocess import CompletedProcess

from mio_core import audio


def test_defender_scan_passes_path_as_a_process_argument(monkeypatch, tmp_path):
    defender = tmp_path / "Windows Defender" / "MpCmdRun.exe"
    defender.parent.mkdir()
    defender.touch()
    sample = tmp_path / "quote' and spaces.wav"
    sample.touch()
    captured = {}

    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(
        audio,
        "_run",
        lambda args, timeout: (
            captured.update(args=args, timeout=timeout)
            or CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        ),
    )

    settings = type("Settings", (), {"enable_defender_scan": True})()
    audio.defender_scan(sample, settings)

    assert captured["args"][0] == str(defender)
    assert captured["args"][1:5] == ["-Scan", "-ScanType", "3", "-File"]
    assert captured["args"][5] == str(sample)
    assert captured["args"][6] == "-DisableRemediation"
    assert captured["timeout"] == 300
