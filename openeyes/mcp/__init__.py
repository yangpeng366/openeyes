"""OpenEyes MCP server."""


def main() -> int:
    from openeyes.mcp.server import main as server_main

    return server_main()

__all__ = ["main"]
