# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import json
import re
import sys
from google.adk.workflow import Workflow, node, START
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.genai import types
from pydantic import BaseModel, Field
from app.config import config

# 1. Pydantic Schemas
class LeadInfo(BaseModel):
    name: str = Field(default="Unknown", description="Name of the lead/contact person")
    company: str = Field(default="Unknown", description="Name of the company")
    email: str = Field(default="Unknown", description="Email address of the lead")
    domain_info: str = Field(default="Unknown", description="Industry or domain of the company")
    company_size: str = Field(default="Unknown", description="Estimated company size (e.g. Small, Medium, Large)")
    crm_status: str = Field(default="Unknown", description="CRM status (e.g. Existing, New Lead, Disqualified)")

class LeadEnrichmentOutput(BaseModel):
    lead_info: LeadInfo = Field(description="Enriched lead details")

class LeadScoringOutput(BaseModel):
    score: int = Field(description="Qualification score from 0 to 100 based on company size, domain fit, and CRM status")
    reasoning: str = Field(description="Reasoning behind the score")

class LeadQualificationResult(BaseModel):
    lead_info: LeadInfo = Field(description="Enriched lead details")
    score: int = Field(description="Qualification score from 0 to 100")
    reasoning: str = Field(description="Detailed reasoning for the score")


# 2. MCP Server Configuration, Model Configuration & Toolset
from google.adk.models import Gemini

llm_model = Gemini(
    model=config.model,
    retry_options=types.HttpRetryOptions(attempts=5),
)

mcp_script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "mcp_server.py"))
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[mcp_script_path],
        )
    )
)


# 3. Specialized LLM Sub-agents
lead_enricher = LlmAgent(
    name="lead_enricher",
    model=llm_model,
    instruction="""
    You are a Lead Enrichment Specialist. Your job is to take raw lead details and enrich them.
    Use the tools in the MCP toolset to find:
    - Company industry, size, and headquarters using lookup_company.
    - CRM status and owner using check_crm_status.
    - Domain reputation and trust level using verify_domain_reputation.
    Be precise. Return the structured lead information.
    """,
    tools=[mcp_toolset],
    output_schema=LeadEnrichmentOutput,
    description="Enriches raw lead data using company registry, domain lookup, and CRM tools."
)

lead_scorer = LlmAgent(
    name="lead_scorer",
    model=llm_model,
    instruction="""
    You are a Lead Scoring Specialist. Your job is to analyze the enriched lead profile and determine a qualification score (0 to 100).
    Use verify_domain_reputation to verify the domain if needed.
    A lead is high-scoring (Hot) if it's a medium-to-large enterprise, in a high-growth technology/finance sector, or is a new lead with a valid domain.
    A lead is low-scoring (Cold) if it's a tiny company or from a disqualified domain.
    Explain your reasoning clearly.
    """,
    tools=[mcp_toolset],
    output_schema=LeadScoringOutput,
    description="Analyzes enriched lead profiles and calculates a sales qualification score (0-100)."
)



# 3. Orchestrator LLM Agent
orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    model=llm_model,
    instruction="""
    You are the Lead Qualification Orchestrator.
    Your task is to qualify the incoming lead.
    You must follow these steps:
    1. Call the `lead_enricher` tool to enrich the raw lead details.
    2. Call the `lead_scorer` tool with the enriched lead info to calculate the score.
    3. Return the combined qualification result including the lead info, score, and scoring reasoning.
    Do not make up any information. Use the tools.
    """,
    tools=[AgentTool(lead_enricher), AgentTool(lead_scorer)],
    output_schema=LeadQualificationResult,
    output_key="qualification_result"
)


# 4. Workflow Nodes (Functions)

@node
def security_checkpoint(ctx: Context, node_input: types.Content) -> Event:
    """Checks for prompt injection, PII leakage, and competitor domains. Logs security events."""
    import datetime
    
    text_content = ""
    if node_input and node_input.parts:
        text_content = " ".join([p.text for p in node_input.parts if p.text])
        
    ctx.state["raw_lead_query"] = text_content
    
    # 1. PII Scrubbing (Credit Card & SSN)
    scrubbed_text = text_content
    cc_pattern = r'\b(?:\d[ -]*?){13,16}\b'
    scrubbed_text = re.sub(cc_pattern, "[REDACTED_CC]", scrubbed_text)
    ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'
    scrubbed_text = re.sub(ssn_pattern, "[REDACTED_SSN]", scrubbed_text)
    
    if scrubbed_text != text_content:
        audit_log = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": "PII_REDACTION",
            "severity": "WARNING",
            "details": "Sensitive PII (Credit Card or SSN) was detected and redacted."
        }
        print(f"AUDIT_LOG: {json.dumps(audit_log)}")
        ctx.state["raw_lead_query"] = scrubbed_text
        
    # 2. Prompt Injection Detection
    injection_keywords = ["ignore instructions", "bypass security", "system prompt", "jailbreak", "override instruction"]
    detected_keywords = [kw for kw in injection_keywords if kw in text_content.lower()]
    if detected_keywords:
        audit_log = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": "PROMPT_INJECTION_DETECTION",
            "severity": "CRITICAL",
            "details": f"Potential prompt injection detected. Keywords: {detected_keywords}"
        }
        print(f"AUDIT_LOG: {json.dumps(audit_log)}")
        ctx.state["security_issue"] = f"Prompt Injection Violation ({', '.join(detected_keywords)})"
        return Event(output=scrubbed_text, route="violation")
        
    # 3. Domain-Specific Rule: Competitor Check
    competitors = ["competitor.com", "rival.com", "badguy.com"]
    emails_found = re.findall(r'\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Z|a-z]{2,})\b', text_content)
    competitor_detected = False
    for email_domain in emails_found:
        if email_domain.lower() in competitors:
            competitor_detected = True
            break
            
    if competitor_detected:
        audit_log = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": "COMPETITOR_BLOCK",
            "severity": "CRITICAL",
            "details": "Lead source is from a known competitor domain."
        }
        print(f"AUDIT_LOG: {json.dumps(audit_log)}")
        ctx.state["security_issue"] = "Lead is a Competitor (Routing Blocked)"
        return Event(output=scrubbed_text, route="violation")
        
    # Log clean request
    audit_log = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event": "SECURITY_PASSED",
        "severity": "INFO",
        "details": "Security checks passed successfully."
    }
    print(f"AUDIT_LOG: {json.dumps(audit_log)}")
    return Event(output=scrubbed_text, route="clean")


