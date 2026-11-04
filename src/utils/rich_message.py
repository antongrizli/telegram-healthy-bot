"""Telegram Rich Messages builder and HTML fallback utility.

Implements constraints and models based on Telegram Bot API 10.1–10.3:
- Max characters per Rich Message: 32,768 UTF-8 characters.
- Max blocks per message: 500 blocks.
- Max nesting depth: 16 levels.
- Max table columns: 20 (mobile UX limit: max 4 columns).
- Tables set is_compact=True, is_bordered=True, is_striped=True.
- Transparent fallback to sendMessage / editMessageText (HTML) on rejection.
"""

from __future__ import annotations

import html
import logging
from typing import Any, Dict, List, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods.base import TelegramMethod

logger = logging.getLogger(__name__)

MAX_CHARACTERS = 32768
MAX_BLOCKS = 500
MAX_NESTING_DEPTH = 16
MAX_TABLE_COLUMNS = 20
RECOMMENDED_MOBILE_COLUMNS = 4


class SendRichMessage(TelegramMethod[Any]):
    __returning__ = Any
    __api_method__ = "sendRichMessage"

    chat_id: int
    rich_message: Dict[str, Any]
    reply_markup: Optional[Any] = None


class EditRichMessage(TelegramMethod[Any]):
    __returning__ = Any
    __api_method__ = "editMessageText"

    chat_id: int
    message_id: int
    rich_message: Dict[str, Any]
    reply_markup: Optional[Any] = None


