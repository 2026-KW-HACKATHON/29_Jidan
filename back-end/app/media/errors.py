"""Why an upload was rejected, mapped to the contract's status/code (openapi uploadManualMedia).

`reason` is a fixed internal tag for tests and logs; it never contains file content.
"""


class MediaRejected(Exception):
    status_code = 422
    code = "MEDIA_INVALID"

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class MediaInvalid(MediaRejected):
    """Empty, truncated, corrupt or undecodable content of a supported type: 422 MEDIA_INVALID."""


class MediaUnsupported(MediaRejected):
    """Not one of the accepted formats for the purpose (also video, animation): 415."""

    status_code = 415
    code = "UNSUPPORTED_MEDIA_TYPE"


class MediaTooLarge(MediaRejected):
    """Over the byte, pixel or duration limit: 413 MEDIA_TOO_LARGE."""

    status_code = 413
    code = "MEDIA_TOO_LARGE"
