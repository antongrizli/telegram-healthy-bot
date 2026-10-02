import logging
from uuid import uuid4
from aiohttp import web

logger = logging.getLogger(__name__)


@web.middleware
async def safe_errors_middleware(request, handler):
    """Keep internal exception details on the server, with a correlation ID."""
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status < 500:
            raise
        status = exc.status
        request_id = uuid4().hex
        logger.exception("Web server error request_id=%s method=%s path=%s status=%s",
                         request_id, request.method, request.path, status)
        return web.json_response({"error": "internal_error", "request_id": request_id}, status=status)
    except Exception:
        status = 500
        request_id = uuid4().hex
        logger.exception("Unhandled web error request_id=%s method=%s path=%s",
                         request_id, request.method, request.path)
        return web.json_response({"error": "internal_error", "request_id": request_id}, status=status)

# List of lowercase substrings representing scanner/crawler User-Agents to block
BLOCKED_USER_AGENTS = [
    "paloaltonetworks",
    "cortex-xpanse",
    "censys",
    "shodan",
    "zgrab",
    "netlas",
    "zoomeye",
    "masscan",
    "nmap",
    "acunetix",
    "nessus",
    "qualys",
    "rapid7",
    "nexpose",
    "detectify",
]

@web.middleware
async def block_scanners_middleware(request: web.Request, handler) -> web.StreamResponse:
    """
    Middleware to block common security scanners and web crawlers by checking their User-Agent.
    """
    user_agent = request.headers.get("User-Agent", "").lower()
    
    for blocked_agent in BLOCKED_USER_AGENTS:
        if blocked_agent in user_agent:
            logger.warning(
                f"Blocked scanner request from {request.remote} | Path: {request.path} | User-Agent: {request.headers.get('User-Agent')}"
            )
            return web.Response(text="Access Denied", status=403)
            
    return await handler(request)
