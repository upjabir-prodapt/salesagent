from google.adk.events.event import Event
from loguru import logger


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max length, appending '...' if truncated.

    Args:
        text: The text to truncate.
        max_len: Maximum length before truncation.

    Returns:
        The truncated text with '...' appended if it exceeds max_len.
    """
    return text[:max_len] + "..." if len(text) > max_len else text


def log_event(event: Event, *, verbose: bool = False) -> None:
    """Print an event to stdout in a user-friendly format.

    Args:
        event: The event to print.
        verbose: If True, shows detailed tool calls and responses. If False,
            shows only text responses for cleaner output.
    """
    if not event.content or not event.content.parts:
        return

    # Collect consecutive text parts to avoid repeating author prefix
    text_buffer: list[str] = []

    def flush_text() -> None:
        """Flush accumulated text parts as a single output."""
        if text_buffer:
            combined_text = "".join(text_buffer)
            logger.info(f"{event.author} > {combined_text}")
            text_buffer.clear()

    for part in event.content.parts:
        # Text parts are always shown regardless of verbose setting
        # because they contain the actual agent responses users expect
        if part.text:
            text_buffer.append(part.text)
        else:
            # Flush any accumulated text before handling non-text parts
            flush_text()

            # Non-text parts (tool calls, code, etc.) are hidden by default
            # to reduce clutter and show only what matters: the final results
            if verbose:
                # Tool invocations show the behind-the-scenes processing
                if part.function_call:
                    logger.info(
                        f"{event.author} > [Calling tool:"
                        f" {part.function_call.name}("
                        f"{_truncate(str(part.function_call.args), 50)})]"
                    )
                # Handle function response parts (tool results)
                elif part.function_response:
                    logger.info(
                        f"{event.author} > [Tool result:"
                        f" {_truncate(str(part.function_response.response), 100)}]"
                    )
                # Handle executable code parts
                elif part.executable_code:
                    lang = part.executable_code.language or "code"
                    logger.info(f"{event.author} > [Executing {lang} code...]")
                # Handle code execution result parts
                elif part.code_execution_result:
                    output = part.code_execution_result.output or "result"
                    logger.info(
                        f"{event.author} > [Code output: {_truncate(str(output), 100)}]"
                    )
                # Handle inline data (images, files)
                elif part.inline_data:
                    mime_type = part.inline_data.mime_type or "data"
                    logger.info(f"{event.author} > [Inline data: {mime_type}]")
                # Handle file data
                elif part.file_data:
                    uri = part.file_data.file_uri or "file"
                    logger.info(f"{event.author} > [File: {uri}]")

    # Flush any remaining text at the end
    flush_text()
