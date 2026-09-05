import os
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class ArchicadNotConfiguredError(RuntimeError):
    """Raised when no Archicad MCP URL is configured (env var or override).

    This means neither ARCHICAD_MCP_URL nor a frontend-set runtime
    override points at a reachable Archicad MCP server. See
    docker-compose.yml for the tailscale sidecar / ARCHICAD_MCP_URL, or
    POST /archicad/connection to switch routes at runtime.
    """


# Handy default for a backend process running on the same machine/network
# as archicad-mcp's HTTP instance (server_http.py), bypassing the
# Tailscale bridge entirely - useful for local development while the
# PC<->AWS bridge isn't set up yet.
LOCAL_PRESET_URL = "http://127.0.0.1:8765/mcp/"

# Runtime override set via POST /archicad/connection, so the frontend can
# switch between "local" and "remote" (ARCHICAD_MCP_URL / Tailscale)
# routes without restarting the backend. None means "no override, use
# ARCHICAD_MCP_URL".
_override_url = None


def get_active_url():
    return _override_url or os.environ.get("ARCHICAD_MCP_URL") or None


def set_connection_url(url):
    """Override the active Archicad MCP URL at runtime.

    Pass None (or an empty string) to clear the override and fall back
    to the ARCHICAD_MCP_URL environment variable.
    """
    global _override_url
    _override_url = url or None
    return get_active_url()


def get_connection_info():
    return {
        "active_url": get_active_url(),
        "overridden": _override_url is not None,
        "env_default": os.environ.get("ARCHICAD_MCP_URL") or None,
        "local_preset_url": LOCAL_PRESET_URL,
    }


def is_configured():
    return bool(get_active_url())


def _default_transport():
    url = get_active_url()

    if not url:
        raise ArchicadNotConfiguredError(
            "No Archicad MCP URL is configured - set one via "
            "POST /archicad/connection, or the ARCHICAD_MCP_URL env var."
        )

    return streamable_http_client(url)


@asynccontextmanager
async def _session(transport=None):
    # `transport` is injectable so tests can point this at an in-memory
    # MCP server instead of a real network connection (mcp.client._memory.
    # InMemoryTransport) - the real Archicad MCP server's tool contract
    # (tool names/arguments) is unknown until the PC bridge is live, so
    # these functions stay a generic passthrough rather than assuming
    # specific tool names.
    transport = transport or _default_transport()

    async with transport as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def open_session(transport=None):
    """Public entry point to `_session()`, for callers that need to run

    several tool calls back-to-back without paying per-call MCP session
    setup/teardown overhead (initialize handshake, SSE stream, session
    close). Each call_tool()/list_tools() call normally opens and closes
    its own session, which is fine in isolation but adds up badly over a
    slow/relayed link (Tailscale DERP) when a caller needs many calls in
    a row - see archicad_mcp/server.py's sync_from_archicad(), which was
    measured taking ~80s over a relayed connection for ~5700 elements
    almost entirely in repeated session negotiation, not actual data
    transfer. Use as `async with archicad_client.open_session() as
    session:` then pass `session=session` into call_tool()/tapir.py's
    helpers to reuse it.
    """
    return _session(transport)


async def list_tools(transport=None, session=None):
    """List the tool names exposed by the connected Archicad MCP server.

    Use this once the PC bridge is live to discover the real tool
    contract (e.g. whatever the Tapir-based server calls its
    "get elements" / "move element" tools).
    """

    if session is not None:
        result = await session.list_tools()
        return [tool.name for tool in result.tools]

    async with _session(transport) as session:
        result = await session.list_tools()
        return [tool.name for tool in result.tools]


async def call_tool(name, arguments=None, transport=None, session=None):
    """Call a tool on the connected Archicad MCP server by name.

    Generic passthrough - the caller is responsible for knowing the
    tool's argument shape (discoverable via list_tools()).

    Pass an already-open `session` (from `open_session()`) to run this
    call on it instead of opening a new one - see `open_session()`
    docstring. `transport` is ignored when `session` is given.
    """

    if session is not None:
        return await session.call_tool(name, arguments or {})

    async with _session(transport) as session:
        return await session.call_tool(name, arguments or {})


def _describe_exception(exc):
    # anyio task groups wrap the real cause in an ExceptionGroup, so a bare
    # str(exc) collapses to an unhelpful "unhandled errors in a TaskGroup
    # (1 sub-exception)" - unwrap it so the actual reason (connection
    # refused, timeout, DNS failure, ...) is visible to whoever is
    # debugging the PC-side bridge from the /archicad/status response.
    if isinstance(exc, BaseExceptionGroup):
        nested = "; ".join(_describe_exception(e) for e in exc.exceptions)
        return f"{type(exc).__name__}: {nested}"

    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


async def check_connection(transport=None):
    """Best-effort connectivity check, safe to call from a plain REST route.

    Never raises - returns a JSON-serializable status dict describing
    whether ARCHICAD_MCP_URL is set and whether the bridge actually
    answered, so this can be curled directly while setting up the
    PC-side Tailscale bridge without needing an MCP client.
    """

    # is_configured() only applies to the default (real) transport path -
    # an explicitly injected transport (tests) bypasses env-var config.
    if transport is None and not is_configured():
        return {
            "configured": False,
            "reachable": False,
            "error": "ARCHICAD_MCP_URL is not set",
        }

    try:
        tools = await list_tools(transport)
        return {"configured": True, "reachable": True, "tools": tools}
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "error": _describe_exception(exc),
        }
