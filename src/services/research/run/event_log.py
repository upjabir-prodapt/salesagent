from __future__ import annotations

from pathlib import Path

from google.adk.events.event import Event

from ....core.logging_config import logger

__all__ = ["log_event"]


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max length, appending '...' if truncated."""
    return text[:max_len] + "..." if len(text) > max_len else text


def _emit_log(message: str, log_file: Path | None) -> None:
    """Emit one log line via logger and optionally append to a file."""
    logger.info(message)

    if log_file is None:
        return

    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(message)
        if not message.endswith("\n"):
            fh.write("\n")
        fh.flush()


def log_event(
    event: Event,
    *,
    verbose: bool = False,
    log_file: str | Path | None = None,
) -> None:
    """Log agent events via logger.info; optionally mirror to a file."""
    if not event.content or not event.content.parts:
        return

    file_path = Path(log_file) if log_file is not None else None

    text_buffer: list[str] = []

    def flush_text() -> None:
        if text_buffer:
            combined_text = "".join(text_buffer)
            _emit_log(f"{event.author} > {combined_text}", file_path)
            text_buffer.clear()

    for part in event.content.parts:
        if part.text:
            text_buffer.append(part.text)
        else:
            flush_text()

            if verbose:
                if part.function_call:
                    _emit_log(
                        f"{event.author} > [Tool Call: {part.function_call.name} "
                        f"with args {part.function_call.args}]",
                        file_path,
                    )
                elif part.function_response:
                    _emit_log(
                        f"{event.author} > [Tool result:"
                        f" {_truncate(str(part.function_response.response), 100)}]",
                        file_path,
                    )
                elif part.executable_code:
                    lang = part.executable_code.language or "code"
                    _emit_log(f"{event.author} > [Executing {lang} code...]", file_path)
                elif part.code_execution_result:
                    output = part.code_execution_result.output or "result"
                    _emit_log(
                        f"{event.author} > [Code output: {_truncate(str(output), 100)}]",
                        file_path,
                    )
                elif part.inline_data:
                    mime_type = part.inline_data.mime_type or "data"
                    _emit_log(f"{event.author} > [Inline data: {mime_type}]", file_path)
                elif part.file_data:
                    uri = part.file_data.file_uri or "file"
                    _emit_log(f"{event.author} > [File: {uri}]", file_path)

    flush_text()
