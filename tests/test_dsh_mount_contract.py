from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dsh_fragment_uses_repository_local_stdio_server():
    fragment = (ROOT / "examples" / "dsh-openeyes-mcp.yml").read_text(encoding="utf-8")

    assert "id: mcp-openeyes" in fragment
    assert "name: '@deepseek-ai/dsh-mcp-client'" in fragment
    assert "transport: stdio" in fragment
    assert "cwd: E:/gitAll/openeyes" in fragment
    assert "command: python" in fragment
    assert "- 'openeyes.mcp.server'" in fragment
    assert "failOnStartupError: true" in fragment


def test_dsh_preflight_is_read_only_and_has_dump_config_gate():
    script = (ROOT / "examples" / "dsh-preflight.ps1").read_text(encoding="utf-8")
    contract = (ROOT / "docs" / "capability-contract.md").read_text(encoding="utf-8")

    assert "& npm" not in script
    assert "& dsh plugin" not in script
    assert "Test-Path -LiteralPath $pluginPackagePath" in script
    assert "dsh_mcp_client = $pluginVersion -eq '0.1.1-rc.2'" in script
    assert "missing_prerequisites" in script
    assert "next_action" in script
    assert "npm install -g @deepseek-ai/dsh@0.1.1-rc.2" in script
    assert "dsh plugin --profile web add @deepseek-ai/dsh-mcp-client@0.1.1-rc.2" in script
    assert "Then rerun: pwsh -NoProfile -File examples\\dsh-preflight.ps1" in script
    assert "dsh plugin --profile web add @deepseek-ai/dsh-mcp-client@0.1.1-rc.2" in contract
    assert "--dump-config" in script
    assert "exit 2" in script
