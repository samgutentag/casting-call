def coverage_report(entries, caller_label='Caller'):
    """Coverage over originally-Caller lines.

    Each entry carries 'original_label' (before relabel) and 'label' (after). A
    still-caller_label line is a lost attribution.
    """
    caller_entries = [e for e in entries if e.get('original_label') == caller_label]
    caller_lines = len(caller_entries)
    lost = [{'t': e['t'], 'text': e['text']} for e in caller_entries if e['label'] == caller_label]
    attributed_lines = caller_lines - len(lost)

    pct = round(attributed_lines / caller_lines * 100, 1) if caller_lines else 0.0
    return {
        'caller_lines': caller_lines,
        'attributed_lines': attributed_lines,
        'attributed_pct': pct,
        'lost_lines': lost,
    }
