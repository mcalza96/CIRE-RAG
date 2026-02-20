import re
from typing import Any


class RecursiveTextSplitter:
    def split(self, text: str, max_chars: int) -> list[dict[str, Any]]:
        paragraphs = text.split("\n\n")
        chunks: list[dict[str, Any]] = []
        current_chunk = ""
        chunk_start = 0

        for para in paragraphs:
            if len(para) > max_chars:
                sub_paras = [para[i : i + max_chars] for i in range(0, len(para), max_chars)]
                for sub in sub_paras:
                    if current_chunk and len(current_chunk) + len(sub) + 2 > max_chars:
                        chunks.append(
                            {
                                "content": current_chunk.strip(),
                                "heading_path": "",
                                "char_start": chunk_start,
                                "char_end": chunk_start + len(current_chunk),
                            }
                        )
                        chunk_start += len(current_chunk) + 2
                        current_chunk = sub
                    else:
                        current_chunk += ("\n\n" if current_chunk else "") + sub
                continue

            if current_chunk and len(current_chunk) + len(para) + 2 > max_chars:
                chunks.append(
                    {
                        "content": current_chunk.strip(),
                        "heading_path": "",
                        "char_start": chunk_start,
                        "char_end": chunk_start + len(current_chunk),
                    }
                )
                chunk_start += len(current_chunk) + 2
                current_chunk = para
            else:
                current_chunk += ("\n\n" if current_chunk else "") + para

        if current_chunk.strip():
            chunks.append(
                {
                    "content": current_chunk.strip(),
                    "heading_path": "",
                    "char_start": chunk_start,
                    "char_end": chunk_start + len(current_chunk),
                }
            )

        return chunks


class SemanticHeadingSplitter:
    _heading_pattern = re.compile(r"^(?:#{2,4}|(?:\d+\.)+)\s+(.+)", re.MULTILINE)

    def __init__(self, fallback_splitter: RecursiveTextSplitter | None = None):
        self.fallback_splitter = fallback_splitter or RecursiveTextSplitter()

    def split(self, markdown_text: str, max_chars: int = 4000) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        matches = list(self._heading_pattern.finditer(markdown_text))
        if not matches:
            return self.fallback_splitter.split(markdown_text, max_chars)

        if matches[0].start() > 0:
            preamble = markdown_text[: matches[0].start()].strip()
            if preamble:
                sections.append(
                    {
                        "content": preamble,
                        "heading_path": "[Preámbulo]",
                        "char_start": 0,
                        "char_end": matches[0].start(),
                    }
                )

        heading_stack: list[str] = []
        for i, match in enumerate(matches):
            level = 2
            if match.group(0).startswith("#"):
                hash_match = re.match(r"^#+", match.group(0))
                if hash_match:
                    level = len(hash_match.group(0))

            title = match.group(1).strip()
            heading_stack = heading_stack[: level - 2]
            heading_stack.append(title)
            heading_path = " > ".join(heading_stack)

            section_start = match.start()
            section_end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
            section_content = markdown_text[section_start:section_end].strip()
            if not section_content:
                continue

            if len(section_content) > max_chars:
                sections.extend(
                    self._split_long_section(
                        content=section_content,
                        heading_path=heading_path,
                        base_offset=section_start,
                        max_chars=max_chars,
                    )
                )
            else:
                sections.append(
                    {
                        "content": section_content,
                        "heading_path": heading_path,
                        "char_start": section_start,
                        "char_end": section_end,
                    }
                )

        return sections

    @staticmethod
    def _split_long_section(
        content: str,
        heading_path: str,
        base_offset: int,
        max_chars: int,
    ) -> list[dict[str, Any]]:
        paragraphs = content.split("\n\n")
        chunks: list[dict[str, Any]] = []
        current_chunk = ""
        chunk_start = base_offset
        part_num = 0

        for para in paragraphs:
            if current_chunk and len(current_chunk) + len(para) + 2 > max_chars:
                part_num += 1
                chunks.append(
                    {
                        "content": current_chunk.strip(),
                        "heading_path": f"{heading_path} (parte {part_num})",
                        "char_start": chunk_start,
                        "char_end": chunk_start + len(current_chunk),
                    }
                )
                chunk_start += len(current_chunk) + 2
                current_chunk = para
            else:
                current_chunk += ("\n\n" if current_chunk else "") + para

        if current_chunk.strip():
            part_num += 1
            chunks.append(
                {
                    "content": current_chunk.strip(),
                    "heading_path": f"{heading_path} (parte {part_num})"
                    if part_num > 1
                    else heading_path,
                    "char_start": chunk_start,
                    "char_end": chunk_start + len(current_chunk),
                }
            )

        return chunks
