import json
from mcp.server.fastmcp import FastMCP

# Create a FastMCP server
mcp = FastMCP("lead-qualifier-mcp-server")

@mcp.tool()
def lookup_company(company_name: str) -> str:
    """Lookup company details like employee size, industry, and headquarters.
    
    Args:
        company_name: The name of the company to look up.
    """
    c = company_name.lower().strip()
    if "google" in c:
        return json.dumps({
            "company": "Google LLC",
            "size": "Large (100,000+ employees)",
            "industry": "Technology",
            "hq": "Mountain View, CA"
        })
    elif "stripe" in c:
        return json.dumps({
            "company": "Stripe, Inc.",
            "size": "Large (5,000+ employees)",
            "industry": "Financial Technology",
            "hq": "San Francisco, CA"
        })
    elif "acme" in c:
        return json.dumps({
            "company": "Acme Corp",
            "size": "Medium (250 employees)",
            "industry": "Manufacturing",
            "hq": "Chicago, IL"
        })
    return json.dumps({
        "company": company_name,
        "size": "Small (10 employees)",
        "industry": "Generic / Unknown",
        "hq": "Unknown"
    })

@mcp.tool()
def check_crm_status(email: str) -> str:
    """Check if the contact email already exists in our CRM.
    
    Args:
        email: The email address to check.
    """
    e = email.lower().strip()
    if "google.com" in e:
        return json.dumps({
            "status": "Existing Customer",
            "assigned_owner": "Sarah Jenkins",
            "last_contact": "2026-06-15"
        })
    elif "stripe.com" in e:
        return json.dumps({
            "status": "Existing Lead - Contact Active",
            "assigned_owner": "David Miller",
            "last_contact": "2026-06-28"
        })
    elif "disqualified" in e or "spam" in e:
        return json.dumps({
            "status": "Disqualified",
            "reason": "Spam/Blocklisted domain"
        })
    return json.dumps({
        "status": "New Lead",
        "assigned_owner": "None",
        "last_contact": "Never"
    })

@mcp.tool()
def verify_domain_reputation(domain: str) -> str:
    """Verify if the domain has a good email reputation and is not blocklisted.
    
    Args:
        domain: The domain name to check (e.g. google.com).
    """
    d = domain.lower().strip()
    if d in ["gmail.com", "yahoo.com", "outlook.com"]:
        return json.dumps({
            "domain": domain,
            "status": "Warning",
            "details": "Generic public email domain. Low business authority."
        })
    if "spam" in d or "tempmail" in d:
        return json.dumps({
            "domain": domain,
            "status": "Failed",
            "details": "High risk spam or disposable domain."
        })
    return json.dumps({
        "domain": domain,
        "status": "Passed",
        "details": "Verified business domain with high trust score."
    })

if __name__ == "__main__":
    mcp.run()
