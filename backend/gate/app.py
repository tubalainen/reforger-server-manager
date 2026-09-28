"""The Docker gate: a small HTTP proxy in front of the real Docker socket (#204).

It runs as its own container, the only one in a stack that holds
/var/run/docker.sock. It has no network at all: its manager reaches it over a
unix socket in a volume only the two of them share. Every request goes through
policy.route(); container-scoped ones are checked against the container's
labels; answers that would describe other stacks are filtered on the way back.
The rules themselves are in gate/policy.py.
"""
import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, unquote

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

import config
import stacks
from gate import policy

logger = logging.getLogger("gate")

# The host part is ignored on a unix socket.
UPSTREAM = "http://docker"

# Request headers a Docker client legitimately sends; nothing else is passed on.
_FORWARD_REQUEST = {"content-type", "x-registry-auth", "x-registry-config"}
# Response headers worth handing back. Hop-by-hop ones (chunking, keep-alive)
# belong to each leg of the trip, not to the answer.
_FORWARD_RESPONSE = {"content-type", "api-version", "docker-experimental", "ostype", "server"}

_METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]


def _error(status: int, message: str) -> JSONResponse:
    """An error in Docker's own shape, so docker-py raises the matching exception."""
    return JSONResponse({"message": message}, status_code=status)


def _headers(source, allowed: set[str]) -> dict:
    return {k: v for k, v in source.items() if k.lower() in allowed}


def _version_prefix(raw_path: str) -> str:
    """'/v1.47' of '/v1.47/containers/json', or '' for an unversioned path."""
    head = raw_path.split("/", 2)[1] if raw_path.count("/") >= 2 else ""
    return f"/{head}" if head.startswith("v") and head[1:2].isdigit() else ""


def create_app(scope: policy.Scope, upstream: httpx.AsyncClient) -> Starlette:
    """The gate's ASGI app for one stack, talking to Docker through `upstream`."""

    async def forward(request: Request, raw_path: str, query: str, *,
                      body: bytes | None = None, stream: bool = False) -> Response:
        headers = _headers(request.headers, _FORWARD_REQUEST)
        if body is None:
            body = await request.body()
        else:
            headers["content-type"] = "application/json"
        url = UPSTREAM + raw_path + (f"?{query}" if query else "")
        req = upstream.build_request(request.method, url, headers=headers, content=body)
        resp = await upstream.send(req, stream=stream)
        out = _headers(resp.headers, _FORWARD_RESPONSE)
        if not stream:
            return Response(resp.content, status_code=resp.status_code, headers=out)

        async def chunks():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()  # the client went away, or the stream ended

        return StreamingResponse(chunks(), status_code=resp.status_code, headers=out)

    async def inspect(version: str, ref: str) -> dict | None:
        resp = await upstream.get(f"{UPSTREAM}{version}/containers/{ref}/json")
        return resp.json() if resp.status_code == 200 else None

    async def json_body(request: Request):
        try:
            return json.loads(await request.body() or b"null")
        except ValueError as exc:
            raise policy.Denied("the request body is not valid JSON") from exc

    async def dispatch(request: Request, route: policy.Route, raw_path: str, query: str):
        if route.kind == "gate":
            return JSONResponse(
                {"gate": "rsm", "stack": scope.stack, "version": config.APP_VERSION}
            )
        if route.kind in ("ping", "version"):
            return await forward(request, raw_path, query)
        if route.kind in ("info", "list"):
            return await filtered(route, raw_path, query)
        if route.kind == "container":
            return await container(request, route, raw_path, query)
        return await other(request, route, raw_path, query)

    async def filtered(route: policy.Route, raw_path: str, query: str) -> Response:
        """`docker info` and the container list, with other stacks left out."""
        resp = await upstream.get(UPSTREAM + raw_path + (f"?{query}" if query else ""))
        if resp.status_code != 200:
            return Response(resp.content, status_code=resp.status_code)
        if route.kind == "info":
            return JSONResponse(policy.filter_info(resp.json()))
        return JSONResponse(policy.filter_list(resp.json(), scope))

    async def container(request: Request, route: policy.Route, raw_path: str,
                        query: str) -> Response:
        found = await inspect(_version_prefix(raw_path), route.ref)
        labels = ((found or {}).get("Config") or {}).get("Labels")
        if found is None or not stacks.owns(labels, scope.stack, write=route.write):
            # Another stack's container does not exist, as far as this one knows.
            return _error(404, f"No such container: {route.ref}")
        if route.action == "json" and not query:
            return JSONResponse(found)  # already fetched, and it is ours
        if route.action == "update":
            body = policy.check_update(await json_body(request))
            return await forward(request, raw_path, query, body=json.dumps(body).encode())
        return await forward(request, raw_path, query, stream=route.action in ("logs", "stats"))

    async def other(request: Request, route: policy.Route, raw_path: str,
                    query: str) -> Response:
        """Container create, image pull and inspect, network inspect."""
        params = parse_qs(query)
        if route.kind == "create":
            name = params.get("name", [None])[0]
            body = policy.check_create(await json_body(request), name, scope)
            return await forward(request, raw_path, query, body=json.dumps(body).encode())
        if route.kind == "pull":
            image = params.get("fromImage", [""])[0]
            tag = params.get("tag", [None])[0]
            if not image or not policy.image_allowed(scope, image, tag):
                raise policy.Denied(f"pulling {image!r} is not allowed for this stack")
            return await forward(request, raw_path, query, stream=True)
        if route.kind == "image":
            ref = unquote(route.ref)
            if not policy.image_allowed(scope, ref):
                raise policy.Denied(f"image {ref!r} is not one this stack runs")
            return await forward(request, raw_path, query)
        if route.kind == "network":
            resp = await upstream.get(UPSTREAM + raw_path)
            if resp.status_code == 200 and resp.json().get("Name") in scope.networks:
                return Response(resp.content, status_code=200,
                                headers=_headers(resp.headers, _FORWARD_RESPONSE))
            return _error(404, f"network {route.ref} not found")
        raise policy.Denied(f"{request.method} {raw_path} is not allowed")

    async def handle(request: Request) -> Response:
        # Match and forward the path exactly as it arrived, before any decoding,
        # so what the policy checked is what the daemon receives.
        raw = request.scope.get("raw_path") or request.url.path.encode()
        raw_path = raw.decode("latin-1").split("?", 1)[0]
        query = request.url.query
        try:
            route = policy.route(request.method, raw_path)
            return await dispatch(request, route, raw_path, query)
        except policy.Denied as exc:
            logger.warning("Refused %s %s: %s", request.method, raw_path, exc)
            return _error(403, f"rsm-gate: {exc}")
        except httpx.HTTPError as exc:
            logger.warning("Docker did not answer %s %s: %s", request.method, raw_path, exc)
            return _error(502, "rsm-gate: the Docker daemon is not reachable")

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await upstream.aclose()

    return Starlette(
        routes=[Route("/{path:path}", handle, methods=_METHODS)],
        lifespan=lifespan,
    )
