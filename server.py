#!/usr/bin/env python3
"""
CVRAPI MCP Server

This server implements the Model Context Protocol (MCP) to provide
Danish business registry data to language models through the CVRAPI.
It includes rate limiting, caching, and compliance with CVRAPI terms.

Configuration is handled through environment variables:
- CVRAPI_BASE_URL: API base URL (default: https://cvrapi.dk/api)
- CVRAPI_USER_AGENT: User agent string (REQUIRED - your company and project name)
- CVRAPI_COUNTRY: Country code (default: dk)
- CVRAPI_FORMAT: Output format (default: json)
- CVRAPI_TOKEN: Optional API token
- CVRAPI_RATE_LIMIT: Daily request limit (default: 50)
- CVRAPI_COOLDOWN: Cooldown period in seconds (default: 86400)
- CACHE_ENABLED: Enable caching (default: true)
- CACHE_EXPIRATION: Cache expiration in seconds (default: 604800)
"""

import hashlib
import json
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("cvrapi-mcp-server")


@dataclass
class AppContext:
    """Application context containing initialized services."""
    config: Dict[str, Any]
    cache: 'ResponseCache'
    rate_limiter: 'RateLimiter'
    cvr_client: 'CVRAPIClient'


class ResponseCache:
    """Simple in-memory cache for API responses to avoid redundant requests."""

    def __init__(self, enabled: bool = True, expiration: int = 604800):
        """Initialize the cache.
        
        Args:
            enabled: Whether caching is enabled
            expiration: Cache TTL in seconds (default: 7 days)
        """
        self.enabled = enabled
        self.expiration = expiration
        self.cache = {}

    def _generate_key(self, endpoint: str, params: Dict) -> str:
        """Generate a cache key from the endpoint and parameters."""
        key_string = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
        return hashlib.md5(key_string.encode()).hexdigest()

    def get(self, endpoint: str, params: Dict) -> Optional[Dict]:
        """Retrieve a cached response if available and not expired."""
        if not self.enabled:
            return None

        key = self._generate_key(endpoint, params)
        if key not in self.cache:
            return None

        value, timestamp = self.cache[key]
        if time.time() - timestamp > self.expiration:
            # Cache entry expired
            del self.cache[key]
            return None

        return value

    def set(self, endpoint: str, params: Dict, data: Dict):
        """Store a response in the cache."""
        if not self.enabled:
            return

        key = self._generate_key(endpoint, params)
        self.cache[key] = (data, time.time())

    def clear(self):
        """Clear all cached entries."""
        self.cache.clear()


class RateLimiter:
    """Tracks and enforces API rate limits."""

    def __init__(self, daily_limit: int = 50, cooldown_period: int = 86400):
        """Initialize the rate limiter.
        
        Args:
            daily_limit: Maximum requests per day
            cooldown_period: Period in seconds for the limit (default: 1 day)
        """
        self.daily_limit = daily_limit
        self.cooldown_period = cooldown_period
        self.requests = []  # List of timestamps

    def check_limit(self) -> bool:
        """Check if the rate limit has been reached.
        
        Returns:
            True if request is allowed, False if limit reached
        """
        # Clean up old entries
        self._cleanup_old_requests()

        # Count recent requests
        return len(self.requests) < self.daily_limit

    def record_request(self):
        """Record a new API request."""
        self.requests.append(time.time())
        self._cleanup_old_requests()

    def _cleanup_old_requests(self):
        """Remove request records older than the cooldown period."""
        current_time = time.time()
        cutoff = current_time - self.cooldown_period
        self.requests = [t for t in self.requests if t > cutoff]

    def get_usage_stats(self) -> Dict[str, int]:
        """Get current usage statistics.
        
        Returns:
            Dict containing usage data
        """
        self._cleanup_old_requests()
        count = len(self.requests)

        return {
            "requests_today": count,
            "daily_limit": self.daily_limit,
            "remaining": max(0, self.daily_limit - count),
            "reset_in_seconds": self.cooldown_period - (int(time.time()) % self.cooldown_period)
        }


