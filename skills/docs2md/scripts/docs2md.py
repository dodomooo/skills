#!/usr/bin/env python3
"""
docs2md Stage 1: Pandoc conversion + regex cleanup for enterprise .doc/.docx files.

Usage:
    python docs2md.py <input.doc|input.docx> [-o <output-dir>] [--config <config.yaml>]
    python docs2md.py <input-dir> [-o <output-dir>] [--config <config.yaml>]

Dependencies: Pandoc (in PATH), pyyaml. .doc pre-conversion also needs
doc_to_docx_wps.py in this scripts directory.
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml


def configure_stdio() -> None:
    """Use UTF-8 for script logs when the runtime supports reconfiguration."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 0. Config loading
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load configuration file, return config dict."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_path(raw_path: str) -> Path:
    """Resolve a CLI path while preserving non-ASCII characters."""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def path_key(path: Path) -> str:
    """Return a normalized key for comparing paths on case-insensitive systems."""
    return os.path.normcase(str(path.resolve())).casefold()


def discover_input_files(input_path: str) -> list[Path]:
    """
    Discover input .doc/.docx files.

    If both `name.doc` and `name.docx` exist in the same directory, prefer the
    native `.docx` file to avoid generating duplicate markdown outputs.
    Directories are scanned recursively and Office temp files are ignored.
    """
    allowed_suffixes = {".doc", ".docx"}
    root = resolve_path(input_path)

    if root.is_dir():
        entries = sorted(
            (
                path.resolve()
                for path in root.rglob("*")
                if path.is_file()
                and not path.name.startswith("~$")
                and path.suffix.lower() in allowed_suffixes
            ),
            key=lambda path: str(path.relative_to(root)).casefold(),
        )
    elif root.is_file():
        if root.name.startswith("~$") or root.suffix.lower() not in allowed_suffixes:
            return []
        entries = [root]
    else:
        return []

    docx_targets = {
        path_key(path.with_suffix(".docx"))
        for path in entries
        if path.suffix.lower() == ".docx"
    }

    files = []
    for path in entries:
        if path.suffix.lower() == ".doc":
            target_docx = path_key(path.with_suffix(".docx"))
            if target_docx in docx_targets:
                print(
                    f"Skipping .doc because matching .docx already exists: {path.name}",
                    file=sys.stderr,
                )
                continue
        files.append(path.resolve())
    return files


def convert_doc_via_external_script(doc_path: Path) -> tuple[Path | None, str | None]:
    """Call sibling script `doc_to_docx_wps.py` to convert `.doc` into `.docx`."""
    script_path = Path(__file__).resolve().parent / "doc_to_docx_wps.py"
    output_path = doc_path.with_suffix(".docx")

    if not script_path.exists():
        return None, f"Pre-conversion script not found: {script_path}"

    cmd = [sys.executable, str(script_path), str(doc_path)]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return None, f"Failed to run pre-conversion script: {exc}"

    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or "").strip()
        if not error:
            error = "Unknown pre-conversion error"
        return None, error

    if not output_path.exists():
        return None, f"Pre-conversion completed but output .docx not found: {output_path}"

    return output_path, None


# ---------------------------------------------------------------------------
# 1. Pandoc conversion
# ---------------------------------------------------------------------------

