"""Campaign intake, rule confirmation, the approval gate, brief re-checks and source registration
(FR-01 to FR-05, FR-73, spec 12.5)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from psycopg.types.json import Jsonb

from . import ffmpeg
from .agents import rules as agent_a
from .alerts import raise_alert, registry
from .core import AppError, GateError, audit, enqueue, get_setting, sha256_text

RECHECK_VALID_HOURS = 6   # one posting batch (docs/decisions.md)


def fee_pct_from_marketplace(fee_rules: dict, model: str | None, budget_total) -> Decimal | None:
    """Creator fee from the marketplace's published rule, or None when unknown. Never a guess."""
    for rule in (fee_rules or {}).get("creator_fee") or []:
        if model not in rule.get("models", []):
            continue
        if "budget_lt" in rule or "budget_gte" in rule:
            if budget_total is None:
                return None
            if "budget_lt" in rule and not Decimal(str(budget_total)) < rule["budget_lt"]:
                continue
            if "budget_gte" in rule and not Decimal(str(budget_total)) >= rule["budget_gte"]:
                continue
        return Decimal(str(rule["pct"]))
    return None


def _write_version(conn, campaign: dict, marketplace: dict, brief_text: str, licence_ref: str | None) -> dict:
    version_no = conn.execute("select coalesce(max(version_no), 0) + 1 as n from campaign_versions where campaign_id = %s",
                              (campaign["id"],)).fetchone()["n"]
    read_at = dt.datetime.now(dt.timezone.utc)
    result = agent_a.run(conn, registry, brief_text, {
        "campaign_ref": campaign["external_ref"] or str(campaign["id"]), "marketplace": marketplace["slug"],
        "version": version_no, "read_at": read_at.isoformat(), "licence_ref": licence_ref,
        "max_accounts": int(get_setting(conn, "max_accounts_per_campaign_platform")),
    }, campaign["owner_operator_id"])
    rules = result["rules_json"]
    payout = rules["payout"]
    # Only platforms the marketplace tracks are offered (spec 9.7).
    rules["platforms_allowed"] = [p for p in rules["platforms_allowed"] if p in marketplace["platforms"]]
    if not rules["platforms_allowed"] and "platforms_allowed" not in result["unclear_fields"]:
        result["unclear_fields"].append("platforms_allowed")
        if result["compliance_status"] == "pass":
            result["compliance_status"] = "review"
    fee = payout["fee_pct"]
    if fee is None:
        fee = fee_pct_from_marketplace(marketplace["fee_rules"], payout["model"], payout["budget_total"])
    version = conn.execute(
        """insert into campaign_versions (campaign_id, owner_operator_id, version_no, snapshot_hash, raw_text, read_at, rules_json,
               rate_per_1k, fee_pct, max_payout_per_clip, budget_total, budget_remaining, min_views_to_qualify, payout_model,
               compliance_status, hard_blocks, unclear_fields)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *""",
        (campaign["id"], campaign["owner_operator_id"], version_no, sha256_text(brief_text), brief_text, read_at, Jsonb(rules),
         payout["rate_per_1k"], fee, payout["max_payout_per_clip"], payout["budget_total"],
         payout["budget_remaining"] if payout["budget_remaining"] is not None else payout["budget_total"],
         payout["min_views_to_qualify"], payout["model"], result["compliance_status"],
         Jsonb(result["hard_blocks"]), Jsonb(result["unclear_fields"])),
    ).fetchone()
    for rule in rules["content_rules"]:
        conn.execute(
            """insert into campaign_rules (version_id, owner_operator_id, rule_text_verbatim, category, requirement, source_quote)
               values (%s, %s, %s, %s, %s, %s)""",
            (version["id"], campaign["owner_operator_id"], rule["text"], rule["category"], rule["requirement"], rule["quote"]),
        )
    return version


def intake(conn, actor, marketplace_slug: str, name: str, brief_text: str, url: str | None = None,
           category: str = "podcast", deadline: str | None = None, licence_ref: str | None = None,
           external_ref: str | None = None) -> dict:
    """FR-01/02/04: store the raw snapshot, run Agent A, apply hard blocks. The result is a draft."""
    if not brief_text or not brief_text.strip():
        raise AppError("Paste the brief text.", "empty_brief")
    marketplace = conn.execute("select * from marketplaces where slug = %s", (marketplace_slug,)).fetchone()
    if marketplace is None:
        raise AppError(f"Unknown marketplace: {marketplace_slug}", "unknown_marketplace")
    category_row = conn.execute("select * from categories where name = %s", (category,)).fetchone()
    if category_row is None:
        raise AppError(f"Unknown category: {category}", "unknown_category")
    campaign = conn.execute(
        """insert into campaigns (owner_operator_id, marketplace_id, external_ref, name, source_url, category_id, status, deadline)
           values (%s, %s, %s, %s, %s, %s, 'reading', %s) returning *""",
        (actor.operator_id, marketplace["id"], external_ref, name.strip() or "Untitled campaign", url, category_row["id"], deadline),
    ).fetchone()
    version = _write_version(conn, campaign, marketplace, brief_text, licence_ref)
    extracted_deadline = version["rules_json"].get("deadline")
    blocked = version["compliance_status"] == "blocked"
    reason = "; ".join(b["reason"] for b in version["hard_blocks"]) if blocked else None
    campaign = conn.execute(
        """update campaigns set status = %s, status_reason = %s, current_version_id = %s,
               deadline = coalesce(deadline, %s::date + time '23:59')
           where id = %s returning *""",
        ("rejected" if blocked else "pending_approval", reason, version["id"], extracted_deadline, campaign["id"]),
    ).fetchone()
    if blocked:
        raise_alert(conn, campaign["owner_operator_id"], "hard_block", "warn", "campaigns", campaign["id"],
                    f"{campaign['name']} was blocked: {reason}")
    return {"campaign": campaign, "version": version}


