"""The MCP wire: JSON-RPC 2.0 over one POST per request.

Contract read out of EAG_V3_capstone/document/openapi.json, not guessed:

  POST {base}/api/mcp
      "One POST carries one JSON-RPC request. A request with an id gets a
       response; a notification (no id) is acknowledged 202 with an empty
       body."
  GET  {base}/api/mcp
      405. The server does not offer an SSE stream and says so, rather than
      leaving a client hanging on a stream that will never open.
  Auth
      Authorization: Bearer <token>. A session cookie also works for the
      browser doors; this harness is not a browser.

Zero runtime dependencies: the POST is a stdlib urllib call pushed onto a
worker thread so the surrounding async workflow is never blocked.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 30.0
PROTOCOL_VERSION = "2024-11-05"


class TransportError(Exception):
    """A request did not produce a usable JSON-RPC result."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class JsonRpcError(TransportError):
    """The server answered with a JSON-RPC error object (code/message/data)."""

    def __init__(self, code: int, message: str, *, status: int | None = None, data: Any = None):
        super().__init__(f"jsonrpc error {code}: {message}", status=status, body=data)
        self.code = code
        self.message = message
        self.data = data


class ToolResultError(TransportError):
    """A tools/call reply came back flagged as an error."""


def _maybe_json(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def unwrap_tool_result(result: Any) -> Any:
    """Unwrap the MCP tools/call content envelope.

    Spec shape:
        {"content": [{"type": "text", "text": "..."}], "isError": false}

    AgentSwitch carries the record payload as JSON text inside that envelope,
    so a successful call is decoded back into dicts and lists here instead of
    leaking the envelope into the workflow layer.
    """
    if not isinstance(result, dict) or "content" not in result:
        return result
    parts = result.get("content") or []
    texts = [p.get("text", "") for p in parts
             if isinstance(p, dict) and p.get("type") == "text"]
    if result.get("isError"):
        raise ToolResultError("; ".join(t for t in texts if t) or "tool reported an error")
    if len(texts) == 1:
        return _maybe_json(texts[0])
    if texts:
        return [_maybe_json(t) for t in texts]
    return result


class JsonRpcTransport:
    """One POST per JSON-RPC request against {base}/api/mcp."""

    def __init__(self, base_url: str, token: str = "", *,
                 timeout: float = DEFAULT_TIMEOUT,
                 endpoint_path: str = "/api/mcp"):
        if not base_url:
            raise ValueError("MCP base URL is required (AGENTSWITCH_MCP_URL)")
        self._url = base_url.rstrip("/") + endpoint_path
        self._token = token
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._initialised = False

    @property
    def url(self) -> str:
        return self._url

    async def _post(self, payload: dict) -> Any:
        return await asyncio.to_thread(self._post_blocking, payload)

    async def _request(self, method: str, params: dict | None = None) -> Any:
        """Send one JSON-RPC request and return its ``result``.

        The endpoint does not use an SSE session.  A monotonically increasing
        id is sufficient for the one-request-per-POST transport described in
        the captured OpenAPI document, while also making malformed replies
        visible instead of accidentally treating them as tool data.
        """
        request_id = next(self._ids)
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        reply = await self._post(payload)
        if not isinstance(reply, dict):
            raise TransportError(f"empty or non-object reply to {method!r}", body=reply)
        if reply.get("jsonrpc") != "2.0":
            raise TransportError(f"invalid JSON-RPC version in reply to {method!r}", body=reply)
        if reply.get("id") != request_id:
            raise TransportError(
                f"reply id {reply.get('id')!r} did not match request id {request_id!r}", body=reply
            )
        error = reply.get("error")
        if error is not None:
            if isinstance(error, dict):
                raise JsonRpcError(
                    int(error.get("code", -32000)), str(error.get("message", "unknown error")),
                    data=error.get("data"),
                )
            raise TransportError(f"malformed JSON-RPC error in reply to {method!r}", body=reply)
        if "result" not in reply:
            raise TransportError(f"reply to {method!r} had neither result nor error", body=reply)
        return reply["result"]

    async def _notify_initialised(self) -> None:
        """Send the standard post-initialize notification.

        Notifications have no reply by contract (HTTP 202 / empty body), so
        they deliberately bypass ``_request``'s response validation.
        """
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def initialise(self, *, client_name: str, client_version: str) -> Any:
        """Perform the MCP handshake once, safely under concurrent callers."""
        if self._initialised:
            return None
        # The CLI currently has one workflow, but the client is also used by
        # the harness and must not send two initialize requests if reused.
        if not hasattr(self, "_initialise_lock"):
            self._initialise_lock = asyncio.Lock()
        async with self._initialise_lock:
            if self._initialised:
                return None
            result = await self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": client_version},
            })
            await self._notify_initialised()
            self._initialised = True
            return result

    async def call_tool(self, name: str, arguments: dict, *,
                        client_name: str = "pipeline-agent", client_version: str = "0.1.0") -> Any:
        """Initialise if necessary, invoke ``tools/call``, and unwrap MCP text."""
        await self.initialise(client_name=client_name, client_version=client_version)
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        return unwrap_tool_result(result)

    async def list_tools(self, *, client_name: str = "pipeline-agent",
                         client_version: str = "0.1.0") -> list[dict]:
        """Enumerate the catalogue this credential exposes, following cursors.

        This is the only way to answer "which tool surface did we actually get"
        without guessing from configuration - see preflight.py.
        """
        await self.initialise(client_name=client_name, client_version=client_version)
        tools: list[dict] = []
        cursor: Any = None
        seen_cursors: set[str] = set()
        while True:
            result = await self._request("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(result, dict):
                raise TransportError("tools/list did not return an object", body=result)
            tools.extend(t for t in (result.get("tools") or []) if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
            # A server that repeats a cursor would page forever; stop instead.
            if str(cursor) in seen_cursors:
                raise TransportError(f"tools/list repeated cursor {cursor!r}", body=result)
            seen_cursors.add(str(cursor))

    def _post_blocking(self, payload: dict) -> Any:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(self._url, data=body,
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise TransportError(
                f"HTTP {exc.code} from {self._url}: {detail[:400]}",
                status=exc.code, body=detail,
            ) from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"cannot reach {self._url}: {exc.reason}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TransportError(
                f"reply from {self._url} was not json: {raw[:200]!r}"
            ) from exc
