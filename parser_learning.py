"""Parser learning rules: teach the parser from Training Center corrections."""

from __future__ import annotations

import re
from typing import Any

from condition_normalizer import (
    CONDITION_CONFIDENCE_HIGH,
    CONDITION_SOURCE_EXPLICIT,
    NEW_CONDITION,
    PRE_OWNED_CONDITION,
    normalize_wear_condition,
)

Record = dict[str, Any]

FIELD_TYPES = frozenset({
    "condition",
    "brand",
    "brand_header",
    "reference",
    "price",
    "intent",
    "currency",
    "row_split",
})
SCOPES = frozenset({"global", "dealer", "group"})
RULE_STATUSES = frozenset({"active", "disabled"})

CONDITION_TRAINING_TERMS: tuple[str, ...] = (
    "fresh",
    "mint",
    "stock",
    "clean",
    "never worn",
)

CONDITION_TRAINING_TERM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bnever\s+worn\b", re.I), "never worn"),
    (re.compile(r"\bfresh\b", re.I), "fresh"),
    (re.compile(r"\bmint\b", re.I), "mint"),
    (re.compile(r"\bstock\b", re.I), "stock"),
    (re.compile(r"\bclean\b", re.I), "clean"),
]

SCOPE_PRIORITY = {"group": 0, "dealer": 1, "global": 2}


def _normalize_term(term: str) -> str:
    return re.sub(r"\s+", " ", term.strip().lower())


def _rule_specificity(rule: Record) -> int:
    return SCOPE_PRIORITY.get(str(rule.get("scope") or "global"), 99)


def find_matching_learning_rule(
    rules: list[Record],
    *,
    field_type: str,
    term: str,
    dealer_id: str | None = None,
    group_id: str | None = None,
) -> Record | None:
    """Return the most specific active rule for a term."""
    normalized_term = _normalize_term(term)
    if not normalized_term:
        return None

    matches: list[Record] = []
    for rule in rules:
        if str(rule.get("status") or "active") != "active":
            continue
        if str(rule.get("field_type") or "") != field_type:
            continue
        if _normalize_term(str(rule.get("term") or "")) != normalized_term:
            continue

        scope = str(rule.get("scope") or "global")
        rule_dealer_id = str(rule.get("dealer_id") or "") or None
        rule_group_id = str(rule.get("group_id") or "") or None
        if scope == "dealer":
            if not dealer_id or rule_dealer_id != str(dealer_id):
                continue
        elif scope == "group":
            if not group_id or rule_group_id != str(group_id):
                continue
        matches.append(rule)

    if not matches:
        return None
    return sorted(matches, key=_rule_specificity)[0]


def detect_condition_training_term(message_text: str, watch: Record | None = None) -> str | None:
    """Return the first unknown condition training term found in message or watch."""
    haystacks: list[str] = []
    if message_text.strip():
        haystacks.append(message_text)
    if watch:
        for key in ("condition", "raw_condition", "source_line", "notes"):
            value = watch.get(key)
            if value:
                haystacks.append(str(value))

    for text in haystacks:
        for pattern, label in CONDITION_TRAINING_TERM_PATTERNS:
            if pattern.search(text):
                return label
    return None


def _apply_condition_learning_rule(watch: Record, rule: Record, term: str) -> bool:
    normalized_value = str(rule.get("normalized_value") or "").strip()
    if normalized_value not in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        return False

    watch["condition"] = normalized_value
    watch["raw_condition"] = term.title() if term != "never worn" else "Never worn"
    watch["condition_source"] = CONDITION_SOURCE_EXPLICIT
    watch["condition_confidence"] = CONDITION_CONFIDENCE_HIGH
    watch["condition_explicit"] = True
    watch["condition_learned_rule_id"] = rule.get("id")
    watch.pop("condition_needs_training", None)
    watch.pop("condition_training_term", None)
    return True


