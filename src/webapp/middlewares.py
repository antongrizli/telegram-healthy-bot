import logging
from aiohttp import web

logger = logging.getLogger(__name__)

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
