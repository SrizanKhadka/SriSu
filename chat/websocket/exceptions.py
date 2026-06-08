class ChatServiceError(Exception):
    """Base exception for chat service errors."""


class ChatRoomNotFoundError(ChatServiceError):
    """Raised when a chat room does not exist or is inaccessible."""


class MessageNotFoundError(ChatServiceError):
    """Raised when a message does not exist or is inaccessible."""


class PermissionDeniedError(ChatServiceError):
    """Raised when a user is not allowed to perform an action."""


class InvalidMessagePayloadError(ChatServiceError):
    """Raised when a message payload is invalid."""


class InvalidReplyTargetError(ChatServiceError):
    """Raised when reply target is missing or belongs to another room."""


class InvalidDeleteOperationError(ChatServiceError):
    """Raised when delete action is not allowed."""