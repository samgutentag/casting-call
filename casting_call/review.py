def collect_unknowns(spans):
    """Assign a stable review_id to each DISTINCT unresolved raw label.

    Mutates spans in place (sets review_id) and returns the review list:
    [{'review_id': 'unknown_01', 'raw_ocr': '...'}].
    """
    order = []
    seen = {}
    for span in spans:
        if not span.unresolved:
            continue
        raw = span.raw_ocr
        if raw not in seen:
            rid = f'unknown_{len(order) + 1:02d}'
            seen[raw] = rid
            order.append({'review_id': rid, 'raw_ocr': raw})
        span.review_id = seen[raw]
    return order


def reconcile(spans, mapping, bail=False):
    """Apply a review_id -> name mapping.

    - mapped review_ids become that name (resolved).
    - if bail: every still-unresolved span collapses to Caller (name stays None,
      unresolved cleared).
    - if not bail: unmapped unknowns remain pending.
    """
    for span in spans:
        if span.review_id and span.review_id in mapping:
            span.name = mapping[span.review_id]
            span.unresolved = False
        elif span.unresolved and bail:
            span.unresolved = False
    return spans
