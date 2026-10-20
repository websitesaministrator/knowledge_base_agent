"""
agents/verifier.py - Verifier Agent.
Performs deterministic, plain-Python fact-checking FIRST on structured fields and triage steps,
then delegates strictly qualitative checks to the LLM Verifier.
PASS only if both code checks and LLM checks have zero issues.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any, Literal
import pandas as pd
from pydantic import BaseModel, Field

from agents.base import run_agent
from tools.tickets import get_cluster_tickets, cluster_stats, fix_usage_map, data_quality_report
from tools.audit_log import append_entry

logger = logging.getLogger(__name__)

AGENT_NAME = "Verifier"

PROHIBITED_SYSTEM_WORDS = [
    "reset", "update", "restart", "reconfigure", "reboot",
    "install", "modify", "patch", "delete", "remove",
    "reinstall", "upgrade", "downgrade", "change config"
]

PROHIBITED_CAUSAL_PHRASES = [
    "to avoid", "because", "so that", "in order to",
    "as a result of", "due to", "caused by"
]


class VerifierIssue(BaseModel):
    check: str = Field(description="Name of check that failed")
    detail: str = Field(description="Exact discrepancy or violation found, stating expected value")
    how_to_fix: str = Field(description="Clear instruction for the Author on how to fix this exact issue")


class VerifierVerdict(BaseModel):
    verdict: Literal["PASS", "REVISE"] = Field(description="PASS if zero issues, else REVISE")
    issues: list[VerifierIssue] = Field(default_factory=list, description="List of issues found")


def run_code_checks(
    article: dict,
    cluster_id: str,
    window: str = "baseline",
) -> list[VerifierIssue]:
    """
    Plain-Python deterministic checks that compare structured article fields
    with values computed from tools/tickets.py. No LLM used.
    Returns a list of VerifierIssue objects for any discrepancies.
    """
    issues: list[VerifierIssue] = []

    cat, subcat = cluster_id.split("|", 1) if "|" in cluster_id else (cluster_id, "")
    variant_suffix = subcat.replace(cat, "").strip(" -")

    # 1. Title check: Real cluster name used, never "Category 3" or generic placeholders
    title = str(article.get("title", ""))
    if re.search(r"\bcategory\s*\d+\b", title, re.IGNORECASE) or (cat.lower() not in title.lower()):
        issues.append(
            VerifierIssue(
                check="check_title",
                detail=f"Title '{title}' does not use real cluster name or contains generic 'Category' label.",
                how_to_fix=f"Use title with real cluster name, e.g. '{cat} issues ({variant_suffix}): recurring tickets'." if variant_suffix else f"Use title with real cluster name, e.g. '{cat}: recurring tickets'."
            )
        )

    # Fetch ground-truth cluster tickets
    tickets_res = get_cluster_tickets(cluster_id, window=window, limit=500)
    ticket_list = tickets_res.get("tickets", [])
    valid_ticket_ids = {t["ticket_id"] for t in ticket_list}
    df_tickets = pd.DataFrame(ticket_list) if ticket_list else pd.DataFrame(columns=["ticket_id", "product", "severity", "fix_id", "is_repetitive"])

    # 2. Check ticket IDs in evidence
    evidence = article.get("evidence") or {}
    cited_ids = evidence.get("ticket_ids", [])
    if not cited_ids:
        issues.append(
            VerifierIssue(
                check="check_ticket_ids",
                detail="No ticket IDs cited in evidence.",
                how_to_fix=f"Populate evidence.ticket_ids with verified ticket IDs from cluster (e.g. {sorted(list(valid_ticket_ids))[:5]})."
            )
        )
    else:
        for tid in cited_ids:
            if tid not in valid_ticket_ids:
                issues.append(
                    VerifierIssue(
                        check="check_ticket_ids",
                        detail=f"Ticket ID '{tid}' does not exist in cluster '{cluster_id}' ({window} window).",
                        how_to_fix=f"Remove '{tid}'. Valid cluster tickets are: {sorted(list(valid_ticket_ids))}."
                    )
                )

    # 3. Check ticket_count and repetitive_rate in evidence
    expected_ticket_count = len(valid_ticket_ids)
    actual_t_count = evidence.get("ticket_count")
    if actual_t_count != expected_ticket_count:
        issues.append(
            VerifierIssue(
                check="check_evidence_numbers",
                detail=f"ticket_count in evidence is {actual_t_count}, expected {expected_ticket_count}.",
                how_to_fix=f"Set evidence.ticket_count to {expected_ticket_count}."
            )
        )

    expected_rep_count = int(df_tickets["is_repetitive"].sum()) if not df_tickets.empty else 0
    expected_rep_rate = round(expected_rep_count / expected_ticket_count, 3) if expected_ticket_count else 0.0
    actual_rep_rate = evidence.get("repetitive_rate")
    if actual_rep_rate is None or abs(float(actual_rep_rate) - expected_rep_rate) > 0.01:
        issues.append(
            VerifierIssue(
                check="check_evidence_numbers",
                detail=f"repetitive_rate in evidence is {actual_rep_rate}, expected {expected_rep_rate}.",
                how_to_fix=f"Set evidence.repetitive_rate to {expected_rep_rate}."
            )
        )

    # 4. Check product counts vs product column
    expected_prod_counts = df_tickets["product"].value_counts().to_dict() if not df_tickets.empty else {}
    art_products_raw = article.get("products", [])
    if not art_products_raw:
        issues.append(
            VerifierIssue(
                check="check_product_counts",
                detail="Structured 'products' field is missing or empty.",
                how_to_fix=f"Add structured products field matching the product column: {[{'name': k, 'count': v} for k, v in expected_prod_counts.items()]}."
            )
        )
    else:
        art_prod_counts: dict[str, int] = {}
        for p in art_products_raw:
            if isinstance(p, dict):
                pname = p.get("name") or p.get("product") or ""
                art_prod_counts[str(pname)] = int(p.get("count", 0))

        if art_prod_counts != expected_prod_counts:
            issues.append(
                VerifierIssue(
                    check="check_product_counts",
                    detail=f"Product counts mismatch. Article has {art_prod_counts}, expected {expected_prod_counts} based on product column.",
                    how_to_fix=f"Set products to: {[{'name': k, 'count': v} for k, v in expected_prod_counts.items()]}."
                )
            )

    # 5. Check severity counts
    expected_sev_counts = df_tickets["severity"].value_counts().to_dict() if not df_tickets.empty else {}
    art_sev_raw = article.get("severity_mix", [])
    if not art_sev_raw:
        issues.append(
            VerifierIssue(
                check="check_severity_mix",
                detail="Structured 'severity_mix' field is missing or empty.",
                how_to_fix=f"Add structured severity_mix matching cluster tickets: {[{'severity': k, 'count': v} for k, v in expected_sev_counts.items()]}."
            )
        )
    else:
        art_sev_counts: dict[str, int] = {}
        for s in art_sev_raw:
            if isinstance(s, dict):
                sname = s.get("severity") or s.get("level") or ""
                art_sev_counts[str(sname)] = int(s.get("count", 0))

        if art_sev_counts != expected_sev_counts:
            issues.append(
                VerifierIssue(
                    check="check_severity_mix",
                    detail=f"Severity mix mismatch. Article has {art_sev_counts}, expected {expected_sev_counts}.",
                    how_to_fix=f"Set severity_mix to: {[{'severity': k, 'count': v} for k, v in expected_sev_counts.items()]}."
                )
            )

    # 6. Check per-fix ticket_count and other_categories_count == (total categories using that fix) - 1
    fum = fix_usage_map()
    fixes_info = fum.get("fixes", {})
    actual_fix_counts = df_tickets["fix_id"].dropna().astype(int).value_counts().to_dict() if not df_tickets.empty else {}

    art_resolutions = article.get("resolutions_on_record", [])
    if not art_resolutions:
        issues.append(
            VerifierIssue(
                check="check_resolutions_on_record",
                detail="Missing structured 'resolutions_on_record' field.",
                how_to_fix="Add resolutions_on_record listing fixes from cluster tickets."
            )
        )
    else:
        for r in art_resolutions:
            if not isinstance(r, dict):
                continue
            fid_raw = r.get("fix_id")
            if fid_raw is None:
                continue
            fid = int(fid_raw)
            if fid not in actual_fix_counts:
                issues.append(
                    VerifierIssue(
                        check="check_resolutions_on_record",
                        detail=f"Fix #{fid} was not applied to any tickets in cluster '{cluster_id}'. Actual fixes in cluster: {sorted(list(actual_fix_counts.keys()))}.",
                        how_to_fix=f"Cite only fix IDs present in the cluster: {sorted(list(actual_fix_counts.keys()))}."
                    )
                )
                continue

            # Check per-fix ticket count
            exp_t_count = actual_fix_counts[fid]
            found_t_count = r.get("ticket_count")
            if found_t_count != exp_t_count:
                issues.append(
                    VerifierIssue(
                        check="check_resolutions_on_record",
                        detail=f"Fix #{fid} has ticket_count={found_t_count}, expected {exp_t_count}.",
                        how_to_fix=f"Set ticket_count={exp_t_count} for fix #{fid}."
                    )
                )

            # Check other_categories_count == (categories using that fix) - 1
            fix_meta = fixes_info.get(str(fid), {})
            total_cat_count = fix_meta.get("category_count", 0)
            expected_other = max(0, total_cat_count - 1)
            found_other = r.get("other_categories_count")
            if found_other != expected_other:
                issues.append(
                    VerifierIssue(
                        check="check_resolutions_on_record",
                        detail=(
                            f"Fix #{fid} has other_categories_count={found_other}, expected {expected_other}. "
                            f"(Total categories using fix #{fid} is {total_cat_count}, minus 1 for {cat})."
                        ),
                        how_to_fix=f"Set other_categories_count={expected_other} for fix #{fid}."
                    )
                )

    # 7. Check every number in what_we_see prose appears in structured fields
    allowed_numbers: set[str] = set()

    for p in art_products_raw:
        if isinstance(p, dict) and p.get("count") is not None:
            allowed_numbers.add(str(p["count"]))

    for s in art_sev_raw:
        if isinstance(s, dict):
            if s.get("count") is not None:
                allowed_numbers.add(str(s["count"]))
            if s.get("severity"):
                for d in re.findall(r"\d+", str(s["severity"])):
                    allowed_numbers.add(d)

    for r in art_resolutions:
        if isinstance(r, dict):
            for k in ["fix_id", "ticket_count", "other_categories_count"]:
                if r.get(k) is not None:
                    allowed_numbers.add(str(r[k]))

    if actual_t_count is not None:
        allowed_numbers.add(str(actual_t_count))
    if actual_rep_rate is not None:
        rate_f = float(actual_rep_rate)
        allowed_numbers.add(str(rate_f))
        allowed_numbers.add(str(int(round(rate_f * 100))))
        allowed_numbers.add(str(rate_f).replace("0.", ""))
        allowed_numbers.add(str(expected_rep_count))

    for tid in cited_ids:
        for d in re.findall(r"\d+", str(tid)):
            allowed_numbers.add(d)
            allowed_numbers.add(str(int(d)))

    for d in re.findall(r"\d+", cluster_id):
        allowed_numbers.add(d)

    if article.get("version"):
        allowed_numbers.add(str(article["version"]))

    # Known platform constants: 242/300 mismatch note, 24 clusters, 20 fixes, 0
    allowed_numbers.update(["0", "242", "300", "24", "20", "48"])

    what_we_see_text = str(article.get("what_we_see", ""))
    prose_tokens = re.findall(r"\b\d+\b", what_we_see_text)
    for token in prose_tokens:
        if token not in allowed_numbers and str(int(token)) not in allowed_numbers:
            issues.append(
                VerifierIssue(
                    check="check_prose_numbers",
                    detail=f"Number '{token}' found in what_we_see prose is not present in structured fields. what_we_see must not state numbers absent from structured fields.",
                    how_to_fix=f"Remove or correct number '{token}' in what_we_see narrative."
                )
            )

    # 8. Check suggested_triage_steps
    if "suggested_steps" in article and "suggested_triage_steps" not in article:
        issues.append(
            VerifierIssue(
                check="check_suggested_triage_steps",
                detail="Field 'suggested_steps' is obsolete. Must use 'suggested_triage_steps'.",
                how_to_fix="Rename field 'suggested_steps' to 'suggested_triage_steps'."
            )
        )

    triage_steps = article.get("suggested_triage_steps") or article.get("suggested_steps", [])
    if not triage_steps:
        issues.append(
            VerifierIssue(
                check="check_suggested_triage_steps",
                detail="Missing 'suggested_triage_steps' field.",
                how_to_fix="Add suggested_triage_steps (max 5 generic steps about gathering and checking info)."
            )
        )
    elif len(triage_steps) > 5:
        issues.append(
            VerifierIssue(
                check="check_suggested_triage_steps",
                detail=f"Found {len(triage_steps)} triage steps, maximum 5 allowed.",
                how_to_fix="Reduce suggested_triage_steps to at most 5 steps."
            )
        )
    else:
        for idx, step in enumerate(triage_steps, start=1):
            if isinstance(step, dict):
                stext = str(step.get("text", ""))
                label = str(step.get("label", ""))
            else:
                stext = str(step)
                label = ""

            if label != "AI-suggested, unverified":
                issues.append(
                    VerifierIssue(
                        check="check_suggested_triage_steps",
                        detail=f"Step {idx} ('{stext[:40]}...') has label '{label}', expected 'AI-suggested, unverified'.",
                        how_to_fix="Label every step as 'AI-suggested, unverified'."
                    )
                )

            # Check system altering words
            word_pat = r"\b(" + "|".join(PROHIBITED_SYSTEM_WORDS) + r")\b"
            m = re.search(word_pat, stext, re.IGNORECASE)
            if m:
                issues.append(
                    VerifierIssue(
                        check="check_suggested_triage_steps",
                        detail=f"Step {idx} contains prohibited system-altering instruction '{m.group(0)}': '{stext}'.",
                        how_to_fix="Remove any instruction that changes a system (no reset, update, restart, reconfigure). Steps must only gather or check information."
                    )
                )

            # Check causal claims
            for phrase in PROHIBITED_CAUSAL_PHRASES:
                if phrase in stext.lower():
                    issues.append(
                        VerifierIssue(
                            check="check_suggested_triage_steps",
                            detail=f"Step {idx} contains prohibited causal claim '{phrase}': '{stext}'.",
                            how_to_fix=f"Remove causal claim '{phrase}'. Steps must not carry causal claims."
                        )
                    )

    return issues


QUALITATIVE_INSTRUCTION = (
    "You are the Verifier agent for an IT knowledge base lifecycle system.\n"
    "Your job is strictly qualitative audit of a drafted KB article.\n"
    "Note: Quantitative numbers, ticket counts, product counts, and triage step schemas are already verified by deterministic code checks.\n"
    "You MUST execute these qualitative checks:\n"
    "1. check_unsupported_symptoms: Verify that 'what_we_see' does NOT invent symptoms or use speculative words not present in the data (e.g. 'unexpected', 'unanticipated', 'intermittent', 'corrupted', 'slow performance').\n"
    "2. check_causal_claims: Verify that 'what_we_see' and triage steps do NOT state unproven causal claims (e.g. 'because', 'caused by', 'due to', 'in order to', 'to avoid').\n"
    "3. check_required_statements: Verify that the article explicitly states:\n"
    "   a) ticket summaries contain only templated category wording with no symptom detail;\n"
    "   b) fix procedures are not present in the support ticket data;\n"
    "   c) fix IDs are shared across multiple categories;\n"
    "   d) the product mismatch note (product in summary differs from product column in 242/300 rows).\n"
    "4. check_system_alteration: Verify that no prose or triage steps instruct changing, resetting, updating, or modifying systems.\n\n"
    "VERDICT RULES:\n"
    "- If no qualitative issues exist: verdict must be 'PASS' and issues must be [].\n"
    "- If any qualitative issue exists: verdict must be 'REVISE' and list the exact issues.\n"
    "- You must NEVER rewrite the article yourself."
)


def verify_article(
    article: dict,
    cluster_id: str,
    window: str = "baseline",
    max_steps: int = 8,
    skip_llm: bool = False,
) -> dict:
    """
    Execute verification on an article draft.
    1. Runs deterministic plain-Python code checks FIRST.
    2. Runs LLM Verifier for strictly qualitative checks.
    3. PASS only if code checks AND LLM checks have zero issues.
    Logs code checks to the audit log as 'code_check'.
    """
    # 1. Plain-Python deterministic checks run FIRST
    code_issues = run_code_checks(article=article, cluster_id=cluster_id, window=window)

    append_entry(
        agent="Verifier",
        action="code_check",
        inputs={
            "article_id": article.get("article_id"),
            "cluster_id": cluster_id,
            "window": window,
        },
        outputs={
            "issue_count": len(code_issues),
            "passed": len(code_issues) == 0,
            "issues": [i.model_dump() for i in code_issues],
        },
        sources=article.get("evidence", {}).get("ticket_ids", []),
        outcome="success" if not code_issues else "partial",
        notes=f"Deterministic code check: {len(code_issues)} issues found",
    )

    llm_issues: list[VerifierIssue] = []
    run_id = None
    steps = 0
    raw_content = ""

    if not skip_llm:
        article_str = json.dumps(article, indent=2)
        user_input = (
            f"Qualitatively audit the following KB article draft for cluster '{cluster_id}' in '{window}' window.\n"
            f"Check for invented symptoms, causal assertions, required statements (templated wording note, no fix procedures, shared fix IDs, 242/300 mismatch), and system-altering instructions.\n"
            f"Article draft to verify:\n"
            f"```json\n{article_str}\n```\n"
            f"Output strictly valid JSON conforming to the VerifierVerdict schema."
        )

        tools = [
            get_cluster_tickets,
            cluster_stats,
            fix_usage_map,
            data_quality_report,
        ]

        result = run_agent(
            name=AGENT_NAME,
            instruction=QUALITATIVE_INSTRUCTION,
            tools=tools,
            user_input=user_input,
            output_schema=VerifierVerdict,
            max_steps=max_steps,
        )

        run_id = result.get("run_id")
        steps = result.get("steps", 0)
        raw_content = result.get("content", "")

        data = result.get("data") or {}
        raw_llm_issues = data.get("issues", [])
        for iss in raw_llm_issues:
            if isinstance(iss, VerifierIssue):
                llm_issues.append(iss)
            elif isinstance(iss, dict):
                llm_issues.append(VerifierIssue(**iss))

    # Combine issues: PASS only if code checks AND LLM checks have zero issues
    all_issues = code_issues + llm_issues
    final_verdict = "PASS" if len(all_issues) == 0 else "REVISE"

    return {
        "verdict": final_verdict,
        "issues": [i.model_dump() for i in all_issues],
        "code_issues_count": len(code_issues),
        "llm_issues_count": len(llm_issues),
        "run_id": run_id,
        "steps": steps,
        "raw_content": raw_content,
    }
