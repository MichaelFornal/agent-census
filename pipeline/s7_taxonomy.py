"""S7 taxonomy (PRD §4 S7). Completed in Task 16."""
LABEL_SCHEMA = {"type": "object", "properties": {"label": {"type": "string"}, "non_coding": {"type": "boolean"}},
                "required": ["label", "non_coding"]}
