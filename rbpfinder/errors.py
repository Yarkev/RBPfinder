"""The two error domains a user-facing run is allowed to end in.

Everything else is a bug and must keep failing loudly with its own traceback. The point
of naming exactly two domains is to stop each provider inventing a third: M15A found a
bare FileNotFoundError from subprocess, a KeyError from a csv reader, and a
BlastUnavailable from one provider -- three shapes for two situations.

    CapabilityUnavailable   an external tool, database or service this run needs is not
                            present. Not a defect; a fact about the installation.
    InputValidationError    the user's input cannot be read as claimed.

Exit codes are fixed here so that wrappers never have to grep stdout -- a lesson this
project already paid for once, when a downloader's success was judged from captured
output and a completed 176 MB file was destroyed.
"""

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_CAPABILITY = 3
EXIT_INTERNAL = 1


class RBPFinderError(Exception):
    exit_code = EXIT_INTERNAL


class CapabilityUnavailable(RBPFinderError):
    exit_code = EXIT_CAPABILITY

    def __init__(self, message, capability=None):
        super(CapabilityUnavailable, self).__init__(message)
        self.capability = capability


class InputValidationError(RBPFinderError):
    exit_code = EXIT_INPUT

    def __init__(self, message, path=None, line=None, column=None):
        super(InputValidationError, self).__init__(message)
        self.path, self.line, self.column = path, line, column