class RichMessageBuilder:
    """Builder for Telegram Rich Messages with limits validation and HTML fallback."""

    def __init__(self) -> None:
        self.blocks: List[Dict[str, Any]] = []

    def add_section_heading(self, text: str) -> RichMessageBuilder:
        self.blocks.append({
            "type": "section_heading",
            "text": {"type": "plain", "text": text}
        })
        return self

    def add_paragraph(self, text: str, is_bold: bool = False, is_html: bool = False) -> RichMessageBuilder:
        self.blocks.append({
            "type": "paragraph",
            "is_html": is_html,
            "text": {"type": "bold" if is_bold else "plain", "text": text}
        })
        return self

    def add_table(
        self,
        cells: List[List[Dict[str, Any]]],
        is_bordered: bool = True,
        is_striped: bool = True,
        is_compact: bool = True,
    ) -> RichMessageBuilder:
        """Adds an InputRichBlockTable.
        
        cells: 2D array of dicts: {'text': str, 'is_header': bool, 'align': 'left'|'right'|'center'}
        """
        for r_idx, row in enumerate(cells):
            if len(row) > MAX_TABLE_COLUMNS:
                raise ValueError(
                    f"Row {r_idx} exceeds max table columns ({len(row)} > {MAX_TABLE_COLUMNS})"
                )

        self.blocks.append({
            "type": "table",
            "is_bordered": is_bordered,
            "is_striped": is_striped,
            "is_compact": is_compact,
            "cells": cells,
        })
        return self

    def add_details(
        self,
        title: str,
        blocks: List[Dict[str, Any]],
        is_open: bool = False,
    ) -> RichMessageBuilder:
        """Adds an InputRichBlockDetails collapsible block."""
        self.blocks.append({
            "type": "details",
            "title": {"type": "plain", "text": title},
            "is_open": is_open,
            "blocks": blocks,
        })
        return self

    def add_block(self, block: Dict[str, Any]) -> RichMessageBuilder:
        self.blocks.append(block)
        return self

    # --- Validation ---

    @staticmethod
    def _count_chars(block: Any) -> int:
        count = 0
        if isinstance(block, dict):
            for k, v in block.items():
                if k == "text" and isinstance(v, str):
                    count += len(v.encode("utf-8"))
                else:
                    count += RichMessageBuilder._count_chars(v)
        elif isinstance(block, list):
            for item in block:
                count += RichMessageBuilder._count_chars(item)
        return count

    @staticmethod
    def _count_blocks(blocks: List[Dict[str, Any]]) -> int:
        total = len(blocks)
        for b in blocks:
            if isinstance(b, dict) and "blocks" in b and isinstance(b["blocks"], list):
                total += RichMessageBuilder._count_blocks(b["blocks"])
        return total

    @staticmethod
    def _max_depth(block: Any, current_depth: int = 1) -> int:
        if not isinstance(block, dict):
            return current_depth
        max_d = current_depth
        if "blocks" in block and isinstance(block["blocks"], list):
            for child in block["blocks"]:
                max_d = max(max_d, RichMessageBuilder._max_depth(child, current_depth + 1))
        return max_d

    def validate(self) -> None:
        total_blocks = self._count_blocks(self.blocks)
        if total_blocks > MAX_BLOCKS:
            raise ValueError(f"Max blocks exceeded: {total_blocks} > {MAX_BLOCKS}")

        total_chars = self._count_chars(self.blocks)
        if total_chars > MAX_CHARACTERS:
            raise ValueError(f"Max characters exceeded: {total_chars} > {MAX_CHARACTERS}")

        for b in self.blocks:
            depth = self._max_depth(b, 1)
            if depth > MAX_NESTING_DEPTH:
                raise ValueError(f"Max nesting depth exceeded: {depth} > {MAX_NESTING_DEPTH}")

    def build_payload(self, chat_id: Optional[int] = None) -> Dict[str, Any]:
        self.validate()
        clean_blocks = []
        for b in self.blocks:
            cb = dict(b)
            cb.pop("is_html", None)
            clean_blocks.append(cb)
        payload: Dict[str, Any] = {
            "rich_message": {
                "blocks": clean_blocks
            }
        }
        if chat_id is not None:
            payload["chat_id"] = chat_id
        return payload

    # --- HTML Fallback Renderer ---

    @classmethod
    def _render_blocks_html(cls, blocks: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for b in blocks:
            b_type = b.get("type")
            if b_type == "section_heading":
                raw_text = b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict) else str(b.get("text", ""))
                parts.append(f"<b>{html.escape(raw_text)}</b>\n")
            elif b_type == "paragraph":
                t_obj = b.get("text", {})
                is_html = b.get("is_html", False)
                if isinstance(t_obj, dict):
                    raw_text = t_obj.get("text", "")
                    if t_obj.get("type") == "bold":
                        parts.append(f"<b>{raw_text if is_html else html.escape(raw_text)}</b>\n")
                    else:
                        parts.append(f"{raw_text if is_html else html.escape(raw_text)}\n")
                else:
                    parts.append(f"{t_obj if is_html else html.escape(str(t_obj))}\n")
            elif b_type == "table":
                cells = b.get("cells", [])
                if cells:
                    table_str = cls._render_table_html(cells)
                    parts.append(f"{table_str}\n")
            elif b_type == "details":
                title_obj = b.get("title", {})
                title_text = title_obj.get("text", "") if isinstance(title_obj, dict) else str(title_obj)
                inner_blocks = b.get("blocks", [])
                inner_html = cls._render_blocks_html(inner_blocks).strip()
                parts.append(f"<blockquote expandable><b>{html.escape(title_text)}</b>\n\n{inner_html}</blockquote>\n")
            else:
                # Generic fallback for unknown block
                txt = b.get("text")
                if txt:
                    parts.append(f"{html.escape(str(txt))}\n")
        return "\n".join(parts).strip()

    @classmethod
    def _render_table_html(cls, cells: List[List[Dict[str, Any]]]) -> str:
        """Renders table cells as mobile-friendly HTML.
        
        For multi-column tables (>2 cols or >32 chars width), renders structured mobile cards
        so lines never break or wrap awkwardly on mobile screens.
        """
        if not cells:
            return ""

        headers = [str(c.get("text", "")) for c in cells[0]] if any(c.get("is_header") for c in cells[0]) else []
        data_rows = cells[1:] if headers else cells

        # Calculate max width if rendered as monospace table
        col_widths: List[int] = []
        for row in cells:
            for c_idx, cell in enumerate(row):
                text_len = len(str(cell.get("text", "")))
                if c_idx >= len(col_widths):
                    col_widths.append(text_len)
                else:
                    col_widths[c_idx] = max(col_widths[c_idx], text_len)

        total_width = sum(col_widths) + (3 * (len(col_widths) - 1)) if col_widths else 0

        # If 2 columns or less and fits mobile width without wrapping (<= 32 chars):
        if len(col_widths) <= 2 and total_width <= 32:
            lines: List[str] = []
            is_first = True
            for row in cells:
                row_parts = []
                for c_idx, cell in enumerate(row):
                    w = col_widths[c_idx]
                    align = cell.get("align", "left")
                    t = str(cell.get("text", ""))
                    aligned_t = t.rjust(w) if align == "right" else (t.center(w) if align == "center" else t.ljust(w))
                    row_parts.append(aligned_t)
                lines.append(" | ".join(row_parts))
                if is_first and headers:
                    lines.append("-+-".join("-" * w for w in col_widths))
                    is_first = False
            table_body = html.escape("\n".join(lines))
            return f"<pre>{table_body}</pre>"

        # Mobile-optimized card format for 3+ columns or wide tables
        cards: List[str] = []
        target_label = headers[3] if len(headers) >= 4 else "Next Target"

        for row in data_rows:
            r_texts = [str(c.get("text", "")) for c in row]
            if len(r_texts) >= 4:
                name, load, feel, target = r_texts[0], r_texts[1], r_texts[2], r_texts[3]
                card = (
                    f"• <b>{html.escape(name)}</b>\n"
                    f"  ├ {html.escape(load)} • {html.escape(feel)}\n"
                    f"  └ 🎯 <i>{html.escape(target_label)}:</i> <b>{html.escape(target)}</b>"
                )
                cards.append(card)
            elif len(r_texts) == 3:
                name, load, feel = r_texts[0], r_texts[1], r_texts[2]
                cards.append(f"• <b>{html.escape(name)}</b>: {html.escape(load)} ({html.escape(feel)})")
            elif len(r_texts) == 2:
                cards.append(f"• <b>{html.escape(r_texts[0])}</b>: {html.escape(r_texts[1])}")
            else:
                cards.append(" • ".join(html.escape(t) for t in r_texts))

        return "\n\n".join(cards)

    def to_html(self) -> str:
        """Converts rich message blocks to Telegram-compatible HTML."""
        return self._render_blocks_html(self.blocks)