def apply_learning_rules_to_watch(
    watch: Record,
    *,
    message_text: str = "",
    dealer_id: str | None = None,
    group_id: str | None = None,
    rules: list[Record] | None = None,
) -> Record:
    """Apply learned parser rules before default inference."""
    from database import list_active_parser_learning_rules

    active_rules = rules if rules is not None else list_active_parser_learning_rules()

    training_term = detect_condition_training_term(message_text, watch)
    if training_term:
        condition_rule = find_matching_learning_rule(
            active_rules,
            field_type="condition",
            term=training_term,
            dealer_id=dealer_id,
            group_id=group_id,
        )
        if condition_rule and _apply_condition_learning_rule(watch, condition_rule, training_term):
            watch["condition_learning_applied"] = True
            return watch

    for field_type in ("brand", "reference", "currency", "price", "intent"):
        candidate_terms: list[str] = []
        if field_type == "brand" and watch.get("brand"):
            candidate_terms.append(str(watch["brand"]))
        if field_type == "reference" and watch.get("reference"):
            candidate_terms.append(str(watch["reference"]))
        if field_type == "currency":
            currency = watch.get("original_currency") or watch.get("currency")
            if currency:
                candidate_terms.append(str(currency))
        if field_type == "price":
            price = watch.get("original_price") or watch.get("price")
            if price is not None:
                candidate_terms.append(str(price))
        if message_text.strip():
            candidate_terms.append(message_text)

        for term in candidate_terms:
            rule = find_matching_learning_rule(
                active_rules,
                field_type=field_type,
                term=term,
                dealer_id=dealer_id,
                group_id=group_id,
            )
            if not rule:
                continue
            normalized_value = str(rule.get("normalized_value") or "").strip()
            if not normalized_value:
                continue
            if field_type == "brand":
                watch["brand"] = normalized_value
            elif field_type == "reference":
                watch["reference"] = normalized_value.upper()
            elif field_type == "currency":
                watch["original_currency"] = normalized_value.upper()
                watch["currency"] = normalized_value.upper()
            elif field_type == "price":
                try:
                    parsed_price = int(float(normalized_value.replace(",", "")))
                except ValueError:
                    continue
                watch["original_price"] = parsed_price
                watch["price"] = parsed_price
            watch[f"{field_type}_learned_rule_id"] = rule.get("id")
            break

    return watch


def flag_condition_training(
    watch: Record,
    *,
    message_text: str = "",
    dealer_id: str | None = None,
    group_id: str | None = None,
    rules: list[Record] | None = None,
) -> bool:
    """Flag watches with unknown condition words that still need training."""
    if watch.get("condition_learning_applied"):
        return False

    training_term = detect_condition_training_term(message_text, watch)
    if not training_term:
        watch.pop("condition_needs_training", None)
        watch.pop("condition_training_term", None)
        return False

    from database import list_active_parser_learning_rules

    active_rules = rules if rules is not None else list_active_parser_learning_rules()
    if find_matching_learning_rule(
        active_rules,
        field_type="condition",
        term=training_term,
        dealer_id=dealer_id,
        group_id=group_id,
    ):
        apply_learning_rules_to_watch(
            watch,
            message_text=message_text,
            dealer_id=dealer_id,
            group_id=group_id,
            rules=active_rules,
        )
        return False

    watch["condition_needs_training"] = True
    watch["condition_training_term"] = training_term
    watch["condition"] = None
    watch.pop("raw_condition", None)
    watch.pop("condition_source", None)
    watch.pop("condition_confidence", None)
    watch.pop("condition_explicit", None)
    return True


def prepare_watch_for_ingest(
    watch: Record,
    *,
    message_text: str = "",
    dealer_id: str | None = None,
    group_id: str | None = None,
    rules: list[Record] | None = None,
) -> Record:
    """Apply learned rules and condition-training flags before offer safety checks."""
    apply_learning_rules_to_watch(
        watch,
        message_text=message_text,
        dealer_id=dealer_id,
        group_id=group_id,
        rules=rules,
    )
    flag_condition_training(
        watch,
        message_text=message_text,
        dealer_id=dealer_id,
        group_id=group_id,
        rules=rules,
    )
    return watch


def teach_condition_rule(
    *,
    term: str,
    normalized_value: str,
    scope: str = "global",
    dealer_id: str | None = None,
    group_id: str | None = None,
    source_import_log_id: str | None = None,
    created_by_user_id: str | None = None,
) -> Record:
    """Persist a condition learning rule."""
    from database import create_parser_learning_rule, invalidate_parser_learning_rules_cache

    rule = create_parser_learning_rule(
        field_type="condition",
        term=term,
        normalized_value=normalized_value,
        scope=scope,
        dealer_id=dealer_id,
        group_id=group_id,
        source_import_log_id=source_import_log_id,
        created_by_user_id=created_by_user_id,
    )
    invalidate_parser_learning_rules_cache()
    return rule


def apply_condition_once(
    watch: Record,
    *,
    term: str,
    normalized_value: str,
) -> Record:
    """Apply a one-off condition correction without saving a rule."""
    normalized, _ = normalize_wear_condition(normalized_value)
    if normalized not in {NEW_CONDITION, PRE_OWNED_CONDITION}:
        raise ValueError("Condition must be New or Pre-Owned")
    watch["condition"] = normalized
    watch["raw_condition"] = term.strip().title()
    watch["condition_source"] = CONDITION_SOURCE_EXPLICIT
    watch["condition_confidence"] = CONDITION_CONFIDENCE_HIGH
    watch["condition_explicit"] = True
    watch.pop("condition_needs_training", None)
    watch.pop("condition_training_term", None)
    return watch