class CVRAPIClient:
    """Client for interacting with the CVRAPI."""

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        country: str = "dk",
        output_format: str = "json",
        token: Optional[str] = None,
        cache: Optional[ResponseCache] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        """Initialize the CVRAPI client.
        
        Args:
            base_url: The base URL for the API
            user_agent: User agent string for requests
            country: Country code (dk or no)
            output_format: Response format (json or xml)
            token: Optional API token
            cache: Optional response cache
            rate_limiter: Optional rate limiter
        """
        self.base_url = base_url
        self.user_agent = user_agent
        self.country = country
        self.output_format = output_format
        self.token = token
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": self.user_agent}
        )

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def _make_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Make a request to the CVRAPI with proper error handling.
        
        This method handles all API interactions, including:
        - Cache checking
        - Rate limit enforcement
        - Input sanitization
        - Error handling and transformation
        - Response caching
        
        Args:
            params: Query parameters
        
        Returns:
            API response data
        
        Raises:
            Exception: If rate limit is exceeded or request fails
        """
        # Check cache first
        if self.cache:
            cached_response = self.cache.get("search", params)
            if cached_response:
                logger.info(f"Cache hit for search with params {params}")
                return cached_response

        # Check rate limit
        if self.rate_limiter and not self.rate_limiter.check_limit():
            logger.warning("Rate limit exceeded")
            raise Exception("QUOTA_EXCEEDED: Daily API rate limit exceeded. Please try again tomorrow.")

        # Add common parameters
        request_params = {
            "country": self.country,
            "format": self.output_format,
            **params
        }

        # Add token if available
        if self.token:
            request_params["token"] = self.token

        try:
            # Make the request
            url = f"{self.base_url}"
            logger.info(f"Making request to {url} with params {request_params}")

            response = await self.client.get(url, params=request_params)

            # Handle specific API errors
            if response.status_code != 200:
                error_text = response.text
                if "QUOTA_EXCEEDED" in error_text:
                    raise Exception("QUOTA_EXCEEDED: Daily API rate limit exceeded. Please try again tomorrow.")
                elif "BANNED" in error_text:
                    raise Exception("BANNED: Your IP address has been blocked.")
                elif "INVALID_VAT" in error_text:
                    raise Exception("INVALID_VAT: The CVR number format is incorrect.")
                elif "NOT_FOUND" in error_text:
                    raise Exception("NOT_FOUND: No results found for your query.")
                elif "INTERNAL_ERROR" in error_text:
                    raise Exception("INTERNAL_ERROR: An error occurred in the API.")
                elif "INVALID_UA" in error_text:
                    raise Exception("INVALID_UA: Please use a proper user agent with your company and project name.")
                else:
                    response.raise_for_status()

            # Record the request in rate limiter
            if self.rate_limiter:
                self.rate_limiter.record_request()

            # Log API usage for monitoring
            logger.info(f"API request successful: {params} (Remaining: {self.rate_limiter.get_usage_stats().get('remaining', 'unknown') if self.rate_limiter else 'unknown'})")

            # Parse the response
            data = response.json()

            # Cache the response
            if self.cache:
                self.cache.set("search", params, data)

            return data

        except httpx.HTTPStatusError as exc:
            logger.error(f"HTTP error: {exc.response.status_code} {exc.response.text}")
            raise Exception(f"API error: {exc.response.text}")

        except httpx.RequestError as exc:
            logger.error(f"Request error: {str(exc)}")
            raise Exception(f"Request error: {str(exc)}")

    async def lookup_by_cvr(self, cvr: str, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Look up a company by CVR number.
        
        Args:
            cvr: The CVR number
            fields: Optional list of fields to include
        
        Returns:
            Company data
        """
        params = {"vat": cvr}

        data = await self._make_request(params)
        return self._process_response(data, fields)

    async def search_by_name(
        self,
        name: str,
        fields: Optional[List[str]] = None,
        max_results: int = 10
    ) -> Dict[str, Any]:
        """Search for companies by name.
        
        Args:
            name: Company name to search for
            fields: Optional list of fields to include
            max_results: Maximum number of results to return
        
        Returns:
            List of matching companies
        """
        params = {"name": name}

        data = await self._make_request(params)

        # Limit number of results
        if isinstance(data, list) and len(data) > max_results:
            data = data[:max_results]

        return self._process_response(data, fields)

    async def lookup_production_unit(self, pno: str, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Look up a production unit by P-number.
        
        Args:
            pno: The P-number
            fields: Optional list of fields to include
        
        Returns:
            Production unit data
        """
        params = {"produ": pno}

        data = await self._make_request(params)
        return self._process_response(data, fields)

    async def lookup_by_phone(self, phone: str, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Look up a company by phone number.
        
        Args:
            phone: The phone number
            fields: Optional list of fields to include
        
        Returns:
            Company data
        """
        params = {"phone": phone}

        data = await self._make_request(params)
        return self._process_response(data, fields)

    async def general_search(self, search_term: str, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """General search that can match CVR, P-number, or company name.
        
        Args:
            search_term: The search term
            fields: Optional list of fields to include
        
        Returns:
            Search results
        """
        params = {"search": search_term}

        data = await self._make_request(params)
        return self._process_response(data, fields)

    async def get_company_ownership(self, cvr: str) -> Dict[str, Any]:
        """Get company ownership information.
        
        Args:
            cvr: The CVR number
        
        Returns:
            Ownership data
        """
        # First get the company data
        company_data = await self.lookup_by_cvr(cvr)

        # Extract and structure ownership information if available
        ownership_data = {
            "company": cvr,
            "owners": []
        }

        if "owners" in company_data:
            ownership_data["owners"] = company_data["owners"]

        return ownership_data

    def _process_response(self, data: Dict[str, Any], fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Process the API response.
        
        Args:
            data: The raw API response
            fields: Optional list of fields to filter
        
        Returns:
            Processed data
        """
        # Check for protection status
        if isinstance(data, dict) and data.get("protected") == True:
            logger.info("Detected advertising-protected company")
            data["_note"] = "This company is advertising-protected. Limited data available."

        # Filter fields if requested
        if fields and isinstance(data, dict):
            # Always include 'protected' field due to Danish business registry compliance requirements
            filtered_data = {k: v for k, v in data.items() if k in fields or k == "protected" or k == "_note"}
            # Add metadata to explain why 'protected' is always included if it wasn't requested
            if "protected" not in fields and "protected" in data:
                filtered_data["_metadata"] = "Note: The 'protected' field is always included for compliance with Danish business registry rules, even when not explicitly requested."
            return filtered_data

        return data

    def summarize_company(self, data: Dict[str, Any], max_tokens: int = 500) -> Dict[str, Any]:
        """Create a token-optimized summary of company data.
        
        Args:
            data: Full company data
            max_tokens: Approximate token limit for the summary
        
        Returns:
            Summarized company data
        """
        if not data:
            return {}

        # Essential fields to always include
        essential = ["vat", "name", "address", "zipcode", "city", "protected"]

        # Determine the size of essential data (rough estimation)
        essential_data = {k: data.get(k, "") for k in essential if k in data}
        essential_size = len(json.dumps(essential_data))

        # Calculate remaining size budget
        # Rough estimation: 1 token ≈ 4 characters
        remaining_budget = max_tokens * 4 - essential_size

        # Add additional fields until budget is exhausted
        summary = dict(essential_data)

        # Priority order for additional fields
        priority_fields = [
            "phone", "email", "www", "industrycode", "industrydesc",
            "companydesc", "creditstatus", "founders", "status"
        ]

        for field in priority_fields:
            if field in data:
                field_data = {field: data[field]}
                field_size = len(json.dumps(field_data))

                if field_size <= remaining_budget:
                    summary[field] = data[field]
                    remaining_budget -= field_size

        # Add note if company is protected
        if data.get("protected") == True or "_note" in data:
            summary["_note"] = data.get("_note", "This company is advertising-protected. Limited data available.")

        return summary


def get_env_bool(name: str, default: bool) -> bool:
    """Get a boolean value from an environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in ('true', 'yes', '1', 't', 'y')


def get_env_int(name: str, default: int) -> int:
    """Get an integer value from an environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


# Setup FastMCP Server

@asynccontextmanager
async def lifespan(server: FastMCP) -> AppContext:
    """Application lifespan manager for startup and shutdown."""
    # Load configuration from environment variables
    user_agent = os.environ.get("CVRAPI_USER_AGENT")

    # CRITICAL: Validate User-Agent - this is required for CVRAPI
    if not user_agent:
        logger.error("CVRAPI_USER_AGENT environment variable not set! Cannot continue.")
        print("Error: CVRAPI_USER_AGENT environment variable must be set.")
        print("Please set it to: '[COMPANY NAME] - [PROJECT NAME] - [CONTACT NAME] [CONTACT EMAIL/PHONE]")
        sys.exit(1)

    config = {
        "api": {
            "base_url": os.environ.get("CVRAPI_BASE_URL", "https://cvrapi.dk/api"),
            "user_agent": user_agent,
            "country": os.environ.get("CVRAPI_COUNTRY", "dk"),
            "output_format": os.environ.get("CVRAPI_FORMAT", "json"),
            "token": os.environ.get("CVRAPI_TOKEN"),
        },
        "rate_limit": {
            "daily_limit": get_env_int("CVRAPI_RATE_LIMIT", 50),
            "cooldown_period": get_env_int("CVRAPI_COOLDOWN", 86400),
        },
        "cache": {
            "enabled": get_env_bool("CACHE_ENABLED", True),
            "expiration": get_env_int("CACHE_EXPIRATION", 604800),
        },
    }

    # Initialize services
    cache = ResponseCache(
        enabled=config["cache"]["enabled"],
        expiration=config["cache"]["expiration"]
    )

    rate_limiter = RateLimiter(
        daily_limit=config["rate_limit"]["daily_limit"],
        cooldown_period=config["rate_limit"]["cooldown_period"]
    )

    cvr_client = CVRAPIClient(
        base_url=config["api"]["base_url"],
        user_agent=config["api"]["user_agent"],
        country=config["api"]["country"],
        output_format=config["api"]["output_format"],
        token=config["api"]["token"],
        cache=cache,
        rate_limiter=rate_limiter
    )

    app_context = AppContext(
        config=config,
        cache=cache,
        rate_limiter=rate_limiter,
        cvr_client=cvr_client
    )

    logger.info("Server starting up")
    logger.info(f"API configuration: {config['api']['base_url']}, Country: {config['api']['country']}")
    logger.info(f"Rate limit: {config['rate_limit']['daily_limit']} requests per {config['rate_limit']['cooldown_period']}s")

    try:
        yield app_context
    finally:
        logger.info("Server shutting down")
        await cvr_client.close()


# Initialize MCP server
mcp = FastMCP(
    name="CVRAPI-mcp",
    description="MCP server for interacting with the CVRAPI",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=["httpx"]
)


@mcp.tool()
async def lookup_by_cvr(
    ctx: Context,
    cvr: str,
    fields: Optional[List[str]] = None,
    optimize_tokens: bool = False,
    max_tokens: int = 500,
) -> Dict[str, Any]:
    """Look up a company by its CVR number.
    
    Args:
        cvr: The CVR number to look up (8 digits)
        fields: Optional list of specific fields to include
        optimize_tokens: If true, returns a summarized version
        max_tokens: Maximum token count if optimizing (approximate)
    
    Returns:
        Company information
    """
    # Validate CVR number format
    if not re.match(r'^\d{8}$', cvr):
        return {"error": "Invalid CVR number format. Must be 8 digits."}

    cvr_client = ctx.request_context.lifespan_context.cvr_client

    try:
        result = await cvr_client.lookup_by_cvr(cvr, fields)

        if optimize_tokens:
            return cvr_client.summarize_company(result, max_tokens)

        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def search_by_name(
    ctx: Context,
    name: str,
    fields: Optional[List[str]] = None,
    max_results: int = 5,
    optimize_tokens: bool = False,
    max_tokens: int = 500,
) -> Dict[str, Any]:
    """Search for companies by name.
    
    Args:
        name: Company name to search for
        fields: Optional list of specific fields to include
        max_results: Maximum number of results to return
        optimize_tokens: If true, returns summarized versions
        max_tokens: Maximum token count if optimizing (approximate)
    
    Returns:
        List of matching companies
    """
    if len(name) < 2:
        return {"error": "Search term must be at least 2 characters long."}

    cvr_client = ctx.request_context.lifespan_context.cvr_client

    try:
        results = await cvr_client.search_by_name(name, fields, max_results)

        if optimize_tokens and isinstance(results, list):
            return [cvr_client.summarize_company(company, max_tokens) for company in results]

        return results
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def lookup_production_unit(
    ctx: Context,
    pno: str,
    fields: Optional[List[str]] = None,
    optimize_tokens: bool = False,
    max_tokens: int = 500,
) -> Dict[str, Any]:
    """Look up a production unit by its P-number.
    
    Args:
        pno: The P-number to look up (10 digits)
        fields: Optional list of specific fields to include
        optimize_tokens: If true, returns a summarized version
        max_tokens: Maximum token count if optimizing (approximate)
    
    Returns:
        Production unit information
    """
    # Validate P-number format
    if not re.match(r'^\d{10}$', pno):
        return {"error": "Invalid P-number format. Must be 10 digits."}

    cvr_client = ctx.request_context.lifespan_context.cvr_client

    try:
        result = await cvr_client.lookup_production_unit(pno, fields)

        if optimize_tokens:
            return cvr_client.summarize_company(result, max_tokens)

        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def lookup_by_phone(
    ctx: Context,
    phone: str,
    fields: Optional[List[str]] = None,
    optimize_tokens: bool = False,
    max_tokens: int = 500,
) -> Dict[str, Any]:
    """Look up a company by phone number.
    
    Args:
        phone: The phone number to look up
        fields: Optional list of specific fields to include
        optimize_tokens: If true, returns a summarized version
        max_tokens: Maximum token count if optimizing (approximate)
    
    Returns:
        Company information
    """
    # Clean phone number format
    phone = re.sub(r'[^0-9+]', '', phone)

    cvr_client = ctx.request_context.lifespan_context.cvr_client

    try:
        result = await cvr_client.lookup_by_phone(phone, fields)

        if optimize_tokens:
            return cvr_client.summarize_company(result, max_tokens)

        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def general_search(
    ctx: Context,
    search_term: str,
    fields: Optional[List[str]] = None,
    optimize_tokens: bool = False,
    max_tokens: int = 500,
) -> Dict[str, Any]:
    """Search for a company by CVR, P-number, or name.
    
    Args:
        search_term: The term to search for
        fields: Optional list of specific fields to include
        optimize_tokens: If true, returns a summarized version
        max_tokens: Maximum token count if optimizing (approximate)
    
    Returns:
        Company information
    """
    cvr_client = ctx.request_context.lifespan_context.cvr_client

    try:
        result = await cvr_client.general_search(search_term, fields)

        if optimize_tokens:
            if isinstance(result, list):
                return [cvr_client.summarize_company(company, max_tokens) for company in result]
            else:
                return cvr_client.summarize_company(result, max_tokens)

        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def filter_fields(
    ctx: Context,
    cvr: str,
    fields: List[str],
) -> Dict[str, Any]:
    """Extract specific fields only from a company record.
    
    Args:
        cvr: The CVR number
        fields: List of field names to include
    
    Returns:
        Filtered company data
    """
    cvr_client = ctx.request_context.lifespan_context.cvr_client

    try:
        return await cvr_client.lookup_by_cvr(cvr, fields)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_company_ownership(
    ctx: Context,
    cvr: str,
) -> Dict[str, Any]:
    """Get ownership information for a company.
    
    Args:
        cvr: The CVR number
    
    Returns:
        Ownership information
    """
    # Validate CVR number format
    if not re.match(r'^\d{8}$', cvr):
        return {"error": "Invalid CVR number format. Must be 8 digits."}

    cvr_client = ctx.request_context.lifespan_context.cvr_client

    try:
        return await cvr_client.get_company_ownership(cvr)
    except Exception as e:
        return {"error": str(e)}


@mcp.resource("api://status")
async def get_api_status() -> str:
    """Get the current API status and usage statistics."""
    rate_limiter = mcp.runtime.lifespan_context.rate_limiter
    stats = rate_limiter.get_usage_stats()

    return json.dumps({
        "status": "operational",
        "usage": stats,
        "timestamp": datetime.now().isoformat()
    }, indent=2)


@mcp.resource("api://terms")
async def get_api_terms() -> str:
    """Get the CVRAPI terms and usage notice."""
    return """
Danish CVR API Terms and Usage:

1. Daily Request Limit: 
   - Free tier: 50 requests per day
   - Contact CVRAPI for higher limits

2. User-Agent Requirements:
   - Must include your company name and project
   - Format: "[COMPANY_NAME] - [PROJECT_NAME] - [CONTACT_NAME] [CONTACT_EMAIL/PHONE]"

3. Protected Companies:
   - Some companies are advertising-protected
   - It is prohibited to contact these for advertising purposes
   - Always respect the 'protected' flag in responses

4. Error Codes:
   - QUOTA_EXCEEDED: Daily limit reached
   - BANNED: IP address blocked
   - INVALID_VAT: Incorrect format
   - NOT_FOUND: No results found
   - INTERNAL_ERROR: API error
   - INVALID_UA: Invalid User-Agent

For more information, visit: https://cvrapi.dk
"""


if __name__ == "__main__":
    # Start the server
    mcp.run(transport="stdio")
