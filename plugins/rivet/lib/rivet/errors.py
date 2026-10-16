"""Engine errors.

Every domain, filesystem, or database failure surfaces as a RivetError. The CLI
prints it as one JSON object on stderr with a stable machine-readable ``code``
next to the human-readable ``error`` text, and exits non-zero.
"""


class RivetError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_json(self):
        payload = {"error": self.message, "code": self.code}
        payload.update(self.details)
        return payload