@node
def security_event_handler(ctx: Context, node_input: str) -> Event:
    """Handles flagged security issues and halts processing."""
    issue = ctx.state.get("security_issue", "Security Violation")
    msg = f"❌ Security Event: {issue}. Lead processing halted for safety."
    return Event(
        output={"status": "Security Violation", "message": msg},
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)])
    )


@node
async def decision_node(ctx: Context, node_input: dict) -> Event:
    """Decides if the lead requires Human-in-the-Loop (HITL) review based on score."""
    score = node_input.get("score", 0)
    
    ctx.state["score"] = score
    ctx.state["lead_info"] = node_input.get("lead_info", {})
    ctx.state["reasoning"] = node_input.get("reasoning", "")
    
    if score >= 80:
        if not ctx.resume_inputs or "human_approval" not in ctx.resume_inputs:
            yield RequestInput(
                interrupt_id="human_approval",
                message=f"⚠️ Lead is high-scoring (Score: {score}). Approve routing to priority Sales? (yes/no)"
            )
            return
        
        approval = ctx.resume_inputs["human_approval"].strip().lower()
        if approval == "yes":
            ctx.state["lead_status"] = "Approved by Manager"
        else:
            ctx.state["lead_status"] = "Rejected by Manager"
    else:
        if score >= 50:
            ctx.state["lead_status"] = "Auto-Assigned to Inside Sales"
        else:
            ctx.state["lead_status"] = "Auto-Disqualified (Low Score)"
            
    yield Event(output=node_input)


@node
def sales_router(ctx: Context, node_input: dict) -> Event:
    """Routes the qualified lead to the appropriate sales channel."""
    status = ctx.state.get("lead_status", "Auto-Processed")
    lead_info = ctx.state.get("lead_info", {})
    score = ctx.state.get("score", 0)
    reasoning = ctx.state.get("reasoning", "")
    
    routing_action = ""
    if "Approved" in status or "Inside Sales" in status:
        routing_action = f"Forwarding contact {lead_info.get('name')} ({lead_info.get('email')}) from {lead_info.get('company')} to Sales CRM."
    else:
        routing_action = f"Archiving contact {lead_info.get('name')} from {lead_info.get('company')} as unqualified."
        
    result_summary = {
        "status": status,
        "lead": lead_info,
        "score": score,
        "reasoning": reasoning,
        "routing_action": routing_action
    }
    
    ctx.state["final_result"] = result_summary
    return Event(output=result_summary)


@node
def final_output(ctx: Context, node_input: dict) -> Event:
    """Formats and displays the final workflow outcome to the user."""
    if node_input.get("status") == "Security Violation":
        return Event(output=node_input)
        
    status = node_input.get("status", "Unknown")
    lead = node_input.get("lead", {})
    score = node_input.get("score", 0)
    reasoning = node_input.get("reasoning", "")
    action = node_input.get("routing_action", "")
    
    md_output = f"""
## Lead Qualification Report

* **Lead Name:** {lead.get('name', 'Unknown')}
* **Company:** {lead.get('company', 'Unknown')} ({lead.get('company_size', 'Unknown')} size)
* **Email:** {lead.get('email', 'Unknown')}
* **Domain Fit:** {lead.get('domain_info', 'Unknown')}
* **CRM Status:** {lead.get('crm_status', 'Unknown')}

### Qualification Score: **{score}/100**
* **Status:** {status}
* **Action:** {action}

**Reasoning:** {reasoning}
"""
    yield Event(content=types.Content(role="model", parts=[types.Part.from_text(text=md_output)]))
    yield Event(output=node_input)


# 5. Workflow Graph Definition
root_agent = Workflow(
    name="lead_qualifier_workflow",
    edges=[
        ('START', security_checkpoint),
        (security_checkpoint, {"clean": orchestrator_agent, "violation": security_event_handler}),
        (orchestrator_agent, decision_node),
        (decision_node, sales_router),
        (sales_router, final_output),
        (security_event_handler, final_output)
    ],
    description="Orchestrates lead enrichment, qualification, scoring, and manager approval routing."
)

app = App(
    name="app",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True)
)
