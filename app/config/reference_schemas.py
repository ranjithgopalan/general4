"""Reference schema definitions — expected fields for tracking critical capabilities.

When analyzing requirements for FIELD_ADDITION or SCHEMA_CHANGE patterns,
this module defines what fields a KB entity SHOULD have to support specific
features (delivery tracking, payment reconciliation, etc.).

If a requirement mentions delivery tracking but the target table is missing
`delivery_status` or `delivery_timestamp` fields, this gap is flagged as a
BlindSpot during analysis.

Usage:
  - Schema gaps detected during analysis → BlindSpots surfaced to BA
  - BA uses this to understand if requirement can be met (schema exists)
    or if schema evolution is needed first
"""

# Maps entity name/label to expected schema for various tracking features
ENTITY_SCHEMAS = {
    "WEB_RECEIPTS": {
        "source": "ENT-JAUTO-DB-051",
        "kind": "Entity",
        "purpose": "Receipt generation and tracking for renewal process",
        "existing_fields": [
            "receipt_id",
            "customer_id",
            "receipt_date",
            "print_count",
            "created_timestamp",
        ],
        # What fields SHOULD exist for delivery tracking capability
        "expected_delivery_tracking_fields": [
            "delivery_status",  # PENDING, SENT, DELIVERED, FAILED, BOUNCED
            "preferred_delivery_method",  # EMAIL, SMS, POSTAL
            "delivery_timestamp",  # When was it sent?
            "delivery_failure_reason",  # Why did it fail?
        ],
        "gap_if_missing": "No delivery tracking or delivery failure diagnosis capability — renewal handlers cannot determine if receipt reached customer",
    },
    "WEB_RECEIPTS_HISTORY": {
        "source": "ENT-JAUTO-DB-053",
        "kind": "Entity",
        "purpose": "Audit trail for receipt status changes over time",
        "existing_fields": ["receipt_history_id", "receipt_id", "changed_timestamp", "change_type"],
        "expected_delivery_tracking_fields": [
            "delivery_status",  # Track when status changes
            "delivery_timestamp",  # When was status reached?
        ],
        "gap_if_missing": "Cannot audit trail delivery status changes — compliance/reconciliation issues",
    },
    "DRCT_MSG_REGST": {
        "source": "ENT-JAUTO-DB-041",
        "kind": "Entity",
        "purpose": "Message/notification registration and routing",
        "existing_fields": ["msg_id", "customer_id", "message_type", "created_date"],
        "expected_delivery_tracking_fields": [
            "delivery_status",  # Track message delivery (sent/delivered/failed)
            "delivery_timestamp",  # When message was delivered
            "delivery_method_preference",  # Email/SMS/Postal preference
        ],
        "gap_if_missing": "Cannot track delivery of notifications — delivery status opaque to handlers",
    },
    "DRCT_MSG_EMAIL_DATA": {
        "source": "ENT-JAUTO-DB-042",
        "kind": "Entity",
        "purpose": "Email-specific message data and tracking",
        "existing_fields": ["email_id", "msg_id", "email_address", "subject"],
        "expected_delivery_tracking_fields": [
            "delivery_status",  # Bounced, Delivered, Opened, etc.
            "delivery_timestamp",  # When delivered to mailbox
            "opened_timestamp",  # If customer opened email
        ],
        "gap_if_missing": "Cannot determine email delivery success — no feedback from email provider",
    },
    "T_PAYMENT_DETAILS": {
        "source": "ENT-JAUTO-DB-051",
        "kind": "Entity",
        "purpose": "Payment information and reconciliation data",
        "existing_fields": ["payment_id", "receipt_id", "amount", "payment_date"],
        "expected_payment_tracking_fields": [
            "payment_status",  # PENDING, PROCESSED, FAILED, RECONCILED
            "reconciliation_status",  # MATCHED, UNMATCHED, DISPUTED
        ],
        "gap_if_missing": "Cannot track payment status or reconciliation state",
    },
}

# Quick lookup: if requirement mentions X capability, check Y entity for Z fields
CAPABILITY_FIELD_MAP = {
    "delivery_tracking": {
        "entities": ["WEB_RECEIPTS", "DRCT_MSG_REGST", "DRCT_MSG_EMAIL_DATA"],
        "required_fields": ["delivery_status", "delivery_timestamp"],
    },
    "delivery_audit": {
        "entities": ["WEB_RECEIPTS_HISTORY", "DRCT_MSG_EMAIL_DATA"],
        "required_fields": ["delivery_status", "delivery_timestamp"],
    },
    "payment_reconciliation": {
        "entities": ["T_PAYMENT_DETAILS"],
        "required_fields": ["payment_status", "reconciliation_status"],
    },
    "customer_preference": {
        "entities": ["WEB_RECEIPTS", "DRCT_MSG_REGST"],
        "required_fields": ["preferred_delivery_method"],
    },
}


def get_schema_gaps(entity_label: str, capability: str) -> list[str]:
    """Check if entity has all fields needed for a capability.

    Args:
        entity_label: Entity name (e.g., "WEB_RECEIPTS")
        capability: Capability being checked (e.g., "delivery_tracking")

    Returns:
        List of missing field names (empty if all present)
    """
    schema = ENTITY_SCHEMAS.get(entity_label, {})
    if not schema:
        return []

    # Determine what fields are needed for this capability
    if capability == "delivery_tracking":
        expected = schema.get("expected_delivery_tracking_fields", [])
    elif capability == "payment_tracking":
        expected = schema.get("expected_payment_tracking_fields", [])
    else:
        expected = []

    existing = set(schema.get("existing_fields", []))
    missing = [f for f in expected if f not in existing]
    return missing