def get_campaign(conn, campaign_id) -> dict:
    campaign = conn.execute("select * from campaigns where id = %s", (campaign_id,)).fetchone()
    if campaign is None:
        raise AppError("Campaign not found.", "not_found")
    return campaign


def confirm_rules(conn, actor, campaign_id) -> dict:
    """FR-02: the owner confirms the extracted draft. Needed before approval."""
    campaign = get_campaign(conn, campaign_id)
    version = conn.execute(
        "update campaign_versions set rules_confirmed_by = %s, rules_confirmed_at = now() where id = %s returning *",
        (actor.operator_id, campaign["current_version_id"]),
    ).fetchone()
    if version is None:
        raise GateError("You cannot confirm rules for this campaign.")
    audit(conn, "rules.confirmed", "campaign_versions", version["id"], {"unclear_fields": version["unclear_fields"]},
          rule_version_id=version["id"])
    return version


def approve(conn, actor, campaign_id) -> dict:
    """Gate 1: campaign approval. A human action; hard-blocked campaigns cannot pass (database trigger)."""
    if actor.kind != "operator":
        raise GateError("Campaign approval needs a human.")
    campaign = get_campaign(conn, campaign_id)
    if campaign["status"] not in ("pending_approval", "paused"):
        raise AppError(f"A campaign that is {campaign['status']} cannot be approved.", "bad_status")
    version = conn.execute("select * from campaign_versions where id = %s", (campaign["current_version_id"],)).fetchone()
    if version["compliance_status"] == "blocked":
        raise GateError("This campaign is hard-blocked and cannot be approved.")
    if version["rules_confirmed_by"] is None:
        confirm_rules(conn, actor, campaign_id)   # approving the draft is confirming it
    campaign = conn.execute("update campaigns set status = 'approved', status_reason = null where id = %s returning *",
                            (campaign_id,)).fetchone()
    audit(conn, "gate.campaign_approval", "campaigns", campaign_id,
          {"version_no": version["version_no"], "compliance_status": version["compliance_status"]}, rule_version_id=version["id"])
    return campaign


def park(conn, actor, campaign_id) -> dict:
    campaign = get_campaign(conn, campaign_id)
    if campaign["status"] not in ("pending_approval", "approved"):
        raise AppError(f"A campaign that is {campaign['status']} cannot be parked.", "bad_status")
    return conn.execute("update campaigns set status = 'paused', status_reason = 'Parked by the owner' where id = %s returning *",
                        (campaign_id,)).fetchone()


def reject(conn, actor, campaign_id, reason: str) -> dict:
    if not reason or not reason.strip():
        raise AppError("A rejection needs a reason.", "reason_required")
    get_campaign(conn, campaign_id)
    return conn.execute("update campaigns set status = 'rejected', status_reason = %s where id = %s returning *",
                        (reason.strip(), campaign_id)).fetchone()


def close(conn, actor, campaign_id) -> dict:
    get_campaign(conn, campaign_id)
    return conn.execute("update campaigns set status = 'closed' where id = %s returning *", (campaign_id,)).fetchone()


# ------------------------------------------------------------------ rule-change detection (spec 12.5)

