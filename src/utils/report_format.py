"""Mobile-readable plain-text reports, including legacy Markdown snapshots."""
import re
from src.utils.escape import split_message


def report_plain_text(text: str) -> str:
    text = re.sub(r'\\([\\*_\[\]`])', r'\1', text or '')
    text = re.sub(r'^\s*```[^\n]*$', '', text, flags=re.MULTILINE)
    text = text.replace('```', '')
    text = re.sub(r'\[([^\]\n]+)\]\(([^)\n]+)\)', r'\1 (\2)', text)
    text = re.sub(r'\*\*([^\n]+?)\*\*', r'\1', text)
    text = re.sub(r'(?<!\w)[*_]([^\n]+?)[*_](?!\w)', r'\1', text)
    text = re.sub(r'`([^`\n]+)`', r'\1', text)
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^(\s*)[*+-]\s+', r'\1• ', text, flags=re.MULTILINE)
    lines = text.splitlines()
    result = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith('|') and line.strip().endswith('|'):
            rows = []
            while index < len(lines) and lines[index].strip().startswith('|') and lines[index].strip().endswith('|'):
                cells = [cell.strip() for cell in lines[index].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[:\-\s]+', cell or '-') for cell in cells):
                    rows.append(cells)
                index += 1
            if rows:
                headers = rows[0]
                for row in rows[1:]:
                    if len(row) == len(headers):
                        result.append(f'• {row[0]}: ' + ' · '.join(
                            f'{label}: {value}' for label, value in zip(headers[1:], row[1:])))
                    else:
                        result.append(' · '.join(row))
                if len(rows) == 1:
                    result.append(' · '.join(headers))
            continue
        result.append(line)
        index += 1
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(result)).strip()


def report_parts(text: str) -> list[str]:
    # 2000 Unicode code points fit Telegram's 4096 UTF-16-unit limit even with emoji.
    return [part[start:start + 2000]
            for part in split_message(report_plain_text(text), max_length=2000)
            for start in range(0, len(part), 2000)]