def run_pandoc(docx_path: Path, output_path: Path) -> bool:
    """Run Pandoc to convert .docx to markdown. Returns True on success."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    table_filter = Path(__file__).resolve().parent / "gfm-table-normalize.lua"
    if not table_filter.exists():
        print(f"ERROR: Table normalization filter not found: {table_filter}", file=sys.stderr)
        return False

    cmd = [
        "pandoc", str(docx_path),
        "-t", "markdown",
        "--wrap=none",
        "--track-changes=accept",
        "--lua-filter", str(table_filter),
        "-o", str(output_path),
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return True
    except FileNotFoundError:
        print("ERROR: Pandoc not found. Please install Pandoc and ensure it is in PATH.", file=sys.stderr)
        return False
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Pandoc failed: {e.stderr}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# 2. OOXML fallback (plain text extraction)
# ---------------------------------------------------------------------------

def ooxml_fallback(docx_path: Path) -> str | None:
    """Extract plain text from .docx via OOXML as a last resort."""
    try:
        with zipfile.ZipFile(docx_path) as z:
            xml_content = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        print(f"ERROR: OOXML fallback failed: {e}", file=sys.stderr)
        return None

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        # Regex fallback for broken XML
        texts = re.findall(r"<w:t[^>]*>([^<]+)</w:t>", xml_content.decode("utf-8", errors="replace"))
        return "\n".join(html.unescape(t) for t in texts) if texts else None

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for p in root.iter(f"{{{ns['w']}}}p"):
        texts = [t.text for t in p.iter(f"{{{ns['w']}}}t") if t.text]
        if texts:
            paragraphs.append("".join(texts))

    text = "\n\n".join(paragraphs)
    text = html.unescape(text)
    # Remove control characters
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    return text


# ---------------------------------------------------------------------------
# 3. Embedded attachment scan
# ---------------------------------------------------------------------------

_EMBEDDED_REL_TYPES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject": "ole_object",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package": "package",
}
_ATTACHMENT_EXTENSIONS = {
    ".bin", ".doc", ".docx", ".docm", ".xls", ".xlsx", ".xlsm",
    ".ppt", ".pptx", ".pptm", ".pdf", ".zip", ".7z", ".rar",
}
_CONTENT_TYPE_EXTENSIONS = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-excel.sheet.macroEnabled.12": ".xlsm",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/vnd.openxmlformats-officedocument.oleObject": ".bin",
}


def _read_zip_text(z: zipfile.ZipFile, name: str) -> str | None:
    """Read a UTF-8-ish text member from a docx zip package."""
    try:
        return z.read(name).decode("utf-8", errors="replace")
    except KeyError:
        return None


def _load_content_types(z: zipfile.ZipFile) -> dict[str, str]:
    """Return {part_name: content_type} entries from [Content_Types].xml."""
    xml_content = _read_zip_text(z, "[Content_Types].xml")
    if not xml_content:
        return {}

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return {}

    content_types = {}
    for override in root.findall("{http://schemas.openxmlformats.org/package/2006/content-types}Override"):
        part_name = override.attrib.get("PartName", "").lstrip("/")
        content_type = override.attrib.get("ContentType", "")
        if part_name and content_type:
            content_types[part_name] = content_type
    return content_types


def _scan_relationship_targets(z: zipfile.ZipFile) -> dict[str, dict]:
    """Return relationship metadata keyed by normalized target path."""
    relationships = {}
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"

    for name in z.namelist():
        if not name.endswith(".rels"):
            continue
        xml_content = _read_zip_text(z, name)
        if not xml_content:
            continue
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            continue

        base_dir = Path(name).parent.parent.as_posix()
        for rel in root.findall(f"{rel_ns}Relationship"):
            rel_type = rel.attrib.get("Type", "")
            target = rel.attrib.get("Target", "")
            if rel_type not in _EMBEDDED_REL_TYPES or not target:
                continue
            if target.startswith("/"):
                target_path = target.lstrip("/")
            else:
                target_path = (Path(base_dir) / target).as_posix()
            relationships[target_path] = {
                "relationship_id": rel.attrib.get("Id", ""),
                "relationship_type": _EMBEDDED_REL_TYPES[rel_type],
                "relationship_source": name,
            }
    return relationships


def _infer_attachment_extension(path: str, content_type: str) -> str:
    """Infer the original extension when OOXML only exposes a generic part name."""
    suffix = Path(path).suffix.lower()
    if suffix and suffix != ".bin":
        return suffix
    return _CONTENT_TYPE_EXTENSIONS.get(content_type, suffix or "")


def scan_embedded_attachments(docx_path: Path) -> dict:
    """
    Scan a .docx package for embedded attachments/OLE objects.

    The scanner only reports presence and metadata. It does not extract or
    convert attachment contents.
    """
    try:
        with zipfile.ZipFile(docx_path) as z:
            names = set(z.namelist())
            content_types = _load_content_types(z)
            relationships = _scan_relationship_targets(z)
    except zipfile.BadZipFile:
        return {"total": 0, "items": []}

    candidate_paths = {
        name for name in names
        if name.startswith("word/embeddings/")
        and Path(name).suffix.lower() in _ATTACHMENT_EXTENSIONS
    }
    candidate_paths.update(path for path in relationships if path.startswith("word/embeddings/"))

    items = []
    for index, path in enumerate(sorted(candidate_paths), start=1):
        content_type = content_types.get(path, "")
        rel = relationships.get(path, {})
        extension = _infer_attachment_extension(path, content_type)
        relationship_type = rel.get("relationship_type", "")
        risk = "embedded_attachment_not_converted"
        if extension == ".bin" or relationship_type == "ole_object":
            risk = "embedded_ole_object_not_converted"

        items.append({
            "id": index,
            "type": "embedded_attachment",
            "name": Path(path).name,
            "path": path,
            "extension": extension,
            "content_type": content_type,
            "relationship_type": relationship_type,
            "relationship_id": rel.get("relationship_id", ""),
            "relationship_source": rel.get("relationship_source", ""),
            "risk": risk,
            "message": "Embedded attachment is not converted into Markdown; confirm whether it needs separate extraction.",
        })

    return {
        "total": len(items),
        "items": items,
    }


# ---------------------------------------------------------------------------
# 4. TOC extraction — build heading number map before removing TOC
# ---------------------------------------------------------------------------

# TOC entry patterns:
# Format A (double-nested): [1.1 总体说明 [5](#总体说明)](#总体说明)
_TOC_ENTRY_RE_A = re.compile(
    r"\[(\d[\d.]*\.?)\s+(.+?)\s+\[\d+\]\(#[^)]*\)\]\(#[^)]*\)"
)
# Format B (single-link): [1. 介绍 7](#介绍)  or  [4.6.1.3.1. 模块1安全设计 20](#模块1安全设计)
_TOC_ENTRY_RE_B = re.compile(
    r"\[(\d[\d.]*\.?)\s+(.+?)\s+\d+\]\(#[^)]*\)"
)


def extract_toc_map(lines: list[str]) -> list[dict]:
    """
    Extract an ordered list of {level, title, number} from TOC entries.
    Must be called BEFORE TOC lines are removed.
    Supports both double-nested and single-link TOC formats.
    """
    entries = []
    for line in lines:
        m = _TOC_ENTRY_RE_A.search(line) or _TOC_ENTRY_RE_B.search(line)
        if m:
            number_str = m.group(1)  # e.g. "1.1" or "1.1."
            title = m.group(2).strip()
            # Compute level: count digit segments (ignore trailing dot)
            segments = [s for s in number_str.rstrip(".").split(".") if s]
            level = len(segments)
            entries.append({
                "level": level,
                "title": title,
                "number": number_str,
            })
    return entries


# ---------------------------------------------------------------------------
# 5. Front-page detection and removal
# ---------------------------------------------------------------------------

_TOC_ANCHOR_RE = re.compile(r"\[\]\{#_Toc\d+\s*\.anchor\}")
_TOC_LINK_RE_A = re.compile(r"\[.+?\[\d+\]\(#[^)]*\)\]\(#[^)]*\)")  # double-nested
_TOC_LINK_RE_B = re.compile(r"\[\d[\d.]*\.?\s+.+?\s+\d+\]\(#[^)]*\)")  # single-link
_OLD_TABLE_SEP_RE = re.compile(r"^\s*[-+]+[-+\s]*$")  # matches both --- --- and +---+---+
_GRID_TABLE_LINE_RE = re.compile(r"^\s*[+|]")  # grid table line starting with + or |


def _build_front_page_patterns(config: dict) -> dict:
    """Build compiled regex patterns and thresholds from front_page config."""
    fp = config.get("front_page", {})

    cover_kw = fp.get("cover_keywords", [
        "内部文档", "不得复制", "有限公司", "科技股份",
        "版权所有", "保密等级", "密级", "CONFIDENTIAL",
    ])
    approval_kw = fp.get("approval_keywords", [
        "拟制", "审核", "批准", "审批", "日期",
    ])
    revision_kw = fp.get("revision_keywords", [
        "修订记录", "变更记录", "版本号", "修订版本",
    ])
    toc_word = fp.get("toc_word", "目录")

    def _build_re(keywords, flags=0):
        if not keywords:
            return re.compile(r"(?!)")  # never matches
        return re.compile("(" + "|".join(re.escape(k) for k in keywords) + ")", flags)

    return {
        "cover": _build_re(cover_kw, re.IGNORECASE),
        "approval": _build_re(approval_kw),
        "revision": _build_re(revision_kw),
        "toc_word": re.compile(r"^" + re.escape(toc_word) + r"\s*$"),
        "keyword_line_max_length": fp.get("keyword_line_max_length", 100),
        "consecutive_text_threshold": fp.get("consecutive_text_threshold", 2),
    }


def _is_front_page_line(line: str, patterns: dict) -> bool:
    """Check if a line belongs to front-page content."""
    stripped = line.strip()
    if not stripped:
        return True  # blank lines in front area are OK to remove
    if _TOC_ANCHOR_RE.search(stripped):
        return True
    if _TOC_LINK_RE_A.search(stripped) or _TOC_LINK_RE_B.search(stripped):
        return True
    if patterns["toc_word"].match(stripped):
        return True
    if _OLD_TABLE_SEP_RE.match(stripped):
        return True
    if _GRID_TABLE_LINE_RE.match(stripped):
        return True
    if patterns["cover"].search(stripped):
        return True
    max_len = patterns["keyword_line_max_length"]
    if patterns["approval"].search(stripped) and len(stripped) < max_len:
        return True
    if patterns["revision"].search(stripped) and len(stripped) < max_len:
        return True
    # Bold-only lines (cover title candidates)
    if re.match(r"^\*\*[^*]+\*\*$", stripped):
        return True
    # Image references
    if re.match(r"^!\[.*?\]\(.*?\)(\{.*?\})?$", stripped):
        return True
    return False


def find_front_page_end(lines: list[str], patterns: dict) -> int:
    """
    Return the index of the first line that is NOT front-page content.
    Scans from the start, stops when hitting an ATX heading or N consecutive
    non-front-page text lines (N = consecutive_text_threshold from config).
    """
    threshold = patterns["consecutive_text_threshold"]
    consecutive_text = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        # ATX heading that's not a TOC anchor → start of body
        if re.match(r"^#{1,6}\s+", stripped) and not _TOC_ANCHOR_RE.search(stripped):
            return i
        if _is_front_page_line(line, patterns):
            consecutive_text = 0
        else:
            consecutive_text += 1
            if consecutive_text >= threshold:
                return i - (threshold - 1)  # rewind to the first non-front line
    return len(lines)  # entire document is front page?? keep nothing


# ---------------------------------------------------------------------------
# 6. Noise cleanup (regex rules)
# ---------------------------------------------------------------------------

def cleanup_noise(text: str, config: dict) -> str:
    """Apply regex cleanup rules, gated by config toggles."""
    cleanup = config.get("cleanup", {})

    # Image references (with optional dimension attributes)
    if cleanup.get("remove_images", True):
        text = re.sub(r"!\[.*?\]\([^)]*\)(\{[^}]*\})?", "", text)

    # Empty anchors: []{#...}
    if cleanup.get("remove_empty_anchors", True):
        text = re.sub(r"\[\]\{#[^}]+\}", "", text)

    # Empty links: [...]()
    if cleanup.get("remove_empty_links", True):
        text = re.sub(r"\[.*?\]\(\)", "", text)

    # Empty HTML comments
    if cleanup.get("remove_empty_html_comments", True):
        text = re.sub(r"<!--\s*-->", "", text)

    # Control characters
    if cleanup.get("remove_control_characters", True):
        text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)

    # Trailing backslash (soft line break)
    if cleanup.get("remove_trailing_backslash", True):
        text = re.sub(r"\\\s*$", "", text, flags=re.MULTILINE)

    # Full-width space indentation at line start
    if cleanup.get("remove_fullwidth_indent", True):
        text = re.sub(r"^[\u3000]+", "", text, flags=re.MULTILINE)

    # HTML raw blocks from Pandoc
    if cleanup.get("remove_html_raw_blocks", True):
        text = re.sub(r"^```\{=html\}\n[\s\S]*?\n```$", "", text, flags=re.MULTILINE)

    # XML residual entities (but NOT &nbsp; which Pandoc uses intentionally in tables)
    if cleanup.get("unescape_xml_entities", True):
        def unescape_non_nbsp(match):
            entity = match.group(0)
            if entity.lower() == "&nbsp;":
                return entity
            return html.unescape(entity)

        text = re.sub(r"&(?:amp|lt|gt|apos|quot|#\d+|#x[\da-fA-F]+);", unescape_non_nbsp, text)

    # Escaped parentheses: \( and \)
    if cleanup.get("unescape_escaped_parens", True):
        text = re.sub(r"\\([()])", r"\1", text)

    # Pandoc span annotations: [text]{.mark}, [text]{.underline}, etc.
    # Preserve the text content, remove the annotation
    if cleanup.get("remove_pandoc_annotations", True):
        annotation_types = cleanup.get("pandoc_annotation_types",
                                       ["mark", "underline", "smallcaps", "strikeout"])
        if annotation_types:
            types_pattern = "|".join(re.escape(t) for t in annotation_types)
            text = re.sub(rf"\[([^\]]*)\]\{{\.(?:{types_pattern})\}}", r"\1", text)

    # Escaped brackets in code contexts: \[ and \]
    if cleanup.get("unescape_escaped_brackets", True):
        text = re.sub(r"\\([\[\]])", r"\1", text)

    # Compress multiple blank lines to at most one blank line
    if cleanup.get("compress_blank_lines", True):
        threshold = cleanup.get("blank_line_threshold", 3)
        text = re.sub(r"\n{" + str(threshold) + r",}", "\n\n", text)

    return text


# ---------------------------------------------------------------------------
# 7. Heading processing
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)$", re.MULTILINE)


def normalize_title(title: str) -> str:
    """Normalize a title for matching: strip whitespace and common punctuation."""
    return re.sub(r"\s+", "", title).strip()


def restore_heading_numbers(text: str, toc_map: list[dict], config: dict) -> tuple[str, list[str]]:
    """
    Restore heading numbers from TOC map and shift all headings down one level.
    Uses a hybrid approach: TOC numbers for matched headings, generated sub-numbers
    for headings deeper than the TOC covers.
    Returns (processed_text, attention_items).
    """
    heading_cfg = config.get("heading", {})
    level_shift = heading_cfg.get("level_shift", 1)
    max_level = heading_cfg.get("max_level", 6)

    attention = []
    lines = text.split("\n")
    result = []

    if toc_map:
        # Detect trailing dot style from TOC
        has_trailing_dot = any(e["number"].endswith(".") for e in toc_map)

        # Build ordinal-aware lookup: (level, normalized_title) -> [entries]
        toc_lookup: dict[tuple, list[dict]] = {}
        for entry in toc_map:
            key = (entry["level"], normalize_title(entry["title"]))
            toc_lookup.setdefault(key, []).append(entry)

        # Track consumption counters for body-side ordinal matching
        body_counters: dict[tuple, int] = {}

        # Track current section hierarchy for generating sub-numbers
        # known_numbers[pandoc_level] = list of int segments from last known heading
        known_numbers: dict[int, list[int]] = {}
        # child_counters[pandoc_level] = sequential counter for non-TOC headings at that level
        child_counters: dict[int, int] = {}

        for line in lines:
            m = re.match(r"^(#{1,6})\s+(.*?)$", line)
            if not m:
                result.append(line)
                continue

            hashes = m.group(1)
            title_text = m.group(2).strip()
            pandoc_level = len(hashes)

            # Shift down (H1 → H2, etc.)
            new_level = min(pandoc_level + level_shift, max_level)
            if pandoc_level + level_shift > max_level:
                attention.append(f"Heading exceeds H{max_level} limit: '{title_text}'")
            new_hashes = "#" * new_level

            # Try to find number from TOC map
            norm_title = normalize_title(title_text)
            lookup_key = (pandoc_level, norm_title)

            body_counters.setdefault(lookup_key, 0)
            body_counters[lookup_key] += 1
            ordinal = body_counters[lookup_key]

            number_str = ""
            if lookup_key in toc_lookup and ordinal <= len(toc_lookup[lookup_key]):
                # TOC match found
                entry = toc_lookup[lookup_key][ordinal - 1]
                number_str = entry["number"]
                # Update hierarchy state
                segs = [int(s) for s in number_str.rstrip(".").split(".") if s]
                known_numbers[pandoc_level] = segs
                # Reset child counters and known_numbers for deeper levels
                for k in list(child_counters.keys()):
                    if k >= pandoc_level:
                        del child_counters[k]
                for k in list(known_numbers.keys()):
                    if k > pandoc_level:
                        del known_numbers[k]
            else:
                # Not in TOC — generate number from parent section context
                parent_level = None
                for pl in range(pandoc_level - 1, 0, -1):
                    if pl in known_numbers:
                        parent_level = pl
                        break

                if parent_level is not None:
                    parent_segs = known_numbers[parent_level]
                    child_counters.setdefault(pandoc_level, 0)
                    child_counters[pandoc_level] += 1

                    # Build number: parent segments + intermediate 1s + child counter
                    segs = list(parent_segs)
                    for intermediate in range(parent_level + 1, pandoc_level):
                        segs.append(child_counters.get(intermediate, 1))
                    segs.append(child_counters[pandoc_level])

                    number_str = ".".join(str(s) for s in segs)
                    if has_trailing_dot:
                        number_str += "."

                    # Update state
                    known_numbers[pandoc_level] = segs
                    for k in list(child_counters.keys()):
                        if k > pandoc_level:
                            del child_counters[k]
                    for k in list(known_numbers.keys()):
                        if k > pandoc_level:
                            del known_numbers[k]

            if number_str:
                result.append(f"{new_hashes} {number_str} {title_text}")
            else:
                result.append(f"{new_hashes} {title_text}")
    else:
        # Fallback: level counter method
        counters = [0] * 7  # index 1-6, always cover all Pandoc heading levels
        for line in lines:
            m = re.match(r"^(#{1,6})\s+(.*?)$", line)
            if m:
                hashes = m.group(1)
                title_text = m.group(2).strip()
                pandoc_level = len(hashes)

                new_level = min(pandoc_level + level_shift, max_level)
                if pandoc_level + level_shift > max_level:
                    attention.append(f"Heading exceeds H{max_level} limit: '{title_text}'")
                new_hashes = "#" * new_level

                # Check if title already has a number prefix
                if re.match(r"^\d[\d.]*\.?\s", title_text):
                    # Already numbered, just shift level
                    result.append(f"{new_hashes} {title_text}")
                else:
                    # Generate number via counter
                    counters[pandoc_level] += 1
                    # Reset all sub-level counters
                    for j in range(pandoc_level + 1, max_level + 1):
                        counters[j] = 0

                    parts = []
                    for j in range(1, pandoc_level + 1):
                        parts.append(str(max(counters[j], 1)))
                    number = ".".join(parts)

                    result.append(f"{new_hashes} {number} {title_text}")
            else:
                result.append(line)

    return "\n".join(result), attention


# ---------------------------------------------------------------------------
# 8. Heading spacing normalization
# ---------------------------------------------------------------------------

def normalize_heading_spacing(text: str) -> str:
    """Ensure exactly one blank line before and after each ATX heading."""
    lines = text.split("\n")
    result = []
    for i, line in enumerate(lines):
        is_heading = bool(re.match(r"^#{1,6}\s+", line))
        if is_heading:
            # Ensure blank line before (unless at start of document)
            if result and result[-1].strip() != "":
                result.append("")
            result.append(line)
            # We'll handle the blank line after in the next iteration
        else:
            # If previous line was a heading and current is non-empty, add blank line
            if result and re.match(r"^#{1,6}\s+", result[-1]) and line.strip() != "":
                result.append("")
            result.append(line)

    # Clean up any resulting triple+ blank lines
    text = "\n".join(result)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# ---------------------------------------------------------------------------
# 9. Document title insertion
# ---------------------------------------------------------------------------

def make_doc_title(docx_path: Path) -> str:
    """Generate H1 document title from filename (without extension)."""
    name_no_ext = docx_path.stem
    return f"# {name_no_ext}"


# ---------------------------------------------------------------------------
# 10. Heading number validation
# ---------------------------------------------------------------------------

def validate_heading_numbers(text: str) -> list[str]:
    """Check heading number sequence for gaps or inconsistencies."""
    attention = []
    prev_numbers: dict[int, list[int]] = {}  # level -> last number segments

    for m in _HEADING_RE.finditer(text):
        hashes = m.group(1)
        title = m.group(2).strip()
        level = len(hashes)

        # Extract number prefix if present
        num_match = re.match(r"^(\d[\d.]*\.?)\s", title)
        if not num_match:
            continue

        num_str = num_match.group(1).rstrip(".")
        segments = [int(s) for s in num_str.split(".") if s]

        if level in prev_numbers:
            prev_segs = prev_numbers[level]
            # Same-level siblings should increment by 1
            if len(prev_segs) == len(segments) and segments[:-1] == prev_segs[:-1]:
                expected = prev_segs[-1] + 1
                actual = segments[-1]
                if actual != expected and actual != 1:  # reset to 1 is OK (new parent)
                    attention.append(
                        f"Heading number gap at level {level}: "
                        f"expected ...{expected} but got ...{actual} in '{title}'"
                    )

        prev_numbers[level] = segments

    return attention


# ---------------------------------------------------------------------------
# 11. Risk scanning for Stage 2 targeted processing
# ---------------------------------------------------------------------------

_TERMINAL_PUNCT_RE = re.compile(r"[。；：！？.;:!?\)）》」』\]】]\s*$")
_STRUCTURAL_LINE_RE = re.compile(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|\||\s*$|```|>)")
_GRID_TABLE_RE = re.compile(r"^\+[-=+]+\+")
_OLD_STYLE_TABLE_RE = re.compile(r"^\s*-{3,}\s+-{3,}")
_PIPE_TABLE_SEPARATOR_RE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+$")
_NOISE_PATTERNS = [
    (re.compile(r"\{\.(?:mark|underline|smallcaps|strikeout)\}"), "pandoc_annotation"),
    (re.compile(r"^!\["), "image_remnant"),
    (re.compile(r"\[\]\{#[^}]+\}"), "anchor_remnant"),
]


def scan_risks(text: str) -> dict:
    """
    Scan .md for lines that need Stage 2 AI attention.
    Returns a risk report with line numbers, types, and clustered regions.
    """
    lines = text.split("\n")
    risks = []

    # --- 1. Paragraph merge candidates ---
    for i in range(1, len(lines)):
        curr = lines[i]
        prev = lines[i - 1]
        if (prev.strip()
                and curr.strip()
                and not _STRUCTURAL_LINE_RE.match(prev)
                and not _STRUCTURAL_LINE_RE.match(curr)
                and not _TERMINAL_PUNCT_RE.search(prev)):
            risks.append({
                "line": i + 1,
                "type": "paragraph_merge",
                "preview": f"{prev.strip()[-40:]}|{curr.strip()[:40]}",
            })

    # --- 2. Non-GFM table patterns ---
    in_grid_table = False
    grid_table_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _GRID_TABLE_RE.match(stripped):
            if not in_grid_table:
                grid_table_start = i + 1
                in_grid_table = True
        elif in_grid_table and not stripped.startswith(("+", "|")):
            risks.append({
                "line": grid_table_start,
                "type": "grid_table",
                "end_line": i,
            })
            in_grid_table = False
        if _OLD_STYLE_TABLE_RE.match(stripped):
            risks.append({"line": i + 1, "type": "old_style_table"})
    if in_grid_table:
        risks.append({
            "line": grid_table_start,
            "type": "grid_table",
            "end_line": len(lines),
        })

    # --- 3. Residual noise ---
    for i, line in enumerate(lines):
        for pattern, noise_type in _NOISE_PATTERNS:
            if pattern.search(line):
                risks.append({"line": i + 1, "type": noise_type})
                break

    # --- 4. Heading level jumps ---
    headings = []
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            headings.append((i + 1, len(m.group(1)), m.group(2).strip()))
    for j in range(1, len(headings)):
        _, prev_level, _ = headings[j - 1]
        curr_line, curr_level, _ = headings[j]
        if curr_level > prev_level + 1:
            risks.append({
                "line": curr_line,
                "type": "heading_jump",
                "detail": f"H{prev_level}->H{curr_level}",
            })

    # --- 5. Pipe table alignment issues ---
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if i + 1 < len(lines) and _PIPE_TABLE_SEPARATOR_RE.match(lines[i + 1].strip()):
                cols_header = stripped.count("|") - 1
                cols_sep = lines[i + 1].strip().count("|") - 1
                if cols_header != cols_sep:
                    risks.append({
                        "line": i + 1,
                        "type": "table_alignment",
                        "detail": f"header={cols_header}cols, separator={cols_sep}cols",
                    })

    # --- 6. Template directive italic inconsistency ---
    in_italic_section = False
    italic_count = 0
    plain_guidance_lines = []
    for i, line in enumerate(lines):
        if re.match(r"^#{2,6}\s", line):
            if in_italic_section and italic_count > 0 and plain_guidance_lines:
                for pl in plain_guidance_lines:
                    risks.append({"line": pl, "type": "italic_missing"})
            in_italic_section = False
            italic_count = 0
            plain_guidance_lines = []
        elif re.match(r"^\*[^*]+\*$", line.strip()):
            italic_count += 1
            in_italic_section = True
        elif (in_italic_section and line.strip()
              and not _STRUCTURAL_LINE_RE.match(line)
              and len(line.strip()) > 20):
            plain_guidance_lines.append(i + 1)
    # Flush last section
    if in_italic_section and italic_count > 0 and plain_guidance_lines:
        for pl in plain_guidance_lines:
            risks.append({"line": pl, "type": "italic_missing"})

    risks.sort(key=lambda r: r["line"])
    total_lines = len(lines)
    regions = _cluster_risks(risks, context_lines=5, max_line=total_lines)

    return {
        "total_lines": len(lines),
        "total_risks": len(risks),
        "total_regions": len(regions),
        "risks": risks,
        "regions": regions,
    }


def _cluster_risks(risks: list[dict], context_lines: int = 5, max_line: int = 0) -> list[dict]:
    """
    Group nearby risks into read regions. Each region = one Read call.
    Risks within 2*context_lines of each other are merged.
    """
    if not risks:
        return []

    merge_gap = context_lines * 2
    regions = []
    current = {
        "start_line": max(1, risks[0]["line"] - context_lines),
        "end_line": min(risks[0].get("end_line", risks[0]["line"]) + context_lines,
                        max_line) if max_line else risks[0].get("end_line", risks[0]["line"]) + context_lines,
        "risk_count": 1,
        "types": {risks[0]["type"]},
    }

    for risk in risks[1:]:
        risk_start = risk["line"] - context_lines
        risk_end = risk.get("end_line", risk["line"]) + context_lines
        if max_line:
            risk_end = min(risk_end, max_line)
        if risk_start <= current["end_line"] + merge_gap:
            current["end_line"] = max(current["end_line"], risk_end)
            current["risk_count"] += 1
            current["types"].add(risk["type"])
        else:
            current["types"] = sorted(current["types"])
            regions.append(current)
            current = {
                "start_line": max(1, risk_start),
                "end_line": risk_end,
                "risk_count": 1,
                "types": {risk["type"]},
            }

    current["types"] = sorted(current["types"])
    regions.append(current)
    return regions


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def relative_to_input_root(source_path: Path, input_root: Path | None) -> Path:
    """Return the source path relative to a directory input, or just the file name."""
    if input_root is None:
        return Path(source_path.name)
    try:
        return source_path.relative_to(input_root)
    except ValueError:
        return Path(source_path.name)


def convert_one(source_path: Path, output_dir: Path, config: dict, input_root: Path | None = None) -> dict:
    """Convert a single `.doc`/`.docx` source file into markdown."""
    source_path = source_path.resolve()
    relative_source = relative_to_input_root(source_path, input_root)
    source_basename = source_path.name
    source_label = str(relative_source)
    source_suffix = source_path.suffix.lower()
    docx_path = source_path

    if source_suffix == ".doc":
        print(f"  Pre-converting .doc to .docx: {source_basename}")
        converted_docx_path, error = convert_doc_via_external_script(source_path)
        if converted_docx_path is None:
            return {
                "file_name": source_label,
                "conversion_result": "failed",
                "script_result": {
                    "status": "failed",
                    "error": f".doc pre-conversion failed: {error}",
                },
            }
        docx_path = converted_docx_path

    basename = docx_path.name
    name_no_ext = docx_path.stem
    output_subdir = output_dir / relative_source.parent
    output_subdir.mkdir(parents=True, exist_ok=True)
    md_path = output_subdir / f"{name_no_ext}.md"

    report = {
        "file_name": source_label,
        "conversion_result": "success",
        "script_result": {"status": "success", "output_md": str(md_path)},
        "attention": [],
    }
    if source_suffix == ".doc":
        report["script_result"]["preconverted_docx"] = str(docx_path)

    # Stage 1a: Pandoc conversion
    pandoc_ok = run_pandoc(docx_path, md_path)

    if not pandoc_ok:
        # Try OOXML fallback
        print(f"  Pandoc failed, trying OOXML fallback for {basename}...")
        text = ooxml_fallback(docx_path)
        if text is None:
            report["conversion_result"] = "failed"
            report["script_result"]["status"] = "failed"
            report["script_result"]["error"] = "Both Pandoc and OOXML fallback failed"
            return report
        report["script_result"]["method"] = "ooxml_fallback"
        report["attention"].append("Used OOXML fallback — structure (headings/tables/lists) is lost")
    else:
        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()

    # Stage 1b: Extract TOC map before removing front page
    lines = text.split("\n")
    toc_map = extract_toc_map(lines)

    # Stage 1c: Remove front page
    patterns = _build_front_page_patterns(config)
    front_end = find_front_page_end(lines, patterns)
    body_lines = lines[front_end:]
    text = "\n".join(body_lines)

    # Stage 1d: Noise cleanup
    text = cleanup_noise(text, config)

    # Stage 1e: Restore heading numbers + shift levels
    text, heading_attention = restore_heading_numbers(text, toc_map, config)
    report["attention"].extend(heading_attention)

    # Stage 1f: Insert document title as H1
    doc_title = make_doc_title(docx_path)
    text = doc_title + "\n\n" + text.lstrip("\n")

    # Stage 1g: Normalize heading spacing
    text = normalize_heading_spacing(text)

    # Stage 1h: Final cleanup — strip trailing whitespace on each line, ensure trailing newline
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = text.strip() + "\n"

    # Validate heading numbers
    validation_attention = validate_heading_numbers(text)
    report["attention"].extend(validation_attention)

    # Write output
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(text)

    # Stage 1i: Scan Markdown risks and OOXML embedded attachments
    attachment_scan = scan_embedded_attachments(docx_path)
    if attachment_scan["total"] > 0:
        report["script_result"]["embedded_attachments"] = attachment_scan["total"]
        report["attention"].append(
            f"Detected {attachment_scan['total']} embedded attachment(s); "
            "attachment contents were not converted into Markdown"
        )
        print(f"  WARNING: Embedded attachments detected: {attachment_scan['total']}")
        for item in attachment_scan["items"]:
            extension = item["extension"] or "unknown"
            print(f"    - {item['path']} ({extension})")
        print("  Attachment contents are not converted into Markdown; confirm whether separate extraction is needed.")

    scan = scan_risks(text)
    scan["source"] = f"{name_no_ext}.md"
    scan["total_attachment_risks"] = attachment_scan["total"]
    scan["attachments"] = attachment_scan
    scan_path = output_subdir / f"{name_no_ext}.scan.json"
    with open(scan_path, "w", encoding="utf-8") as f:
        json.dump(scan, f, ensure_ascii=False, indent=2)
    report["scan_result"] = {
        "total_risks": scan["total_risks"],
        "total_regions": scan["total_regions"],
        "total_attachments": attachment_scan["total"],
        "scan_path": str(scan_path),
    }

    # Clean empty attention list
    if not report["attention"]:
        del report["attention"]

    return report


def main():
    parser = argparse.ArgumentParser(description="docs2md Stage 1: Convert .doc/.docx to .md")
    parser.add_argument("input", help="Input .doc/.docx file or directory containing .doc/.docx files")
    parser.add_argument("-o", "--output-dir", default="md", help="Output directory (default: ./md)")
    parser.add_argument("--config", default=None, help="Config file path (default: config.yaml in skill directory)")
    parser.add_argument("--report", action="store_true", help="Generate JSON reports in <output-dir>/reports/")
    args = parser.parse_args()

    # Determine config file path
    if args.config:
        config_path = args.config
    else:
        script_dir = Path(__file__).resolve().parent
        config_path = script_dir.parent / "config.yaml"

    if not Path(config_path).exists():
        print(f"Warning: config file not found: {config_path}, using defaults", file=sys.stderr)
        config = {}
    else:
        config = load_config(str(config_path))

    input_path = resolve_path(args.input)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    input_root = input_path if input_path.is_dir() else None

    # Determine file list
    input_files = discover_input_files(str(input_path))
    if not input_files:
        if not input_path.exists():
            print(f"ERROR: Input path does not exist: {input_path}", file=sys.stderr)
        else:
            print("No .doc/.docx files found.", file=sys.stderr)
        sys.exit(1)

    # Process each file
    reports = []
    success_count = 0
    fail_count = 0

    for source_path in input_files:
        source_label = str(relative_to_input_root(source_path, input_root))
        print(f"Processing: {source_label}")
        report = convert_one(source_path, output_dir, config, input_root)
        reports.append(report)
        if report["conversion_result"] == "success":
            success_count += 1
        else:
            fail_count += 1

    # Summary
    print(f"\nDone: {success_count} success, {fail_count} failed, {len(input_files)} total")

    # Write reports as JSON (only when --report is specified)
    if args.report:
        reports_dir = output_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        for report in reports:
            report_name = Path(report["file_name"]).with_suffix(".json")
            report_path = reports_dir / report_name
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"Report: {report_path}")

    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    configure_stdio()
    main()
