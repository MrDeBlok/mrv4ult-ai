"""Natural-language AI Trading Advisor summaries from opportunity scoring output."""

from __future__ import annotations

from typing import Any

Record = dict[str, Any]


def _first_sentence_for_score(score: int | None) -> str:
    if score is None:
        return "There is not enough data to assess this opportunity yet."
    if score >= 90:
        return "This looks like a strong opportunity."
    if score >= 75:
        return "This looks like a promising opportunity."
    if score >= 50:
        return "This could work, but the match is not perfect."
    if score >= 25:
        return "This opportunity looks weak on the current data."
    return "This opportunity looks critical and likely not worth pursuing right now."


def _match_sentence(analysis: Record) -> str | None:
    positive = analysis.get("positive_reasons") or []
    for reason in positive:
        text = str(reason).lstrip("✔ ").strip()
        if text.lower().startswith("exact reference"):
            return "The reference matches exactly."
        if "alias" in text.lower() or "nickname" in text.lower():
            return "The model match is based on alias or nickname identification."
    return None


def _recency_sentence(analysis: Record) -> str | None:
    positive = analysis.get("positive_reasons") or []
    for reason in positive:
        text = str(reason)
        if "Offer posted" in text:
            age = text.split("Offer posted", 1)[1].strip().rstrip(".")
            return f"The offer is very recent, posted {age}."
    urgency = analysis.get("urgency")
    if urgency == "OLD":
        return "The offer is quite old, so timing may be less attractive."
    if urgency == "HOT":
        return "The offer is very recent and timing looks urgent."
    return None


def _dealer_sentence(analysis: Record) -> str | None:
    best_match = analysis.get("best_match") or {}
    rating = best_match.get("dealer_rating")
    if rating == "Trusted Dealer":
        return "The dealer looks established and trusted based on recent activity."
    if rating == "Established Dealer":
        return "The dealer has a solid track record in recent imports."
    if rating == "New Dealer":
        return "The dealer is relatively new in the activity data."
    return None


def _gap_sentence(analysis: Record) -> str | None:
    warnings = analysis.get("warning_reasons") or []
    if not warnings:
        return None
    labels = [str(item).lstrip("⚠ ").strip().lower() for item in warnings]
    if any("budget" in label for label in labels):
        return "The only missing information is the buyer's budget."
    if len(labels) == 1:
        return f"The main gap is {labels[0]}."
    return f"There are a few data gaps, including {labels[0]}."


def _recommendation_sentence(analysis: Record) -> str:
    recommendation = analysis.get("recommendation") or analysis.get("recommended_action")
    if not recommendation:
        return "Keep monitoring for a clearer match."
    mapping = {
        "BUY NOW": "I recommend acting immediately before this offer disappears.",
        "CALL TODAY": "I recommend contacting the dealer today.",
        "GOOD OPPORTUNITY": "I recommend reviewing this match and reaching out soon.",
        "WATCH": "I recommend keeping this on watch for now.",
        "IGNORE": "I recommend ignoring this match for now.",
    }
    return mapping.get(str(recommendation), f"I recommend {str(recommendation).lower()}.")


def generate_trading_advisor_summary(analysis: Record) -> str:
    """Build a short trader-style summary from scored opportunity analysis data."""
    if not analysis.get("has_opportunities"):
        return "No matching offers yet. Keep monitoring market requests for fresh stock."

    sentences = [_first_sentence_for_score(analysis.get("opportunity_score"))]
    for builder in (_match_sentence, _recency_sentence, _dealer_sentence, _gap_sentence):
        sentence = builder(analysis)
        if sentence:
            sentences.append(sentence)
    sentences.append(_recommendation_sentence(analysis))
    return " ".join(sentences)
