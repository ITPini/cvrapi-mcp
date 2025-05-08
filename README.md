# CVRAPI-mcp Server

Model Context Protocol (MCP) server for accessing CVR API, allowing large language models to retrieve and work with Danish business registry data.

## Features

- Query business data by CVR number, name, phone, or P-number
- Token-efficient responses with field filtering and summarization
- Proper handling of advertising-protected companies
- Simple in-memory caching to reduce API calls
- Rate limiting to comply with API terms of 50 daily requests

## CVR API Terms Compliance
This project is not affiliated with CVR API ApS. This is an independent tool for interacting with the CVR API service using large language models. Users must comply with all [CVR API terms of service](https://cvrapi.dk/terms) when using this tool.

This MCP server implements these measures to ensure compliance with CVR API terms:

1. Rate limiting to respect daily 50 query limit
2. Require `User-Agent` identification with company and project information
3. Special handling of advertising-protected companies
4. In-memory caching to reduce unnecessary API calls

## Requirements

- Python 3.10+
- Dependencies: httpx, mcp

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/ITPini/cvrapi-mcp.git
   cd cvrapi-mcp
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

## Usage

### Claude Desktop Integration

Add the server to your Claude Desktop configuration:

```JSON
{
    "cvrapi-mcp": {
        "command": "uv",
        "args": [
            "--directory",
            "path/to/cvrapi-mcp",
            "run",
            "server.py"
        ],
        "env": {
            "CVRAPI_USER_AGENT": "[COMPANY_NAME] - [PROJECT_NAME] - [CONTACT_NAME] [CONTACT_EMAIL/PHONE]"
        }
    }
}
```

Replace `path/to/cvrapi-mcp` and fill out CVRAPI_USER_AGENT with correct information.

## Configuration

The server is configured using environment variables:

- `CVRAPI_BASE_URL`: API base URL (default: https://cvrapi.dk/api)
- `CVRAPI_USER_AGENT`: User agent string (REQUIRED: [COMPANY_NAME] - [PROJECT_NAME] - [CONTACT_NAME] [CONTACT_EMAIL/PHONE])
- `CVRAPI_COUNTRY`: Country code (default: dk)
- `CVRAPI_FORMAT`: Output format (default: json)
- `CVRAPI_TOKEN`: Optional API token
- `CVRAPI_RATE_LIMIT`: Daily request limit (default: 50)
- `CVRAPI_COOLDOWN`: Cooldown period in seconds (default: 86400)
- `CACHE_ENABLED`: Enable caching (default: true)
- `CACHE_EXPIRATION`: Cache expiration in seconds (default: 604800)

## Available Tools

The server exposes the following MCP tools:

### 1. `lookup_by_cvr`

**Description**: Retrieve company data by CVR number

**Parameters**:
- `cvr` (required): 8-digit CVR number
- `fields` (optional): List of specific fields to return
- `optimize_tokens` (optional): Boolean, if true returns summarized version
- `max_tokens` (optional): Maximum token count if optimizing (default: 500)

**Example**:

Request:
```JSON
{
  "cvr": "24256790",
  "optimize_tokens": true
}
```
Response:
```JSON
{
  "vat": 24256790,
  "name": "NOVO NORDISK A/S",
  "address": "Novo Alle 1",
  "zipcode": "2880",
  "city": "Bagsværd",
  "protected": false,
  "phone": "44448888",
  "email": null,
  "industrycode": 212000,
  "industrydesc": "Fremstilling af farmaceutiske præparater",
  "companydesc": "Aktieselskab",
  "creditstatus": null
}
```

### 2. `search_by_name`

**Description**: Find companies matching a name

**Parameters**:
- `name` (required): Company name to search for
- `fields` (optional): List of specific fields to return
- `max_results` (optional): Maximum number of results (default: 5)
- `optimize_tokens` (optional): Boolean, if true returns summarized versions
- `max_tokens` (optional): Maximum token count if optimizing (default: 500)

**Example**:

Request:
```JSON
{
  "name": "Novo",
  "optimize_tokens": true
}
```
Response:
```JSON
{
  "vat": 24256790,
  "name": "NOVO NORDISK A/S",
  "address": "Novo Alle 1",
  "zipcode": "2880",
  "city": "Bagsværd",
  "cityname": null,
  "protected": false,
  "phone": "44448888",
  "email": null,
  "fax": null,
  "startdate": "28/11 - 1931",
  "enddate": null,
  "employees": 33090,
  "addressco": null,
  "industrycode": 212000,
  "industrydesc": "Fremstilling af farmaceutiske præparater",
  "companycode": 60,
  "companydesc": "Aktieselskab",
  "creditstartdate": null,
  "creditbankrupt": false,
  "creditstatus": null,
  "owners": null,
  "productionunits": [ "..." ]
}
```

### 3. `lookup_production_unit`

**Description**: Get production unit data by P-number

**Parameters**:
- `pno` (required): 10-digit P-number
- `fields` (optional): List of specific fields to return
- `optimize_tokens` (optional): Boolean, if true returns summarized version
- `max_tokens` (optional): Maximum token count if optimizing (default: 500)

**Example**:

Request:
```JSON
{
  "pno": "1009731292",
  "optimize_tokens": true
}
```

Response:
```JSON
{
  "vat": 69749917,
  "name": "COLOPLAST A/S",
  "address": "Møllevej 11-15",
  "zipcode": "2990",
  "city": "Nivå",
  "protected": false,
  "phone": null,
  "email": null,
  "industrycode": 252490,
  "industrydesc": "Fremstilling af andre plastprodukter i øvrigt",
  "companydesc": null,
  "creditstatus": null
}
```

### 4. `lookup_by_phone`

**Description**: Find company by phone number

**Parameters**:
- `phone` (required): Phone number to search for
- `fields` (optional): List of specific fields to return
- `optimize_tokens` (optional): Boolean, if true returns summarized version
- `max_tokens` (optional): Maximum token count if optimizing (default: 500)

**Example**:

Request:
```JSON
{
  "phone": "44460000",
  "optimize_tokens": true
}
```
Response:

```JSON
{
  "vat": 29603537,
  "name": "Novozymes Biopharma DK A/S",
  "address": "Krogshøjvej 36",
  "zipcode": "2880",
  "city": "Bagsværd",
  "protected": false,
  "phone": "44460000",
  "email": null,
  "industrycode": 464610,
  "industrydesc": "Engroshandel med medicinalvarer og sygeplejeartikler",
  "companydesc": "Aktieselskab",
  "creditstatus": null
}
```

### 5. `general_search`

**Description**: Search across all fields (CVR, P-number, name)

**Parameters**:
- `search_term` (required): Term to search for
- `fields` (optional): List of specific fields to return
- `optimize_tokens` (optional): Boolean, if true returns summarized version
- `max_tokens` (optional): Maximum token count if optimizing (default: 500)

**Example**:

Request:
```JSON
{
  "search_term": "Maersk",
  "optimize_tokens": true
}
```
Response:
```JSON
{
  "vat": 32345794,
  "name": "MAERSK A/S",
  "address": "Esplanaden 50",
  "zipcode": "1263",
  "city": "København K",
  "protected": false,
  "phone": "33633363",
  "email": null,
  "industrycode": 502000,
  "industrydesc": "Sø- og kysttransport af gods",
  "companydesc": "Aktieselskab",
  "creditstatus": null
}
```

### 6. `filter_fields`

**Description**: Extract specific fields from company data

**Parameters**:
- `cvr` (required): 8-digit CVR number
- `fields` (required): List of fields to include

**Example**:

Request:
```JSON
{
  "cvr": "24256790",
  "fields": ["name", "address", "email"]
}
```

Response:
```JSON
{
  "name": "NOVO NORDISK A/S",
  "address": "Novo Alle 1",
  "protected": false,
  "email": null,
  "_metadata": "Note: The 'protected' field is always included for compliance with Danish business registry rules, even when not explicitly requested."
}
```

### 7. `get_company_ownership`

**Description**: Get ownership information for a company

**Parameters**:
- `cvr` (required): 8-digit CVR number

**Example**:

Request:
```JSON
{ "cvr": "69749917" }
```

Response:

```JSON
{
  "company": "69749917",
  "owners": [
    {
      "name": "Benedicte Anne Find"
    }
  ]
}
```

## MCP Resources

The server also exposes these MCP resources:

- `api://status` - Current API status and usage statistics
- `api://terms` - CVR API terms and usage notice

## Error Handling

The server handles all standard CVR API error codes:

- `QUOTA_EXCEEDED` - Daily limit reached
- `BANNED` - IP address blocked
- `INVALID_VAT` - Incorrect format
- `NOT_FOUND` - No results found
- `INTERNAL_ERROR` - API error
- `INVALID_UA` - Invalid User-Agent

## License

This project is licensed under the MIT License - see the LICENSE file for details. Note that while this code is freely available, usage of the CVR API service itself is subject to their terms and conditions.