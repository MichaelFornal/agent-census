"""A parser raises ParseError for a malformed file; the partial result is kept, never dropped (PRD §4 S3)."""


class ParseError(Exception):
    def __init__(self, error_class: str, partial: dict | None = None) -> None:
        super().__init__(error_class)
        self.error_class = error_class
        self.partial = partial or {}