async def send_rich_or_fallback(
    bot: Bot,
    chat_id: int,
    builder: RichMessageBuilder,
    reply_markup: Optional[Any] = None,
) -> Any:
    """Attempts sendRichMessage, falling back to sendMessage with HTML if rejected."""
    payload = builder.build_payload(chat_id=chat_id)
    if reply_markup is not None:
        payload["reply_markup"] = (
            reply_markup.model_dump(exclude_none=True)
            if hasattr(reply_markup, "model_dump")
            else reply_markup
        )
    try:
        method = SendRichMessage(**payload)
        return await bot(method)
    except Exception as e:
        logger.info("sendRichMessage rejected or unavailable (%s), falling back to HTML sendMessage", e)
        html_text = builder.to_html()
        return await bot.send_message(
            chat_id=chat_id,
            text=html_text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )


async def edit_rich_or_fallback(
    bot: Bot,
    chat_id: int,
    message_id: int,
    builder: RichMessageBuilder,
    reply_markup: Optional[Any] = None,
) -> Any:
    """Attempts editMessageText with rich_message, falling back to HTML editMessageText if rejected."""
    payload = builder.build_payload(chat_id=chat_id)
    payload["message_id"] = message_id
    if reply_markup is not None:
        payload["reply_markup"] = (
            reply_markup.model_dump(exclude_none=True)
            if hasattr(reply_markup, "model_dump")
            else reply_markup
        )
    try:
        method = EditRichMessage(**payload)
        return await bot(method)
    except Exception as e:
        logger.info("editMessageText (rich) rejected or unavailable (%s), falling back to HTML editMessageText", e)
        html_text = builder.to_html()
        return await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=html_text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