def recheck_brief(conn, actor, campaign_id, current_text: str | None = None, unchanged_confirmed: bool = False) -> dict:
    """Re-check the brief before a posting batch. Any change writes a new version (the old one is never
    overwritten), sends the campaign back for approval and raises a block alert."""
    campaign = get_campaign(conn, campaign_id)
    marketplace = conn.execute("select * from marketplaces where id = %s", (campaign["marketplace_id"],)).fetchone()
    version = conn.execute("select * from campaign_versions where id = %s",
                           (campaign["approved_version_id"] or campaign["current_version_id"],)).fetchone()
    new_text = current_text
    if new_text is None and campaign.get("listing_id"):
        from . import discovery

        new_text = discovery.current_brief_text(conn, campaign)
    if new_text is None and marketplace["source_adapter"] in ("scraper", "api") and marketplace["scraper_enabled"]:
        result, _ = registry.call(conn, "source", "check_changes", {
            "ref": {"external_ref": campaign["external_ref"], "url": campaign["source_url"], "marketplace": marketplace},
            "last_hash": version["snapshot_hash"]}, campaign["owner_operator_id"])
        new_text = result.get("text")
    if new_text is None:
        if not unchanged_confirmed:
            raise AppError("Open the brief, then either paste its current text or confirm it is unchanged.", "recheck_needed")
        audit(conn, "brief.recheck", "campaigns", campaign_id, {"changed": False, "method": "owner_confirmed"},
              rule_version_id=version["id"])
        return {"changed": False, "version_id": version["id"]}
    if sha256_text(new_text) == version["snapshot_hash"]:
        audit(conn, "brief.recheck", "campaigns", campaign_id, {"changed": False, "method": "hash"}, rule_version_id=version["id"])
        return {"changed": False, "version_id": version["id"]}
    new_version = _write_version(conn, campaign, marketplace, new_text, None)
    blocked = new_version["compliance_status"] == "blocked"
    conn.execute(
        "update campaigns set status = %s, status_reason = %s, current_version_id = %s where id = %s",
        ("rejected" if blocked else "pending_approval",
         "The brief changed and is now hard-blocked." if blocked else "The brief changed. Re-approve before posting.",
         new_version["id"], campaign_id),
    )
    audit(conn, "brief.recheck", "campaigns", campaign_id, {"changed": True, "new_version_no": new_version["version_no"]},
          rule_version_id=new_version["id"])
    raise_alert(conn, campaign["owner_operator_id"], "rule_change", "block", "campaigns", campaign_id,
                f"{campaign['name']}: the brief changed. Posting for it is blocked until you re-approve.")
    return {"changed": True, "version_id": new_version["id"]}


def brief_recheck_is_fresh(conn, campaign: dict) -> bool:
    """True when the latest re-check found no change, against the approved version, within this batch."""
    if campaign["status"] != "approved":
        return False
    row = conn.execute(
        """select (detail->>'changed')::boolean as changed, rule_version_id, at > now() - make_interval(hours => %s) as recent
           from audit_log where action = 'brief.recheck' and entity_id = %s order by at desc, created_at desc limit 1""",
        (RECHECK_VALID_HOURS, campaign["id"]),
    ).fetchone()
    return bool(row and not row["changed"] and row["recent"] and row["rule_version_id"] == campaign["approved_version_id"])


# ------------------------------------------------------------------ sources (S3)

def register_source(conn, actor, campaign_id, file_path: str, language: str, dialect: str | None = None,
                    licence_ref: str | None = None, permission_ref: str | None = None, origin_url: str | None = None,
                    expected_hash: str | None = None) -> dict:
    """Register permitted footage for an approved campaign and start its pipeline (FR-10)."""
    campaign = get_campaign(conn, campaign_id)
    if campaign["status"] != "approved":
        raise GateError("Only an approved campaign can take source footage.")
    if language not in ("en", "ar"):
        raise AppError("Language must be en or ar.", "bad_language")
    if not (licence_ref or permission_ref):
        raise AppError("Permitted footage only: give a licence reference or a written-permission reference.", "no_permission")
    path = Path(file_path)
    if not path.is_file():
        raise AppError("Source file not found.", "no_file")
    file_hash = ffmpeg.sha256_file(path)
    if expected_hash and expected_hash != file_hash:
        raise AppError("The file hash does not match the expected hash.", "hash_mismatch")
    info = ffmpeg.probe(path)
    storage = registry.get(conn, "storage")
    key = f"sources/{file_hash}{path.suffix.lower() or '.mp4'}"
    storage.put(key, str(path))
    if language == "ar" and not dialect:
        dialect = "ar"   # Modern Standard Arabic until the dialect bake-off exists (O2)
    retention = int(get_setting(conn, "source_retention_days"))
    source = conn.execute(
        """insert into source_assets (campaign_id, owner_operator_id, origin_url, file_hash, duration_s, language, dialect,
               licence_ref, permission_ref, stored_path, delete_after)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(days => %s)) returning *""",
        (campaign_id, campaign["owner_operator_id"], origin_url, file_hash, info["duration_s"], language, dialect,
         licence_ref, permission_ref, key, retention),
    ).fetchone()
    enqueue(conn, "transcribe", {"source_id": str(source["id"]), "file_hash": file_hash, "language": language, "dialect": dialect},
            campaign["owner_operator_id"], source_id=source["id"], campaign_version_id=campaign["approved_version_id"])
    return source


def purge_expired_sources(conn) -> int:
    """Delete source footage past its retention date (spec 15.4)."""
    storage = registry.get(conn, "storage")
    rows = conn.execute("select id, stored_path from source_assets where delete_after < now() and deleted_at is null").fetchall()
    for row in rows:
        storage.delete(row["stored_path"])
        conn.execute("update source_assets set deleted_at = now() where id = %s", (row["id"],))
    return len(rows)
